from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "weekly_briefing.db")

# US-Sektor-ETFs (SPDR)
US_SECTOR_ETFS = {
    "XLK":  "Technologie",
    "XLF":  "Finanzen",
    "XLE":  "Energie",
    "XLV":  "Gesundheit",
    "XLI":  "Industrie",
    "XLY":  "Zyklischer Konsum",
    "XLP":  "Basiskonsumgüter",
    "XLU":  "Versorger",
    "XLRE": "Immobilien",
    "XLB":  "Materialien",
    "XLC":  "Kommunikation",
}

# EU-Sektor-ETFs (iShares/Xtrackers)
EU_SECTOR_ETFS = {
    "EXV1.DE": "EU-Banken",
    "EXV4.DE": "EU-Gesundheit",
    "EXV6.DE": "EU-Industrie",
    "EXV8.DE": "EU-Technologie",
    "EXH7.DE": "EU-Energie",
    "EXV3.DE": "EU-Konsumgüter",
    "IQQP.DE": "EU-Immobilien",
    "EXV5.DE": "EU-Versorger",
}

# Krypto-Symbole
CRYPTO_TICKERS = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "BNB-USD": "BNB",
    "XRP-USD": "XRP",
}

# Wichtige Makro-Tickers
MACRO_TICKERS = {
    "^VIX":    "VIX",
    "^TNX":    "10J-Treasury",
    "DX-Y.NYB": "US-Dollar-Index",
    "GLD":     "Gold",
    "USO":     "Öl (USO)",
    "TLT":     "US-Anleihen (TLT)",
}


