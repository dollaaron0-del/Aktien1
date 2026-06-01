"""
Daily Dashboard – Tägliche Telegram-Zusammenfassung nach Börsenschluss

Gesendet einmal täglich um 21:00 UTC (nach NYSE-Schluss).
Enthält:
  - Portfolio-Wert & Tages-P&L
  - Offene Positionen (beste/schlechteste)
  - Bot-Score & neue Rekorde
  - Congressional/Insider Trades heute
  - Claude-Kommentar zur Portfolio-Gesundheit
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, date
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_SENT_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "dashboard_sent.json")
_LOCK_FILE = _SENT_FILE + ".lock"


def _atomic_save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=os.path.dirname(path), suffix=".tmp", delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


class DailyDashboard:
    """Erstellt und versendet die tägliche Portfolio-Zusammenfassung."""

    # UTC-Stunde ab der das Dashboard gesendet werden darf (20 = nach NYSE-Schluss 20:00 UTC)
    SEND_HOUR_UTC = 20

    def should_send(self) -> bool:
        """True wenn es Zeit ist UND heute noch nicht gesendet wurde."""
        now = datetime.utcnow()
        if now.hour < self.SEND_HOUR_UTC:
            return False
        today = date.today().isoformat()
        try:
            with open(_SENT_FILE) as f:
                data = json.load(f)
            return data.get("last_sent") != today
        except Exception:
            return True

    def mark_sent(self) -> None:
        _atomic_save(_SENT_FILE, {"last_sent": date.today().isoformat()})

    def try_claim_send(self) -> bool:
        """Atomarer Lock: gibt True zurück wenn DIESE Instanz senden darf.
        Verhindert Doppel-Sends bei mehreren gleichzeitig laufenden Bot-Prozessen.
        """
        today = date.today().isoformat()
        try:
            # Exklusiver Lock via O_CREAT|O_EXCL (atomar auf Linux)
            fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, today.encode())
            os.close(fd)
        except FileExistsError:
            # Anderer Prozess hat den Lock — prüfen ob er von heute ist
            try:
                with open(_LOCK_FILE) as f:
                    lock_date = f.read().strip()
                if lock_date == today:
                    return False   # Heute bereits gesendet
                # Lock vom Vortag → überschreiben
                os.unlink(_LOCK_FILE)
                fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, today.encode())
                os.close(fd)
            except Exception:
                return False
        except Exception:
            return True   # Lock fehlgeschlagen → trotzdem senden (Fallback)
        return True

    def generate(
        self,
        portfolio,
        tracker,
        scorer,
        broker,
        initial_capital: float = 10_000.0,
    ) -> str:
        """Generiert die Telegram-Markdown-Nachricht für das tägliche Dashboard."""
        try:
            tickers = list(portfolio.all_positions().keys())
            prices = broker.get_prices(tickers) if tickers else {}
            portfolio_value = portfolio.total_value(prices)

            lines = [
                "📊 *Täglicher Portfolio-Bericht*",
                f"_{datetime.utcnow().strftime('%d.%m.%Y, %H:%M UTC')}_",
                "",
            ]

            # ── Portfolio-Übersicht ──────────────────────────────────────────
            total_return_pct = (portfolio_value - initial_capital) / initial_capital * 100
            cash_pct = portfolio.cash / portfolio_value * 100 if portfolio_value else 0
            lines += [
                "💼 *Portfolio*",
                f"  Wert:     ${portfolio_value:>10,.2f}",
                f"  Cash:     ${portfolio.cash:>10,.2f}  ({cash_pct:.0f}%)",
                f"  Gesamt:   {'+' if total_return_pct >= 0 else ''}{total_return_pct:.1f}% seit Start",
                "",
            ]

            # ── Offene Positionen ────────────────────────────────────────────
            positions = portfolio.all_positions()
            if positions:
                lines.append("📈 *Offene Positionen*")
                pos_data = []
                for ticker, pos in positions.items():
                    price = prices.get(ticker, pos.entry_price)
                    ret = (price - pos.entry_price) / pos.entry_price * 100
                    days = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
                    pos_data.append((ticker, ret, price, days))

                pos_data.sort(key=lambda x: x[1], reverse=True)
                for ticker, ret, price, days in pos_data:
                    icon = "🟢" if ret >= 0 else "🔴"
                    lines.append(
                        f"  {icon} {ticker:<6}  {'+' if ret >= 0 else ''}{ret:.1f}%"
                        f"  @ ${price:.2f}  ({days}d)"
                    )

                if pos_data:
                    best = pos_data[0]
                    worst = pos_data[-1]
                    lines += [
                        "",
                        f"  🏆 Beste:    {best[0]}  {'+' if best[1]>=0 else ''}{best[1]:.1f}%",
                        f"  ⚠️ Schlechteste: {worst[0]}  {'+' if worst[1]>=0 else ''}{worst[1]:.1f}%",
                    ]
            else:
                lines.append("📈 *Positionen:* Keine offenen Positionen")
            lines.append("")

            # ── Trade-Statistiken ────────────────────────────────────────────
            try:
                stats = tracker.get_stats()
                total_trades = stats.get("total_trades", 0)
                win_rate = stats.get("win_rate", 0) * 100
                avg_ret = stats.get("avg_return_pct", 0)
                lines += [
                    "📉 *Trade-Statistiken*",
                    f"  Trades gesamt: {total_trades}",
                    f"  Win-Rate:      {win_rate:.0f}%",
                    f"  Ø Rendite:     {'+' if avg_ret >= 0 else ''}{avg_ret:.1f}%",
                    "",
                ]
            except Exception:
                pass

            # ── Bot-Score ───────────────────────────────────────────────────
            try:
                score = scorer.get()
                mod_label = ""
                from analyzers.bot_scorer import get_modifiers
                mod = get_modifiers(score.current)
                mod_label = f" ({mod.score_range})"
                lines += [
                    "🤖 *Bot-Score*",
                    f"  Score: *{score.current:.1f}/100*  {score.bar}{mod_label}",
                    f"  Peak:  {score.peak:.1f}  |  Trades bewertet: {score.trades_scored}",
                ]
                # Persönliche Rekorde
                pb = score.personal_bests
                records_today = []
                today_str = date.today().isoformat()
                for name, rec in [
                    ("Win-Rate", pb.best_win_rate_20),
                    ("Ø-Rendite", pb.best_avg_return_20),
                    ("Gewinnserie", pb.best_streak),
                    ("Einzel-Trade", pb.best_single_trade),
                ]:
                    if rec and rec.date == today_str:
                        records_today.append(f"{name}: {rec.value:.1f}")
                if records_today:
                    lines.append(f"  🏆 Heute neue Rekorde: {', '.join(records_today)}")
                lines.append("")
            except Exception:
                pass

            # ── Letzte Insider/Congressional Trades ─────────────────────────
            try:
                from collectors.insider_collector import InsiderCollector
                insider = InsiderCollector(lookback_days=1)
                insider_items = []
                for ticker in (tickers or []):
                    items = insider.collect(ticker)
                    insider_items.extend(items[:2])  # max 2 per ticker

                if insider_items:
                    lines.append("🏛️ *Insider/Congress Trades heute*")
                    for item in insider_items[:5]:
                        itype = item.get("insider_type", "")
                        icon = "🏛️" if "congressional" in itype else "👔"
                        action = item.get("transaction_type", "?")
                        name = item.get("insider_name", item.get("representative", "?"))
                        ticker_i = item.get("ticker", "?")
                        lines.append(f"  {icon} {ticker_i} – {name}: {action}")
                    lines.append("")
            except Exception:
                pass

            # ── Markt-Zusammenfassung ────────────────────────────────────────
            try:
                import yfinance as yf
                indices = {"S&P 500": "^GSPC", "NASDAQ": "^IXIC", "VIX": "^VIX"}
                mkt_lines = []
                for name, sym in indices.items():
                    ticker_data = yf.Ticker(sym)
                    hist = ticker_data.history(period="2d")
                    if hist is not None and len(hist) >= 2:
                        prev = hist["Close"].iloc[-2]
                        curr = hist["Close"].iloc[-1]
                        chg = (curr - prev) / prev * 100
                        icon = "🟢" if chg >= 0 else "🔴"
                        mkt_lines.append(f"  {icon} {name}: {'+' if chg>=0 else ''}{chg:.1f}%")
                if mkt_lines:
                    lines.append("🌍 *Markt heute*")
                    lines.extend(mkt_lines)
                    lines.append("")
            except Exception:
                pass

            # ── Abschluss ────────────────────────────────────────────────────
            lines.append("_Nächste Vollanalyse: morgen vor Börseneröffnung_")

            return "\n".join(lines)

        except Exception as e:
            log.warning("DailyDashboard.generate fehlgeschlagen: %s", e)
            return f"📊 *Täglicher Bericht*\n_Fehler beim Generieren: {e}_"
