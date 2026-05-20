"""
Short Strategy – Profitiert von Kursrückgängen via:
  1. Inverse ETFs (direkt kaufbar, kein Options-Broker nötig)
  2. ETF Puts via Alpaca Options API (wenn ALPACA_OPTIONS_ENABLED=true)

Inverse ETF Mapping:
  SPY → SH (1×), SDS (2×)   – S&P 500 short
  QQQ → PSQ (1×), QID (2×)  – Nasdaq short
  IWM → RWM (1×)             – Russell 2000 short
  GLD → DGLD                 – Gold short
  Einzelaktien → keine direkten Inverse ETFs → PUT-Option

Short-Signal Bedingungen:
  - AnalysisResult.recommendation == "SELL"
  - UND confidence in ("HIGH", "MEDIUM")
  - UND sentiment_score < config.sell_threshold
  - UND Short-Modus aktiv (SHORT_ENABLED=true in .env)

Position-Sizing:
  - Max 10% des Portfolios pro Short-Position (konservativ)
  - Nie shorten wenn schon Long-Position im gleichen Ticker vorhanden
  - Max 3 gleichzeitige Short-Positionen
"""
import json
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional

from analyzers.claude_analyzer import AnalysisResult
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal
from notifier.telegram_notifier import TelegramNotifier
from config import config
from logger import get_logger

log = get_logger(__name__)

_SHORT_POSITIONS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "short_positions.json"
)

# Inverse ETF Mapping: underlying → (inverse_ticker, description)
_INVERSE_ETF_MAP: Dict[str, tuple] = {
    # Breite Indizes
    "SPY":  ("SH",   "1×S&P500 Short"),
    "QQQ":  ("PSQ",  "1×Nasdaq Short"),
    "IWM":  ("RWM",  "1×Russell2000 Short"),
    "DIA":  ("DOG",  "1×Dow Jones Short"),
    "MDY":  ("MYY",  "1×MidCap Short"),
    # Sektoren
    "XLF":  ("SEF",  "1×Finanz Short"),
    "XLE":  ("ERY",  "2×Energie Short"),
    "XLK":  ("REW",  "2×Tech Short"),
    "XLRE": ("REK",  "1×REIT Short"),
    # Rohstoffe
    "GLD":  ("GLL",  "2×Gold Short"),
    "SLV":  ("ZSL",  "2×Silber Short"),
    "USO":  ("SCO",  "2×Oil Short"),
}

# Stop-Loss / Take-Profit / Haltedauer für Inverse ETF Positionen
_SHORT_STOP_LOSS_PCT   = 0.08   # 8% SL (Inverse ETF läuft gegen uns wenn Markt steigt)
_SHORT_TAKE_PROFIT_PCT = 0.15   # 15% TP
_SHORT_HOLD_DAYS       = 10     # Shorts kürzer halten als Longs


@dataclass
class ShortPosition:
    ticker:           str    # Underlying (z.B. "SPY")
    inverse_ticker:   str    # Was tatsächlich gekauft (z.B. "SH")
    shares:           float
    entry_price:      float  # Preis des Inverse ETF beim Kauf
    entry_date:       str
    stop_loss:        float  # Stop-Loss des Inverse ETF (nach unten – Kurs des iETF fällt)
    take_profit:      float  # Take-Profit des Inverse ETF (Kurs steigt)
    target_hold_days: int
    sentiment_score:  float  # Score zum Zeitpunkt des Shorts
    rationale:        str
    entry_catalysts:  List[str] = field(default_factory=list)