class WeekendPrep:
    """
    Erstellt Wochenvorbereitungs-Briefings mit:
    - Sektorperformance (US + EU) der vergangenen Woche
    - VIX-Level + Trend
    - Makro-News-Zusammenfassung via Claude
    - Earnings-Kalender der kommenden Woche
    - Krypto-Wochenperformance
    """

    def __init__(self, anthropic_api_key: str = "", watchlist: Optional[List[str]] = None):
        self.api_key = anthropic_api_key
        self.watchlist = watchlist or []
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_briefings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                week_start  TEXT NOT NULL,
                briefing    TEXT NOT NULL,
                raw_data    TEXT
            )
        """)
        self._conn.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> str:
        """Hauptmethode: sammelt alle Daten, generiert Briefing, speichert in DB."""
        log.info("WeekendPrep: Starte Wochenvorbereitung...")

        raw: Dict = {}

        # 1. Sektor-Performance
        raw["us_sectors"]  = self._get_sector_performance(US_SECTOR_ETFS)
        raw["eu_sectors"]  = self._get_sector_performance(EU_SECTOR_ETFS)
        raw["crypto"]      = self._get_sector_performance(CRYPTO_TICKERS)
        raw["macro"]       = self._get_macro_data()

        # 2. VIX
        raw["vix"]         = self._get_vix()

        # 3. Earnings-Kalender
        raw["earnings"]    = self._get_earnings_calendar()

        # 4. Makro-Events (ECB, BoE, etc.)
        raw["macro_events"] = self._detect_macro_events()

        # 5. Claude-Briefing generieren
        briefing = self._generate_briefing(raw)

        # 6. Speichern
        week_start = (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).strftime("%Y-%m-%d")
        self._conn.execute(
            "INSERT INTO weekly_briefings (created_at, week_start, briefing, raw_data) VALUES (?,?,?,?)",
            (datetime.utcnow().isoformat(), week_start, briefing, json.dumps(raw, default=str)),
        )
        self._conn.commit()
        log.info("WeekendPrep: Briefing gespeichert (Woche %s)", week_start)
        return briefing

    def get_current_briefing(self) -> Optional[str]:
        """Gibt das Briefing für die aktuelle (oder nächste) Handelswoche zurück.
        Auf Wochenende: sucht nach Montag der NÄCHSTEN Woche.
        Unter der Woche: sucht nach Montag der AKTUELLEN Woche."""
        from datetime import date
        today = date.today()
        if today.weekday() >= 5:
            days_to_monday = (7 - today.weekday()) % 7 or 7
            monday = (today + timedelta(days=days_to_monday)).isoformat()
        else:
            monday = (today - timedelta(days=today.weekday())).isoformat()
        row = self._conn.execute(
            "SELECT briefing FROM weekly_briefings WHERE week_start=? ORDER BY created_at DESC LIMIT 1",
            (monday,),
        ).fetchone()
        return row[0] if row else None

    def get_latest_briefing(self) -> Optional[str]:
        """Gibt das neueste gespeicherte Briefing zurück."""
        row = self._conn.execute(
            "SELECT briefing, created_at FROM weekly_briefings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return f"[Erstellt: {row[1][:16]}]\n\n{row[0]}"

    def get_briefing_age_hours(self) -> Optional[float]:
        """Gibt das Alter des neuesten Briefings in Stunden zurück."""
        row = self._conn.execute(
            "SELECT created_at FROM weekly_briefings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        try:
            ts = datetime.fromisoformat(row[0])
            return (datetime.utcnow() - ts).total_seconds() / 3600
        except Exception:
            return None

    # ── Data collectors ───────────────────────────────────────────────────────

    def _get_sector_performance(self, etf_map: Dict[str, str]) -> Dict:
        result = {}
        for sym, name in etf_map.items():
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if len(hist) >= 2:
                    start = float(hist["Close"].iloc[0])
                    end   = float(hist["Close"].iloc[-1])
                    if start > 0:
                        pct = (end - start) / start * 100
                        result[sym] = {"name": name, "change_pct": round(pct, 2), "price": round(end, 2)}
            except Exception:
                pass
        return result

    def _get_macro_data(self) -> Dict:
        result = {}
        for sym, name in MACRO_TICKERS.items():
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if len(hist) >= 2:
                    start = float(hist["Close"].iloc[0])
                    end   = float(hist["Close"].iloc[-1])
                    if start > 0:
                        pct = (end - start) / start * 100
                        result[sym] = {"name": name, "change_pct": round(pct, 2), "price": round(end, 4)}
            except Exception:
                pass
        return result

    def _get_vix(self) -> Dict:
        try:
            hist = yf.Ticker("^VIX").history(period="5d")
            if not hist.empty:
                current = float(hist["Close"].iloc[-1])
                week_ago = float(hist["Close"].iloc[0])
                label = (
                    "Sehr ruhig" if current < 15 else
                    "Normal"     if current < 20 else
                    "Erhöht"     if current < 25 else
                    "Nervös"     if current < 30 else
                    "Angst"      if current < 40 else
                    "Panik"
                )
                return {
                    "current": round(current, 2),
                    "week_ago": round(week_ago, 2),
                    "change": round(current - week_ago, 2),
                    "label": label,
                }
        except Exception:
            pass
        return {"current": None, "label": "Unbekannt"}

    def _get_earnings_calendar(self) -> List[Dict]:
        """
        Earnings der nächsten Woche aus yfinance für Watchlist-Ticker.
        Gibt Liste von {ticker, date, expected_eps} zurück.
        """
        results = []
        # Nächste 7 Tage
        now = datetime.utcnow()
        end = now + timedelta(days=7)
        for ticker in (self.watchlist or [])[:30]:  # max 30 für Speed
            try:
                cal = yf.Ticker(ticker).calendar
                if cal is None or cal.empty:
                    continue
                # Earnings Date ist oft ein Index
                if hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                    ed = cal["Earnings Date"].iloc[0]
                elif hasattr(cal, "index") and "Earnings Date" in cal.index:
                    ed = cal.loc["Earnings Date"].iloc[0] if hasattr(cal.loc["Earnings Date"], "iloc") else cal.loc["Earnings Date"]
                else:
                    continue
                if pd_timestamp_in_range(ed, now, end):
                    results.append({"ticker": ticker, "date": str(ed)[:10]})
            except Exception:
                pass
        return results

    def _detect_macro_events(self) -> List[str]:
        """Einfache Heuristik: erkennt bekannte Makro-Event-Wörter in aktuellen Nachrichten."""
        events = []
        keywords = ["FOMC", "Fed", "ECB", "EZB", "BoE", "CPI", "PPI", "NFP", "GDP", "PCE",
                    "Zinsentscheidung", "Zinserhöhung", "Zinssenkung", "Inflation", "Arbeitsmarkt"]
        try:
            import feedparser
            feeds = [
                "https://feeds.reuters.com/reuters/businessNews",
                "https://feeds.bbci.co.uk/news/business/rss.xml",
            ]
            seen = set()
            for url in feeds:
                feed = feedparser.parse(url)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    for kw in keywords:
                        if kw.lower() in title.lower() and title not in seen:
                            events.append(title)
                            seen.add(title)
                            break
        except Exception:
            pass
        return events[:10]

    # ── Claude briefing generator ─────────────────────────────────────────────

    def _generate_briefing(self, raw: Dict) -> str:
        """Sendet alle gesammelten Daten an Claude und erhält ein strukturiertes Briefing."""
        if not self.api_key:
            return self._fallback_briefing(raw)

        # Daten komprimieren für Prompt
        us_top = sorted(
            raw.get("us_sectors", {}).items(),
            key=lambda x: abs(x[1].get("change_pct", 0)), reverse=True
        )[:5]
        eu_top = sorted(
            raw.get("eu_sectors", {}).items(),
            key=lambda x: abs(x[1].get("change_pct", 0)), reverse=True
        )[:3]
        crypto_top = sorted(
            raw.get("crypto", {}).items(),
            key=lambda x: abs(x[1].get("change_pct", 0)), reverse=True
        )[:3]
        macro = raw.get("macro", {})
        vix   = raw.get("vix", {})
        events = raw.get("macro_events", [])
        earnings = raw.get("earnings", [])

        us_lines = "\n".join(
            f"  {v['name']}: {v['change_pct']:+.1f}%" for _, v in us_top
        )
        eu_lines = "\n".join(
            f"  {v['name']}: {v['change_pct']:+.1f}%" for _, v in eu_top
        ) if eu_top else "  (keine EU-Daten)"
        crypto_lines = "\n".join(
            f"  {v['name']}: {v['change_pct']:+.1f}%" for _, v in crypto_top
        ) if crypto_top else "  (keine Krypto-Daten)"
        macro_lines = "\n".join(
            f"  {v['name']}: {v['change_pct']:+.1f}% (${v['price']})" for v in macro.values()
        ) if macro else "  (keine Makro-Daten)"
        vix_line = (
            f"VIX: {vix['current']} ({vix['label']}) | Δ Woche: {vix['change']:+.1f}"
            if vix.get("current") else "VIX: Unbekannt"
        )
        events_text = "\n".join(f"  - {e}" for e in events) if events else "  Keine bekannten Makro-Events"
        earnings_text = (
            "\n".join(f"  {e['ticker']}: {e['date']}" for e in earnings[:10])
            if earnings else "  Keine Earnings in den nächsten 7 Tagen"
        )

        prompt = f"""Du bist ein erfahrener Marktanalyst. Erstelle ein strukturiertes Wochenbriefing auf Deutsch.

