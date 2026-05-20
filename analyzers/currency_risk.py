"""
Währungsrisiko-Manager für EU-Aktien

Für einen EUR-basierten Investor entstehen bei US-Dollar-Stärke Verluste auf
EU-Positionen die in Fremdwährung notieren (GBP, CHF, DKK). Und bei CHF-Stärke
werden Schweizer Titel teurer – oder günstiger falls CHF fällt.

Logik:
  - Lädt 30-Tage-Kursdaten für die relevanten Währungspaare via yfinance
  - Berechnet Trend (30d-Return) und kurzfristige Bewegung (5d)
  - Gibt einen Positions-Größen-Multiplikator zurück: 0.5–1.0 bei Gegenwind,
    1.0–1.2 bei Rückenwind, 1.0 bei neutral

Währungspaare (aus EUR-Sicht):
  EUR/USD (EURUSD=X) → für alle .DE/.PA/.AS/.BE/.MI/.MC Ticker
  GBP/EUR (GBPEUR=X) → für .L Ticker (GBP-notiert, EUR-Investor = Gegenwind wenn GBP fällt)
  CHF/EUR (CHFEUR=X) → für .SW Ticker
  DKK/EUR (DKKEUR=X) → für .CO Ticker (weitgehend an EUR gepegggt, kaum Risiko)

Rückenwind (Multiplikator > 1.0):
  EUR steigt vs USD → EU-Investor profitiert nicht direkt, aber Kaufkraft steigt
  Tatsächlich relevant: USD/EUR Trend – wenn EUR fällt vs USD, werden US-Gewinne teurer,
  aber EU-Aktien bleiben in EUR stabil.

Konkrete Anwendung:
  Wenn EUR/USD FÄLLT (USD stark): EU-Aktien in EUR sind stabiler.
    Für einen EUR-Investor: neutral bis leicht positiv (hedgt USD-Exposure).
  Wenn EUR/USD STEIGT (EUR stark): US-Assets verlieren in EUR an Wert.
    Für EU-Aktien: leicht positiv (EUR-Nominierung = kein FX-Verlust).

  GBP/EUR FÄLLT: .L Positionen verlieren in EUR → Multiplikator < 1.0
  CHF/EUR FÄLLT: .CH Positionen verlieren in EUR → Multiplikator < 1.0
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from logger import get_logger

log = get_logger(__name__)

# Suffix → Währungspaar (aus EUR-Sicht: wie viel EUR ist 1 Einheit wert)
_SUFFIX_TO_FX: Dict[str, str] = {
    ".DE": "EURUSD=X",   # EUR-notiert, kein direktes FX-Risiko für EUR-Investor
    ".F":  "EURUSD=X",
    ".MU": "EURUSD=X",
    ".PA": "EURUSD=X",
    ".AS": "EURUSD=X",
    ".MI": "EURUSD=X",
    ".MC": "EURUSD=X",
    ".BR": "EURUSD=X",
    ".BE": "EURUSD=X",
    ".VI": "EURUSD=X",
    ".L":  "GBPEUR=X",   # GBP-notiert → GBP/EUR Kurs relevant
    ".SW": "CHFEUR=X",   # CHF-notiert
    ".CO": "DKKEUR=X",   # DKK – nahezu fest an EUR
    ".ST": "SEKEUR=X",   # SEK
    ".HE": "EUREUR=X",   # EUR (Finland) – kein FX-Risiko
    ".OL": "NOKEUR=X",   # NOK
}

_CACHE_TTL_HOURS = 4   # FX-Daten alle 4 Stunden neu laden
_cache: Dict[str, tuple] = {}   # pair → (timestamp, modifier, trend_str)


@dataclass
class FXSignal:
    pair:             str
    rate_now:         float
    change_5d_pct:    float
    change_30d_pct:   float
    trend:            str           # HEADWIND | TAILWIND | NEUTRAL
    modifier:         float         # Multiplikator für Positionsgröße (0.5–1.2)
    note:             str


def _get_suffix(ticker: str) -> Optional[str]:
    for suffix in sorted(_SUFFIX_TO_FX.keys(), key=len, reverse=True):
        if ticker.upper().endswith(suffix.upper()):
            return suffix
    return None


def _fetch_fx(pair: str) -> Optional[FXSignal]:
    """Lädt 30-Tage-Kurs für ein Währungspaar. Cached für _CACHE_TTL_HOURS."""
    now = datetime.utcnow()
    cached = _cache.get(pair)
    if cached and (now - cached[0]).total_seconds() < _CACHE_TTL_HOURS * 3600:
        return cached[1]

    try:
        import yfinance as yf
        hist = yf.Ticker(pair).history(period="35d")
        if hist.empty or len(hist) < 6:
            return None

        rate_now   = float(hist["Close"].iloc[-1])
        rate_5d    = float(hist["Close"].iloc[-6])
        rate_30d   = float(hist["Close"].iloc[0])
        chg_5d     = (rate_now / rate_5d  - 1) * 100
        chg_30d    = (rate_now / rate_30d - 1) * 100

        # Interpretiere Trend und Multiplikator
        # Für .L/.SW/.CO Ticker: sinkender GBP/EUR bzw. CHF/EUR = Gegenwind
        # Für EUR-notierte (.DE/.PA/.AS): EUR/USD ist weitgehend irrelevant für EUR-Investor
        if pair in ("GBPEUR=X", "CHFEUR=X", "SEKEUR=X", "NOKEUR=X", "DKKEUR=X"):
            # Fremdwährungs-Stärke gegenüber EUR = gut für EUR-Investor
            if chg_30d < -3.0:
                trend, modifier, note = "HEADWIND", 0.7, f"{pair}: −{abs(chg_30d):.1f}% ggü. EUR (30d) → Positionsgröße reduziert"
            elif chg_30d < -1.0:
                trend, modifier, note = "HEADWIND", 0.85, f"{pair}: −{abs(chg_30d):.1f}% ggü. EUR (30d)"
            elif chg_30d > 2.0:
                trend, modifier, note = "TAILWIND", 1.1, f"{pair}: +{chg_30d:.1f}% ggü. EUR (30d) → Rückenwind"
            else:
                trend, modifier, note = "NEUTRAL", 1.0, f"{pair}: {chg_30d:+.1f}% ggü. EUR (30d)"
        else:
            # EUR/USD – für EUR-Investor bei EUR-notierten Aktien kaum direkt relevant
            trend, modifier, note = "NEUTRAL", 1.0, f"{pair}: {chg_30d:+.1f}% (30d)"

        signal = FXSignal(
            pair=pair,
            rate_now=round(rate_now, 4),
            change_5d_pct=round(chg_5d, 2),
            change_30d_pct=round(chg_30d, 2),
            trend=trend,
            modifier=modifier,
            note=note,
        )
        _cache[pair] = (now, signal)
        return signal

    except Exception as e:
        log.debug("FX %s: %s", pair, e)
        return None


def get_currency_modifier(ticker: str) -> tuple[float, str]:
    """
    Gibt (modifier, note) für einen EU-Ticker zurück.
    modifier: 0.5–1.2 — wird als Multiplikator auf die Positionsgröße angewandt.
    note: kurze Erklärung für den Log.
    Falls kein EU-Ticker oder FX-Daten nicht verfügbar: (1.0, "")
    """
    suffix = _get_suffix(ticker)
    if not suffix:
        return 1.0, ""

    pair = _SUFFIX_TO_FX.get(suffix)
    if not pair or pair == "EUREUR=X":
        return 1.0, ""

    signal = _fetch_fx(pair)
    if signal is None:
        return 1.0, ""

    return signal.modifier, signal.note


def get_fx_overview() -> Dict[str, FXSignal]:
    """Lädt alle relevanten Währungspaare für das EU-Dashboard."""
    pairs = list(set(_SUFFIX_TO_FX.values()) - {"EUREUR=X"})
    result = {}
    for pair in pairs:
        sig = _fetch_fx(pair)
        if sig:
            result[pair] = sig
    return result
