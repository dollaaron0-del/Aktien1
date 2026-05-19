"""
Enhanced Options Flow Intelligence — erkennt Smart-Money-Aktivität via yfinance.
Signale: Put/Call-Ratio, IV-Skew, Unusual-Volume, Deep-OTM Call-OI.
Cache: data/options_intelligence.json, TTL 1 Stunde.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "options_intelligence.json"
)
_CACHE_TTL_HOURS = 1

_HIGH_VOLUME_MULTIPLIER = 5.0   # > 5x avg volume = unusual
_BULL_PC_THRESHOLD = 0.5
_BEAR_PC_THRESHOLD = 1.5
_MIN_PREMIUM_SCORE_HIGH = 0.70
_MIN_PREMIUM_SCORE_MEDIUM = 0.40


@dataclass
class TopStrike:
    strike: float
    open_interest: int
    last_price: float


@dataclass
class OptionsSnapshot:
    ticker: str
    timestamp: str
    expiration: Optional[str]
    put_call_ratio: Optional[float]
    iv_skew: Optional[float]           # puts IV - calls IV; > 0 = bearish hedge buying
    top_call_strikes: List[dict] = field(default_factory=list)
    unusual_volume: bool = False
    smart_money_signal: str = "NEUTRAL"   # BULLISH | BEARISH | NEUTRAL
    premium_score: float = 0.5            # 0.0 – 1.0
    signal_strength: str = "LOW"          # HIGH | MEDIUM | LOW
    notes: List[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        pc = f"{self.put_call_ratio:.2f}" if self.put_call_ratio is not None else "N/A"
        skew = f"{self.iv_skew:+.4f}" if self.iv_skew is not None else "N/A"
        lines = [
            f"=== Options Intelligence: {self.ticker} ===",
            f"Timestamp: {self.timestamp} | Expiration: {self.expiration or 'N/A'}",
            f"Put/Call Ratio: {pc} | IV Skew: {skew} | Unusual Volume: {self.unusual_volume}",
            f"Smart Money: {self.smart_money_signal} | Premium Score: {self.premium_score:.2f} | Strength: {self.signal_strength}",
        ]
        for s in self.top_call_strikes:
            lines.append(f"  CallOI Strike ${s['strike']:.1f} | OI {s['open_interest']} | Last ${s['last_price']:.2f}")
        if self.notes:
            lines.append("Notes: " + "; ".join(self.notes))
        return "\n".join(lines)


class OptionsIntelligence:
    """Analyses options chain data from yfinance for smart-money signals."""

    def __init__(self) -> None:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, ticker: str) -> OptionsSnapshot:
        cached = self._load_cache(ticker)
        if cached is not None:
            log.debug("options_intelligence: using cached snapshot for %s", ticker)
            return cached

        log.info("options_intelligence: analyzing %s", ticker)
        snapshot = self._build_snapshot(ticker)
        self._save_cache(snapshot)
        return snapshot

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def _build_snapshot(self, ticker: str) -> OptionsSnapshot:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        neutral = OptionsSnapshot(
            ticker=ticker,
            timestamp=now,
            expiration=None,
            put_call_ratio=None,
            iv_skew=None,
            smart_money_signal="NEUTRAL",
            premium_score=0.5,
            signal_strength="LOW",
        )

        try:
            ticker_obj = yf.Ticker(ticker)
            expirations = ticker_obj.options
        except Exception as exc:
            log.warning("options_intelligence: failed to fetch options list for %s: %s", ticker, exc)
            neutral.notes.append(f"yfinance error: {exc}")
            return neutral

        if not expirations:
            neutral.notes.append("no options expirations available")
            return neutral

        exp = expirations[0]

        try:
            chain = ticker_obj.option_chain(exp)
            calls = chain.calls
            puts = chain.puts
        except Exception as exc:
            log.warning("options_intelligence: failed to fetch chain for %s/%s: %s", ticker, exp, exc)
            neutral.notes.append(f"chain fetch error: {exc}")
            return neutral

        notes: list[str] = []

        # --- Put/Call Volume Ratio ---
        pc_ratio: Optional[float] = None
        call_vol = 0
        put_vol = 0
        try:
            if calls is not None and not calls.empty:
                call_vol = int(calls["volume"].fillna(0).sum())
            if puts is not None and not puts.empty:
                put_vol = int(puts["volume"].fillna(0).sum())
            if call_vol + put_vol > 0:
                pc_ratio = round(put_vol / max(call_vol, 1), 4)
        except Exception as exc:
            log.warning("options_intelligence: pc ratio calc failed for %s: %s", ticker, exc)

        # --- IV Skew ---
        iv_skew: Optional[float] = None
        try:
            if (
                calls is not None and not calls.empty
                and puts is not None and not puts.empty
                and "impliedVolatility" in calls.columns
                and "impliedVolatility" in puts.columns
            ):
                calls_iv = calls["impliedVolatility"].fillna(0)
                puts_iv = puts["impliedVolatility"].fillna(0)
                if calls_iv.mean() > 0 or puts_iv.mean() > 0:
                    iv_skew = round(float(puts_iv.mean()) - float(calls_iv.mean()), 6)
        except Exception as exc:
            log.warning("options_intelligence: IV skew calc failed for %s: %s", ticker, exc)

        # --- Top Call Strikes by Open Interest ---
        top_call_strikes: list[dict] = []
        try:
            if calls is not None and not calls.empty and "openInterest" in calls.columns:
                top3 = calls.nlargest(3, "openInterest")[["strike", "openInterest", "lastPrice"]]
                for _, row in top3.iterrows():
                    top_call_strikes.append({
                        "strike": float(row.get("strike", 0)),
                        "open_interest": int(row.get("openInterest", 0) or 0),
                        "last_price": float(row.get("lastPrice", 0) or 0),
                    })
        except Exception as exc:
            log.warning("options_intelligence: top strikes calc failed for %s: %s", ticker, exc)

        # --- Unusual Volume Detection ---
        # Heuristic: if total options volume > 5× avg daily volume proxy (open interest / 30)
        unusual_volume = False
        try:
            total_volume = call_vol + put_vol
            total_oi = 0
            if calls is not None and not calls.empty and "openInterest" in calls.columns:
                total_oi += int(calls["openInterest"].fillna(0).sum())
            if puts is not None and not puts.empty and "openInterest" in puts.columns:
                total_oi += int(puts["openInterest"].fillna(0).sum())
            if total_oi > 0:
                avg_daily_proxy = total_oi / 30
                if total_volume > avg_daily_proxy * _HIGH_VOLUME_MULTIPLIER:
                    unusual_volume = True
                    notes.append(f"unusual volume detected: {total_volume} vs avg proxy {avg_daily_proxy:.0f}")
        except Exception as exc:
            log.warning("options_intelligence: unusual volume calc failed for %s: %s", ticker, exc)

        # --- Premium Score ---
        premium_score = self._compute_premium_score(pc_ratio, iv_skew, unusual_volume, top_call_strikes)

        # --- Smart Money Signal ---
        smart_money_signal, signal_strength = self._classify_signal(
            pc_ratio, iv_skew, unusual_volume, premium_score
        )

        return OptionsSnapshot(
            ticker=ticker,
            timestamp=now,
            expiration=exp,
            put_call_ratio=pc_ratio,
            iv_skew=iv_skew,
            top_call_strikes=top_call_strikes,
            unusual_volume=unusual_volume,
            smart_money_signal=smart_money_signal,
            premium_score=premium_score,
            signal_strength=signal_strength,
            notes=notes,
        )

    def _compute_premium_score(
        self,
        pc_ratio: Optional[float],
        iv_skew: Optional[float],
        unusual_volume: bool,
        top_strikes: list[dict],
    ) -> float:
        scores: list[float] = []

        # PC ratio: low = bullish = high score
        if pc_ratio is not None:
            if pc_ratio < _BULL_PC_THRESHOLD:
                scores.append(0.85)
            elif pc_ratio > _BEAR_PC_THRESHOLD:
                scores.append(0.15)
            else:
                # Linear interpolation between 0.5 and 1.5
                scores.append(round(1.0 - (pc_ratio - 0.5), 3))

        # IV skew: negative = calls more expensive = bullish
        if iv_skew is not None:
            skew_score = min(1.0, max(0.0, 0.5 - iv_skew * 5))
            scores.append(skew_score)

        # Unusual volume: ambiguous but adds conviction
        if unusual_volume:
            scores.append(0.7)

        # Large call OI at high strikes (deep OTM) → conviction bet
        if top_strikes:
            max_oi = max(s["open_interest"] for s in top_strikes)
            oi_score = min(1.0, max_oi / 50_000)
            scores.append(oi_score)

        return round(sum(scores) / len(scores), 3) if scores else 0.5

    def _classify_signal(
        self,
        pc_ratio: Optional[float],
        iv_skew: Optional[float],
        unusual_volume: bool,
        premium_score: float,
    ) -> tuple[str, str]:
        bullish_votes = 0
        bearish_votes = 0

        if pc_ratio is not None:
            if pc_ratio < _BULL_PC_THRESHOLD:
                bullish_votes += 2
            elif pc_ratio > _BEAR_PC_THRESHOLD:
                bearish_votes += 2

        if iv_skew is not None:
            if iv_skew > 0.02:
                bearish_votes += 1   # put protection being bought
            elif iv_skew < -0.02:
                bullish_votes += 1   # call premium elevated

        if unusual_volume:
            # Volume spikes favour whichever side the ratio points to
            if pc_ratio is not None and pc_ratio < 1.0:
                bullish_votes += 1
            else:
                bearish_votes += 1

        if bullish_votes > bearish_votes:
            signal = "BULLISH"
        elif bearish_votes > bullish_votes:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        total_votes = bullish_votes + bearish_votes
        if total_votes >= 4 or (premium_score >= _MIN_PREMIUM_SCORE_HIGH and signal != "NEUTRAL"):
            strength = "HIGH"
        elif total_votes >= 2 or premium_score >= _MIN_PREMIUM_SCORE_MEDIUM:
            strength = "MEDIUM"
        else:
            strength = "LOW"

        return signal, strength

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self, ticker: str) -> Optional[OptionsSnapshot]:
        try:
            if not os.path.exists(_CACHE_PATH):
                return None
            with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                store: dict = json.load(fh)
            entry = store.get(ticker)
            if not entry:
                return None
            ts = datetime.fromisoformat(entry["timestamp"].rstrip("Z"))
            if datetime.utcnow() - ts > timedelta(hours=_CACHE_TTL_HOURS):
                return None
            return OptionsSnapshot(**entry)
        except Exception as exc:
            log.warning("options_intelligence: cache load failed: %s", exc)
            return None

    def _save_cache(self, snapshot: OptionsSnapshot) -> None:
        try:
            store: dict = {}
            if os.path.exists(_CACHE_PATH):
                try:
                    with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                        store = json.load(fh)
                except Exception:
                    pass
            store[snapshot.ticker] = asdict(snapshot)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_CACHE_PATH), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(store, fh, indent=2)
            os.replace(tmp, _CACHE_PATH)
            log.debug("options_intelligence: cache saved for %s", snapshot.ticker)
        except Exception as exc:
            log.warning("options_intelligence: cache save failed: %s", exc)
