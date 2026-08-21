"""
analyzers/eu_market_context.py – EU Marktbarometer + EZB-Kalender.

Liefert für EU-Aktien-Analysen:
  1. Index-Trend: DAX, Euro Stoxx 50, CAC 40, FTSE 100
  2. EZB-Kalender: Tage bis zur nächsten EZB-Sitzung (< 3 Tage = Warnsignal)
  3. Markt-Signal: RISK_ON | NEUTRAL | RISK_OFF

Ähnlich wie CrossAssetSignals – aber EU-spezifisch.
Caching: 2 Stunden (Index-Daten ändern sich nicht in Echtzeit nötig).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_CACHE_TTL_HOURS = 2
_cache: Dict[str, tuple] = {}  # key → (timestamp, snapshot)

# ── EU Index-Ticker (yfinance) ────────────────────────────────────────────────
_INDICES = {
    "DAX":       "^GDAXI",    # Deutschland
    "STOXX50":   "^STOXX50E", # Euro Stoxx 50
    "CAC40":     "^FCHI",     # Frankreich
    "FTSE100":   "^FTSE",     # Großbritannien
}

# ── EZB Ratssitzungen (Zinsentscheide) – veröffentlicht für 2025/2026 ────────
# Quelle: https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html
_ECB_DATES: List[date] = [
    # 2025
    date(2025,  1, 30),
    date(2025,  3,  6),
    date(2025,  4, 17),
    date(2025,  6,  5),
    date(2025,  7, 24),
    date(2025,  9, 11),
    date(2025, 10, 23),
    date(2025, 12, 11),
    # 2026
    date(2026,  1, 29),
    date(2026,  3, 12),
    date(2026,  4, 30),
    date(2026,  6, 11),
    date(2026,  7, 23),
    date(2026,  9, 10),
    date(2026, 10, 22),
    date(2026, 12, 10),
]

# BoE (Bank of England) – für .L Ticker
_BOE_DATES: List[date] = [
    date(2025,  2,  6),
    date(2025,  3, 20),
    date(2025,  5,  8),
    date(2025,  6, 19),
    date(2025,  8,  7),
    date(2025,  9, 18),
    date(2025, 11,  6),
    date(2025, 12, 18),
    date(2026,  2,  5),
    date(2026,  3, 19),
    date(2026,  5,  7),
    date(2026,  6, 18),
    date(2026,  8,  6),
    date(2026,  9, 17),
    date(2026, 11,  5),
    date(2026, 12, 17),
]

# Tage-Schwellenwerte für Warnsignale
_ALERT_DAYS   = 3   # Sofort-Warnung
_CAUTION_DAYS = 7   # Vorsicht


@dataclass
class IndexData:
    name: str
    change_5d_pct: float
    change_20d_pct: float
    above_ma50: bool
    trend: str   # BULL | BEAR | SIDEWAYS


@dataclass
class EUMarketSnapshot:
    indices: Dict[str, IndexData] = field(default_factory=dict)
    ecb_days: Optional[int] = None         # Tage bis nächste EZB-Sitzung (None = > 30d)
    ecb_note: str = ""
    boe_days: Optional[int] = None         # Tage bis nächste BoE-Sitzung
    boe_note: str = ""
    signal: str = "NEUTRAL"                # RISK_ON | NEUTRAL | RISK_OFF

    def to_prompt_block(self, include_boe: bool = False) -> str:
        if not self.indices and not self.ecb_note:
            return ""
        lines = ["=== EU MARKTKONTEXT ==="]

        # Index-Trends
        if self.indices:
            lines.append("EU-Indizes (Trend):")
            for name, idx in self.indices.items():
                trend_icon = "📈" if idx.trend == "BULL" else "📉" if idx.trend == "BEAR" else "➡️"
                lines.append(
                    f"  {trend_icon} {name}: {idx.change_5d_pct:+.1f}% (5d) | "
                    f"{idx.change_20d_pct:+.1f}% (20d) | "
                    f"{'über' if idx.above_ma50 else 'unter'} MA50"
                )

        # EZB-Warnung
        if self.ecb_note:
            lines.append(f"🏦 EZB: {self.ecb_note}")

        # BoE (nur für GB-Ticker)
        if include_boe and self.boe_note:
            lines.append(f"🏦 BoE: {self.boe_note}")

        lines.append(f"Markt-Signal: {self.signal}")
        lines.append(
            "Berücksichtige EU-Marktlage bei Kauf/Verkaufsentscheidung: "
            "RISK_OFF = höhere Hürde für BUY, RISK_ON = Rückenwind."
        )
        return "\n".join(lines)


class EUMarketContext:
    """Lädt EU-Indizes und EZB-Kalender, cached 2 Stunden."""

    def get_snapshot(self) -> Optional[EUMarketSnapshot]:
        cache_key = "eu_market"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cached = _cache.get(cache_key)
        if cached and (now - cached[0]).total_seconds() < _CACHE_TTL_HOURS * 3600:
            return cached[1]

        snapshot = self._build_snapshot()
        if snapshot:
            _cache[cache_key] = (now, snapshot)
        return snapshot

    def _build_snapshot(self) -> Optional[EUMarketSnapshot]:
        try:
            import yfinance as yf
        except ImportError:
            return None

        indices: Dict[str, IndexData] = {}
        for name, yf_ticker in _INDICES.items():
            try:
                hist = yf.Ticker(yf_ticker).history(period="60d")
                if hist.empty or len(hist) < 6:
                    continue
                close = hist["Close"]
                price    = float(close.iloc[-1])
                p_5d     = float(close.iloc[-6]) if len(close) >= 6 else price
                p_20d    = float(close.iloc[-21]) if len(close) >= 21 else float(close.iloc[0])
                ma50     = float(close.tail(50).mean()) if len(close) >= 50 else float(close.mean())
                chg_5d   = (price / p_5d  - 1) * 100
                chg_20d  = (price / p_20d - 1) * 100
                above_ma50 = price > ma50

                if chg_5d > 1.5 and above_ma50:
                    trend = "BULL"
                elif chg_5d < -1.5 or not above_ma50:
                    trend = "BEAR"
                else:
                    trend = "SIDEWAYS"

                indices[name] = IndexData(
                    name=name,
                    change_5d_pct=round(chg_5d, 2),
                    change_20d_pct=round(chg_20d, 2),
                    above_ma50=above_ma50,
                    trend=trend,
                )
                log.debug("EU index %s: trend=%s 5d=%.1f%%", name, trend, chg_5d)
            except Exception as e:
                log.debug("EU index %s: %s", name, e)

        # EZB-Kalender
        ecb_days, ecb_note = self._next_event(_ECB_DATES, "EZB-Ratssitzung")
        boe_days, boe_note = self._next_event(_BOE_DATES, "BoE-Sitzung")

        # Gesamt-Signal
        signal = self._calc_signal(indices, ecb_days)

        return EUMarketSnapshot(
            indices=indices,
            ecb_days=ecb_days,
            ecb_note=ecb_note,
            boe_days=boe_days,
            boe_note=boe_note,
            signal=signal,
        )

    @staticmethod
    def _next_event(dates: List[date], label: str) -> tuple[Optional[int], str]:
        today = date.today()
        future = sorted(d for d in dates if d >= today)
        if not future:
            return None, ""
        next_date = future[0]
        days = (next_date - today).days
        if days <= _ALERT_DAYS:
            return days, (
                f"⚠️  {label} in {days} Tag(en) ({next_date}) – "
                "erhöhte Volatilität möglich, Positionsgröße reduzieren"
            )
        if days <= _CAUTION_DAYS:
            return days, f"{label} in {days} Tagen ({next_date}) – Vorsicht bei neuen Käufen"
        if days <= 14:
            return days, f"{label} in {days} Tagen ({next_date})"
        return None, ""

    @staticmethod
    def _calc_signal(indices: Dict[str, IndexData], ecb_days: Optional[int]) -> str:
        if not indices:
            return "NEUTRAL"

        bull_count = sum(1 for i in indices.values() if i.trend == "BULL")
        bear_count = sum(1 for i in indices.values() if i.trend == "BEAR")
        total = len(indices)

        # EZB nahe: immer NEUTRAL, nicht RISK_ON
        if ecb_days is not None and ecb_days <= _ALERT_DAYS:
            return "NEUTRAL" if bull_count >= bear_count else "RISK_OFF"

        if bull_count > total / 2:
            return "RISK_ON"
        if bear_count > total / 2:
            return "RISK_OFF"
        return "NEUTRAL"
