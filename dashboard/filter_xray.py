"""
dashboard/filter_xray.py — Lern-Filter-Röntgenblick (Roadmap L6.2,
docs/FABRIK_LEBENDIG.md).

Der Lern-Filter wirkt sichtbar („Lern-Filter AVOID" im Trockenlauf, in
den Skip-Gründen), aber worauf er gewichtet, konnte man im eigenen
Dashboard nirgends nachsehen. Dieses Modul macht die gelernten Gewichte
aus `data/rl_weights.json` lesbar — Pfad über
`analyzers.rl_agent._WEIGHTS_FILE` (Single Source, kein zweiter
hartkodierter Pfad), read-only.

EHRLICHKEITS-PFLICHT (Kern des Punkts): `trade_count` MUSS mitgeliefert
und angezeigt werden. Stand 16.7.2026 sind das **6 Trades** — auf dieser
Basis sind die Gewichte nicht mehr als ein erster Anhaltspunkt. Die
Labels bleiben darum bewusst nüchtern-beschreibend („News-Tempo"), es
wird NICHT interpretiert („achtet auf X") — das wäre bei n=6 blanker
Überverkauf.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

# Nüchterne deutsche Bezeichnungen der echten `feature_names` aus
# rl_weights.json (16.7.2026 geprüft). Nur Übersetzung, keine Deutung.
_FEATURE_LABELS_DE: Dict[str, str] = {
    "sentiment_score": "Sentiment-Score",
    "vix_level": "VIX-Stand",
    "momentum_5d": "Momentum (5 Tage)",
    "news_velocity": "News-Tempo",
    "confidence_encoded": "Konfidenz-Stufe",
    "regime_encoded": "Marktregime",
}


def feature_weights(path: Optional[str] = None) -> Dict:
    """Gelernte Gewichte des Lern-Filters:
    `{"trade_count", "features": [{"key", "label", "weight"}]}`.

    `features` ist absteigend nach Gewicht sortiert. Fehlen
    `feature_names` (ältere Datei), wird auf „Merkmal N" ausgewichen
    statt zu raten. Fail-open: `{"trade_count": 0, "features": []}`.
    """
    out: Dict = {"trade_count": 0, "features": []}
    try:
        if path is None:
            from analyzers.rl_agent import _WEIGHTS_FILE
            path = _WEIGHTS_FILE
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return out
    try:
        weights = list(data.get("weights") or [])
        names = list(data.get("feature_names") or [])
        out["trade_count"] = int(data.get("trade_count") or 0)
        features: List[Dict] = []
        for i, w in enumerate(weights):
            if not isinstance(w, (int, float)):
                continue
            key = str(names[i]) if i < len(names) else f"feature_{i}"
            features.append({
                "key": key,
                "label": _FEATURE_LABELS_DE.get(key, key.replace("_", " ")),
                "weight": float(w),
            })
        features.sort(key=lambda f: -f["weight"])
        out["features"] = features
    except Exception:
        return {"trade_count": 0, "features": []}
    return out
