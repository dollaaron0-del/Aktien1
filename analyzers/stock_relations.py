"""
Stock Relations Graph – thematische Verbindungen zwischen Aktien.

Kombiniert:
  • Statische Themen-Cluster (kuratiert, Stand 2025/26)
  • Dynamisch gelernte Beziehungen aus echten Bot-BUY-Signalen

Wenn Aktie A ein BUY-Signal hat, sucht der Runner automatisch verwandte
Kandidaten aus demselben Thema und ergänzt die Analyseschlange.
"""
import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List

from logger import get_logger

log = get_logger(__name__)

_GRAPH_FILE             = os.path.join("data", "stock_relations.json")
_MAX_ENTRIES_PER_TICKER = 6

# ── Themen-Cluster (Stand 2025/26) ────────────────────────────────────────────
# Alle Ticker innerhalb eines Clusters sind thematisch verwandt.
# Neue Themen einfach als weiteren Eintrag ergänzen.

_THEMES: Dict[str, List[str]] = {

    # KI – Chips & Hardware ───────────────────────────────────────────────────
    "AI_CHIPS": [
        "NVDA", "AMD", "AVGO", "ARM", "INTC",
        "TSM", "ASML", "AMAT", "LRCX", "KLAC", "MU", "MRVL",
    ],

    # KI – Hyperscaler & Cloud ────────────────────────────────────────────────
    "AI_HYPERSCALER": [
        "MSFT", "GOOGL", "META", "AMZN", "ORCL",
        "IBM", "SNOW", "PLTR",
    ],

    # KI – Software & Agenten ─────────────────────────────────────────────────
    "AI_SOFTWARE": [
        "CRM", "NOW", "PANW", "ADBE", "INTU", "SNOW", "PLTR",
    ],

    # Rüstung & Verteidigung – USA ────────────────────────────────────────────
    "DEFENSE_US": [
        "LMT", "RTX", "NOC", "GD", "HII", "LDOS", "CACI",
    ],

    # Rüstung & Verteidigung – Europa (NATO-Aufrüstung) ───────────────────────
    "DEFENSE_EU": [
        "RHM.DE", "AIR.PA", "BAES.L", "MTX.DE", "SAAB.ST",
    ],

    # Halbleiter-Lieferkette ──────────────────────────────────────────────────
    "SEMICONDUCTORS": [
        "TSM", "ASML", "NVDA", "AMD", "AVGO", "QCOM", "TXN",
        "AMAT", "LRCX", "KLAC", "MU", "MRVL", "ARM",
    ],

    # Öl & Gas ────────────────────────────────────────────────────────────────
    "OIL_GAS": [
        "XOM", "CVX", "COP", "OXY", "PSX", "VLO",
        "SLB", "HAL", "BKR",
        "TTE.PA", "SHEL.L",
    ],

    # GLP-1 / Adipositas-Medikamente ──────────────────────────────────────────
    "GLP1_OBESITY": [
        "LLY", "NVO", "AMGN", "ABBV", "PFE", "VKTX",
    ],

    # Payments & Fintech ──────────────────────────────────────────────────────
    "PAYMENTS_FINTECH": [
        "V", "MA", "AXP", "PYPL", "SQ", "SOFI", "NU", "HOOD",
    ],

    # Krypto-Proxy ────────────────────────────────────────────────────────────
    "CRYPTO_PROXY": [
        "COIN", "MSTR", "RIOT", "MARA", "CLSK",
    ],

    # Rechenzentren & Strom (KI-Infrastruktur) ────────────────────────────────
    "DATA_CENTER_POWER": [
        "EQIX", "DLR", "AMT",
        "NRG", "CEG", "VST", "OKLO",
    ],

    # Europäische Industrie & Tech ────────────────────────────────────────────
    "EU_INDUSTRIAL": [
        "SAP.DE", "SIE.DE", "ALV.DE", "BMW.DE", "MBG.DE",
        "IFX.DE", "ENGI.PA", "RWE.DE",
    ],

    # Safe-Haven & Edelmetalle ────────────────────────────────────────────────
    "SAFE_HAVEN": [
        "GLD", "SLV", "GDX", "NEM", "GOLD", "AEM", "WPM",
    ],

    # E-Commerce & Konsum ─────────────────────────────────────────────────────
    "ECOMMERCE_CONSUMER": [
        "AMZN", "SHOP", "MELI", "WMT", "COST", "HD", "NFLX",
    ],

    # Erneuerbare Energie ─────────────────────────────────────────────────────
    "CLEAN_ENERGY": [
        "NEE", "ENPH", "FSLR", "BEP",
        "NESTE.HE", "RWE.DE", "ORSTED.CO",
    ],

    # Enterprise Software ─────────────────────────────────────────────────────
    "ENTERPRISE_SOFTWARE": [
        "SAP.DE", "CRM", "NOW", "ORCL", "MSFT", "INTU", "WDAY",
    ],

    # Gesundheit & Biotechnologie ─────────────────────────────────────────────
    "BIOTECH_HEALTH": [
        "LLY", "NVO", "MRNA", "BNTX", "REGN", "VRTX",
        "ABBV", "JNJ", "TMO", "ABT",
    ],
}

