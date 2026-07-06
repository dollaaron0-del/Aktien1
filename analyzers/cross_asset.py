"""
Cross-Asset Signal Engine

Erkennt makroökonomische Signale aus verwandten Märkten, die Aktien-Bewegungen
oft vorwegnehmen. Datenquellen (alle kostenlos via yfinance):

  ^DXY  – US-Dollar-Index
  TLT   – 20-Jahres-Treasury ETF
  HYG   – High-Yield Corporate Bond ETF
  GLD   – Gold ETF
  ^VIX  – Volatility Index
  ^IRX  – 3-Monat Treasury Yield
  ^TNX  – 10-Jahres Treasury Yield

Signale:
  1. Yield-Curve  (TNX − IRX) — negativ = Rezessions-Warnung
  2. Credit-Spread (HYG Momentum) — fallend = Risk-Off
  3. Dollar-Stärke (DXY 20-Tage-Momentum)
  4. Bond-Equity-Divergenz (TLT vs SPY)
  5. Risk-Appetite-Score (0.0–1.0)

Empfehlung: "RISK_ON" | "NEUTRAL" | "RISK_OFF"
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "cross_asset_cache.json")
_CACHE_TTL_HOURS = 2

_TICKERS = {
    "dxy": "DX-Y.NYB",   # yfinance symbol for DXY
    "tlt": "TLT",
    "hyg": "HYG",
    "gld": "GLD",
    "vix": "^VIX",
    "irx": "^IRX",
    "tnx": "^TNX",
    "spy": "SPY",
}


@dataclass
class CrossAssetSnapshot:
    timestamp: str
    yield_curve_spread: Optional[float]       # TNX - IRX; < 0 = inverted
    yield_curve_inverted: bool
    hyg_momentum: Optional[float]             # % change over period
    dxy_momentum: Optional[float]             # % change, 20-day
    bond_equity_divergence: Optional[float]   # correlation sign; same dir = uncertainty
    vix_level: Optional[float]
    risk_appetite_score: float                # 0.0 – 1.0
    recommendation: str                       # RISK_ON | NEUTRAL | RISK_OFF
    signals: dict = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        lines = [
            "=== Cross-Asset Macro Signals ===",
            f"Timestamp       : {self.timestamp}",
            f"Yield Curve     : {self.yield_curve_spread:.2f}% ({'INVERTED' if self.yield_curve_inverted else 'normal'})"
            if self.yield_curve_spread is not None else "Yield Curve     : N/A",
            f"HYG Momentum    : {self.hyg_momentum:+.2f}%"
            if self.hyg_momentum is not None else "HYG Momentum    : N/A",
            f"DXY Momentum    : {self.dxy_momentum:+.2f}%"
            if self.dxy_momentum is not None else "DXY Momentum    : N/A",
            f"VIX Level       : {self.vix_level:.1f}"
            if self.vix_level is not None else "VIX Level       : N/A",
            f"Risk Appetite   : {self.risk_appetite_score:.2f}",
            f"Recommendation  : {self.recommendation}",
        ]
        return "\n".join(lines)


class CrossAssetSignals:
    """Fetches and scores cross-asset macro signals."""

    def __init__(self) -> None:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self, days: int = 30) -> CrossAssetSnapshot:
        cached = self._load_cache()
        if cached is not None:
            log.debug("cross_asset: using cached snapshot")
            return cached

        log.info("cross_asset: fetching live data (days=%d)", days)
        snapshot = self._build_snapshot(days)
        self._save_cache(snapshot)
        return snapshot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_snapshot(self, days: int) -> CrossAssetSnapshot:
        period = f"{days}d"
        data: dict = {}

        for key, sym in _TICKERS.items():
            try:
                hist = yf.Ticker(sym).history(period=period)
                if not hist.empty:
                    data[key] = hist["Close"]
            except Exception as exc:
                log.warning("cross_asset: failed to fetch %s (%s): %s", key, sym, exc)

        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

        # 1. Yield-Curve signal
        yield_spread: Optional[float] = None
        inverted = False
        if "tnx" in data and "irx" in data and len(data["tnx"]) and len(data["irx"]):
            try:
                tnx_val = float(data["tnx"].iloc[-1])
                irx_val = float(data["irx"].iloc[-1])
                yield_spread = round(tnx_val - irx_val, 3)
                inverted = yield_spread < 0
            except Exception as exc:
                log.warning("cross_asset: yield curve calc failed: %s", exc)

        # 2. Credit-Spread / HYG momentum
        hyg_mom: Optional[float] = None
        if "hyg" in data and len(data["hyg"]) >= 5:
            try:
                hyg_mom = round(
                    (float(data["hyg"].iloc[-1]) / float(data["hyg"].iloc[0]) - 1) * 100, 3
                )
            except Exception as exc:
                log.warning("cross_asset: HYG momentum calc failed: %s", exc)

        # 3. Dollar-Stärke: DXY 20-Tage-Momentum
        dxy_mom: Optional[float] = None
        if "dxy" in data and len(data["dxy"]) >= 2:
            try:
                dxy_series = data["dxy"]
                lookback = min(20, len(dxy_series) - 1)
                dxy_mom = round(
                    (float(dxy_series.iloc[-1]) / float(dxy_series.iloc[-1 - lookback]) - 1) * 100, 3
                )
            except Exception as exc:
                log.warning("cross_asset: DXY momentum calc failed: %s", exc)

        # 4. Bond-Equity divergence: positive = same direction = uncertainty
        bond_eq_div: Optional[float] = None
        if "tlt" in data and "spy" in data and len(data["tlt"]) >= 5 and len(data["spy"]) >= 5:
            try:
                tlt_ret = float(data["tlt"].iloc[-1]) / float(data["tlt"].iloc[0]) - 1
                spy_ret = float(data["spy"].iloc[-1]) / float(data["spy"].iloc[0]) - 1
                bond_eq_div = round(tlt_ret * spy_ret, 6)  # positive = same direction
            except Exception as exc:
                log.warning("cross_asset: bond-equity divergence calc failed: %s", exc)

        # 5. VIX level
        vix_val: Optional[float] = None
        if "vix" in data and len(data["vix"]):
            try:
                vix_val = round(float(data["vix"].iloc[-1]), 2)
            except Exception:
                pass

        risk_score, signals = self._compute_risk_appetite(
            yield_spread, inverted, hyg_mom, dxy_mom, bond_eq_div, vix_val
        )
        recommendation = self._score_to_recommendation(risk_score)

        return CrossAssetSnapshot(
            timestamp=now,
            yield_curve_spread=yield_spread,
            yield_curve_inverted=inverted,
            hyg_momentum=hyg_mom,
            dxy_momentum=dxy_mom,
            bond_equity_divergence=bond_eq_div,
            vix_level=vix_val,
            risk_appetite_score=round(risk_score, 3),
            recommendation=recommendation,
            signals=signals,
        )

    def _compute_risk_appetite(
        self,
        yield_spread: Optional[float],
        inverted: bool,
        hyg_mom: Optional[float],
        dxy_mom: Optional[float],
        bond_eq_div: Optional[float],
        vix_val: Optional[float],
    ) -> tuple[float, dict]:
        scores: list[float] = []
        signals: dict = {}

        # Yield curve: inverted → bearish (0.1), normal → bullish (0.8)
        if yield_spread is not None:
            yc_score = 0.1 if inverted else min(1.0, 0.5 + yield_spread * 0.05)
            scores.append(yc_score)
            signals["yield_curve"] = {"spread": yield_spread, "score": round(yc_score, 3)}

        # HYG momentum: rising credit = risk-on
        if hyg_mom is not None:
            hyg_score = min(1.0, max(0.0, 0.5 + hyg_mom * 0.1))
            scores.append(hyg_score)
            signals["hyg_momentum"] = {"pct": hyg_mom, "score": round(hyg_score, 3)}

        # DXY: strong dollar bad for risk assets
        if dxy_mom is not None:
            dxy_score = min(1.0, max(0.0, 0.5 - dxy_mom * 0.05))
            scores.append(dxy_score)
            signals["dxy_momentum"] = {"pct": dxy_mom, "score": round(dxy_score, 3)}

        # Bond-Equity divergence: same direction = uncertainty → lower risk appetite
        if bond_eq_div is not None:
            div_score = 0.4 if bond_eq_div > 0 else 0.65
            scores.append(div_score)
            signals["bond_equity_div"] = {"value": bond_eq_div, "score": div_score}

        # VIX: high VIX → risk-off
        if vix_val is not None:
            if vix_val < 15:
                vix_score = 0.85
            elif vix_val < 20:
                vix_score = 0.65
            elif vix_val < 30:
                vix_score = 0.40
            else:
                vix_score = 0.15
            scores.append(vix_score)
            signals["vix"] = {"level": vix_val, "score": vix_score}

        risk_score = sum(scores) / len(scores) if scores else 0.5
        return risk_score, signals

    @staticmethod
    def _score_to_recommendation(score: float) -> str:
        if score >= 0.65:
            return "RISK_ON"
        if score <= 0.35:
            return "RISK_OFF"
        return "NEUTRAL"

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self) -> Optional[CrossAssetSnapshot]:
        try:
            if not os.path.exists(_CACHE_PATH):
                return None
            with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            ts_str = raw.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str.rstrip("Z"))
            if datetime.now(timezone.utc).replace(tzinfo=None) - ts > timedelta(hours=_CACHE_TTL_HOURS):
                return None
            return CrossAssetSnapshot(**raw)
        except Exception as exc:
            log.warning("cross_asset: cache load failed: %s", exc)
            return None

    def _save_cache(self, snapshot: CrossAssetSnapshot) -> None:
        try:
            payload = asdict(snapshot)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_CACHE_PATH), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, _CACHE_PATH)
            log.debug("cross_asset: cache saved")
        except Exception as exc:
            log.warning("cross_asset: cache save failed: %s", exc)
