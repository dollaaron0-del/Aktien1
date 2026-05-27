"""
bot/pre_market_scanner.py – Pre-Market Briefing (90 Min vor Börseneröffnung).

Läuft FRÜHER als die volle Analyse-Runde (die bei 30 Min vor Open startet).
Kein Claude-Aufruf – nur schnelle Daten-Aggregation:

1. Pre-Market Movers  – Aktien die vor Open > ±2% bewegen (yfinance preMarketPrice)
2. Overnight Indizes  – S&P 500 Futures, DAX Futures, Nikkei Schluss
3. Makro-Kalender     – Was steht heute an? (CPI, FOMC, Earnings)
4. Telegram-Briefing  – Kompakte Zusammenfassung, kein LLM

Zweck: Trader weiß bereits beim Aufstehen was los ist, bevor die volle
       Claude-Analyse 60 Minuten später startet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from config import config
from logger import get_logger

log = get_logger(__name__)

# Indizes die wir als Overnight-Barometer nutzen
_OVERNIGHT_INDICES: Dict[str, str] = {
    "S&P 500":    "^GSPC",
    "Nasdaq":     "^IXIC",
    "DAX":        "^GDAXI",
    "Nikkei 225": "^N225",
    "Hang Seng":  "^HSI",
    "Euro Stoxx": "^STOXX50E",
    "FTSE 100":   "^FTSE",
    "VIX":        "^VIX",
}

# Futures als Indikator für die noch nicht geöffneten Märkte
_FUTURES: Dict[str, str] = {
    "S&P 500 Fut": "ES=F",
    "Nasdaq Fut":  "NQ=F",
    "DAX Fut":     "FDAX=F",
    "Gold":        "GC=F",
    "Öl (WTI)":   "CL=F",
    "EUR/USD":     "EURUSD=X",
}

# Mindest-Bewegung (%) für einen Pre-Market Mover
_MOVER_THRESHOLD = 2.0


@dataclass
class PreMarketMover:
    ticker: str
    price_regular: float
    price_pre: float
    change_pct: float
    direction: str    # UP | DOWN


@dataclass
class IndexSnapshot:
    name: str
    change_1d_pct: float
    trend: str     # UP | DOWN | FLAT


@dataclass
class PreMarketBriefing:
    exchange: str
    generated_at: datetime
    movers: List[PreMarketMover] = field(default_factory=list)
    indices: List[IndexSnapshot] = field(default_factory=list)
    futures: List[IndexSnapshot] = field(default_factory=list)
    macro_events_today: List[str] = field(default_factory=list)
    overall_tone: str = "NEUTRAL"    # BULLISH | NEUTRAL | BEARISH
    german_headlines: List[str] = field(default_factory=list)       # ARD/Handelsblatt
    intl_headlines: List[str] = field(default_factory=list)         # Reuters/BBC/CNBC

    def to_telegram(self) -> str:
        lines = [
            f"🌅 *Pre-Market Briefing – {self.exchange}*",
            f"_{self.generated_at.strftime('%d.%m.%Y %H:%M UTC')}_",
            "",
        ]

        # Makro-Ereignisse heute
        if self.macro_events_today:
            lines.append("📅 *Heute:*")
            for ev in self.macro_events_today:
                lines.append(f"  • {ev}")
            lines.append("")

        # Overnight Indizes
        if self.indices:
            lines.append("🌍 *Märkte (Schluss/aktuell):*")
            for idx in self.indices:
                arrow = "📈" if idx.trend == "UP" else "📉" if idx.trend == "DOWN" else "➡️"
                lines.append(f"  {arrow} {idx.name}: {idx.change_1d_pct:+.2f}%")
            lines.append("")

        # Futures
        if self.futures:
            lines.append("⚡ *Futures / FX:*")
            for fut in self.futures:
                arrow = "📈" if fut.trend == "UP" else "📉" if fut.trend == "DOWN" else "➡️"
                lines.append(f"  {arrow} {fut.name}: {fut.change_1d_pct:+.2f}%")
            lines.append("")

        # Pre-Market Movers
        if self.movers:
            lines.append(f"🚀 *Pre-Market Movers (> ±{_MOVER_THRESHOLD:.0f}%):*")
            for m in self.movers:
                icon = "📈" if m.direction == "UP" else "📉"
                lines.append(
                    f"  {icon} {m.ticker}: {m.change_pct:+.1f}%  "
                    f"(${m.price_pre:.2f} vs. ${m.price_regular:.2f} Close)"
                )
            lines.append("")
        else:
            lines.append("📊 Keine signifikanten Pre-Market-Bewegungen")
            lines.append("")

        # Internationale Schlagzeilen (Reuters / BBC / CNBC)
        if self.intl_headlines:
            lines.append("🌐 *International (Reuters/BBC/CNBC):*")
            for hl in self.intl_headlines[:4]:
                lines.append(f"  • {hl}")
            lines.append("")

        # Deutsche Schlagzeilen (ARD / Handelsblatt)
        if self.german_headlines:
            lines.append("🗞 *Schlagzeilen (DE):*")
            for hl in self.german_headlines[:4]:
                lines.append(f"  • {hl}")
            lines.append("")

        # Ton
        tone_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}.get(self.overall_tone, "⚪")
        lines.append(f"Markt-Ton: {tone_emoji} *{self.overall_tone}*")

        return "\n".join(lines)

    def to_console_lines(self) -> List[str]:
        lines = []
        if self.macro_events_today:
            lines.append(f"  📅 Heute: {' | '.join(self.macro_events_today[:3])}")
        if self.movers:
            mover_strs = [
                f"{'↑' if m.direction == 'UP' else '↓'}{m.ticker} {m.change_pct:+.1f}%"
                for m in self.movers[:5]
            ]
            lines.append(f"  🚀 Pre-Market Movers: {', '.join(mover_strs)}")
        bull = sum(1 for i in self.indices if i.trend == "UP")
        bear = sum(1 for i in self.indices if i.trend == "DOWN")
        if self.indices:
            lines.append(f"  🌍 Indizes: {bull} bullisch / {bear} bärisch | Ton: {self.overall_tone}")
        return lines


class PreMarketScanner:
    """
    Scannt Pre-Market Daten und sendet ein Telegram-Briefing.
    Kein Claude-Aufruf – reiner Daten-Aggregator.
    """

    def run(self, exchange: str, watchlist: List[str]) -> Optional[PreMarketBriefing]:
        """
        Führt den Pre-Market-Scan durch und gibt das Briefing zurück.
        exchange: z.B. "NYSE", "XETRA"
        watchlist: Ticker-Liste des Bots
        """
        log.info("Pre-Market-Scan für %s gestartet (%d Ticker)", exchange, len(watchlist))
        t0 = time.monotonic()

        briefing = PreMarketBriefing(
            exchange=exchange,
            generated_at=datetime.utcnow(),
        )

        # 1. Makro-Kalender für heute
        briefing.macro_events_today = self._get_macro_today()

        # 2. Overnight Indizes
        briefing.indices = self._get_index_snapshots(_OVERNIGHT_INDICES)

        # 3. Futures
        briefing.futures = self._get_index_snapshots(_FUTURES)

        # 4. Pre-Market Movers aus Watchlist
        briefing.movers = self._get_premarket_movers(watchlist)

        # 5. Deutsche Schlagzeilen (ARD Tagesschau / Handelsblatt)
        briefing.german_headlines = self._get_german_headlines()

        # 6. Internationale Schlagzeilen (Reuters / BBC / CNBC)
        briefing.intl_headlines = self._get_intl_headlines()

        # 7. Gesamt-Ton berechnen
        briefing.overall_tone = self._calc_tone(briefing)

        dur = time.monotonic() - t0
        log.info(
            "Pre-Market-Scan %s fertig: %d Movers, Ton=%s (%.1fs)",
            exchange, len(briefing.movers), briefing.overall_tone, dur,
        )
        return briefing

    # ── Makro-Kalender ────────────────────────────────────────────────────────

    def _get_macro_today(self) -> List[str]:
        events: List[str] = []
        try:
            from analyzers.macro_calendar import MacroCalendar
            cal = MacroCalendar()
            upcoming = cal.get_upcoming_events(days_ahead=1)
            today = date.today()
            for ev in upcoming:
                if ev.release_date == today:
                    impact_icon = "🔴" if ev.impact == "HIGH" else "🟡" if ev.impact == "MEDIUM" else "⚪"
                    events.append(f"{impact_icon} {ev.name}")
        except Exception as e:
            log.debug("Makro-Kalender fehlgeschlagen: %s", e)

        # EZB / BoE aus eu_market_context
        try:
            from analyzers.eu_market_context import _ECB_DATES, _BOE_DATES
            today = date.today()
            if today in _ECB_DATES:
                events.append("🏦 EZB Zinsentscheid HEUTE")
            elif today in _BOE_DATES:
                events.append("🏦 BoE Zinsentscheid HEUTE")
        except Exception:
            pass

        return events or ["Keine High-Impact Events heute"]

    # ── Deutsche Schlagzeilen ─────────────────────────────────────────────────

    def _get_german_headlines(self) -> List[str]:
        try:
            from collectors.german_media_collector import GermanMediaCollector
            items = GermanMediaCollector(lookback_hours=24).collect_market_context()
            return [
                f"{it['source']}: {it['title']}"
                for it in items[:5]
            ]
        except Exception as e:
            log.debug("Deutsche Schlagzeilen fehlgeschlagen: %s", e)
            return []

    def _get_intl_headlines(self) -> List[str]:
        try:
            from collectors.international_media_collector import InternationalMediaCollector
            items = InternationalMediaCollector(lookback_hours=24).collect_market_context()
            return [
                f"{it['source']}: {it['title']}"
                for it in items[:4]
            ]
        except Exception as e:
            log.debug("Internationale Schlagzeilen fehlgeschlagen: %s", e)
            return []

    # ── Index-Snapshots ───────────────────────────────────────────────────────

    def _get_index_snapshots(self, ticker_map: Dict[str, str]) -> List[IndexSnapshot]:
        results: List[IndexSnapshot] = []
        try:
            import yfinance as yf
            for name, yf_ticker in ticker_map.items():
                try:
                    hist = yf.Ticker(yf_ticker).history(period="2d")
                    if len(hist) < 2:
                        continue
                    prev_close = float(hist["Close"].iloc[-2])
                    last_close = float(hist["Close"].iloc[-1])
                    if prev_close == 0:
                        continue
                    chg = (last_close / prev_close - 1) * 100
                    trend = "UP" if chg > 0.3 else "DOWN" if chg < -0.3 else "FLAT"
                    results.append(IndexSnapshot(name=name, change_1d_pct=round(chg, 2), trend=trend))
                except Exception as e:
                    log.debug("Index %s (%s): %s", name, yf_ticker, e)
        except ImportError:
            pass
        return results

    # ── Pre-Market Movers ─────────────────────────────────────────────────────

    def _get_premarket_movers(self, watchlist: List[str]) -> List[PreMarketMover]:
        movers: List[PreMarketMover] = []
        try:
            import yfinance as yf
        except ImportError:
            return movers

        # Only US stocks support pre-market data via yfinance
        us_tickers = [t for t in watchlist if "." not in t and "/" not in t and "-" not in t]

        for ticker in us_tickers:
            try:
                info = yf.Ticker(ticker).info
                pre_price = info.get("preMarketPrice")
                regular_price = info.get("regularMarketPreviousClose") or info.get("previousClose")
                if not pre_price or not regular_price or regular_price == 0:
                    continue
                change_pct = (pre_price / regular_price - 1) * 100
                if abs(change_pct) >= _MOVER_THRESHOLD:
                    movers.append(PreMarketMover(
                        ticker=ticker,
                        price_regular=round(regular_price, 2),
                        price_pre=round(pre_price, 2),
                        change_pct=round(change_pct, 2),
                        direction="UP" if change_pct > 0 else "DOWN",
                    ))
            except Exception as e:
                log.debug("PreMarket %s: %s", ticker, e)

        movers.sort(key=lambda m: abs(m.change_pct), reverse=True)
        return movers[:10]

    # ── Gesamt-Ton ────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_tone(briefing: PreMarketBriefing) -> str:
        bull_pts = 0
        bear_pts = 0

        # Indizes
        for idx in briefing.indices:
            if idx.trend == "UP":
                bull_pts += 1
            elif idx.trend == "DOWN":
                bear_pts += 1

        # Futures
        for fut in briefing.futures:
            if fut.name in ("S&P 500 Fut", "Nasdaq Fut", "DAX Fut"):
                if fut.trend == "UP":
                    bull_pts += 2
                elif fut.trend == "DOWN":
                    bear_pts += 2

        # Pre-Market Movers (breite Bewegung)
        up_movers = sum(1 for m in briefing.movers if m.direction == "UP")
        dn_movers = sum(1 for m in briefing.movers if m.direction == "DOWN")
        bull_pts += up_movers
        bear_pts += dn_movers

        if bull_pts > bear_pts + 2:
            return "BULLISH"
        if bear_pts > bull_pts + 2:
            return "BEARISH"
        return "NEUTRAL"
