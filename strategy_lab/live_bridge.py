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
_cache: Dict = {"key": None, "ts": 0.0, "map": {}}


def is_enabled() -> bool:
    """Master-Schalter. Default AUS – Bot läuft ohne das Flag unverändert."""
    return os.getenv(_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def reset_cache() -> None:
    _cache.update(key=None, ts=0.0, map={})


def _default_loader() -> Callable:
    from backtesting import data_loader
    return data_loader.load


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
            out = {t: {"conviction": c, "strategies": inv.get(t, []), "regime": reg}
                   for t, c in conv.items()}
    except Exception as e:  # defensiv: niemals den Zyklus reißen
        log.debug("live_bridge.conviction_map fehlgeschlagen (ignoriert): %s", e)
        out = {}

    _cache.update(key=key, ts=now, map=out)
    return out


def brief_for(ticker: str, conv_map: Optional[Dict[str, Dict]]) -> str:
    """Advisorischer Kontext-Satz für einen Ticker (oder "" wenn nichts feuert).
    Wird wie macro_brief in den Analyse-Prompt gefaltet – rein zusätzlich."""
    try:
        if not conv_map:
            return ""
        e = conv_map.get(ticker)
        if not e or float(e.get("conviction", 0)) <= 0:
            return ""
        strats = ", ".join(e.get("strategies") or []) or "—"
        reg = e.get("regime")
        return ("MECHANIK (strategy_lab, additiver Hinweis – KEIN Auto-Trade): robuste "
                f"Strategien feuern HEUTE ({strats}) → Konviktion {float(e['conviction'])*100:.0f}%"
                + (f", Marktregime {reg}" if reg else "") + ".")
    except Exception:
        return ""
