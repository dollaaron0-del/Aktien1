"""
Telegram Notifier
Sendet sofortige Benachrichtigungen bei:
  - Kauf (BUY)
  - Verkauf (SELL) inkl. P&L
  - These gebrochen (⚠ THESIS BROKEN)
  - Tages-Zusammenfassung

Einrichtung:
  1. BotFather auf Telegram anschreiben → /newbot → API-Token erhalten
  2. Bot starten und Chat-ID ermitteln (über getUpdates oder @userinfobot)
  3. In .env eintragen: TELEGRAM_BOT_TOKEN=... und TELEGRAM_CHAT_ID=...

Ohne Token sendet der Notifier still keine Nachrichten (fail-safe).
"""

import requests
from typing import Optional
from config import config

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10


class TelegramNotifier:
    def __init__(self):
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._enabled = bool(self._token and self._chat_id)

    def send(self, message: str):
        if not self._enabled:
            return
        try:
            requests.post(
                _API_BASE.format(token=self._token),
                json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=_TIMEOUT,
            )
        except Exception:
            pass  # Never let notification failure break the bot

    # ── Typed event helpers ───────────────────────────────────────────────────

    def notify_buy(
        self,
        ticker: str,
        shares: float,
        price: float,
        stop_loss: float,
        take_profit: float,
        hold_days: int,
        rationale: str,
        sentiment_score: float,
    ):
        invested = shares * price
        msg = (
            f"🟢 <b>KAUF: {ticker}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 Preis:        <b>${price:.2f}</b>\n"
            f"🔢 Stück:        {shares:.2f}\n"
            f"💰 Investiert:   ${invested:,.2f}\n"
            f"🛑 Stop-Loss:    ${stop_loss:.2f}\n"
            f"🎯 Take-Profit:  ${take_profit:.2f}\n"
            f"📅 Haltedauer:   {hold_days} Tage\n"
            f"🧠 Sentiment:    {sentiment_score:.2f}\n"
            f"📝 Grund: <i>{rationale[:200]}</i>"
        )
        self.send(msg)

    def notify_sell(
        self,
        ticker: str,
        shares: float,
        price: float,
        entry_price: float,
        pnl: float,
        reason: str,
        thesis_broken: bool = False,
    ):
        pnl_pct = (price - entry_price) / entry_price * 100
        icon = "🔴" if pnl < 0 else "🟩"
        warn = "⚠️ <b>THESE GEBROCHEN</b>\n" if thesis_broken else ""
        msg = (
            f"{icon} <b>VERKAUF: {ticker}</b>\n"
            f"{warn}"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📉 Verkaufspreis: <b>${price:.2f}</b>\n"
            f"📊 Einstieg:      ${entry_price:.2f}\n"
            f"💹 P&L:           <b>{'+'if pnl>=0 else ''}{pnl:.2f} USD ({pnl_pct:+.1f}%)</b>\n"
            f"📝 Grund: <i>{reason[:200]}</i>"
        )
        self.send(msg)

    def notify_thesis_warning(self, ticker: str, break_reason: str, confidence: str):
        msg = (
            f"⚠️ <b>THESE GEBROCHEN: {ticker}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔍 Konfidenz:  {confidence}\n"
            f"📝 Grund: <i>{break_reason[:300]}</i>\n"
            f"→ Position wird beim nächsten Kurs geschlossen."
        )
        self.send(msg)

    def notify_daily_summary(
        self,
        total_value: float,
        cash: float,
        open_positions: int,
        phase: str,
        progress_pct: float,
        actions_today: list,
    ):
        actions_text = ""
        if actions_today:
            actions_text = "\n<b>Heutige Aktionen:</b>\n" + "\n".join(
                f"  • {a}" for a in actions_today[:5]
            )
        phase_icon = "🌱" if phase == "GROWTH" else "💸"
        msg = (
            f"📊 <b>Tages-Zusammenfassung</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💼 Gesamtwert:    <b>${total_value:,.2f}</b>\n"
            f"💵 Cash:          ${cash:,.2f}\n"
            f"📂 Offene Pos.:   {open_positions}\n"
            f"{phase_icon} Phase:         {phase} ({progress_pct:.1f}% zum Ziel)"
            f"{actions_text}"
        )
        self.send(msg)

    def notify_insider_signal(
        self,
        ticker: str,
        person: str,
        action: str,
        amount: str,
        source: str,
    ):
        icon = "🏛️" if "Congressional" in source else "🏢"
        msg = (
            f"{icon} <b>Insider-Signal: {ticker}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 Person:   {person}\n"
            f"📌 Aktion:   <b>{action}</b>\n"
            f"💰 Betrag:   {amount}\n"
            f"📂 Quelle:   {source}"
        )
        self.send(msg)

    def notify_contract_signal(
        self,
        ticker: str,
        company: str,
        agency: str,
        amount_fmt: str,
    ):
        msg = (
            f"🇺🇸 <b>Bundesauftrag: {ticker}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏢 Unternehmen:  {company}\n"
            f"🏛️ Auftraggeber: {agency}\n"
            f"💰 Volumen:      <b>{amount_fmt}</b>\n"
            f"📌 Signal: BULLISCH – gesicherter Umsatz"
        )
        self.send(msg)
