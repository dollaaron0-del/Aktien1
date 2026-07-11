"""
Zentrales Daten-Qualitäts-Gate (Roadmap 1.8).

Schlechte Eingangsdaten sind der leiseste Weg zu falschen Trades: die
yfinance-NaN-Falle (NaN + max(0,min(1,x)) kippt Scores still auf 1.0) war
ein Punkt-Fix — dieses Modul prüft systematisch VOR der Analyse:

1. **Kurs gültig?** None/NaN/inf/≤0 → SKIP (kein Claude-Call, kein Trade).
2. **Kurs frisch?** Letzter Handelstag älter als DATA_GATE_MAX_STALE_DAYS
   Kalendertage (Default 5, deckt Wochenende + Feiertag) → SKIP. Greift nur,
   wenn die Quelle ein last_bar_date liefert (fail-open).
3. **Kurs plausibel?** Kurs > Faktor× über 52W-Hoch oder < 1/Faktor× unter
   52W-Tief (Default-Faktor 5) → SKIP. Das ist bewusst NUR ein
   Skalenfehler-Detektor (GBp↔GBP, Split-Artefakte = Faktor ~100), keine
   Volatilitäts-Bremse — echte Ausbrüche über das 52W-Hoch sind normal.
4. **Begleitfelder bereinigt**: nicht-finite Zahlen (NaN/inf) in
   price_change/volume/market_cap/… werden auf None gesetzt, damit
   nachgelagerte Scorer nie wieder still mit NaN rechnen.

Ticker wird übersprungen + Event geloggt statt mit Müll zu rechnen.
Fail-open bei fehlenden Feldern: was die Quelle nicht liefert, wird nicht
geprüft (sonst stünde der Bot bei jeder dünnen Quelle still).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

# Begleitfelder, die nachgelagert in Scores/Prompts einfließen — nicht-finite
# Werte hier sind die dokumentierte NaN-Falle.
_NUMERIC_AUX_FIELDS = (
    "price_change_1w", "price_change_1m", "volume_avg",
    "market_cap", "pe_ratio", "52w_high", "52w_low",
)


@dataclass
class GateResult:
    ok: bool
    code: str = "ok"                # ok | kein_kurs | stale | unplausibel
    reason: str = ""
    sanitized_fields: List[str] = field(default_factory=list)


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def check_price_data(ticker: str, price_data: Optional[Dict],
                     max_stale_days: Optional[int] = None,
                     jump_factor: Optional[float] = None,
                     today: Optional[date] = None) -> GateResult:
    """Prüft price_data vor der Analyse. Bereinigt Begleitfelder IN PLACE
    (nicht-finite → None) und liefert das Gate-Verdikt. Wirft nie."""
    try:
        max_stale = (max_stale_days if max_stale_days is not None
                     else int(os.getenv("DATA_GATE_MAX_STALE_DAYS", "5")))
        factor = (jump_factor if jump_factor is not None
                  else float(os.getenv("DATA_GATE_JUMP_FACTOR", "5")))

        if not price_data:
            return GateResult(False, "kein_kurs", "keine Kursdaten von der Quelle")

        # ── 4. Begleitfelder bereinigen (immer, auch bei späterem SKIP) ──────
        sanitized: List[str] = []
        for f in _NUMERIC_AUX_FIELDS:
            v = price_data.get(f)
            if v is not None and not _finite(v):
                price_data[f] = None
                sanitized.append(f)

        # ── 1. Kurs gültig? ──────────────────────────────────────────────────
        px = price_data.get("current_price")
        if not _finite(px) or float(px) <= 0:
            return GateResult(False, "kein_kurs",
                              f"kein gültiger Kurs (current_price={px!r})",
                              sanitized)
        px = float(px)

        # ── 2. Kurs frisch? ──────────────────────────────────────────────────
        last_bar = price_data.get("last_bar_date")
        if last_bar and max_stale > 0:
            try:
                _d = date.fromisoformat(str(last_bar)[:10])
                _age = ((today or datetime.now().date()) - _d).days
                if _age > max_stale:
                    return GateResult(
                        False, "stale",
                        f"letzter Handelstag {last_bar} liegt {_age} Tage "
                        f"zurück (max {max_stale})", sanitized)
            except (ValueError, TypeError):
                pass  # unlesbares Datum → Check überspringen, nicht blocken

        # ── 3. Kurs plausibel? (reiner Skalenfehler-Detektor) ────────────────
        if factor > 1:
            hi = price_data.get("52w_high")
            lo = price_data.get("52w_low")
            if _finite(hi) and float(hi) > 0 and px > float(hi) * factor:
                return GateResult(
                    False, "unplausibel",
                    f"Kurs {px:g} > {factor:g}× 52W-Hoch {float(hi):g} — "
                    f"Skalenfehler der Quelle?", sanitized)
            if _finite(lo) and float(lo) > 0 and px < float(lo) / factor:
                return GateResult(
                    False, "unplausibel",
                    f"Kurs {px:g} < 52W-Tief {float(lo):g}/{factor:g} — "
                    f"Skalenfehler der Quelle?", sanitized)

        return GateResult(True, "ok", "", sanitized)
    except Exception as exc:  # Gate-Fehler darf nie den Zyklus reißen
        return GateResult(True, "ok", f"Gate-Fehler ignoriert: {exc}")
