"""
SignalConfirmation – Multi-Signal-Bestätigung vor dem Kauf.

Philosophie: Claude-Sentiment ist das Primärsignal, aber allein zu schwach.
Vier unabhängige Sekundärsignale bestätigen oder verwerfen den Trade.

Sekundärsignale (4 gesamt, mind. 2 müssen grün sein):
  1. Volumen    – Heutiges Volumen > signal_volume_boost_ratio × 20d-Avg
  2. Momentum   – 5d-Return zwischen +min% und +max% (positiv, aber nicht überhitzt)
  3. Insider    – Kein aktives Verkaufssignal (NEUTRAL oder besser)
  4. Technisch  – Aktueller Kurs ≥ EMA21

Konfigurierbar via .env:
  SIGNAL_MIN_CONFIRMATIONS   (Standard: 2)
  SIGNAL_VOLUME_BOOST_RATIO  (Standard: 1.20)
  SIGNAL_MOMENTUM_MIN_PCT    (Standard: 0.01 = +1%)
  SIGNAL_MOMENTUM_MAX_PCT    (Standard: 0.10 = +10%)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from logger import get_logger

log = get_logger(__name__)


@dataclass
class SignalCheck:
    name: str
    passed: bool
    value: str   # lesbarer Wert z.B. "128% des Avg"
    reason: str  # kurze Erklärung


@dataclass
class ConfirmationResult:
    checks: List[SignalCheck]
    passed_count: int
    required: int
    approved: bool

    def summary(self) -> str:
        lines = []
        for c in self.checks:
            icon = "✅" if c.passed else "❌"
            lines.append(f"{icon} {c.name}: {c.value}")
        status = "FREIGEGEBEN" if self.approved else f"BLOCKIERT ({self.passed_count}/{self.required} Signale)"
        return f"{status}\n" + "\n".join(lines)

    def telegram_text(self, ticker: str) -> str:
        lines = [f"📋 <b>Signal-Bestätigung {ticker}</b>: {self.passed_count}/{len(self.checks)} grün\n"]
        for c in self.checks:
            icon = "✅" if c.passed else "❌"
            lines.append(f"{icon} <b>{c.name}</b>: {c.value}")
        if not self.approved:
            lines.append(f"\n⏳ Warte auf {self.required - self.passed_count} weitere Bestätigung(en)")
        return "\n".join(lines)


class SignalConfirmation:
    """
    Prüft 4 Sekundärsignale und gibt Freigabe wenn genug grün sind.
    Wird nach dem Primärsignal (Claude-Score) aufgerufen.
    """

    def __init__(
        self,
        min_confirmations: int = 2,
        volume_boost_ratio: float = 1.20,
        momentum_min_pct: float = 0.01,
        momentum_max_pct: float = 0.10,
    ):
        self.min_confirmations = min_confirmations
        self.volume_boost_ratio = volume_boost_ratio
        self.momentum_min_pct = momentum_min_pct
        self.momentum_max_pct = momentum_max_pct

    def check_all(
        self, ticker: str, current_price: float
    ) -> ConfirmationResult:
        checks = [
            self._volume_check(ticker),
            self._momentum_check(ticker, current_price),
            self._insider_check(ticker),
            self._technical_check(ticker, current_price),
        ]
        passed = sum(1 for c in checks if c.passed)
        approved = passed >= self.min_confirmations
        return ConfirmationResult(
            checks=checks,
            passed_count=passed,
            required=self.min_confirmations,
            approved=approved,
        )

    # ── Signal 1: Volumen ─────────────────────────────────────────────────────

    def _volume_check(self, ticker: str) -> SignalCheck:
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="25d", interval="1d")
            if len(hist) < 5:
                return SignalCheck("Volumen", True, "Keine Daten", "Übersprungen")
            avg = float(hist["Volume"].iloc[:-1].tail(20).mean())
            today = float(hist["Volume"].iloc[-1])
            if avg <= 0:
                return SignalCheck("Volumen", True, "Keine Basis", "Übersprungen")
            if today == 0:
                return SignalCheck("Volumen", True, "Markt geschlossen", "Übersprungen (kein Tagesvolumen)")
            ratio = today / avg
            passed = ratio >= self.volume_boost_ratio
            return SignalCheck(
                name="Volumen",
                passed=passed,
                value=f"{ratio*100:.0f}% des 20d-Avg ({today:,.0f})",
                reason=(
                    f"Volumen-Boost bestätigt (+{(ratio-1)*100:.0f}%)"
                    if passed else
                    f"Volumen zu niedrig für Bestätigung (Ziel: {self.volume_boost_ratio*100:.0f}%)"
                ),
            )
        except Exception as e:
            log.debug("Volumen-Check fehlgeschlagen: %s", e)
            return SignalCheck("Volumen", True, "Fehler", "Übersprungen (kein Block)")

    # ── Signal 2: Momentum ────────────────────────────────────────────────────

    def _momentum_check(self, ticker: str, current_price: float) -> SignalCheck:
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="10d", interval="1d")
            if len(hist) < 5:
                return SignalCheck("Momentum 5d", True, "Keine Daten", "Übersprungen")
            price_5d_ago = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else float(hist["Close"].iloc[0])
            if price_5d_ago <= 0:
                return SignalCheck("Momentum 5d", True, "Keine Basis", "Übersprungen")
            ret = (current_price - price_5d_ago) / price_5d_ago
            passed = self.momentum_min_pct <= ret <= self.momentum_max_pct
            sign = "+" if ret >= 0 else ""
            return SignalCheck(
                name="Momentum 5d",
                passed=passed,
                value=f"{sign}{ret*100:.1f}% (5 Tage)",
                reason=(
                    f"Positives Momentum bestätigt"
                    if passed else
                    (f"Zu stark gelaufen (+{ret*100:.1f}% > {self.momentum_max_pct*100:.0f}%)"
                     if ret > self.momentum_max_pct else
                     f"Kein Momentum ({ret*100:.1f}% < +{self.momentum_min_pct*100:.0f}%)")
                ),
            )
        except Exception as e:
            log.debug("Momentum-Check fehlgeschlagen: %s", e)
            return SignalCheck("Momentum 5d", True, "Fehler", "Übersprungen (kein Block)")

    # ── Signal 3: Insider ─────────────────────────────────────────────────────

    def _insider_check(self, ticker: str) -> SignalCheck:
        try:
            from analyzers.insider_signal import get_insider_score
            score = get_insider_score(ticker, lookback_days=30)
            # Grün: kein aktives Verkaufssignal
            passed = score.signal not in ("STRONG_SELL", "MILD_SELL")
            label = {
                "STRONG_BUY":  "Starker Insider-Kauf",
                "MILD_BUY":    "Insider-Kauf",
                "NEUTRAL":     "Neutral (kein Verkauf)",
                "MILD_SELL":   "Insider verkauft",
                "STRONG_SELL": "Starker Insider-Verkauf",
            }.get(score.signal, score.signal)
            return SignalCheck(
                name="Insider",
                passed=passed,
                value=label,
                reason=score.message or label,
            )
        except Exception as e:
            log.debug("Insider-Check fehlgeschlagen: %s", e)
            return SignalCheck("Insider", True, "Keine Daten", "Übersprungen (kein Block)")

    # ── Signal 4: Technisch (EMA21) ───────────────────────────────────────────

    def _technical_check(self, ticker: str, current_price: float) -> SignalCheck:
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="3mo", interval="1d")
            if len(hist) < 21:
                return SignalCheck("EMA21", True, "Keine Daten", "Übersprungen")
            ema21 = float(hist["Close"].ewm(span=21, adjust=False).mean().iloc[-1])
            passed = current_price >= ema21
            deviation = (current_price - ema21) / ema21 * 100
            sign = "+" if deviation >= 0 else ""
            return SignalCheck(
                name="EMA21",
                passed=passed,
                value=f"${current_price:.2f} vs EMA21 ${ema21:.2f} ({sign}{deviation:.1f}%)",
                reason=(
                    f"Kurs über EMA21 – technisch bestätigt"
                    if passed else
                    f"Kurs unter EMA21 – technisch schwach"
                ),
            )
        except Exception as e:
            log.debug("EMA21-Check fehlgeschlagen: %s", e)
            return SignalCheck("EMA21", True, "Fehler", "Übersprungen (kein Block)")
