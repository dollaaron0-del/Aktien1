"""Tests für das zentrale Daten-Qualitäts-Gate (analyzers/data_quality.py,
Roadmap 1.8): ungültige Kurse (NaN-Falle!), veraltete Kurse, Skalenfehler,
Begleitfeld-Bereinigung — und die fail-open-Zusagen."""
import math
from datetime import date

import pytest

from analyzers.data_quality import check_price_data, GateResult
from analyzers.decision_log import bucket_reason


def _pd(**kw):
    base = {"ticker": "TEST", "current_price": 100.0}
    base.update(kw)
    return base


# ── 1. Kurs gültig? ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"),
                                 -float("inf"), 0, -5, "abc"])
def test_invalid_price_blocks(bad):
    r = check_price_data("TEST", _pd(current_price=bad))
    assert not r.ok and r.code == "kein_kurs"


def test_missing_price_data_blocks():
    assert not check_price_data("TEST", None).ok
    assert not check_price_data("TEST", {}).ok


def test_valid_price_passes():
    r = check_price_data("TEST", _pd())
    assert r.ok and r.code == "ok"


# ── 2. Kurs frisch? ──────────────────────────────────────────────────────────

def test_stale_price_blocks():
    r = check_price_data(
        "TEST", _pd(last_bar_date="2026-07-01"),
        max_stale_days=5, today=date(2026, 7, 11),
    )
    assert not r.ok and r.code == "stale"
    assert "10 Tage" in r.reason


def test_fresh_price_passes():
    r = check_price_data(
        "TEST", _pd(last_bar_date="2026-07-10"),
        max_stale_days=5, today=date(2026, 7, 11),
    )
    assert r.ok


def test_weekend_gap_passes():
    # Freitags-Kerze am Montag = 3 Kalendertage alt → muss durch (Default 5)
    r = check_price_data(
        "TEST", _pd(last_bar_date="2026-07-10"),
        max_stale_days=5, today=date(2026, 7, 13),
    )
    assert r.ok


def test_missing_or_broken_bar_date_fails_open():
    assert check_price_data("TEST", _pd(), today=date(2026, 7, 11)).ok
    assert check_price_data("TEST", _pd(last_bar_date="kaputt"),
                            today=date(2026, 7, 11)).ok


def test_stale_check_disabled_via_zero():
    r = check_price_data("TEST", _pd(last_bar_date="2020-01-01"),
                         max_stale_days=0, today=date(2026, 7, 11))
    assert r.ok


# ── 3. Kurs plausibel? (Skalenfehler-Detektor) ───────────────────────────────

def test_scale_error_above_52w_high_blocks():
    # GBp↔GBP-Verwechslung: Kurs 100× über dem 52W-Hoch
    r = check_price_data("TEST", _pd(current_price=5000.0, **{"52w_high": 45.0}),
                         jump_factor=5)
    assert not r.ok and r.code == "unplausibel"


def test_scale_error_below_52w_low_blocks():
    r = check_price_data("TEST", _pd(current_price=0.4, **{"52w_low": 80.0}),
                         jump_factor=5)
    assert not r.ok and r.code == "unplausibel"


def test_real_breakout_passes():
    # Echter Ausbruch: 20 % über 52W-Hoch ist normal, kein Datenfehler
    r = check_price_data("TEST", _pd(current_price=120.0, **{"52w_high": 100.0}),
                         jump_factor=5)
    assert r.ok


def test_new_52w_low_passes():
    r = check_price_data("TEST", _pd(current_price=55.0, **{"52w_low": 80.0}),
                         jump_factor=5)
    assert r.ok


def test_plausibility_fails_open_without_range():
    assert check_price_data("TEST", _pd(current_price=5000.0)).ok


# ── 4. Begleitfelder bereinigen (NaN-Falle) ──────────────────────────────────

def test_nan_aux_fields_sanitized_in_place():
    pd_ = _pd(price_change_1w=float("nan"), pe_ratio=float("inf"),
              volume_avg=1000, market_cap=None)
    r = check_price_data("TEST", pd_)
    assert r.ok
    assert pd_["price_change_1w"] is None      # NaN raus
    assert pd_["pe_ratio"] is None             # inf raus
    assert pd_["volume_avg"] == 1000           # gültige Werte unangetastet
    assert set(r.sanitized_fields) == {"price_change_1w", "pe_ratio"}


def test_sanitize_also_on_skip():
    pd_ = _pd(current_price=None, price_change_1m=float("nan"))
    r = check_price_data("TEST", pd_)
    assert not r.ok
    assert pd_["price_change_1m"] is None


def test_nan_52w_high_sanitized_not_compared():
    # NaN im 52W-Hoch darf den Plausibilitäts-Check nicht vergiften
    r = check_price_data("TEST", _pd(**{"52w_high": float("nan")}))
    assert r.ok


# ── Funnel-Integration ───────────────────────────────────────────────────────

def test_gate_reason_gets_own_funnel_bucket():
    assert bucket_reason("Daten-Gate: kein gültiger Kurs (current_price=nan)") == "daten_gate"
    assert bucket_reason("Daten-Gate: letzter Handelstag 2026-07-01 liegt 10 Tage zurück") == "daten_gate"
    # Alt-Texte bleiben in ihrer Kategorie
    assert bucket_reason("Kein Kurs verfügbar") == "kein_kurs"
