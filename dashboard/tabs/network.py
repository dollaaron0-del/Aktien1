"""Tab "Aktien-Netzwerk" — ausgelagert aus dashboard/app.py (Roadmap 4.4a)."""
from datetime import datetime

import pandas as pd
import streamlit as st


def render(ctx) -> None:
    _ALL_NAMES = ctx._ALL_NAMES
    st.subheader("🕸 Aktien-Netzwerk")
    st.caption("Alle vom Bot analysierten Aktien und ihre thematischen Verbindungen.")

    try:
        import math
        import plotly.graph_objects as go
        from analyzers.bot_data_bridge import BotDataBridge
        from analyzers.stock_relations import StockRelations

        _bridge      = BotDataBridge()
        _net_rel     = StockRelations()
        _all_states  = _bridge.get_all_states()

        if not _all_states:
            st.info("Noch keine Analyse-Daten. Der Bot muss mindestens einen Analyse-Zyklus abgeschlossen haben.")
        else:
            # ── Filter ──────────────────────────────────────────────────────────
            _net_col1, _net_col2, _net_col3 = st.columns([2, 2, 2])
            with _net_col1:
                _rec_filter = st.multiselect(
                    "Empfehlung filtern",
                    ["BUY", "HOLD", "SELL", "SKIP"],
                    default=["BUY", "HOLD", "SELL", "SKIP"],
                )
            with _net_col2:
                _show_isolated = st.checkbox("Ticker ohne Verbindungen anzeigen", value=True)
            with _net_col3:
                _show_edges = st.checkbox("Verbindungslinien anzeigen", value=False)

            # ── Cross-Listing-Deduplizierung: gleiche Firma, verschiedene Börsenplätze ──
            # Zentrale Map aus analyzers/stock_relations (Single Source of Truth).
            from analyzers.stock_relations import CROSS_LISTINGS as _CANONICAL

            # ── Node-Daten aus BotDataBridge (einheitliche Quelle) ──────────────
            _rec_color = {"BUY": "#00e676", "HOLD": "#ffd740", "SELL": "#f44336", "SKIP": "#888888"}
            _nodes: dict = {}
            # Erst alle States sammeln und nach kanonischem Ticker mergen
            # (bestes Signal gewinnt: BUY > HOLD > SELL > SKIP)
            _REC_RANK = {"BUY": 4, "HOLD": 3, "SELL": 2, "SKIP": 1, "UNKNOWN": 0}
            _merged_states: dict = {}  # canon_ticker → _ts
            for _raw_t, _ts in _all_states.items():
                _canon = _CANONICAL.get(_raw_t, _raw_t)
                if _canon not in _merged_states:
                    _merged_states[_canon] = _ts
                else:
                    # Besser bewertetes Signal gewinnt
                    _cur_rank = _REC_RANK.get(_merged_states[_canon].recommendation or "UNKNOWN", 0)
                    _new_rank = _REC_RANK.get(_ts.recommendation or "UNKNOWN", 0)
                    if _new_rank > _cur_rank:
                        _merged_states[_canon] = _ts

            _nodes: dict = {}
            for ticker, _ts in _merged_states.items():
                raw_rec = _ts.recommendation or "UNKNOWN"
                # UNKNOWN / leere Empfehlungen → SKIP (kein echter Signal)
                rec = raw_rec if raw_rec in _rec_color else "SKIP"
                if rec not in _rec_filter:
                    continue
                # Sehr alte Analysen (> 7 Tage) → immer als SKIP markieren
                _stale = False
                if _ts.analyzed_at:
                    try:
                        from datetime import timezone
                        _age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(
                            _ts.analyzed_at.replace("Z", "")
                        )).days
                        _stale = _age_days > 7
                    except Exception:
                        pass
                if _stale:
                    rec = "SKIP"
                    if "SKIP" not in _rec_filter:
                        continue
                _nodes[ticker] = {
                    "rec":    rec,
                    "score":  round(_ts.score, 2),
                    "date":   _ts.analyzed_at[:10] if _ts.analyzed_at else "",
                    "color":  _rec_color[rec],
                    "source": _ts.rec_source,
                    "stale":  _stale,
                }

            # ── Kanten: dynamisch + statische Themen-Cluster ────────────────────
            _edges: list = []
            _edge_labels: list = []
            _edge_seen: set = set()
            _get_related = lambda t: _net_rel.get_related(t)[:6]

            # Themen-Mapping aus der zentralen Single Source of Truth
            # (analyzers/stock_relations.THEMES) – kein dupliziertes Cluster-Dict
            # mehr, damit Bot-Netz und Dashboard nicht auseinanderdriften.
            from analyzers.stock_relations import THEMES as _CENTRAL_THEMES
            _DASH_THEMES = {_th: list(_tks) for _th, _tks in _CENTRAL_THEMES.items()}
            # Dashboard-only Ergänzungen (rein visuelle Cluster, die der Bot
            # nicht zur Kandidaten-Expansion braucht):
            _DASH_THEMES.setdefault("ECOMMERCE_CONSUMER", []).extend(
                ["NKE","LULU","TJX","ULTA","MCD","EL","KMB","PG","CL",
                 "DIS","KO","SNAP","MO","MDLZ"]
            )
            _DASH_THEMES.setdefault("BIOTECH_HEALTH", []).extend(
                ["BIIB","ILMN","MDT","ELV","MCK"]
            )
            _DASH_T2T: dict = {}
            for _th, _tks in _DASH_THEMES.items():
                for _tk in _tks:
                    _DASH_T2T.setdefault(_tk, []).append(_th)
            _get_themes = lambda t: _DASH_T2T.get(t.upper(), [])
            # Catch-all: unklassifizierte Ticker -> Sonstige
            for _ct in list(_nodes.keys()):
                if not _DASH_T2T.get(_ct.upper(), []):
                    _DASH_T2T[_ct.upper()] = ["SONSTIGE"]
            for from_t in list(_nodes.keys()):
                for to_t in _get_related(from_t):
                    if to_t not in _nodes:
                        continue
                    key = tuple(sorted([from_t, to_t]))
                    if key in _edge_seen:
                        continue
                    _edge_seen.add(key)
                    _themes = _get_themes(from_t)
                    _lbl = _themes[0].replace("_", " ") if _themes else "Verwandt"
                    _edges.append((from_t, to_t))
                    _edge_labels.append(_lbl[:60])

            # Isolierte Knoten herausfiltern wenn gewünscht
            if not _show_isolated:
                _connected = {t for edge in _edges for t in edge}
                _nodes = {t: v for t, v in _nodes.items() if t in _connected}

            if not _nodes:
                st.info("Keine Daten für die gewählten Filter.")
            else:
                # ── Themen-basiertes Layout ──────────────────────────────────────
                _ticker_list = list(_nodes.keys())
                _pos: dict = {}

                # Jedes Thema bekommt einen festen Sektor auf dem Canvas
                _theme_centers = {
                    # ── Rüstung & Industrie (enger Block, oben links) ─────────
                    "DEFENSE_EU":         (-0.85,  0.60),
                    "DEFENSE_US":         (-0.58,  0.72),
                    "INDUSTRIALS":        (-0.60,  0.38),
                    "EU_INDUSTRIAL":      (-0.85,  0.26),
                    # ── Energie & Rohstoffe (enger Block, unten links) ────────
                    "OIL_GAS":            (-0.68, -0.42),
                    "SAFE_HAVEN":         (-0.90, -0.22),
                    "MINING_METALS":      (-0.82, -0.55),
                    "CLEAN_ENERGY":       (-0.46, -0.54),
                    # ── Gesundheit (oben Mitte, eng) ──────────────────────────
                    "BIOTECH_HEALTH":     (-0.15,  0.68),
                    "GLP1_OBESITY":       ( 0.10,  0.85),
                    # ── Finanzen (enger Block, Mitte) ─────────────────────────
                    "REAL_ESTATE":        (-0.08, -0.28),
                    "FINANCIALS":         (-0.05,  0.05),
                    "PAYMENTS_FINTECH":   ( 0.22,  0.05),
                    "CRYPTO_PROXY":       ( 0.72, -0.10),
                    # ── KI / Tech (enger Block, oben rechts) ─────────────────
                    "AI_CHIPS":           ( 0.60,  0.75),
                    "AI_HYPERSCALER":     ( 0.40,  0.55),
                    "DATA_CENTER_POWER":  ( 0.72,  0.60),
                    "SEMICONDUCTORS":     ( 0.68,  0.40),
                    # ── Software (rechts Mitte) ───────────────────────────────
                    "AI_SOFTWARE":        ( 0.60,  0.18),
                    "ENTERPRISE_SOFTWARE":( 0.48, -0.18),
                    # ── Konsum / E-Auto (enger Block, unten rechts) ───────────
                    "ECOMMERCE_CONSUMER": ( 0.40, -0.60),
                    "EV_AUTO":            ( 0.65, -0.50),
                }
                # Auto-Platzierung: zentral neu hinzugefügte Themen ohne handgesetztes
                # Zentrum landen deterministisch auf einem Außenring – so verschwindet
                # nie wieder ein Thema still, nur weil die Layout-Map nicht gepflegt wurde.
                import math as _math_ac
                _missing_themes = [th for th in _CENTRAL_THEMES if th not in _theme_centers]
                # Oberer Bogen (27°–153°): hält den unteren Rand für die
                # "Sonstige"-Gruppe frei und vermeidet Kollisionen mit dem Band.
                for _i, _th in enumerate(sorted(_missing_themes)):
                    _frac = _i / max(len(_missing_themes) - 1, 1)
                    _ang  = _math_ac.pi * (0.15 + 0.70 * _frac)
                    _theme_centers[_th] = (1.12 * _math_ac.cos(_ang), 1.12 * _math_ac.sin(_ang))
                # Primär-Thema: erster Eintrag aus get_themes() → bestimmt den Cluster
                # Mehrfachthemen landen im ersten (wichtigsten) Cluster, nicht im Durchschnitt
                _theme_to_tickers: dict = {}
                _no_theme: list = []
                for t in _ticker_list:
                    _primary = next(
                        (th for th in _get_themes(t) if th in _theme_centers), None
                    )
                    if _primary:
                        _theme_to_tickers.setdefault(_primary, []).append(t)
                    else:
                        _no_theme.append(t)

                # Knoten eines Clusters kreisförmig um den Mittelpunkt verteilen
                for _theme, _members in _theme_to_tickers.items():
                    _cx, _cy = _theme_centers[_theme]
                    _n = len(_members)
                    # Radius wächst mit Anzahl der Knoten (min 0.07, max 0.13)
                    _r = min(0.07 + 0.009 * _n, 0.13)
                    for _i, t in enumerate(sorted(_members)):  # sortiert = deterministisch
                        _angle = 2 * math.pi * _i / _n - math.pi / 2
                        _pos[t] = (_cx + _r * math.cos(_angle), _cy + _r * math.sin(_angle))

                # Ticker ohne Sektor: kompakte, beschriftete "Sonstige"-Gruppe als
                # Raster-Band am unteren Rand – zusammengefasst statt rund um die
                # Karte verstreut. Fängt auch künftige (z. B. ausländische) Ticker
                # automatisch auf, ohne dass die Mapping-Tabelle gepflegt werden muss.
                _sonstige_set = set(_no_theme)
                _n_nt = len(_no_theme)
                if _n_nt:
                    _sb_cols = max(1, min(16, math.ceil(math.sqrt(_n_nt * 3.5))))
                    _sb_rows = math.ceil(_n_nt / _sb_cols)
                    _sb_x0, _sb_x1 = -1.16,  1.16     # Bandbreite
                    _sb_yt, _sb_yb = -0.82, -1.07     # oben → unten
                    for _i, t in enumerate(sorted(_no_theme)):
                        _row, _col = divmod(_i, _sb_cols)
                        # Anzahl in dieser Reihe (letzte Reihe ggf. unvoll) → zentriert
                        _in_row = _sb_cols if _row < _sb_rows - 1 else (_n_nt - _sb_cols * (_sb_rows - 1))
                        _fx = (_col + 0.5) / _in_row
                        _fy = _row / max(_sb_rows - 1, 1)
                        _pos[t] = (_sb_x0 + _fx * (_sb_x1 - _sb_x0),
                                   _sb_yt + _fy * (_sb_yb - _sb_yt))

                _n_themed = len(_ticker_list) - _n_nt
                st.caption(
                    f"Sektoren erkannt: {len(_theme_to_tickers)} | "
                    f"Ticker mit Sektor: {_n_themed} | "
                    f"Sonstige (ohne Sektor): {_n_nt}"
                )

                # ── Theme-Farben & deutsche Labels ─────────────────────────
                # Zentral aus analyzers/stock_relations (Single Source of Truth);
                # keine lokalen Kopien mehr → kein Drift bei neuen Themen.
                from analyzers.stock_relations import (
                    THEME_COLORS as _theme_colors,
                    THEME_LABELS_DE as _theme_labels_de,
                )
                # "Sonstige" ist ein reines Anzeige-Cluster (kein Bot-Thema) –
                # Farbe & Label nur hier, damit Rand und Band-Label stimmen.
                _theme_colors    = {**_theme_colors,    "SONSTIGE": "#7c8aa0"}
                _theme_labels_de = {**_theme_labels_de, "SONSTIGE": "Sonstige"}

                # ── Hintergrund-Zonen: Polygon-Kreise (fill="toself" funktioniert immer) ──
                import math as _math
                _N_PTS   = 48                    # Punkte pro Kreis-Polygon
                _angles  = [2 * _math.pi * i / _N_PTS for i in range(_N_PTS + 1)]

                # ── Sektor-zu-Sektor Verbindungen (wirtschaftliche Abhängigkeiten) ──
                _sector_links = [
                    # Rüstung ↔ Industrie
                    ("DEFENSE_US",  "INDUSTRIALS"),
                    ("DEFENSE_EU",  "EU_INDUSTRIAL"),
                    ("INDUSTRIALS", "EU_INDUSTRIAL"),
                    ("DEFENSE_US",  "DEFENSE_EU"),
                    # Tech-Ökosystem
                    ("AI_CHIPS",       "AI_HYPERSCALER"),
                    ("AI_CHIPS",       "SEMICONDUCTORS"),
                    ("AI_CHIPS",       "DATA_CENTER_POWER"),
                    ("AI_HYPERSCALER", "AI_SOFTWARE"),
                    ("AI_HYPERSCALER", "DATA_CENTER_POWER"),
                    ("AI_SOFTWARE",    "ENTERPRISE_SOFTWARE"),
                    # Finanzen-Block
                    ("FINANCIALS",     "PAYMENTS_FINTECH"),
                    ("FINANCIALS",     "REAL_ESTATE"),
                    ("PAYMENTS_FINTECH","CRYPTO_PROXY"),
                    # Pharma
                    ("BIOTECH_HEALTH", "GLP1_OBESITY"),
                    # Energie
                    ("OIL_GAS",        "CLEAN_ENERGY"),
                    ("MINING_METALS",  "CLEAN_ENERGY"),
                    ("OIL_GAS",        "EU_INDUSTRIAL"),
                    # Konsum & Mobilität
                    ("ENTERPRISE_SOFTWARE", "ECOMMERCE_CONSUMER"),
                    ("EV_AUTO",        "ECOMMERCE_CONSUMER"),
                    ("AI_CHIPS",       "EV_AUTO"),
                    ("SEMICONDUCTORS", "EV_AUTO"),
                    ("MINING_METALS",  "EV_AUTO"),     # Batterierohstoffe
                    # Cross-Sektor
                    ("INDUSTRIALS",    "CLEAN_ENERGY"),
                    ("FINANCIALS",     "ENTERPRISE_SOFTWARE"),
                ]
                _sl_x, _sl_y = [], []
                for _t1, _t2 in _sector_links:
                    if _t1 not in _theme_centers or _t2 not in _theme_centers: continue
                    if not _theme_to_tickers.get(_t1) or not _theme_to_tickers.get(_t2): continue
                    _x1, _y1 = _theme_centers[_t1]
                    _x2, _y2 = _theme_centers[_t2]
                    _ddx, _ddy = _x2 - _x1, _y2 - _y1
                    _dd = _math.sqrt(_ddx**2 + _ddy**2) or 1e-6
                    # Kantenpunkte (Linie beginnt/endet am Kreisrand, nicht im Zentrum)
                    _rr1 = min(0.09 + 0.012 * len(_theme_to_tickers.get(_t1, [])), 0.18)
                    _rr2 = min(0.09 + 0.012 * len(_theme_to_tickers.get(_t2, [])), 0.18)
                    _sl_x += [_x1 + _rr1*_ddx/_dd, _x2 - _rr2*_ddx/_dd, None]
                    _sl_y += [_y1 + _rr1*_ddy/_dd, _y2 - _rr2*_ddy/_dd, None]

                _sector_link_trace = go.Scatter(
                    x=_sl_x, y=_sl_y,
                    mode="lines",
                    line=dict(width=1.0, color="rgba(120,140,180,0.30)"),
                    hoverinfo="none", showlegend=False,
                )

                _zone_traces  = []   # je ein Trace pro Cluster (Kreis + Label)
                _label_x2, _label_y2, _label_txt2, _label_col2 = [], [], [], []
                _label_nx2, _label_ny2 = [], []   # Richtungsvektor für xanchor/yanchor
                _label_ax2,  _label_ay2  = [], []   # Pfeilankerpunkt auf dem Kreisrand

                for _theme, (zx, zy) in _theme_centers.items():
                    _in_zone = _theme_to_tickers.get(_theme, [])
                    if not _in_zone:
                        continue
                    _n_zone = len(_in_zone)
                    _r_zone = min(0.09 + 0.012 * _n_zone, 0.18)
                    _zc = _theme_colors.get(_theme, "#666666")
                    _zl = _theme_labels_de.get(_theme, _theme)

                    # Polygon-Koordinaten des Kreises (geschlossen: erster == letzter Punkt)
                    _px = [zx + _r_zone * _math.cos(a) for a in _angles]
                    _py = [zy + _r_zone * _math.sin(a) for a in _angles]

                    # Hex → rgba für Fill und Linie
                    _r8, _g8, _b8 = int(_zc[1:3], 16), int(_zc[3:5], 16), int(_zc[5:7], 16)
                    _fill_rgba = f"rgba({_r8},{_g8},{_b8},0.18)"
                    _line_rgba = f"rgba({_r8},{_g8},{_b8},0.70)"

                    _zone_traces.append(go.Scatter(
                        x=_px, y=_py,
                        mode="lines",
                        fill="toself",
                        fillcolor=_fill_rgba,
                        line=dict(color=_line_rgba, width=1.5, dash="dot"),
                        hoverinfo="none",
                        showlegend=False,
                    ))

                    # Label-Richtung: radial nach außen, mit Overrides für problematische Cluster
                    _dir_overrides = {
                        "FINANCIALS":         (-1.0,  0.0),  # → links (war unten, deckte REAL_ESTATE)
                        "REAL_ESTATE":        ( 0.0, -1.0),
                        "INDUSTRIALS":        (-1.0,  0.0),
                        "EU_INDUSTRIAL":      (-1.0,  0.0),
                        "GLP1_OBESITY":       ( 0.0,  1.0),
                        "BIOTECH_HEALTH":     ( 0.0,  1.0),
                        "PAYMENTS_FINTECH":   ( 0.0,  1.0),
                        "AI_HYPERSCALER":     ( 0.0,  1.0),  # → oben (war oben-rechts, deckte KI-Chips)
                        "ENTERPRISE_SOFTWARE":( 1.0,  0.0),
                        # Untere Cluster: Labels seitlich, damit sie nicht ins
                        # "Sonstige"-Band am unteren Rand hineinragen.
                        "ECOMMERCE_CONSUMER": ( 1.0,  0.0),
                        "EV_AUTO":            ( 1.0,  0.0),
                        "MINING_METALS":      (-1.0,  0.0),
                        "OIL_GAS":            (-1.0,  0.0),
                        "CLEAN_ENERGY":       (-1.0,  0.0),
                    }
                    if _theme in _dir_overrides:
                        _nx, _ny = _dir_overrides[_theme]
                    else:
                        _dist = _math.sqrt(zx ** 2 + zy ** 2)
                        _nx, _ny = (zx / _dist, zy / _dist) if _dist > 0.05 else (0.0, -1.0)
                    # Label weit genug außerhalb des Kreises damit keine Knoten überdeckt werden
                    _lx = zx + (_r_zone + 0.18) * _nx
                    _ly = zy + (_r_zone + 0.18) * _ny
                    _label_x2.append(_lx)
                    _label_y2.append(_ly)
                    _label_txt2.append(_zl)
                    _label_col2.append(_zc)
                    _label_nx2.append(_nx)
                    _label_ny2.append(_ny)
                    # Ankerpunkt auf dem Kreisrand (Pfeilursprung)
                    _label_ax2.append(zx + _r_zone * _nx)
                    _label_ay2.append(zy + _r_zone * _ny)

                # Labels als Annotationen (kein Pfeil — separate Linie unten)
                _zone_annotations = []
                _conn_x, _conn_y, _conn_colors = [], [], []
                for lx, ly, lt, lc, lnx, lny, lax, lay in zip(
                    _label_x2, _label_y2, _label_txt2, _label_col2,
                    _label_nx2, _label_ny2, _label_ax2, _label_ay2,
                ):
                    _r8i = int(lc[1:3], 16)
                    _g8i = int(lc[3:5], 16)
                    _b8i = int(lc[5:7], 16)
                    _xanc = "left"   if lnx >  0.20 else ("right"  if lnx < -0.20 else "center")
                    _yanc = "bottom" if lny >  0.20 else ("top"    if lny < -0.20 else "middle")
                    _zone_annotations.append(dict(
                        x=lx, y=ly,
                        text=f"<b>{lt}</b>",
                        showarrow=False,
                        xanchor=_xanc,
                        yanchor=_yanc,
                        xref="x", yref="y",
                        font=dict(size=10, color="#ffffff"),
                        bgcolor=f"rgba({_r8i},{_g8i},{_b8i},0.85)",
                        bordercolor=lc,
                        borderwidth=1,
                        borderpad=4,
                        opacity=0.95,
                    ))
                    # Verbindungslinie Kreisrand → Label (ein Segment pro Cluster)
                    _conn_x += [lax, lx, None]
                    _conn_y += [lay, ly, None]
                    _conn_colors.append(lc)

                # ── "Sonstige"-Band: Hintergrund-Rechteck + Gruppen-Label ──
                if _sonstige_set:
                    _sc  = _theme_colors["SONSTIGE"]
                    _sr8, _sg8, _sb8 = int(_sc[1:3], 16), int(_sc[3:5], 16), int(_sc[5:7], 16)
                    _zone_traces.append(go.Scatter(
                        x=[-1.22, 1.22, 1.22, -1.22, -1.22],
                        y=[-0.78, -0.78, -1.10, -1.10, -0.78],
                        mode="lines",
                        fill="toself",
                        fillcolor=f"rgba({_sr8},{_sg8},{_sb8},0.10)",
                        line=dict(color=f"rgba({_sr8},{_sg8},{_sb8},0.55)", width=1.2, dash="dot"),
                        hoverinfo="none", showlegend=False,
                    ))
                    _zone_annotations.append(dict(
                        x=-1.20, y=-0.745,
                        text=f"<b>Sonstige · {len(_sonstige_set)}</b>",
                        showarrow=False,
                        xanchor="left", yanchor="bottom",
                        xref="x", yref="y",
                        font=dict(size=10, color="#ffffff"),
                        bgcolor=f"rgba({_sr8},{_sg8},{_sb8},0.85)",
                        bordercolor=_sc, borderwidth=1, borderpad=4, opacity=0.95,
                    ))

                # Alle Verbindungslinien als ein Trace (gleiche Farbe geht nicht pro Segment,
                # deshalb hellgrau — Label-Farbe identifiziert den Cluster bereits)
                _conn_trace = go.Scatter(
                    x=_conn_x, y=_conn_y,
                    mode="lines",
                    line=dict(width=1.2, color="rgba(180,180,180,0.45)"),
                    hoverinfo="none", showlegend=False,
                )

                # ── Kanten (nur wenn Toggle aktiv) ─────────────────────────
                _edge_x, _edge_y = [], []
                if _show_edges:
                    for (src, dst) in _edges:
                        if src in _pos and dst in _pos:
                            x0, y0 = _pos[src]
                            x1, y1 = _pos[dst]
                            _edge_x += [x0, x1, None]
                            _edge_y += [y0, y1, None]

                _edge_trace = go.Scatter(
                    x=_edge_x, y=_edge_y,
                    mode="lines",
                    line=dict(width=0.8, color="#445566"),
                    hoverinfo="none", showlegend=False,
                )

                # ── Knoten ─────────────────────────────────────────────────
                _conn_count  = {t: sum(1 for e in _edges if t in e) for t in _ticker_list}
                _node_x      = [_pos[t][0] for t in _ticker_list]
                _node_y      = [_pos[t][1] for t in _ticker_list]
                _node_colors = [_nodes[t]["color"] for t in _ticker_list]
                # Sonstige-Knoten kleiner & einheitlich → ruhiges, dichtes Band
                _node_sizes  = [
                    10 if t in _sonstige_set else 16 + 6 * _conn_count[t]
                    for t in _ticker_list
                ]

                # Knotenrand in Themenfarbe → sofort sichtbare Sektor-Zugehörigkeit
                _node_borders = [
                    _theme_colors.get((_get_themes(t) or [""])[0], "#333333")
                    for t in _ticker_list
                ]

                _node_hover = [
                    (
                        f"<b>{t}</b>  {_ALL_NAMES.get(t.upper(), '')}<br>"
                        f"Empfehlung: <b>{_nodes[t]['rec']}</b>"
                        + (" ⚠️ veraltet" if _nodes[t].get("stale") else "") +
                        f"  <i>({_nodes[t].get('source','–')})</i><br>"
                        f"Score: {_nodes[t]['score']}  |  "
                        f"Zuletzt: {_nodes[t]['date']}<br>"
                        f"Sektor: {', '.join(_get_themes(t)) or '–'}<br>"
                        f"Verbindungen: {_conn_count[t]}"
                    )
                    for t in _ticker_list
                ]

                # Label: Kürzel + 1. Wort des Firmennamens
                # (im Sonstige-Band nur das Kürzel → weniger Gedränge)
                _node_labels = []
                for t in _ticker_list:
                    if t in _sonstige_set:
                        _node_labels.append(t)
                        continue
                    _nm = _ALL_NAMES.get(t.upper(), "")
                    _short = _nm.split()[0][:9] if _nm else ""
                    _node_labels.append(f"{t} · {_short}" if _short else t)

                _node_trace = go.Scatter(
                    x=_node_x, y=_node_y,
                    mode="markers+text",
                    hoverinfo="text",
                    hovertext=_node_hover,
                    text=_node_labels,
                    textposition="top center",
                    textfont=dict(size=8, color="#cccccc"),
                    marker=dict(
                        size=_node_sizes,
                        color=_node_colors,
                        line=dict(width=2, color=_node_borders),
                    ),
                    showlegend=False,
                )

                # ── Legende ────────────────────────────────────────────────
                _legend_traces = [
                    go.Scatter(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=10, color=_rec_color[r]),
                        name=r, showlegend=True,
                    )
                    for r in ["BUY", "HOLD", "SELL", "SKIP"]
                ]

                fig = go.Figure(
                    data=_zone_traces + [_conn_trace, _edge_trace, _node_trace] + _legend_traces,
                    layout=go.Layout(
                        paper_bgcolor="#0e1117",
                        plot_bgcolor="#0e1117",
                        font=dict(color="#dddddd"),
                        xaxis=dict(showgrid=False, zeroline=False,
                                   showticklabels=False, range=[-1.30, 1.30]),
                        yaxis=dict(showgrid=False, zeroline=False,
                                   showticklabels=False, range=[-1.10, 1.15]),
                        hovermode="closest",
                        height=760,
                        margin=dict(l=10, r=10, t=20, b=10),
                        legend=dict(
                            bgcolor="#1a1a2e", bordercolor="#444",
                            borderwidth=1, font=dict(color="#dddddd"),
                        ),
                        annotations=_zone_annotations,
                    ),
                )
                st.plotly_chart(fig, width="stretch")

                # ── Kennzahlen unter der Map ─────────────────────────────────────
                _kpi1, _kpi2, _kpi3, _kpi4 = st.columns(4)
                _kpi1.metric("Analysierte Ticker", len(_nodes))
                _kpi2.metric("Verbindungen", len(_edges))
                _buy_cnt = sum(1 for v in _nodes.values() if v["rec"] == "BUY")
                _kpi3.metric("BUY-Signale", _buy_cnt)
                _most_connected = max(_ticker_list, key=lambda t: sum(1 for e in _edges if t in e), default="–")
                _kpi4.metric("Am stärksten vernetzt", _most_connected)

                # ── Verbindungstabelle ───────────────────────────────────────────
                if _edges:
                    st.divider()
                    st.subheader("Verbindungen")
                    _conn_rows = [
                        {"Von": src, "Nach": dst, "These": lbl}
                        for (src, dst), lbl in zip(_edges, _edge_labels)
                    ]
                    st.dataframe(
                        pd.DataFrame(_conn_rows),
                        width="stretch", hide_index=True
                    )

    except ImportError:
        st.warning("Plotly nicht installiert. Bitte auf dem Server ausführen: `pip install plotly networkx`")
    except Exception as _e:
        st.error(f"Netzwerk-Fehler: {_e}")
