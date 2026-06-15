"""
SignalDrivenExpander – erkennt unbekannte Ticker aus bestehenden Datenquellen
und fügt sie temporär zur Watchlist hinzu.

Quellen die Ticker liefern:
  • SEC Form 4 (Insider-Trades)      → große Insider-Käufe
  • USASpending (Regierungsaufträge) → große neue Aufträge
  • OptionsFlow (Options-Daten)      → ungewöhnlicher C/P-Flow
  • Reddit / StockTwits              → Social-Media-Spikes
  • Reddit-Titel / Texte             → Ticker-Erwähnungen

Jeder erkannte Ticker bekommt einen Ablauf-Timestamp (Standard: 7 Tage).
Wenn kein neues Signal innerhalb der Frist eintrifft → automatisch entfernt.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# ─── Persistenz ──────────────────────────────────────────────────────────────
_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "signal_tickers.json")

# Bekannte Nicht-Ticker die in Texten auftauchen (False-Positive-Filter)
_BLACKLIST = {
    "CEO", "CFO", "CTO", "IPO", "FDA", "SEC", "ETF", "GDP", "CPI", "FED",
    "USA", "USD", "EUR", "GBP", "JPY", "API", "AI", "ML", "EPS", "PE",
    "ALL", "ARE", "FOR", "NEW", "NOW", "BIG", "TOP", "MAX", "NET", "PAY",
    "IT", "US", "UK", "EU", "AM", "PM", "DD", "RE", "DM", "PR", "IR",
    "YOLO", "FOMO", "IMO", "ATH", "ATL", "OTC", "RH", "WSB",
    # Weitere beobachtete Falsch-Positive aus Text-Extraktion (engl. Wörter,
    # Indizes, falsch geschriebene Ticker):
    "BUY", "SELL", "HOLD", "HIV", "CEOS", "EV", "Q1", "Q2", "Q3", "Q4",
    "ADOBE", "TSMC", "WANT", "GAIN", "LOSS", "RISK", "CASH", "DEBT", "BULL",
    "BEAR", "CALL", "PUT", "LONG", "SHORT", "OPEN", "HIGH", "LOW", "RED",
    "NASA", "NO", "MASK", "QUANT", "WWR", "FLD", "AIS", "MUU", "SPU",
    "TE", "CL", "PG",  # (Crude-/Futures-Kürzel bzw. Large-Cap, kein Small-Cap)
    # Phantom-/Privatfirmen-Ticker: Social-Cashtags meinen die private Firma,
    # Yahoo liefert dafür aber ein delistetes/Phantom-Listing → kein handelbares
    # Signal. SPCX = "$SpaceX"-Erwähnungen (SpaceX ist privat, kein echter Ticker).
    "SPCX",
}

# Indizes / breite ETFs: keine Small-Cap-Entdeckungen, gehören nicht in den Radar.
_INDEX_ETFS = {
    "SPY", "SPX", "QQQ", "DIA", "IWM", "VOO", "VTI", "VXX", "UVXY", "SQQQ",
    "TQQQ", "SOXL", "SOXS", "ARKK", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
    "XLP", "XLU", "XLB", "XLRE", "SMH", "ICLN", "GDX", "TLT", "HYG", "GLD",
    "SLV", "USO", "AGQ", "SIVR", "DXY",
    # zusätzlich beobachtete Indizes/ETFs/Futures-Kürzel:
    "SOXX", "SOX", "IGV", "NDX", "VIX", "DJIA", "NQ", "CHIP", "QQQI",
    "IBIT", "GBTC", "NUGT", "DUST", "XRPI", "XRPR", "XRPC", "QQQM", "RSP",
}

# Krypto-Tickers (keine handelbaren Aktien im Small-Cap-Sinn).
_CRYPTO = {
    "BTC", "ETH", "SOL", "XRP", "DOT", "XLM", "ZEC", "JASMY", "ADA", "DOGE",
    "SHIB", "AVAX", "MATIC", "LTC", "BCH", "LINK", "UNI", "ATOM", "ETC",
    "ALGO", "NEAR", "FTM", "SAND", "MANA", "AAVE", "TRX", "XMR", "PEPE", "BNB",
}

# Bekannte Large-/Mega-Caps außerhalb des Haupt-Universums – keine Small-Cap-
# Entdeckung. Der echte Marktkap-Check beim Eskalieren ist die harte Grenze;
# diese Liste hält nur die offensichtlichen Großen schon aus der Sammlung raus.
_KNOWN_LARGE_CAPS = {
    "IBM", "PYPL", "DELL", "KO", "PEP", "TGT", "SBUX", "AMGN", "REGN", "ROKU",
    "GME", "AMC", "SNAP", "RIVN", "NVO", "SHEL", "HPE", "NOK", "MDT", "HUM",
    "UPS", "AAL", "WBD", "VEEV", "MNDY", "TEAM", "NDAQ", "FOX", "GRAB", "GLW",
    "NLY", "CELH", "BYND", "PSX", "GOOG", "BRK", "BN", "NEM", "DJT", "IBM",
}

# Marktkap-Fenster für den Marktkap-Check beim Eskalieren (USD).
_SC_MIN_MCAP = int(os.getenv("SIGNAL_SC_MIN_MCAP", str(50_000_000)))       # 50 Mio
_SC_MAX_MCAP = int(os.getenv("SIGNAL_SC_MAX_MCAP", str(10_000_000_000)))   # 10 Mrd

# Mindest-Kaufvolumen für Insider-Signal (USD)
_MIN_INSIDER_BUY_USD = 200_000

# Mindest-Auftragswert für USASpending-Signal (USD)
_MIN_CONTRACT_USD = 5_000_000

# Mindest-Score für Social-Signal (Reddit/StockTwits pulse)
_MIN_SOCIAL_SCORE = 0.25

# Ablaufzeit in Tagen
_DEFAULT_TTL_DAYS = 7

# ─── Passive Sammlung → Eskalation ───────────────────────────────────────────
# Jeder Ticker sammelt erst passiv "Gewicht" an. Erst wenn er GENUG Gewicht UND
# Bestätigung aus MEHREREN Quellen hat, wird er zur Analyse-Watchlist promoted
# und voll (Claude) eingeschätzt. So werden nicht Dutzende einmaliger Social-
# Erwähnungen jeden Zyklus teuer analysiert – der Hauptfokus bleibt auf der
# Kern-Marktdaten-Analyse, der Radar liefert nur gelegentlich einen reifen
# Small-Cap nach.
_READY_WEIGHT  = 2.0          # Gewichts-Schwelle (≈5-6 Social-Erwähnungen)
_MIN_SOURCES   = 2           # … UND mind. so viele VERSCHIEDENE Signalquellen
# Gesamt-Obergrenze: so viele eskalierte Small-Caps dürfen INSGESAMT gleichzeitig
# in der aktiven Analyse stehen. Anders als der Pro-Zyklus-Deckel begrenzt dies
# den Gesamtbestand "ready" – verhindert, dass über viele Zyklen Hunderte
# dauerhaft mitanalysiert werden. Nur die schwersten N (höchstes Gewicht zuerst).
_MAX_READY_TOTAL = int(os.getenv("SIGNAL_MAX_READY_TOTAL", "3"))
# Deckel: max. so viele Small-Caps pro Zyklus frisch eskalieren lassen. Schützt
# die Analyse-Watchlist vor plötzlichen Schwüngen. Übrige reife Ticker behalten
# ihr Gewicht und rücken in den Folgezyklen nach (höchstes Gewicht zuerst).
_MAX_PROMOTE_PER_CYCLE = int(os.getenv("SIGNAL_MAX_PROMOTE_PER_CYCLE", "3"))
_W_INSIDER    = 1.0          # starkes Einzelsignal – braucht aber 2. Quelle
_W_CONTRACT   = 1.0
_W_OPTIONS    = 0.6
_W_SOCIAL     = 0.4          # mehrere Erwähnungen sammeln Gewicht an
_W_MENTION    = 0.34


class SignalDrivenExpander:
    """
    Sammelt Signal-Ticker aus allen vorhandenen Collector-Ergebnissen
    und pflegt eine persistente temporäre Watchlist-Erweiterung.
    """

    def __init__(self, ttl_days: int = _DEFAULT_TTL_DAYS):
        self.ttl_days = ttl_days
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def process_news_items(self, items: List[Dict]) -> List[str]:
        """
        Analysiert News-Items (aus beliebigen Collectors) auf Signal-Ticker.
        Gibt neu hinzugefügte Ticker zurück.
        """
        found: Dict[str, tuple] = {}  # ticker → (grund, gewicht, quelle)

        for item in items:
            source = item.get("source", "").lower()
            ticker = (item.get("ticker") or "").upper().strip()

            # ── Insider-Trades (SEC Form 4) ───────────────────────────────
            if "insider" in source or "form 4" in source or "sec" in source:
                action = (item.get("action") or item.get("text") or "").lower()
                value  = item.get("value") or item.get("amount") or 0
                try:
                    value = float(str(value).replace(",", "").replace("$", ""))
                except Exception:
                    value = 0
                if "buy" in action or "purchase" in action or "acqui" in action:
                    if value >= _MIN_INSIDER_BUY_USD and self._is_valid_ticker(ticker):
                        found[ticker] = (f"Insider-Kauf ${value:,.0f}", _W_INSIDER, "insider")

            # ── US-Regierungsaufträge ─────────────────────────────────────
            elif "usaspending" in source or "contract" in source or "government" in source:
                value = item.get("amount") or item.get("value") or 0
                try:
                    value = float(str(value).replace(",", "").replace("$", ""))
                except Exception:
                    value = 0
                if value >= _MIN_CONTRACT_USD and self._is_valid_ticker(ticker):
                    found[ticker] = (f"Regierungsauftrag ${value:,.0f}", _W_CONTRACT, "contract")

            # ── Options-Flow ──────────────────────────────────────────────
            elif "option" in source or "flow" in source:
                cp_ratio = item.get("call_put_ratio") or item.get("cp_ratio") or 0
                try:
                    cp_ratio = float(cp_ratio)
                except Exception:
                    cp_ratio = 0
                if cp_ratio >= 3.0 and self._is_valid_ticker(ticker):
                    found[ticker] = (f"Options-Flow C/P={cp_ratio:.1f}", _W_OPTIONS, "options")

            # ── Social-Erwähnungen (Reddit / StockTwits) ──────────────────
            elif any(s in source for s in ("reddit", "stocktwits", "twitter")):
                score = abs(item.get("sentiment_score") or item.get("score") or 0)
                try:
                    score = float(score)
                except Exception:
                    score = 0
                if score >= _MIN_SOCIAL_SCORE and self._is_valid_ticker(ticker):
                    found[ticker] = (f"Social-Signal score={score:.2f}", _W_SOCIAL, "social")
                # Auch Ticker aus Texten extrahieren
                text = item.get("title", "") + " " + item.get("text", "")
                for t in self._extract_tickers_from_text(text):
                    if t not in found:
                        found[t] = (f"Social-Erwähnung ({source.split('/')[0]})", _W_MENTION, "mention")

        return self._add_tickers(found)

    def process_social_spikes(self, spikes: List[Dict]) -> List[str]:
        """
        Verarbeitet Social-Pulse-Spikes direkt (aus run_social_scan).
        spikes: Liste mit {ticker, spike_ratio, avg_score}
        """
        found: Dict[str, tuple] = {}
        for spike in spikes:
            ticker = (spike.get("ticker") or "").upper().strip()
            ratio  = spike.get("spike_ratio", 0)
            score  = spike.get("avg_score", 0)
            if self._is_valid_ticker(ticker) and ratio >= 3.0:
                # Spike ist stärker als eine Einzel-Erwähnung → höheres Gewicht
                found[ticker] = (f"Social-Spike {ratio:.1f}× (score {score:+.2f})", _W_OPTIONS, "social")
        return self._add_tickers(found)

    def get_active_tickers(self) -> List[str]:
        """Gibt alle noch gültigen Signal-Ticker zurück (nicht abgelaufen)."""
        data = self._load()
        now = datetime.utcnow()
        active = []
        for ticker, entry in data.items():
            try:
                expires = datetime.fromisoformat(entry["expires_at"])
                if expires > now:
                    active.append(ticker)
            except Exception:
                pass
        return active

    def get_ready_tickers(self) -> List[str]:
        """Nur Ticker die eskaliert sind (→ Analyse), hart auf die Gesamt-
        Obergrenze _MAX_READY_TOTAL gedeckelt (schwerste zuerst). Passiv
        sammelnde Ticker unter Schwelle werden NICHT zurückgegeben. Der harte
        Deckel hier ist das Sicherheitsnetz: selbst wenn aus Alt-Daten viele
        ready-Flags existieren, landen nie mehr als N in der Analyse."""
        data = self._load()
        now = datetime.utcnow()
        ready = []
        for ticker, entry in data.items():
            try:
                if datetime.fromisoformat(entry["expires_at"]) <= now:
                    continue
            except Exception:
                continue
            if self._is_ready(entry):
                ready.append((ticker, self._weight_of(entry)))
        ready.sort(key=lambda x: x[1], reverse=True)
        return [t for t, _ in ready[:_MAX_READY_TOTAL]]

    @staticmethod
    def _weight_of(entry: Dict) -> float:
        return entry.get("weight", entry.get("signals", 1) * _W_MENTION)

    @staticmethod
    def _sources_of(entry: Dict) -> set:
        """Distinkte Signalquellen eines Eintrags. Alt-Einträge ohne 'sources'
        werden grob aus dem Grund-Text abgeleitet (sonst leer = einzelne Quelle)."""
        if entry.get("sources"):
            return set(entry["sources"])
        reason = (entry.get("reason") or "").lower()
        for key, src in (
            ("insider", "insider"), ("regierungs", "contract"), ("contract", "contract"),
            ("options", "options"), ("spike", "social"), ("social", "social"),
        ):
            if key in reason:
                return {src}
        return set()

    @classmethod
    def _is_ready(cls, entry: Dict) -> bool:
        """Bereit-für-Analyse = das ready-Flag, das in _add_tickers gedeckelt und
        quellen-geprüft gesetzt wird. Kein impliziter Legacy-Pfad mehr: ein
        Eintrag wird nur über die Eskalations-Logik 'ready'."""
        return bool(entry.get("ready"))

    @classmethod
    def _is_eligible(cls, entry: Dict) -> bool:
        """Eskalations-Kriterium: genug Gewicht UND mind. _MIN_SOURCES
        verschiedene Signalquellen. Reines Social-Rauschen (nur 'mention'/
        'social') reicht so nie allein – es braucht Bestätigung."""
        return (cls._weight_of(entry) >= _READY_WEIGHT
                and len(cls._sources_of(entry)) >= _MIN_SOURCES)

    def get_all_entries(self) -> List[Dict]:
        """Alle Einträge mit Metadaten (für Dashboard)."""
        data = self._load()
        now = datetime.utcnow()
        result = []
        for ticker, entry in data.items():
            try:
                expires = datetime.fromisoformat(entry["expires_at"])
                active  = expires > now
            except Exception:
                active = False
            _ready = self._is_ready(entry)
            sources = sorted(self._sources_of(entry))
            result.append({
                "ticker":     ticker,
                "reason":     entry.get("reason", "–"),
                "added_at":   entry.get("added_at", "–")[:16],
                "expires_at": entry.get("expires_at", "–")[:16],
                "active":     active,
                "signals":    entry.get("signals", 1),
                "sources":    sources,
                "n_sources":  len(sources),
                "weight":     round(self._weight_of(entry), 2),
                "status":     ("🔬 in Analyse" if (active and _ready)
                               else "📥 sammelt" if active else "abgelaufen"),
            })
        result.sort(key=lambda x: (not x["active"], -x["weight"]))
        return result

    def cleanup_expired(self) -> int:
        """Entfernt abgelaufene Einträge. Gibt Anzahl entfernter zurück."""
        data = self._load()
        now = datetime.utcnow()
        before = len(data)
        data = {
            t: e for t, e in data.items()
            if datetime.fromisoformat(e["expires_at"]) > now
        }
        self._save(data)
        return before - len(data)

    def renew(self, ticker: str, reason: str = ""):
        """Verlängert einen bestehenden Ticker um ttl_days und akkumuliert
        Gewicht. Promotet NICHT selbst – die Eskalation (mit Quellen- und
        Gesamt-Deckel-Prüfung) läuft ausschließlich über _add_tickers."""
        data = self._load()
        if ticker in data:
            entry = data[ticker]
            entry["expires_at"] = (
                datetime.utcnow() + timedelta(days=self.ttl_days)
            ).isoformat()
            entry["signals"] = entry.get("signals", 1) + 1
            entry["weight"]  = round(self._weight_of(entry) + _W_MENTION, 3)
            if reason:
                entry["reason"] = reason
            self._save(data)

    def prune(self) -> Dict[str, int]:
        """Bereinigt den Bestand: wirft Blacklist-/Index-/Universum-Ticker und
        ungültige Symbole raus, normalisiert ready-Flags neu (nur die schwersten
        _MAX_READY_TOTAL eskalierten bleiben in Analyse). Echte Small-Caps
        behalten Gewicht & Quellen. Idempotent. Gibt Statistik zurück."""
        data = self._load()
        removed = 0
        for ticker in list(data.keys()):
            if not self._is_valid_ticker(ticker):
                del data[ticker]
                removed += 1

        # ready-Flags komplett neu vergeben: nur eskalierte (Gewicht + Quellen),
        # davon die schwersten N. Alles andere fällt auf "sammelt" zurück.
        eligible = [t for t, e in data.items() if self._is_eligible(e)]
        eligible.sort(key=lambda t: self._weight_of(data[t]), reverse=True)
        keep_ready = set(eligible[:_MAX_READY_TOTAL])
        for t, e in data.items():
            e["ready"] = t in keep_ready

        self._save(data)
        return {"removed": removed, "ready": len(keep_ready), "total": len(data)}

    # ── Interne Helfer ────────────────────────────────────────────────────────

    def _add_tickers(self, found: Dict[str, tuple]) -> List[str]:
        """Sammelt Signale passiv an. Akkumuliert pro Ticker Gewicht; erst ab
        _READY_WEIGHT wird der Ticker zur Analyse promoted (ready=True).
        Gibt frisch promotete Ticker zurück (die jetzt analysiert werden sollen)."""
        data = self._load()
        promoted: List[str] = []
        eligible: List[str] = []          # Schwelle erreicht, wartet auf Promote-Slot
        now = datetime.utcnow()
        expires = (now + timedelta(days=self.ttl_days)).isoformat()

        for ticker, payload in found.items():
            if not self._is_valid_ticker(ticker):
                continue
            if isinstance(payload, tuple):
                reason  = payload[0]
                weight  = payload[1] if len(payload) > 1 else _W_MENTION
                source  = payload[2] if len(payload) > 2 else "mention"
            else:
                reason, weight, source = payload, _W_MENTION, "mention"
            if ticker in data:
                entry = data[ticker]
                entry["expires_at"] = expires
                entry["signals"]    = entry.get("signals", 1) + 1
                entry["weight"]     = round(self._weight_of(entry) + weight, 3)
                entry["reason"]     = reason
                entry["sources"]    = sorted(self._sources_of(entry) | {source})
            else:
                entry = data[ticker] = {
                    "reason":     reason,
                    "added_at":   now.isoformat(),
                    "expires_at": expires,
                    "signals":    1,
                    "weight":     round(weight, 3),
                    "sources":    [source],
                    "ready":      False,
                }

        # Wie viele "ready"-Slots sind insgesamt noch frei? Der Gesamt-Deckel
        # _MAX_READY_TOTAL begrenzt, wie viele Small-Caps INSGESAMT gleichzeitig
        # analysiert werden – nicht nur wie viele pro Zyklus dazukommen.
        active_ready = [
            t for t, e in data.items()
            if e.get("ready") and self._not_expired(e, now)
        ]
        free_slots = max(0, _MAX_READY_TOTAL - len(active_ready))
        slots = min(free_slots, _MAX_PROMOTE_PER_CYCLE)

        # Promote-Kandidaten = aktive, noch nicht ready Einträge, die das
        # Eskalations-Kriterium erfüllen (Gewicht UND ≥2 Quellen). Rückstau aus
        # vorigen Zyklen rückt mit nach (höchstes Gewicht zuerst).
        if slots > 0:
            for ticker, entry in data.items():
                if entry.get("ready") or not self._not_expired(entry, now):
                    continue
                if self._is_eligible(entry):
                    eligible.append(ticker)

            eligible.sort(key=lambda t: self._weight_of(data[t]), reverse=True)
            # Marktkap-Check beim Eskalieren: nur echte Small-/Mid-Caps dürfen in
            # die Analyse. Large-Caps werden verworfen, unklare (Abruf-Fehler)
            # übersprungen und im Folgezyklus erneut geprüft.
            for ticker in eligible:
                if len(promoted) >= slots:
                    break
                verdict = self._passes_mcap_gate(ticker)
                if verdict is False:
                    data.pop(ticker, None)          # kein Small-Cap → raus
                    continue
                if verdict is None:
                    continue                        # unklar → später erneut
                data[ticker]["ready"] = True
                data[ticker]["promoted_at"] = now.isoformat()
                promoted.append(ticker)

        self._save(data)

        # Frisch promotete Ticker still in die BenchList aufnehmen – KEINE
        # Telegram-Nachricht. Die Eskalation läuft passiv im Hintergrund; der
        # aktuelle Stand (📥 sammelt / 🔬 in Analyse + Gewicht) ist jederzeit im
        # Dashboard-Small-Cap-Radar einsehbar. Sonst flutet jeder Zyklus mit
        # Dutzenden Small-Caps das Telegram.
        if promoted:
            try:
                from analyzers.bench_list import BenchList
                bench = BenchList()
                for ticker in promoted:
                    bench.add(ticker, reason=data[ticker].get("reason", "Signal-Expander"), score=0.5)
            except Exception:
                pass

        return promoted

    @classmethod
    def _extract_tickers_from_text(cls, text: str) -> List[str]:
        """Extrahiert $TICKER und #TICKER Muster aus Text."""
        # $AAPL oder $aapl
        cashtags = re.findall(r'\$([A-Z]{1,5})\b', text.upper())
        # Hashtags wie #AAPL
        hashtags = re.findall(r'#([A-Z]{1,5})\b', text.upper())
        result = [t for t in set(cashtags + hashtags) if cls._is_valid_ticker(t)]
        return result

    @staticmethod
    def _not_expired(entry: Dict, now: datetime) -> bool:
        try:
            return datetime.fromisoformat(entry["expires_at"]) > now
        except Exception:
            return False

    @classmethod
    def _is_valid_ticker(cls, ticker: str) -> bool:
        """Validierung für den Small-Cap-Radar: 2–5 Großbuchstaben, nicht in
        Blacklist/Krypto/Index/ETF, kein bekannter Large-Cap und nicht bereits
        im Haupt-Scan-Universum (Mega-/Large-Caps werden ohnehin jeden Zyklus
        analysiert – sie sind keine Small-Cap-Entdeckung). Die harte 'wirklich
        ein Small-Cap'-Grenze ist der Marktkap-Check beim Eskalieren."""
        if not ticker:
            return False
        if ticker in _BLACKLIST or ticker in _INDEX_ETFS or ticker in _CRYPTO:
            return False
        if ticker in _KNOWN_LARGE_CAPS:
            return False
        if ticker in cls._main_universe():
            return False
        if not re.match(r'^[A-Z]{2,5}$', ticker):   # min. 2 Buchstaben
            return False
        return True

    @classmethod
    def _passes_mcap_gate(cls, ticker: str) -> Optional[bool]:
        """Echter Marktkap-Check beim Eskalieren. True = im Small-/Mid-Cap-
        Fenster, False = außerhalb (z.B. Large-Cap → raus), None = unbekannt/
        Abruf fehlgeschlagen (→ diesen Zyklus nicht promoten, später erneut)."""
        try:
            import yfinance as yf
            mcap = (yf.Ticker(ticker).info or {}).get("marketCap") or 0
        except Exception:
            return None
        if not mcap:
            return None
        return _SC_MIN_MCAP <= mcap <= _SC_MAX_MCAP

    @staticmethod
    def _main_universe() -> set:
        """Haupt-Scan-Universum (Large-/Mega-Caps), lazy geladen um Zirkular-
        Import mit dynamic_watchlist zu vermeiden. Fällt bei Fehler auf leer."""
        try:
            from analyzers.dynamic_watchlist import SCAN_UNIVERSE
            return set(SCAN_UNIVERSE)
        except Exception:
            return set()

    def _load(self) -> Dict:
        try:
            with open(_DATA_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: Dict):
        import tempfile, os
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(_DATA_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, _DATA_FILE)