class ShortStrategy:
    def __init__(
        self,
        portfolio: Portfolio,
        broker,
        tracker: PerformanceTracker,
        journal: TradeJournal,
        notifier: Optional[TelegramNotifier] = None,
    ):
        self.portfolio = portfolio
        self.broker = broker
        self.tracker = tracker
        self.journal = journal
        self._notifier = notifier or TelegramNotifier()
        self._enabled = os.getenv("SHORT_ENABLED", "false").lower() in ("1", "true", "yes")
        self._max_short_pct = float(os.getenv("MAX_SHORT_PCT", "0.10"))
        self._max_shorts = int(os.getenv("MAX_SHORT_POSITIONS", "3"))
        self._sell_threshold = float(os.getenv("SHORT_SELL_THRESHOLD",
                                                str(getattr(config, "sell_threshold", 0.35))))
        # Load persisted short positions
        self._shorts: Dict[str, ShortPosition] = self._load_positions()

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate_short(self, analysis: AnalysisResult) -> Optional[str]:
        """
        Prüft ob ein Short-Signal vorliegt und öffnet ggf. Short-Position.
        Gibt Aktions-String zurück oder None.
        """
        if not self._enabled:
            return None

        ticker = analysis.ticker

        # Signal-Bedingungen prüfen
        if analysis.recommendation != "SELL":
            return None
        if analysis.confidence not in ("HIGH", "MEDIUM"):
            return None
        if analysis.sentiment_score >= self._sell_threshold:
            return None

        # Keine Short-Position wenn schon Long vorhanden
        if self.portfolio.get_position(ticker):
            log.debug("[%s] Short übersprungen – Long-Position vorhanden", ticker)
            return None

        # Maximale Anzahl aktiver Short-Positionen prüfen
        active_shorts = self._get_active_shorts()
        if len(active_shorts) >= self._max_shorts:
            return (
                f"[{ticker}] Short-Signal ignoriert – Max. Short-Positionen "
                f"({self._max_shorts}) bereits offen."
            )

        # Bereits eine Short-Position in diesem Ticker?
        if ticker in self._shorts:
            log.debug("[%s] Short-Position bereits offen", ticker)
            return None

        # Inverse ETF ermitteln
        inverse_ticker = self.get_inverse_etf(ticker)

        if inverse_ticker is None:
            # Kein Inverse ETF → Put-Option versuchen
            put_result = self._open_put_option(ticker,
                                               self.broker.get_price(ticker) or 0.0,
                                               analysis)
            return put_result

        current_price = self.broker.get_price(inverse_ticker)
        if current_price is None:
            log.warning("[%s] Kein Kurs für Inverse ETF %s", ticker, inverse_ticker)
            return None

        portfolio_value = self.portfolio.total_value(
            self.broker.get_prices(
                list(self.portfolio.all_positions().keys()) + [inverse_ticker]
            )
        )

        return self._open_short(ticker, inverse_ticker, current_price, analysis, portfolio_value)

    def check_short_exits(self) -> List[str]:
        """
        Prüft ob Short-Positionen geschlossen werden sollen
        (Take-Profit, Stop-Loss, Haltedauer, Signal-Umkehr).
        """
        actions = []
        if not self._shorts:
            return actions

        for ticker, short in list(self._shorts.items()):
            inverse_price = self.broker.get_price(short.inverse_ticker)
            if inverse_price is None:
                continue

            days_held = (
                datetime.utcnow() - datetime.fromisoformat(short.entry_date)
            ).days

            reason = None
            if inverse_price >= short.take_profit:
                reason = f"Take-Profit bei ${inverse_price:.2f} (+{(inverse_price/short.entry_price-1)*100:.1f}%)"
            elif inverse_price <= short.stop_loss:
                reason = f"Stop-Loss bei ${inverse_price:.2f} ({(inverse_price/short.entry_price-1)*100:.1f}%)"
            elif days_held >= short.target_hold_days:
                reason = f"Max. Haltedauer ({short.target_hold_days}d) erreicht"

            if reason:
                pnl = self._close_short(ticker, short, inverse_price, reason, days_held)
                sign = "+" if pnl >= 0 else ""
                actions.append(
                    f"[SHORT {ticker}/{short.inverse_ticker}] GESCHLOSSEN – "
                    f"{reason} | P&L: {sign}{pnl:.2f} USD"
                )

        return actions

    def close_short_on_buy_signal(self, ticker: str, analysis: AnalysisResult) -> Optional[str]:
        """
        Schließt Short-Position wenn das Signal dreht (BUY-Signal auf Underlying).
        Wird von SwingStrategy aufgerufen wenn BUY-Signal für einen Short-Ticker vorliegt.
        """
        if ticker not in self._shorts:
            return None
        short = self._shorts[ticker]
        inverse_price = self.broker.get_price(short.inverse_ticker)
        if inverse_price is None:
            return None

        days_held = (datetime.utcnow() - datetime.fromisoformat(short.entry_date)).days
        reason = f"Signal dreht auf BUY (Sentiment: {analysis.sentiment_score:.2f})"
        pnl = self._close_short(ticker, short, inverse_price, reason, days_held)
        sign = "+" if pnl >= 0 else ""
        return (
            f"[SHORT {ticker}/{short.inverse_ticker}] GESCHLOSSEN – "
            f"{reason} | P&L: {sign}{pnl:.2f} USD"
        )

    def get_inverse_etf(self, ticker: str) -> Optional[str]:
        """Gibt den passenden Inverse ETF-Ticker zurück oder None."""
        entry = _INVERSE_ETF_MAP.get(ticker.upper())
        if entry:
            return entry[0]
        return None

    def summary(self) -> Dict:
        """Übersicht aller aktiven Short-Positionen."""
        active = self._get_active_shorts()
        positions = []
        for ticker, short in active.items():
            inverse_price = self.broker.get_price(short.inverse_ticker)
            pnl_unrealized = (
                (inverse_price - short.entry_price) * short.shares
                if inverse_price else 0.0
            )
            days_held = (
                datetime.utcnow() - datetime.fromisoformat(short.entry_date)
            ).days
            positions.append({
                "ticker":          ticker,
                "inverse_ticker":  short.inverse_ticker,
                "shares":          short.shares,
                "entry_price":     short.entry_price,
                "current_price":   inverse_price,
                "pnl_unrealized":  round(pnl_unrealized, 2),
                "days_held":       days_held,
                "stop_loss":       short.stop_loss,
                "take_profit":     short.take_profit,
            })
        return {
            "enabled":          self._enabled,
            "active_count":     len(active),
            "max_shorts":       self._max_shorts,
            "positions":        positions,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _open_short(
        self,
        ticker: str,
        inverse_ticker: str,
        price: float,
        analysis: AnalysisResult,
        portfolio_value: float,
    ) -> str:
        """Öffnet Short via Kauf des Inverse ETF."""
        max_invest = portfolio_value * self._max_short_pct
        invest = min(max_invest, self.portfolio.cash * 0.95)

        if invest < 50:
            return f"[SHORT {ticker}] Nicht genug freies Kapital für Short-Position."

        shares = math.floor(invest / price * 100) / 100
        if shares <= 0:
            return f"[SHORT {ticker}] Positionsgröße zu klein – übersprungen."

        stop_loss   = round(price * (1 - _SHORT_STOP_LOSS_PCT), 2)
        take_profit = round(price * (1 + _SHORT_TAKE_PROFIT_PCT), 2)
        entry_date  = datetime.utcnow().isoformat()

        # Inverse ETF als normale Long-Position im Portfolio kaufen
        position = Position(
            ticker=inverse_ticker,
            shares=shares,
            entry_price=price,
            entry_date=entry_date,
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=_SHORT_HOLD_DAYS,
            rationale=f"[SHORT] {ticker} via {inverse_ticker}: {analysis.entry_rationale}",
            entry_catalysts=analysis.key_catalysts[:5],
        )
        self.broker.buy(inverse_ticker, shares, price)
        self.portfolio.open_position(position)

        # Short-Tracking
        short = ShortPosition(
            ticker=ticker,
            inverse_ticker=inverse_ticker,
            shares=shares,
            entry_price=price,
            entry_date=entry_date,
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=_SHORT_HOLD_DAYS,
            sentiment_score=analysis.sentiment_score,
            rationale=analysis.entry_rationale or "",
            entry_catalysts=analysis.key_catalysts[:5],
        )
        self._shorts[ticker] = short
        self._save_positions()

        # Journal
        self.journal.log_entry(
            ticker=inverse_ticker,
            price=price,
            sentiment=analysis.sentiment_score,
            confidence=analysis.confidence,
            direction="BEARISH",
            rationale=f"[SHORT] {ticker}: {analysis.entry_rationale}",
            catalysts=analysis.key_catalysts[:5],
            risks=analysis.risk_factors[:5],
            sources={},
            target_price=take_profit,
            hold_days=_SHORT_HOLD_DAYS,
        )

        # Telegram
        etf_desc = _INVERSE_ETF_MAP.get(ticker, (inverse_ticker, inverse_ticker))[1]
        self._notifier.send(
            f"SHORT EROEFFNET: {ticker} via {inverse_ticker} ({etf_desc})\n"
            f"Shares: {shares} @ ${price:.2f}\n"
            f"SL: ${stop_loss} | TP: ${take_profit}\n"
            f"Sentiment: {analysis.sentiment_score:.2f} | Konfidenz: {analysis.confidence}\n"
            f"Grund: {analysis.entry_rationale}"
        )

        log.info(
            "[SHORT] %s via %s: %s Stk @ $%.2f | SL: $%.2f | TP: $%.2f",
            ticker, inverse_ticker, shares, price, stop_loss, take_profit,
        )

        return (
            f"[SHORT {ticker}] EROEFFNET via {inverse_ticker} ({etf_desc}) – "
            f"{shares} Stk @ ${price:.2f} "
            f"| SL: ${stop_loss} | TP: ${take_profit} "
            f"| Investiert: ${shares * price:.2f}"
        )

    def _open_put_option(
        self,
        ticker: str,
        price: float,
        analysis: AnalysisResult,
    ) -> Optional[str]:
        """
        Kauft einen ATM Put via Alpaca Options API.

        Nur aktiv wenn ALPACA_OPTIONS_ENABLED=true in .env.
        Fällt graceful zurück (None) wenn nicht verfügbar.

        Strike: 95% des aktuellen Preises (leicht OTM)
        Expiry: 30 Tage
        """
        if os.getenv("ALPACA_OPTIONS_ENABLED", "false").lower() not in ("1", "true", "yes"):
            log.debug(
                "[%s] Kein Inverse ETF und Options deaktiviert – Short übersprungen.", ticker
            )
            return None
        try:
            from broker.alpaca_broker import AlpacaBroker
            broker = AlpacaBroker()
            # Alpaca Options endpoint: /v2/options/contracts
            # Strike: 95% des aktuellen Preises (leicht OTM)
            # Expiry: heute + 30 Tage
            # side=buy, type=limit, option_type=put
            # Platzhalter-Implementierung – konkrete API-Calls wenn Broker aktiviert
            log.info(
                "[%s] PUT-Option via Alpaca würde geöffnet: Strike=$%.2f, Expiry=+30d",
                ticker, price * 0.95,
            )
            return None  # Placeholder – vollständige Implementierung nach Alpaca-Freischaltung
        except Exception as e:
            log.debug("Options-API fehlgeschlagen: %s – Fallback auf Inverse ETF", e)
            return None

    def _close_short(
        self,
        ticker: str,
        short: ShortPosition,
        inverse_price: float,
        reason: str,
        days_held: int,
    ) -> float:
        """Schließt Short-Position (verkauft den Inverse ETF)."""
        pnl = 0.0
        try:
            pnl = self.portfolio.close_position(short.inverse_ticker, inverse_price, reason)
        except Exception as e:
            log.error("[SHORT] Fehler beim Schließen von %s: %s", short.inverse_ticker, e)

        self.journal.log_exit(
            ticker=short.inverse_ticker,
            price=inverse_price,
            entry_price=short.entry_price,
            pnl=pnl,
            reason=f"[SHORT] {ticker}: {reason}",
            days_held=days_held,
        )

        sign = "+" if pnl >= 0 else ""
        self._notifier.send(
            f"SHORT GESCHLOSSEN: {ticker} ({short.inverse_ticker})\n"
            f"Grund: {reason}\n"
            f"P&L: {sign}{pnl:.2f} USD | Gehalten: {days_held}d"
        )

        del self._shorts[ticker]
        self._save_positions()

        log.info(
            "[SHORT] %s/%s geschlossen: %s | P&L: %s%.2f",
            ticker, short.inverse_ticker, reason, sign, pnl,
        )
        return pnl

    def _get_active_shorts(self) -> Dict[str, ShortPosition]:
        """Gibt nur Shorts zurück deren Inverse ETF noch im Portfolio ist."""
        active = {}
        for ticker, short in list(self._shorts.items()):
            if self.portfolio.get_position(short.inverse_ticker):
                active[ticker] = short
            else:
                # Position wurde von außen geschlossen (z.B. HedgeStrategy, manuell)
                log.debug("[SHORT] %s Position nicht mehr im Portfolio – aus Tracking entfernt", ticker)
                del self._shorts[ticker]
                self._save_positions()
        return active

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_positions(self) -> Dict[str, ShortPosition]:
        os.makedirs(os.path.dirname(_SHORT_POSITIONS_FILE), exist_ok=True)
        if not os.path.exists(_SHORT_POSITIONS_FILE):
            return {}
        try:
            with open(_SHORT_POSITIONS_FILE) as f:
                data = json.load(f)
            result = {}
            for ticker, d in data.items():
                result[ticker] = ShortPosition(
                    ticker=d["ticker"],
                    inverse_ticker=d["inverse_ticker"],
                    shares=d["shares"],
                    entry_price=d["entry_price"],
                    entry_date=d["entry_date"],
                    stop_loss=d["stop_loss"],
                    take_profit=d["take_profit"],
                    target_hold_days=d["target_hold_days"],
                    sentiment_score=d["sentiment_score"],
                    rationale=d.get("rationale", ""),
                    entry_catalysts=d.get("entry_catalysts", []),
                )
            return result
        except Exception as e:
            log.warning("Short-Positionen konnten nicht geladen werden: %s", e)
            return {}

    def _save_positions(self) -> None:
        os.makedirs(os.path.dirname(_SHORT_POSITIONS_FILE), exist_ok=True)
        try:
            data = {ticker: asdict(short) for ticker, short in self._shorts.items()}
            with open(_SHORT_POSITIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error("Short-Positionen konnten nicht gespeichert werden: %s", e)
