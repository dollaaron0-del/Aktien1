"""
Telegram Notifier
Sendet sofortige Benachrichtigungen bei:
  - Kauf (BUY)
  - Verkauf (SELL) inkl. P&L
  - These gebrochen (⚠ THESIS BROKEN)
  - Tages-Zusammenfassung

Wichtigkeits-Stufen (TELEGRAM_MODE, Default "important"):
  Jede Nachricht trägt ein level — "trade" (Käufe/Verkäufe/These), "critical"
  (Fehler, die manuelles Eingreifen brauchen), "digest" (1×-täglich-Berichte),
  "command" (direkte Antwort auf einen Nutzer-Befehl wie /status, Roadmap
  1.5g) oder "info" (alles andere). Im Modus "important" erreichen NUR
  trade/critical/digest/command Telegram; info landet im Log. TELEGRAM_MODE=all
  stellt das alte Verhalten wieder her. Detailtiefe gehört ins Dashboard, nicht
  in den Chat (Tab "Entscheidungen").

Einrichtung:
  1. BotFather auf Telegram anschreiben → /newbot → API-Token erhalten
  2. Bot starten und Chat-ID ermitteln (über getUpdates oder @userinfobot)
  3. In .env eintragen: TELEGRAM_BOT_TOKEN=... und TELEGRAM_CHAT_ID=...

Ohne Token sendet der Notifier still keine Nachrichten (fail-safe).
"""

import logging
import requests
from typing import Optional
from config import config

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10

# Stufen, die im Modus "important" durchkommen.
_IMPORTANT_LEVELS = {"trade", "critical", "digest", "command"}

log = logging.getLogger(__name__)


def dashboard_link(target: str = "", label: str = "Im Leitstand ansehen") -> str:
    """Deep-Link ins Dashboard (Ausbau-Roadmap H5.3) — vom Handy-Alarm
    direkt zur richtigen Stelle im Leitstand. `target` ist eine
    Fabrik-Maschinen-ID (z.B. "warehouse"), die das Dashboard über
    `?factory=<id>` als Detail-Panel öffnet; leer = nur die Startseite.

    Liefert "" wenn `DASHBOARD_URL` nicht gesetzt ist — das ist der
    bewusste Default: Das Dashboard hört nur auf 127.0.0.1 (SSH-Tunnel),
    ein Link nützt also nur, wenn der Tunnel steht. Lieber gar kein Link
    als ein toter.
    """
    base = (getattr(config, "dashboard_url", "") or "").strip()
    if not base:
        return ""
    url = base.rstrip("/")
    if target:
        url += f"/?factory={target}"
    return f'🔗 <a href="{url}">{label}</a>'


