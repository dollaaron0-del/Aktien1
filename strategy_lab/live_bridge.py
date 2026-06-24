"""
Live-Bridge (Roadmap-Punkt d) – flaggengeschützte, defensive Naht zwischen dem
read-only Meta-Allokator (strategy_lab) und dem Live-Bot.

STANDARD AUS: ohne gesetztes Flag STRATEGY_LAB_LIVE liefert alles leer/"" → der
Bot verhält sich EXAKT wie zuvor (ein Neustart ändert nichts). Eingeschaltet speist
der Allokator eine ZUSÄTZLICHE, advisorische "mechanische Konviktion" je Ticker als
Kontext in die KI-Analyse – gleiches additive Muster wie der Makro-Kontext. KEIN
Auto-Trade, keine Sizing-/Threshold-Übersteuerung: die KI bleibt der Entscheider,
sie bekommt nur einen Hinweis mehr.

Jeder Pfad ist in try/except gekapselt – ein Fehler hier darf einen Handelszyklus
NIE crashen. Die zugrundeliegende Registry stammt aus dem Walk-Forward; der erste
Paper-Forward zeigte, dass die Mechanik allein keine Kante ist (siehe Memory), daher
bewusst nur advisorisch.

EHRLICHKEITS-NAHT: Der Brief behauptet KEINE Robustheit mehr. Er trägt die reale
Paper-Forward-Bilanz der heute feuernden Strategien mit – hat die Mechanik vorwärts
keine Kante gezeigt (oder zu wenig Daten), sagt der Brief das ausdrücklich, statt ein
zuversichtliches „robuste Strategien feuern" in den KI-Prompt zu falten.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_FLAG = "STRATEGY_LAB_LIVE"
_REGIME_ENV = "STRATEGY_LAB_REGIME"        # AUTO | off | festes Label
_TTL_SECONDS = 3600                         # eine Zykluslänge; Signale ändern sich täglich
_MIN_CLOSED = 5                             # unter so wenig Paper-Trades kein Urteil
_cache: Dict = {"key": None, "ts": 0.0, "map": {}}


def is_enabled() -> bool:
    """Master-Schalter. Default AUS – Bot läuft ohne das Flag unverändert."""
    return os.getenv(_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def reset_cache() -> None:
    _cache.update(key=None, ts=0.0, map={})


def _default_loader() -> Callable:
    from backtesting import data_loader
    return data_loader.load


def _paper_forward_edge() -> Dict[str, Dict]:
    """Reale Paper-Forward-Bilanz je Strategie aus dem Ledger (netzfrei, nur Datei).
    {strategy: {"n_closed": int, "avg_return": float, "win_rate": float}}.
    Fail-safe → {} (kein Ledger / Fehler ⇒ keine Evidenz)."""
    try:
        from strategy_lab import paper_forward as pf
        s = pf.summary(pf.load_ledger())
        out: Dict[str, Dict] = {}
        for strat, st in (s.get("by_strategy") or {}).items():
            out[strat] = {
                "n_closed": int(st.get("closed", 0)),
                "avg_return": float(st.get("avg_return", 0.0)),
                "win_rate": float(st.get("win_rate", 0.0)),
            }
        return out
    except Exception as e:
        log.debug("live_bridge._paper_forward_edge fehlgeschlagen (ignoriert): %s", e)
        return {}


def _registry_regime_lookup() -> Dict[str, Dict]:
    """Aus der Promotion-Registry je aktiver Strategie: regime_breakdown (OOS je
    Regime) + robust_regimes. Fail-safe → {}."""
    try:
        from strategy_lab import promotion
        out: Dict[str, Dict] = {}
        for e in promotion.active_strategies():
            out[e["strategy"]] = {
                "breakdown": e.get("regime_breakdown") or {},
                "robust": set(e.get("robust_regimes") or []),
            }
        return out
    except Exception as e:
        log.debug("live_bridge._registry_regime_lookup fehlgeschlagen (ignoriert): %s", e)
        return {}


_STANCE_ORDER = {"adverse": 0, "untested": 1, "weak": 2, "favorable": 3}


def _regime_stance(strategies: List[str], regime: Optional[str],
                   lookup: Dict[str, Dict]) -> Dict:
    """Wie hat die HEUTE feuernde Mechanik in DIESEM Marktregime OOS abgeschnitten?
    stance ∈ favorable / weak / adverse / untested. KONSERVATIV: schon EIN adverse →
    adverse (eine Warnung hat Vorrang vor Zuversicht). Das ist der Hebel aus dem
    Befund ([[paper-forward-erster-befund]]): der Swing verlor SPEZIELL im Bull."""
    if not regime or not strategies:
        return {"stance": "untested", "regime": regime, "median": None}
    worst, worst_median = "favorable", None
    for s in strategies:
        info = lookup.get(s)
        if not info:
            st, med = "untested", None
        else:
            bd = info["breakdown"].get(regime)
            if regime in info["robust"]:
                st, med = "favorable", (bd or {}).get("median_test_return")
            elif bd is None:
                st, med = "untested", None
            elif float(bd.get("median_test_return", 0.0)) <= 0:
                st, med = "adverse", bd.get("median_test_return")
            else:
                st, med = "weak", bd.get("median_test_return")
        if _STANCE_ORDER[st] < _STANCE_ORDER[worst]:
            worst, worst_median = st, med
    return {"stance": worst, "regime": regime, "median": worst_median}


def _aggregate_edge(strategies: List[str], pf_edge: Dict[str, Dict]) -> Dict:
    """Verdichtet die Paper-Forward-Bilanz der heute feuernden Strategien zu EINEM
    ehrlichen Urteil für den Ticker. Aggregiert über geschlossene Trades gewichtet.
    verdict ∈ {"none" (keine nachgewiesene Kante), "thin" (zu dünn), "positive"}."""
    n = 0
    ret_weighted = 0.0
    win_weighted = 0.0
    for s in strategies:
        e = pf_edge.get(s)
        if not e:
            continue
        c = e["n_closed"]
        if c <= 0:
            continue
        n += c
        ret_weighted += e["avg_return"] * c
        win_weighted += e["win_rate"] * c
    if n == 0:
        return {"verdict": "thin", "n_closed": 0, "avg_return": 0.0, "win_rate": 0.0}
    avg = ret_weighted / n
    win = win_weighted / n
    if n < _MIN_CLOSED:
        verdict = "thin"
    elif avg > 0:
        verdict = "positive"
    else:
        verdict = "none"
    return {"verdict": verdict, "n_closed": n,
            "avg_return": round(avg, 6), "win_rate": round(win, 4)}


def conviction_map(
    universe: List[str],
    loader: Optional[Callable] = None,
    regime: Optional[str] = None,
) -> Dict[str, Dict]:
    """Pro Ticker die kombinierte mechanische Konviktion der HEUTE feuernden, aktiven
    (robusten) Strategien: {ticker: {"conviction": float, "strategies": [...], "regime": str|None}}.

    Liefert {} wenn das Flag aus ist, das Universum leer ist, die Registry keine
    aktive Strategie hat ODER irgendetwas schiefgeht (fail-safe). Ergebnis wird je
    (Universum, Regime) kurz gecacht, damit der Allokator nicht pro Ticker neu lädt."""
    if not is_enabled() or not universe:
        return {}

    if regime is None:
        regime = os.getenv(_REGIME_ENV, "AUTO")
    key = (tuple(sorted(universe)), str(regime))
    now = time.time()
    if _cache["key"] == key and (now - _cache["ts"]) < _TTL_SECONDS:
        return _cache["map"]

    out: Dict[str, Dict] = {}
    try:
        from strategy_lab import allocator
        ld = loader or _default_loader()

        reg: Optional[str] = None
        if regime and str(regime).lower() != "off":
            reg = (allocator.current_regime(universe, ld)
                   if str(regime).upper() == "AUTO" else str(regime).upper())

        plan = allocator.weight_plan(regime=reg)
        if plan:
            fired = allocator.current_signals(universe, ld, plan=plan)
            conv = allocator.combine_signals(fired, plan=plan)
            inv = {t: [s for s, ts in fired.items() if t in ts] for t in conv}
            pf_edge = _paper_forward_edge()
            reg_lookup = _registry_regime_lookup()
            out = {t: {"conviction": c, "strategies": inv.get(t, []), "regime": reg,
                       "evidence": _aggregate_edge(inv.get(t, []), pf_edge),
                       "regime_stance": _regime_stance(inv.get(t, []), reg, reg_lookup)}
                   for t, c in conv.items()}
    except Exception as e:  # defensiv: niemals den Zyklus reißen
        log.debug("live_bridge.conviction_map fehlgeschlagen (ignoriert): %s", e)
        out = {}

    _cache.update(key=key, ts=now, map=out)
    return out


def _regime_phrase(rs: Optional[Dict]) -> str:
    """Ehrlicher Halbsatz zum Regime-Befund – warnt aktiv im ungünstigen Regime."""
    if not rs or not rs.get("regime"):
        return ""
    reg = rs["regime"]
    stance = rs.get("stance")
    med = rs.get("median")
    medtxt = (f" (OOS-Median {float(med)*100:+.0f}%)"
              if isinstance(med, (int, float)) else "")
    if stance == "adverse":
        return (f" ACHTUNG Regime {reg}: diese Mechanik war hier historisch OOS "
                f"NEGATIV{medtxt} → eher Buy&Hold/Sentiment folgen, Mechanik abwerten.")
    if stance == "favorable":
        return f" Regime {reg}: Mechanik hat hier historisch (OOS) getragen{medtxt}."
    if stance == "weak":
        return f" Regime {reg}: OOS nur schwach-positiv{medtxt}, nicht robust."
    return f" Regime {reg}: für diese Mechanik OOS ungetestet."


def _evidence_phrase(ev: Optional[Dict]) -> str:
    """Ehrlicher Halbsatz zur realen Paper-Forward-Bilanz – behauptet nie Robustheit."""
    if not ev:
        return ("Paper-Forward-Bilanz: noch keine Validierung – rein mechanischer "
                "Hinweis, NICHT als Bestätigung werten.")
    v = ev.get("verdict")
    n = int(ev.get("n_closed", 0))
    avg = float(ev.get("avg_return", 0.0)) * 100
    win = float(ev.get("win_rate", 0.0)) * 100
    if v == "none":
        return (f"Paper-Forward-Bilanz: bislang KEINE nachgewiesene Kante "
                f"(Ø {avg:+.1f}% über {n} geschlossene Paper-Trades, Trefferquote {win:.0f}%) "
                "– als Kontext werten, NICHT als Bestätigung.")
    if v == "positive":
        return (f"Paper-Forward-Bilanz: bislang leicht positiv "
                f"(Ø {avg:+.1f}% über {n} Paper-Trades, Trefferquote {win:.0f}%) "
                "– schwaches Indiz, kein Beweis.")
    # thin / unbekannt
    return (f"Paper-Forward-Bilanz: noch zu dünn für ein Urteil ({n} geschlossene "
            "Paper-Trades) – rein mechanischer Hinweis ohne Validierung.")


def brief_for(ticker: str, conv_map: Optional[Dict[str, Dict]]) -> str:
    """Advisorischer Kontext-Satz für einen Ticker (oder "" wenn nichts feuert).
    Wird wie macro_brief in den Analyse-Prompt gefaltet – rein zusätzlich.

    Behauptet KEINE Robustheit/Kaufempfehlung: nennt nur, dass die Mechanik feuert,
    und hängt die reale Paper-Forward-Bilanz an (oder deren Fehlen)."""
    try:
        if not conv_map:
            return ""
        e = conv_map.get(ticker)
        if not e or float(e.get("conviction", 0)) <= 0:
            return ""
        strats = ", ".join(e.get("strategies") or []) or "—"
        reg = e.get("regime")
        head = ("MECHANIK (strategy_lab – additiver Hinweis, KEIN Auto-Trade, KEINE "
                f"Kaufempfehlung): {strats} feuert HEUTE, Konviktion "
                f"{float(e['conviction'])*100:.0f}%.")
        # Regime-Befund: aus dem Eintrag oder – fehlt er – wenigstens das Label.
        rs = e.get("regime_stance") or ({"stance": "untested", "regime": reg, "median": None}
                                        if reg else None)
        return head + _regime_phrase(rs) + " " + _evidence_phrase(e.get("evidence"))
    except Exception:
        return ""
