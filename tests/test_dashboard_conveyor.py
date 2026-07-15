"""
Tests für dashboard/conveyor.py (Design D4.1: Förderband-Funnel-Visual).
"""
from dashboard.conveyor import build_conveyor_svg


def _funnel(total=10, actions=None, skip_reasons=None):
    return {
        "total": total,
        "actions": actions or {"BUY": 2, "SKIP": 6, "HOLD": 1, "SELL": 1},
        "skip_reasons": skip_reasons or {},
    }


def test_svg_contains_total_and_action_counts():
    svg = build_conveyor_svg(_funnel(total=42, actions={"BUY": 5, "SKIP": 30, "HOLD": 4, "SELL": 3}))
    assert "42" in svg
    assert ">5<" in svg  # BUY count
    assert "HOLD 4" in svg
    assert "SELL 3" in svg
    assert "<svg" in svg and "</svg>" in svg


def test_empty_funnel_renders_without_error():
    svg = build_conveyor_svg({})
    assert "<svg" in svg
    assert ">0<" in svg  # total=0


def test_none_like_funnel_fields_default_to_zero():
    svg = build_conveyor_svg({"total": None, "actions": None, "skip_reasons": None})
    assert "<svg" in svg


def test_skip_reason_labels_and_counts_appear():
    svg = build_conveyor_svg(_funnel(skip_reasons={"unter_schwelle": 12, "korrelation": 4}))
    assert "Unter Schwelle" in svg
    assert "Korrelation" in svg
    assert ">12<" in svg
    assert ">4<" in svg


def test_top5_cap_groups_rest_under_ellipsis():
    reasons = {
        "unter_schwelle": 10, "korrelation": 9, "liquiditaet": 8,
        "max_positionen": 7, "earnings_sperre": 6, "kein_kurs": 5, "daten_gate": 4,
    }
    svg = build_conveyor_svg(_funnel(skip_reasons=reasons))
    # Nur die Top 5 Labels erscheinen einzeln, der Rest (5+4=9) landet unter "…"
    assert "Kein Kurs" not in svg
    assert "Daten-Gate" not in svg
    assert ">…<" in svg
    assert ">9<" in svg


def test_unknown_reason_key_falls_back_to_title_case():
    svg = build_conveyor_svg(_funnel(skip_reasons={"ganz_neuer_grund": 3}))
    assert "Ganz Neuer Grund" in svg


def test_reason_label_html_is_escaped():
    """Ein Reason-Key, der zufällig wie Markup aussieht, darf nicht als
    HTML durchschlagen (auch wenn bucket_reason() das heute nie liefert —
    Verteidigung an der SVG-Grenze selbst). _reason_label() title-cased den
    unbekannten Fallback-Key, daher case-insensitive prüfen."""
    svg = build_conveyor_svg(_funnel(skip_reasons={"<script>alert(1)</script>": 3}))
    assert "<script>alert(1)</script>" not in svg
    assert "&lt;script&gt;" in svg.lower()


def test_width_parameter_is_applied():
    svg = build_conveyor_svg(_funnel(), width=500)
    assert 'viewBox="0 0 500 260"' in svg


def test_belt_pattern_animation_class_present():
    """D4.3: das Band-Muster traegt die px-belt-anim-Klasse, deren
    Keyframe/Reduced-Motion-Aus in theme.py._base_css() definiert ist."""
    svg = build_conveyor_svg(_funnel())
    assert "px-belt-anim" in svg
    assert "px-belt-pattern" in svg