## Sektordaten (Wochenperformance)

US-Sektoren (Top-Mover):
{us_lines}

EU-Sektoren:
{eu_lines}

Krypto (Wochenperformance):
{crypto_lines}

## Makro
{macro_lines}
{vix_line}

## Makro-Ereignisse der Woche
{events_text}

## Anstehende Earnings (nächste 7 Tage)
{earnings_text}

---
Erstelle ein Wochenbriefing mit diesen Abschnitten:
1. **Marktzusammenfassung** (2-3 Sätze: Stimmung, Haupttrend)
2. **Sektor-Rotation** (welche Sektoren führen / hinterherhinken, Schlussfolgerung)
3. **Makro-Risiken** (Top-3 Risiken für die kommende Woche)
4. **Earnings-Watch** (welche Ergebnisse besonders beachten)
5. **Handlungsempfehlungen** (3-5 konkrete Punkte: wo aufstocken, was meiden, worauf achten)
6. **Ausblick** (1-2 Sätze: Bull/Bear-Szenario für die Woche)

Sei direkt, konkret und aktionsorientiert. Kein Marketing-Sprech."""

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text.strip()
        except Exception as e:
            log.warning("WeekendPrep: Claude-Briefing fehlgeschlagen: %s", e)
            return self._fallback_briefing(raw)

    def _fallback_briefing(self, raw: Dict) -> str:
        """Einfaches Text-Briefing ohne Claude, falls API nicht verfügbar."""
        lines = ["# Wochenvorbereitung (ohne KI-Analyse)\n"]

        vix = raw.get("vix", {})
        if vix.get("current"):
            lines.append(f"**VIX**: {vix['current']} ({vix['label']})")

        us = raw.get("us_sectors", {})
        if us:
            lines.append("\n**US-Sektoren (Woche):**")
            for sym, d in sorted(us.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True):
                lines.append(f"  {d['name']}: {d['change_pct']:+.1f}%")

        eu = raw.get("eu_sectors", {})
        if eu:
            lines.append("\n**EU-Sektoren (Woche):**")
            for sym, d in sorted(eu.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True):
                lines.append(f"  {d['name']}: {d['change_pct']:+.1f}%")

        crypto = raw.get("crypto", {})
        if crypto:
            lines.append("\n**Krypto (Woche):**")
            for sym, d in sorted(crypto.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True):
                lines.append(f"  {d['name']}: {d['change_pct']:+.1f}%")

        earnings = raw.get("earnings", [])
        if earnings:
            lines.append("\n**Anstehende Earnings:**")
            for e in earnings[:10]:
                lines.append(f"  {e['ticker']}: {e['date']}")

        events = raw.get("macro_events", [])
        if events:
            lines.append("\n**Makro-Events:**")
            for ev in events[:5]:
                lines.append(f"  - {ev}")

        return "\n".join(lines)


# ── Helper ────────────────────────────────────────────────────────────────────

def pd_timestamp_in_range(ts, start: datetime, end: datetime) -> bool:
    """Prüft ob ein pandas Timestamp oder datetime im Bereich liegt."""
    try:
        import pandas as pd
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime().replace(tzinfo=None)
        elif hasattr(ts, "date"):
            ts = ts.replace(tzinfo=None) if hasattr(ts, "tzinfo") else ts
        return start <= ts <= end
    except Exception:
        return False
