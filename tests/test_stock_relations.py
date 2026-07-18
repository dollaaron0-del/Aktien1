"""
Tests für analyzers/stock_relations.py – sichern die Themen-/Netzwerk-Daten
und verhindern den Drift, der das Modul über die Zeit immer wieder zerlegt hat:
veraltete Kopien der Themen-Cluster und Cross-Listing-Maps in anderen Dateien.

Kernidee: analyzers/stock_relations ist die EINZIGE Quelle für Themen-Cluster,
Cross-Listings und Themen-Metadaten. Diese Tests scheitern, sobald
  • ein Thema ohne Label/Farbe hinzugefügt wird,
  • get_related dieselbe Firma doppelt liefert, oder
  • das Dashboard wieder eine eigene Kopie der Cluster/Maps anlegt.
"""
import os
import re

from analyzers.stock_relations import (
    THEMES,
    THEME_LABELS_DE,
    THEME_COLORS,
    CROSS_LISTINGS,
    StockRelations,
    canonical,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ── Daten-Integrität der Themen-Cluster ──────────────────────────────────────

def test_themes_nonempty_and_clean():
    assert THEMES, "THEMES darf nicht leer sein"
    for theme, tickers in THEMES.items():
        assert tickers, f"Thema {theme} hat keine Ticker"
        for t in tickers:
            assert isinstance(t, str) and t.strip(), f"Leerer Ticker in {theme}"
            assert t == t.upper(), f"Ticker {t} in {theme} ist nicht uppercase"


def test_no_duplicate_ticker_within_a_theme():
    for theme, tickers in THEMES.items():
        assert len(tickers) == len(set(tickers)), f"Doppelter Ticker in {theme}"


def test_no_cross_listing_duplicates_within_a_theme():
    # Kein Thema darf zwei Listings derselben Firma enthalten (z. B. SAP + SAP.DE),
    # sonst würde der Runner dieselbe Firma doppelt analysieren.
    for theme, tickers in THEMES.items():
        canon = [canonical(t) for t in tickers]
        dups = {c for c in canon if canon.count(c) > 1}
        assert not dups, f"Cross-Listing-Duplikat in {theme}: {dups}"


# ── Themen-Metadaten vollständig (verhindert stillen Dashboard-Drift) ─────────

def test_every_theme_has_label_and_color():
    missing_label = set(THEMES) - set(THEME_LABELS_DE)
    missing_color = set(THEMES) - set(THEME_COLORS)
    assert not missing_label, f"Themen ohne deutsches Label: {missing_label}"
    assert not missing_color, f"Themen ohne Farbe: {missing_color}"


def test_no_orphan_metadata():
    # Label/Farbe ohne zugehöriges Thema deutet auf ein umbenanntes/gelöschtes Thema.
    assert not (set(THEME_LABELS_DE) - set(THEMES)), "Verwaiste Labels"
    assert not (set(THEME_COLORS) - set(THEMES)), "Verwaiste Farben"


def test_theme_colors_are_valid_hex():
    for theme, color in THEME_COLORS.items():
        assert _HEX_RE.match(color), f"Ungültige Farbe für {theme}: {color}"


# ── Cross-Listing-Kanonisierung ──────────────────────────────────────────────

def test_canonical_is_idempotent():
    for listing, canon in CROSS_LISTINGS.items():
        assert canonical(listing) == canon
        # Ein kanonischer Ticker ist selbst kein Zweit-Listing (kein Map-Zyklus).
        assert canonical(canon) == canon, f"Map-Zyklus bei {canon}"


def test_canonical_passthrough_for_unknown():
    assert canonical("AAPL") == "AAPL"
    assert canonical("aapl") == "AAPL"


# ── get_related Verhalten ────────────────────────────────────────────────────

def test_get_related_excludes_self_and_cross_listing():
    sr = StockRelations()
    for ticker in ("SAP", "SHEL", "NVDA", "TTE.PA"):
        related = sr.get_related(ticker)
        canon_self = canonical(ticker)
        canons = [canonical(t) for t in related]
        assert canon_self not in canons, f"{ticker} liefert sich selbst zurück"
        assert len(canons) == len(set(canons)), f"{ticker}: Cross-Listing-Duplikat"


def test_get_related_respects_limit():
    sr = StockRelations()
    assert len(sr.get_related("NVDA", limit=3)) <= 3


def test_get_related_ranks_shared_themes_first():
    # Reine statische Sortierung prüfen: leerer dynamischer Graph (gelernte
    # Relationen kommen by design zuerst und würden das Ranking überlagern).
    # NVDA teilt mit AMD/ARM zwei Themen (AI_CHIPS + SEMICONDUCTORS), mit anderen
    # nur eines – Mehrfach-Überlapp muss vorne stehen.
    sr = StockRelations(path=os.path.join(_PROJECT_ROOT, "data", "__does_not_exist__.json"))
    assert not sr._graph, "Testvoraussetzung: leerer dynamischer Graph"
    own = set(sr.get_themes("NVDA"))

    def shared(t):
        return len(own & set(sr.get_themes(t)))

    related = sr.get_related("NVDA", limit=8)
    scores = [shared(t) for t in related]
    assert scores == sorted(scores, reverse=True), \
        f"Ranking nicht nach gemeinsamen Themen sortiert: {list(zip(related, scores))}"


# ── Anti-Drift-Wächter: Dashboard darf keine eigenen Kopien definieren ────────

def test_dashboard_imports_central_and_has_no_local_copies():
    # Ganzes dashboard/-Paket scannen, nicht nur app.py. Seit dem Tab-Umbau
    # (18.7.2026, Netzwerk-Tab geloescht) nutzt das Dashboard THEMES/
    # CROSS_LISTINGS/Farben nicht mehr aktiv — die frühere "muss importieren"-
    # Pflicht ist damit gegenstandslos. Der Anti-Drift-Kern bleibt: WENN
    # kuenftig wieder Beziehungs-Daten im Dashboard auftauchen, duerfen sie
    # nur als Import aus analyzers.stock_relations kommen, nie als lokale
    # Literal-Kopie (genau die drei Kopien, die frueher gedriftet sind).
    dash_dir = os.path.join(_PROJECT_ROOT, "dashboard")
    src_by_file = {}
    for root, _dirs, files in os.walk(dash_dir):
        for fn in files:
            if fn.endswith(".py"):
                fp = os.path.join(root, fn)
                with open(fp, encoding="utf-8") as f:
                    src_by_file[fp] = f.read()

    # Cluster/Maps/Farben duerfen NICHT als Literale hartkodiert werden.
    # Präzise Marker für genau die drei Kopien, die früher gedriftet sind:
    forbidden = {
        'Themen-Cluster (ticker-listen)':  r'"AI_CHIPS"\s*:\s*\[',
        'Cross-Listing-Map':               r'"SHEL\.L"\s*:\s*"',
        'Themen-Farben':                   r'"AI_CHIPS"\s*:\s*"#',
    }
    for fp, src in src_by_file.items():
        for what, pat in forbidden.items():
            assert not re.search(pat, src), (
                f"{os.path.relpath(fp, _PROJECT_ROOT)} hartkodiert wieder eine "
                f"lokale Kopie ({what}). Stattdessen aus analyzers.stock_relations importieren."
            )
