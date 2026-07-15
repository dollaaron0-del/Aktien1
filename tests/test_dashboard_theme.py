"""
Tests für dashboard/theme.py (Design-Roadmap D0: zentrale Stil-Quelle).

DASHBOARD_THEME=plain muss das Theme komplett abschalten (Notausstieg,
D6.2) — jeder Helfer wird sowohl im pixel- als auch im plain-Zustand
getestet. Headless via streamlit.testing.v1 AppTest auf isolierten
Mini-Skripten (Muster: tests/test_dashboard_auth.py), kein echtes
app.py-Rendering nötig.
"""
import re

from streamlit.testing.v1 import AppTest

import dashboard.theme as theme


# ── is_enabled() ──────────────────────────────────────────────────────────────

def test_is_enabled_default_true(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    assert theme.is_enabled() is True


def test_is_enabled_false_when_plain(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    assert theme.is_enabled() is False


def test_is_enabled_true_when_pixel_explicit(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    assert theme.is_enabled() is True


def test_is_enabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "PLAIN")
    assert theme.is_enabled() is False


# ── PALETTE ────────────────────────────────────────────────────────────────────

def test_palette_values_are_seven_char_hex():
    for name, value in theme.PALETTE.items():
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", value), f"{name}={value!r} ist kein Hex-Code"


# ── inject() ───────────────────────────────────────────────────────────────────

def test_inject_renders_nothing_when_plain(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    at = AppTest.from_string(
        "import dashboard.theme as theme\ntheme.inject()\nimport streamlit as st\nst.write('x')"
    )
    at.run()
    assert not at.exception
    # Kein <style>-Markdown-Element von inject() gerendert (nur 'x' bleibt übrig)
    assert not any("px-panel" in str(w.value) for w in at.get("markdown"))


def test_inject_renders_style_block_when_pixel(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    at = AppTest.from_string(
        "import dashboard.theme as theme\ntheme.inject()\nimport streamlit as st\nst.write('x')"
    )
    at.run()
    assert not at.exception
    assert any("px-panel" in str(w.value) for w in at.get("markdown"))


def test_inject_does_not_raise_without_font_files(monkeypatch, tmp_path):
    """Fehlen die woff2-Dateien (Download-Fehlschlag, D0.4-Fallback), darf
    inject() trotzdem nicht werfen — die CSS-Fallback-Kette greift."""
    monkeypatch.setattr(theme, "_FONTS_DIR", str(tmp_path / "nope"))
    theme._font_face_css.cache_clear()
    try:
        at = AppTest.from_string(
            "import dashboard.theme as theme\ntheme.inject()\nimport streamlit as st\nst.write('x')"
        )
        at.run()
        assert not at.exception
    finally:
        theme._font_face_css.cache_clear()


# ── led() ──────────────────────────────────────────────────────────────────────

def test_led_pixel_mode_contains_escaped_label_and_class(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    out = theme.led("ok", "<script>alert(1)</script>")
    assert "px-led--ok" in out
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_led_unknown_status_falls_back_to_off(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    out = theme.led("bogus", "x")
    assert "px-led--off" in out


def test_led_plain_mode_uses_emoji_text(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    out = theme.led("err", "Gateway down")
    assert "px-led" not in out
    assert "🔴" in out
    assert "Gateway down" in out


# ── panel() ────────────────────────────────────────────────────────────────────

def test_panel_wraps_in_pixel_mode(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    out = theme.panel("<b>hi</b>")
    assert out == '<div class="px-panel"><b>hi</b></div>'


def test_panel_passthrough_in_plain_mode(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    out = theme.panel("<b>hi</b>")
    assert out == "<b>hi</b>"


# ── image_b64() ────────────────────────────────────────────────────────────────

def test_image_b64_missing_file_returns_empty_string():
    assert theme.image_b64("does-not-exist.png") == ""


def test_image_b64_reads_existing_file(tmp_path, monkeypatch):
    img_dir = tmp_path / "img"
    img_dir.mkdir()
    (img_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nfakecontent")
    monkeypatch.setattr(theme, "_IMG_DIR", str(img_dir))
    out = theme.image_b64("logo.png")
    assert out.startswith("data:image/png;base64,")


# ── register_chart_themes() ───────────────────────────────────────────────────

def test_register_chart_themes_is_idempotent(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    monkeypatch.setattr(theme, "_charts_registered", False)
    theme.register_chart_themes()
    theme.register_chart_themes()  # zweiter Aufruf darf nicht werfen


def test_register_chart_themes_noop_when_plain(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    monkeypatch.setattr(theme, "_charts_registered", False)
    theme.register_chart_themes()
    assert theme._charts_registered is False