class TelegramNotifier:
    def __init__(self):
        self._token = config.telegram_bot_token
        self._chat_id = config.telegram_chat_id
        self._enabled = bool(self._token and self._chat_id)
        self._mode = (getattr(config, "telegram_mode", "important") or "important").lower()

    def send(self, message: str, level: str = "info", link_target: Optional[str] = None):
        """`link_target` (H5.3): hängt einen Dashboard-Deep-Link an. Bewusst
        HIER zentral statt in jeder notify_*-Methode — eine Stelle, kein
        Duplikat. Ohne DASHBOARD_URL passiert nichts (dashboard_link()
        liefert dann "")."""
        if not self._enabled:
            return
        if self._mode != "all" and level not in _IMPORTANT_LEVELS:
            # Bewusst unterdrückt (TELEGRAM_MODE=important): Detail gehört ins
            # Dashboard. Im Log bleibt nachvollziehbar, was gesendet worden wäre.
            log.info("Telegram unterdrückt (level=%s): %.100s", level,
                     message.replace("\n", " "))
            return
        if link_target is not None:
            link = dashboard_link(link_target)
            if link:
                message = f"{message}\n\n{link}"
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
        confidence: str = "MEDIUM",
        direction: str = "BULLISH",
        target_price: Optional[float] = None,
        key_catalysts: Optional[list] = None,
        risk_factors: Optional[list] = None,
        sources_breakdown: Optional[dict] = None,
    ):
        invested = shares * price
        sl_pct  = (price - stop_loss) / price * 100
        tp_pct  = (take_profit - price) / price * 100
        conf_icon = {"HIGH": "🔥", "MEDIUM": "✅", "LOW": "⚠️"}.get(confidence, "✅")
        dir_icon  = "📈" if direction == "BULLISH" else "📉"

        lines = [
            f"🟢 <b>KAUF: {ticker}</b>  ·  {conf_icon} {confidence}  ·  {dir_icon} {direction}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📈 Einstiegspreis:   <b>${price:.2f}</b>",
            f"🔢 Anteile:          {shares:.4f}  <i>(investiert: ${invested:,.2f})</i>",
        ]
        if target_price and target_price > price:
            lines.append(f"🎯 Kursziel (Claude): <b>${target_price:.2f}  (+{(target_price/price-1)*100:.1f}%)</b>")
        lines += [
            f"🎯 Take-Profit:      ${take_profit:.2f}  (<b>+{tp_pct:.1f}%</b>)",
            f"🛑 Stop-Loss:        ${stop_loss:.2f}  (−{sl_pct:.1f}%)",
            f"📅 Ziel-Haltedauer:  {hold_days} Tage",
            f"🧠 Sentiment-Score:  <b>{sentiment_score:.2f}</b>",
            "",
        ]

        # Analyse-Begründung (erste 450 Zeichen)
        if rationale:
            short = rationale.strip()[:450]
            if len(rationale) > 450:
                short += "…"
            lines += ["📋 <b>Analyse-Begründung:</b>", f"<i>{short}</i>", ""]

        # Kaufkatalysatoren
        if key_catalysts:
            lines.append("⚡ <b>Kaufkatalysatoren:</b>")
            for c in key_catalysts[:4]:
                lines.append(f"  • {c}")
            lines.append("")

        # Risiken
        if risk_factors:
            lines.append("⚠️ <b>Risiken:</b>")
            for r in risk_factors[:3]:
                lines.append(f"  • {r}")
            lines.append("")

        # Quellen
        if sources_breakdown:
            src_parts = [f"{k.capitalize()} ({v})" for k, v in sources_breakdown.items() if v]
            if src_parts:
                lines.append(f"📊 <b>Quellen:</b>  {' · '.join(src_parts)}")

        self.send("\n".join(lines), level="trade", link_target="warehouse")

    def notify_sell(
        self,
        ticker: str,
        shares: float,
        price: float,
        entry_price: float,
        pnl: float,
        reason: str,
        thesis_broken: bool = False,
        days_held: int = 0,
        target_hold_days: int = 0,
        entry_catalysts: Optional[list] = None,
        entry_rationale: str = "",
    ):
        pnl_pct = (price - entry_price) / entry_price * 100
        icon = "🔴" if pnl < 0 else "🟩"
        result_word = "VERLUST" if pnl < 0 else "GEWINN"
        pnl_sign = "+" if pnl >= 0 else ""

        hold_note = ""
        if days_held and target_hold_days:
            hold_note = f"  <i>(Ziel: {target_hold_days} Tage)</i>"
        elif days_held:
            hold_note = ""

        lines = [f"{icon} <b>VERKAUF: {ticker}</b>  ·  {result_word}"]
        if thesis_broken:
            lines.append("⚠️ <b>THESE GEBROCHEN – Position aufgelöst</b>")
        lines += [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📉 Verkaufspreis:    <b>${price:.2f}</b>",
            f"📊 Einstiegspreis:   ${entry_price:.2f}",
            f"💹 P&L:              <b>{pnl_sign}{pnl:.2f} USD  ({pnl_pct:+.1f}%)</b>",
        ]
        if days_held:
            lines.append(f"⏱ Gehalten:          <b>{days_held} Tage</b>{hold_note}")
        lines += [
            "",
            f"📝 <b>Ausstiegsgrund:</b>  <i>{reason}</i>",
        ]

        # Ursprüngliche Kaufgründe
        if entry_catalysts:
            lines += ["", "💡 <b>Ursprüngliche Kaufgründe:</b>"]
            for c in entry_catalysts[:4]:
                lines.append(f"  • {c}")

        # Kurze Begründung aus Erstanalyse (falls vorhanden)
        if entry_rationale and not entry_catalysts:
            short = entry_rationale.strip()[:300]
            if len(entry_rationale) > 300:
                short += "…"
            lines += ["", f"💡 <b>Kaufbegründung war:</b>", f"<i>{short}</i>"]

        self.send("\n".join(lines), level="trade", link_target="warehouse")

    def notify_thesis_warning(self, ticker: str, break_reason: str, confidence: str):
        msg = (
            f"⚠️ <b>THESE GEBROCHEN: {ticker}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔍 Konfidenz:  {confidence}\n"
            f"📝 Grund: <i>{break_reason[:300]}</i>\n"
            f"→ Position wird beim nächsten Kurs geschlossen."
        )
        self.send(msg, level="trade", link_target="warehouse")

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
        self.send(msg, level="digest", link_target="")

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
