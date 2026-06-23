"""
InsiderSignalAnalyzer – explizite Bewertung von Insider-Kauf/-Verkauf-Signalen.

Verarbeitet gesammelte Insider-Items (InsiderCollector-Output) zu einem
quantitativen Score. Unabhängig von Claude-Analyse – programmatische Filterung.

Gewichtung:
  Corporate Form 4 BUY    (CEO/CFO/Director)  → +1.5 pro Trade
  Congressional BUY                            → +0.8 pro Trade
  Form 4 SELL (Exec)                           → −0.8 pro Trade
  Form 144 (geplanter Verkauf)                 → −1.2 pro Ankündigung
  Congressional SELL                           → −0.4 pro Trade

Congress×CEO-Confluence:
  Kaufen *beide Lager unabhängig* denselben Ticker (mind. 1 Exec-Form-4-BUY
  UND mind. 1 Congress-BUY im Lookback), gibt es einen Bonus von +1.0.
  Das ist ein anderes Signal als "3 Execs derselben Firma kaufen" (gleiches
  Lager): Übereinstimmung über zwei unabhängige, anders-informierte Gruppen
  hinweg ist seltener und konvektionsstärker. Der Bonus wirkt nur bullish.

Signal-Stufen:
  score ≥ +2.0  → STRONG_BUY   (blockiert nicht, senkt Kaufschwelle)
  score ≥ +0.8  → MILD_BUY
  −0.8 < score < +0.8 → NEUTRAL
  score ≤ −0.8  → MILD_SELL    (warnt, kein Block)
  score ≤ −2.0  → STRONG_SELL  (blockiert Kauf)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple

_WEIGHTS = {
    "SEC-Form4-Insider":    {"buy": +1.5, "sell": -0.8},
    "Congressional-House":  {"buy": +0.8, "sell": -0.4},
    "Congressional-Senate": {"buy": +0.8, "sell": -0.4},
    "SEC-Form144":          {"buy":  0.0, "sell": -1.2},  # immer bearish
}

# Welche Quelle gehört zu welchem "Lager"? Confluence = beide Lager kaufen.
_CAMP = {
    "SEC-Form4-Insider":    "exec",
    "Congressional-House":  "congress",
    "Congressional-Senate": "congress",
}

# Bonus, wenn Exec UND Congress unabhängig denselben Ticker kaufen.
_CONFLUENCE_BONUS = 1.0


@dataclass
class InsiderScore:
    score: float
    signal: str          # STRONG_BUY / MILD_BUY / NEUTRAL / MILD_SELL / STRONG_SELL
    bullish_count: int
    bearish_count: int
    top_names: List[str]
    message: str
    confluence: bool = False   # Exec- UND Congress-Kauf am selben Ticker?


def score_insider_items(items: List[Dict]) -> InsiderScore:
    """
    Berechnet einen Insider-Score aus gesammelten News-Items.
    Nur Items mit insider-bezogenen Quellen werden verwertet.
    """
    total = 0.0
    bullish = 0
    bearish = 0
    names: List[str] = []
    buy_camps: Set[str] = set()   # welche Lager haben gekauft? {"exec", "congress"}

    for item in items:
        source = item.get("source", "")
        if source not in _WEIGHTS:
            continue

        w = _WEIGHTS[source]
        action_raw = (item.get("action") or item.get("title") or "").lower()

        is_buy = any(k in action_raw for k in ("gekauft", "purchase", "buy", "p)"))
        is_sell = any(k in action_raw for k in ("verkauft", "sale", "sell", "s)", "form144", "form 144"))

        if source == "SEC-Form144":
            # Form 144 ist immer ein Verkaufssignal
            total += w["sell"]
            bearish += 1
        elif is_buy and not is_sell:
            total += w["buy"]
            bullish += 1
            if source in _CAMP:
                buy_camps.add(_CAMP[source])
        elif is_sell and not is_buy:
            total += w["sell"]
            bearish += 1

        person = item.get("person") or item.get("role") or ""
        if person and person not in names:
            names.append(person)

    # Confluence: Exec UND Congress haben unabhängig denselben Ticker gekauft.
    confluence = {"exec", "congress"} <= buy_camps
    if confluence:
        total += _CONFLUENCE_BONUS

    if total >= 2.0:
        signal = "STRONG_BUY"
        msg = (
            f"Starkes Insider-Kaufsignal: {bullish} Käufe "
            f"({', '.join(names[:3])})"
        )
    elif total >= 0.8:
        signal = "MILD_BUY"
        msg = f"Insider-Kaufsignal: {bullish} Kauf/Käufe von {', '.join(names[:2])}"
    elif total <= -2.0:
        signal = "STRONG_SELL"
        msg = (
            f"Starkes Insider-Verkaufssignal: {bearish} Verkäufe/Ankündigungen "
            f"({', '.join(names[:3])})"
        )
    elif total <= -0.8:
        signal = "MILD_SELL"
        msg = f"Insider-Verkaufssignal: {bearish} Verkauf/Ankündigung(en)"
    else:
        signal = "NEUTRAL"
        msg = ""

    if confluence and msg:
        msg += " · ⚡ Congress×CEO-Confluence (beide Lager kaufen)"

    return InsiderScore(
        score=round(total, 2),
        signal=signal,
        bullish_count=bullish,
        bearish_count=bearish,
        top_names=names[:3],
        message=msg,
        confluence=confluence,
    )


def get_insider_score(ticker: str, lookback_days: int = 30) -> InsiderScore:
    """Konvenienz-Funktion: sammelt Insider-Daten und berechnet Score direkt."""
    try:
        from collectors.insider_collector import InsiderCollector
        items = InsiderCollector(lookback_days=lookback_days).collect(ticker)
        return score_insider_items(items)
    except Exception:
        return InsiderScore(
            score=0.0, signal="NEUTRAL",
            bullish_count=0, bearish_count=0,
            top_names=[], message="", confluence=False,
        )