# Reverse-Index: ticker → themes (wird beim Import gebaut)
_TICKER_TO_THEMES: Dict[str, List[str]] = {}
for _theme, _tickers in _THEMES.items():
    for _t in _tickers:
        _TICKER_TO_THEMES.setdefault(_t, []).append(_theme)


# ── Hauptklasse ───────────────────────────────────────────────────────────────

class StockRelations:

    def __init__(self, path: str = _GRAPH_FILE):
        self._path  = path
        self._graph: Dict[str, List[Dict]] = self._load()

    # ── Schreiben ─────────────────────────────────────────────────────────────

    def add_relation(self, from_ticker: str, related: List[str], reason: str) -> None:
        """Speichert: from_ticker → related mit Begründung (aus BUY-These)."""
        if not related:
            return
        key           = from_ticker.upper()
        related_clean = [t.upper() for t in related if t.strip()]
        if not related_clean:
            return
        entry = {
            "related": related_clean,
            "reason":  reason[:120],
            "date":    datetime.utcnow().date().isoformat(),
        }
        if key not in self._graph:
            self._graph[key] = []
        existing_sets = {tuple(sorted(e["related"])) for e in self._graph[key]}
        if tuple(sorted(related_clean)) not in existing_sets:
            self._graph[key].insert(0, entry)
            self._graph[key] = self._graph[key][:_MAX_ENTRIES_PER_TICKER]
            self._save()

    # ── Lesen ─────────────────────────────────────────────────────────────────

    def get_related(self, ticker: str, limit: int = 6) -> List[str]:
        """
        Verwandte Ticker: dynamisch gelernte zuerst, dann Themen-Cluster.
        Der Ticker selbst wird nie zurückgegeben.
        """
        upper  = ticker.upper()
        seen   = {upper}
        result: List[str] = []

        # 1. Dynamisch gelernte Verbindungen aus echten Bot-Signalen
        for entry in self._graph.get(upper, []):
            for t in entry["related"]:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
                    if len(result) >= limit:
                        return result

        # 2. Statische Themen-Cluster als Fallback
        for theme in _TICKER_TO_THEMES.get(upper, []):
            for t in _THEMES[theme]:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
                    if len(result) >= limit:
                        return result

        return result

    def get_themes(self, ticker: str) -> List[str]:
        """Gibt alle Themen zurück, zu denen dieser Ticker gehört."""
        return _TICKER_TO_THEMES.get(ticker.upper(), [])

    def get_by_theme(self, theme: str) -> List[str]:
        """Alle Ticker eines bestimmten Themas."""
        return list(_THEMES.get(theme, []))

    def all_themes(self) -> List[str]:
        """Alle verfügbaren Themen-Namen."""
        return list(_THEMES.keys())

    def get_all_connections(self) -> List[Dict]:
        """Für Dashboard: dynamisch gelernte Verbindungen als flache Liste."""
        rows = []
        for from_t, entries in self._graph.items():
            for e in entries[:2]:
                rows.append({
                    "Von":            from_t,
                    "Verbunden mit":  ", ".join(e["related"]),
                    "These":          e["reason"],
                    "Datum":          e["date"],
                })
        return rows

    def stats(self) -> Dict:
        dyn_conn    = sum(len(e["related"]) for v in self._graph.values() for e in v)
        static_conn = sum(len(t) * (len(t) - 1) for t in _THEMES.values())
        return {
            "themes":              len(_THEMES),
            "tickers_in_themes":   len(_TICKER_TO_THEMES),
            "dynamic_sources":     len(self._graph),
            "dynamic_connections": dyn_conn,
            "static_connections":  static_conn,
        }

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self) -> None:
        dirpath = os.path.dirname(self._path) or "."
        os.makedirs(dirpath, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._graph, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            log.warning("StockRelations: Speichern fehlgeschlagen: %s", e)
