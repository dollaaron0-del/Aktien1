"""
Stock Relations Graph – thematische Verbindungen zwischen Aktien.

Kombiniert:
  • Statische Themen-Cluster (kuratiert, Stand 2026)
  • Dynamisch gelernte Beziehungen aus echten Bot-BUY-Signalen

Wenn Aktie A ein BUY-Signal hat, sucht der Runner automatisch verwandte
Kandidaten aus demselben Thema und ergänzt die Analyseschlange.

Die Themen-Cluster (``THEMES``) sind die zentrale Single Source of Truth –
auch das Dashboard importiert sie von hier, statt eine eigene Kopie zu pflegen.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
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
        "NVDA", "AMD", "AVGO", "ARM", "INTC", "TSM", "ASML",
        "AMAT", "LRCX", "KLAC", "MU", "MRVL", "QCOM",
        "SMCI", "DELL", "ANET", "ASM.AS", "AIXA.DE",
    ],

    # KI – Hyperscaler & Cloud ────────────────────────────────────────────────
    "AI_HYPERSCALER": [
        "MSFT", "GOOGL", "META", "AMZN", "ORCL",
        "IBM", "SNOW", "PLTR", "BIDU", "VNET",
    ],

    # KI – Software & Agenten ─────────────────────────────────────────────────
    "AI_SOFTWARE": [
        "CRM", "NOW", "PANW", "ADBE", "INTU", "SNOW", "PLTR",
        "CRWD", "FTNT", "ZS", "DDOG", "NET", "S", "OKTA",
        "MDB", "HUBS", "DBX",
    ],

    # Cybersecurity ───────────────────────────────────────────────────────────
    "CYBERSECURITY": [
        "PANW", "CRWD", "ZS", "FTNT", "S", "OKTA", "NET",
        "CYBR", "TENB", "RBRK", "QLYS", "VRNS",
    ],

    # Rüstung & Verteidigung – USA ────────────────────────────────────────────
    "DEFENSE_US": [
        "LMT", "RTX", "NOC", "GD", "HII", "LDOS", "CACI",
        "BA", "HEI", "LHX", "AXON",
    ],

    # Rüstung & Verteidigung – Europa (NATO-Aufrüstung) ───────────────────────
    "DEFENSE_EU": [
        "RHM.DE", "AIR.PA", "BAES.L", "MTX.DE", "SAAB.ST",
        "HO.PA", "LDO.MI",
    ],

    # Halbleiter-Lieferkette ──────────────────────────────────────────────────
    "SEMICONDUCTORS": [
        "TSM", "ASML", "NVDA", "AMD", "AVGO", "QCOM", "TXN",
        "AMAT", "LRCX", "KLAC", "MU", "MRVL", "ARM",
        "ASM.AS", "WOLF", "CDNS", "SNPS", "ON",
    ],

    # Quantencomputing ────────────────────────────────────────────────────────
    "QUANTUM_COMPUTING": [
        "IONQ", "RGTI", "QBTS", "QUBT", "ARQQ",
        "IBM", "GOOGL", "HON",
    ],

    # Robotik & Humanoide / Automatisierung ───────────────────────────────────
    "ROBOTICS_AUTOMATION": [
        "TSLA", "NVDA", "ISRG", "ROK", "TER", "ZBRA",
        "PATH", "SYM", "ABBN.SW", "KUKA.DE",
    ],

    # Weltraum & Satelliten ───────────────────────────────────────────────────
    "SPACE_ECONOMY": [
        "RKLB", "LUNR", "ASTS", "PL", "RDW", "BA", "LMT", "NOC",
    ],

    # Öl & Gas ────────────────────────────────────────────────────────────────
    "OIL_GAS": [
        "XOM", "CVX", "COP", "OXY", "PSX", "VLO",
        "SLB", "HAL", "BKR",
        "TTE.PA", "SHEL", "BP",
    ],

    # Nuklear & Uran ──────────────────────────────────────────────────────────
    "NUCLEAR_URANIUM": [
        "CCJ", "LEU", "UEC", "OKLO", "SMR", "NNE",
        "DNN", "UUUU", "URA", "CEG", "VST",
    ],

    # GLP-1 / Adipositas-Medikamente ──────────────────────────────────────────
    "GLP1_OBESITY": [
        "LLY", "NVO", "AMGN", "ABBV", "PFE", "VKTX", "MED",
    ],

    # Payments & Fintech ──────────────────────────────────────────────────────
    "PAYMENTS_FINTECH": [
        "V", "MA", "AXP", "PYPL", "SQ", "SOFI", "NU", "HOOD",
    ],

    # Krypto-Proxy ────────────────────────────────────────────────────────────
    "CRYPTO_PROXY": [
        "COIN", "MSTR", "RIOT", "MARA", "CLSK", "HUT", "BMNR",
    ],

    # Banken & Finanzwerte ────────────────────────────────────────────────────
    "FINANCIALS": [
        "GS", "JPM", "MS", "BAC", "C", "WFC", "BLK", "SCHW",
        "BRK-B", "BNP.PA",
    ],

    # Rechenzentren & Strom (KI-Infrastruktur) ────────────────────────────────
    "DATA_CENTER_POWER": [
        "EQIX", "DLR", "AMT",
        "NRG", "CEG", "VST", "OKLO", "VRT", "ETN", "GEV",
    ],

    # Europäische Industrie & Tech ────────────────────────────────────────────
    "EU_INDUSTRIAL": [
        "SAP.DE", "SIE.DE", "ALV.DE", "BMW.DE", "MBG.DE",
        "IFX.DE", "ENGI.PA", "RWE.DE", "DSV.CO", "AIR.PA", "ADS.DE",
    ],

    # Industrie & Logistik (USA) ──────────────────────────────────────────────
    "INDUSTRIALS": [
        "CAT", "HON", "GE", "DE", "UPS", "FDX", "EMR",
        "ETN", "MMM", "ITW", "PH", "XPO", "GXO",
    ],

    # Immobilien (REITs) ──────────────────────────────────────────────────────
    "REAL_ESTATE": [
        "AVB", "ARE", "O", "VTR", "PLD", "SPG", "EQR", "PSA",
    ],

    # Bergbau & Metalle ───────────────────────────────────────────────────────
    "MINING_METALS": [
        "BHP", "RIO", "FCX", "VALE", "AA", "SCCO",
        "NEM", "GOLD", "AEM", "WPM",
    ],

    # Safe-Haven & Edelmetalle ────────────────────────────────────────────────
    "SAFE_HAVEN": [
        "GLD", "SLV", "GDX", "NEM", "GOLD", "AEM", "WPM",
        "SPY", "QQQ",
    ],

    # E-Commerce & Konsum ─────────────────────────────────────────────────────
    "ECOMMERCE_CONSUMER": [
        "AMZN", "SHOP", "MELI", "WMT", "COST", "HD", "NFLX",
        "EBAY", "ETSY", "BABA", "JD", "SBUX", "MC.PA",
    ],

    # EV & Mobilität ──────────────────────────────────────────────────────────
    "EV_AUTO": [
        "TSLA", "RIVN", "LCID", "GM", "F", "STLA",
        "NIO", "XPEV", "LI", "BYDDY",
    ],

    # Erneuerbare Energie ─────────────────────────────────────────────────────
    "CLEAN_ENERGY": [
        "NEE", "ENPH", "FSLR", "BEP", "SEDG", "RUN", "PLUG", "BE",
        "NESTE.HE", "RWE.DE", "ORSTED.CO",
    ],

    # Enterprise Software ─────────────────────────────────────────────────────
    "ENTERPRISE_SOFTWARE": [
        "SAP", "CRM", "NOW", "ORCL", "MSFT", "INTU", "WDAY",
    ],

    # Gesundheit & Biotechnologie ─────────────────────────────────────────────
    "BIOTECH_HEALTH": [
        "LLY", "NVO", "MRNA", "BNTX", "REGN", "VRTX",
        "ABBV", "JNJ", "TMO", "ABT", "GILD", "MRK", "BMY",
        "AZN", "AZN.L", "ALNY",
    ],
}

# Öffentlicher Alias – zentrale Single Source of Truth für Themen-Cluster.
# Dashboard & Analyzer importieren THEMES von hier, statt eigene Kopien zu pflegen.
THEMES = _THEMES

# ── Cross-Listing-Kanonisierung ──────────────────────────────────────────────
# Gleiche Firma an verschiedenen Börsenplätzen → ein kanonischer Ticker.
# Verhindert, dass Runner & Dashboard dieselbe Firma doppelt führen/analysieren.
# Schlüssel = Zweit-Listing, Wert = kanonischer (i. d. R. US-)Ticker.
CROSS_LISTINGS: Dict[str, str] = {
    "ASML.AS":   "ASML",
    "ASMI.AS":   "ASM.AS",   # ASM International – Amsterdam-Varianten
    "NOVO-B.CO": "NVO",
    "BAES.L":    "BA",        # BAE Systems – UK vs. US
    "SHEL.L":    "SHEL",
    "TTE.PA":    "TTE",
    "SAP.DE":    "SAP",       # SAP US-ADR
    "LVMH.PA":   "MC.PA",     # LVMH
    "GOOG":      "GOOGL",     # Alphabet – C-Aktie auf A-Aktie (stimmrechtslos)
}


def canonical(ticker: str) -> str:
    """Kanonischer Ticker einer Firma (Zweit-Listings → Haupt-Listing)."""
    return CROSS_LISTINGS.get(ticker.upper(), ticker.upper())


# ── Themen-Anzeige-Metadaten ─────────────────────────────────────────────────
# Deutsches Label + Farbe je Thema – zentral, damit das Dashboard sie importiert
# statt eigener Kopien. Jedes Thema in THEMES MUSS hier einen Eintrag haben;
# tests/test_stock_relations.py erzwingt das (verhindert stillen Drift bei neuen
# Themen). Pflege ein neues Thema also immer an diesen drei Stellen: THEMES,
# THEME_LABELS_DE, THEME_COLORS.
THEME_LABELS_DE: Dict[str, str] = {
    "AI_CHIPS":            "KI-Chips",
    "AI_HYPERSCALER":      "Hyperscaler",
    "AI_SOFTWARE":         "KI-Software",
    "CYBERSECURITY":       "Cybersecurity",
    "ENTERPRISE_SOFTWARE": "Enterprise SW",
    "SEMICONDUCTORS":      "Halbleiter",
    "QUANTUM_COMPUTING":   "Quantum",
    "ROBOTICS_AUTOMATION": "Robotik",
    "SPACE_ECONOMY":       "Weltraum",
    "DEFENSE_US":          "Rüstung USA",
    "DEFENSE_EU":          "Rüstung EU",
    "OIL_GAS":             "Öl & Gas",
    "NUCLEAR_URANIUM":     "Nuklear & Uran",
    "CLEAN_ENERGY":        "Erneuerbare",
    "GLP1_OBESITY":        "GLP-1",
    "BIOTECH_HEALTH":      "Biotech",
    "PAYMENTS_FINTECH":    "Payments",
    "CRYPTO_PROXY":        "Krypto",
    "FINANCIALS":          "Finanzen",
    "DATA_CENTER_POWER":   "Rechenzentren",
    "EU_INDUSTRIAL":       "EU Industrie",
    "INDUSTRIALS":         "Industrie",
    "REAL_ESTATE":         "Immobilien",
    "MINING_METALS":       "Rohstoffe",
    "SAFE_HAVEN":          "Safe-Haven",
    "ECOMMERCE_CONSUMER":  "E-Commerce",
    "EV_AUTO":             "EV & Auto",
}

THEME_COLORS: Dict[str, str] = {
    "AI_CHIPS":            "#7B68EE",
    "AI_HYPERSCALER":      "#3498DB",
    "AI_SOFTWARE":         "#5DADE2",
    "CYBERSECURITY":       "#546E7A",
    "ENTERPRISE_SOFTWARE": "#1ABC9C",
    "SEMICONDUCTORS":      "#9B59B6",
    "QUANTUM_COMPUTING":   "#00ACC1",
    "ROBOTICS_AUTOMATION": "#FF7043",
    "SPACE_ECONOMY":       "#5C6BC0",
    "DEFENSE_US":          "#E74C3C",
    "DEFENSE_EU":          "#C0392B",
    "OIL_GAS":             "#F39C12",
    "NUCLEAR_URANIUM":     "#C0CA33",
    "CLEAN_ENERGY":        "#2ECC71",
    "GLP1_OBESITY":        "#FF69B4",
    "BIOTECH_HEALTH":      "#E91E63",
    "PAYMENTS_FINTECH":    "#00BCD4",
    "CRYPTO_PROXY":        "#FFD700",
    "FINANCIALS":          "#2980B9",
    "DATA_CENTER_POWER":   "#4CAF50",
    "EU_INDUSTRIAL":       "#E67E22",
    "INDUSTRIALS":         "#795548",
    "REAL_ESTATE":         "#AB47BC",
    "MINING_METALS":       "#8D6E63",
    "SAFE_HAVEN":          "#BDC3C7",
    "ECOMMERCE_CONSUMER":  "#26C6DA",
    "EV_AUTO":             "#43A047",
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
            "date":    datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat(),
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

        Statische Kandidaten werden nach Anzahl gemeinsamer Themen gerankt –
        ein Ticker, der mit dem Ausgangswert mehrere Cluster teilt (z. B. NVDA
        in AI_CHIPS *und* SEMICONDUCTORS), gilt als stärker verwandt und steht
        weiter vorne. Der Ticker selbst wird nie zurückgegeben.
        """
        upper  = ticker.upper()
        # Dedup pro Firma (kanonisch): verhindert, dass z. B. SAP und SAP.DE
        # beide zurückkommen und der Runner dieselbe Firma doppelt analysiert.
        seen_canon = {canonical(upper)}
        result: List[str] = []

        def _take(t: str) -> bool:
            c = canonical(t)
            if c in seen_canon:
                return False
            seen_canon.add(c)
            result.append(t)
            return len(result) >= limit

        # 1. Dynamisch gelernte Verbindungen aus echten Bot-Signalen
        for entry in self._graph.get(upper, []):
            for t in entry["related"]:
                if _take(t):
                    return result

        # 2. Statische Themen-Cluster, gerankt nach gemeinsamen Themen
        own_themes = _TICKER_TO_THEMES.get(upper, [])
        if own_themes:
            shared: Dict[str, int] = {}
            for theme in own_themes:
                for t in _THEMES[theme]:
                    if canonical(t) not in seen_canon:
                        shared[t] = shared.get(t, 0) + 1
            # Mehr gemeinsame Themen zuerst; bei Gleichstand alphabetisch (stabil)
            for t in sorted(shared, key=lambda x: (-shared[x], x)):
                if _take(t):
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
