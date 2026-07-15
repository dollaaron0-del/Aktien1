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

def test_inject_renders_legacy_css_when_plain(monkeypatch):
    """D1.1: plain rendert exakt den alten Inline-Block (heutiges Aussehen),
    keine px-*-Klassen."""
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    at = AppTest.from_string(
        "import dashboard.theme as theme\ntheme.inject()\nimport streamlit as st\nst.write('x')"
    )
    at.run()
    assert not at.exception
    markdown_texts = [str(w.value) for w in at.get("markdown")]
    assert not any("px-panel" in t for t in markdown_texts)
    assert any("metric-container" in t for t in markdown_texts)


def test_inject_renders_style_block_when_pixel(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    at = AppTest.from_string(
        "import dashboard.theme as theme\ntheme.inject()\nimport streamlit as st\nst.write('x')"
    )
    at.run()
    assert not at.exception
    assert any("px-panel" in str(w.value) for w in at.get("markdown"))


def test_inject_includes_belt_animation_keyframe_and_reduced_motion_guard(monkeypatch):
    """D4.3: die Foerderband-Animation muss abschaltbar sein
    (prefers-reduced-motion), sonst waere sie eine UX-Falle."""
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    at = AppTest.from_string(
        "import dashboard.theme as theme\ntheme.inject()\nimport streamlit as st\nst.write('x')"
    )
    at.run()
    css = "".join(str(w.value) for w in at.get("markdown"))
    assert "px-belt-scroll" in css
    assert "prefers-reduced-motion" in css


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


def test_register_chart_themes_registers_and_enables_altair(monkeypatch):
    import altair as alt
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    monkeypatch.setattr(theme, "_charts_registered", False)
    theme.register_chart_themes()
    assert alt.themes.active == "pixel"
    alt.themes.enable("default")  # Testisolation: globalen State zurücksetzen


def test_register_chart_themes_leaves_default_active_when_plain(monkeypatch):
    import altair as alt
    alt.themes.enable("default")
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    monkeypatch.setattr(theme, "_charts_registered", False)
    theme.register_chart_themes()
    assert alt.themes.active == "default"


def test_register_chart_themes_sets_plotly_default_template(monkeypatch):
    import plotly.io as pio
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    monkeypatch.setattr(theme, "_charts_registered", False)
    theme.register_chart_themes()
    assert pio.templates.default == "pixel"
    pio.templates.default = "plotly"  # Testisolation zurücksetzen


def test_altair_theme_config_uses_palette_colors():
    cfg = theme._altair_theme()
    assert cfg["config"]["range"]["category"][0] == theme.PALETTE["cobalt"]


# ── D7.3: Laufband-Anzeigetafel ──────────────────────────────────────────────

def test_ticker_escapes_items_and_duplicates_track(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    out = theme.ticker(["10:00 TRADE: <script>x</script>", "NÄCHSTER LAUF: 15:00"])
    assert out.startswith('<div class="px-ticker">')
    assert "<script>" not in out
    assert out.count("&lt;script&gt;") == 2  # Inhalt verdoppelt (nahtlose Schleife)
    assert out.count("NÄCHSTER LAUF: 15:00") == 2


def test_ticker_empty_in_plain_mode_and_without_items(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    assert theme.ticker(["x"]) == ""
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    assert theme.ticker([]) == ""


def test_ticker_css_present_and_reduced_motion_safe(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    css = theme._base_css()
    assert ".px-ticker" in css
    assert "px-ticker-scroll" in css
    # reduced-motion-Block muss den Track abschalten:
    rm = css.split("prefers-reduced-motion", 1)[1][:400]
    assert ".px-ticker-track" in rm


# ── D7.4: CRT-Atmosphäre ─────────────────────────────────────────────────────

def test_crt_scanlines_on_by_default_and_disableable(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    monkeypatch.delenv("DASHBOARD_CRT", raising=False)
    assert "repeating-linear-gradient" in theme._base_css()
    monkeypatch.setenv("DASHBOARD_CRT", "0")
    assert "repeating-linear-gradient" not in theme._base_css()


def test_crt_overlay_never_blocks_interaction(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    monkeypatch.delenv("DASHBOARD_CRT", raising=False)
    crt = theme._crt_css()
    assert "pointer-events: none" in crt


def test_boot_lines_css_reduced_motion_safe(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    css = theme._base_css()
    assert ".px-boot-line" in css
    # Basis-Deckkraft 1 + animation:none unter reduced-motion → Zeilen
    # bleiben ohne Animation einfach sichtbar (kein unsichtbarer Text).
    assert "opacity: 1;" in css.split(".px-boot-line", 1)[1][:200]
    rm_blocks = [b[:300] for b in css.split("prefers-reduced-motion")[1:]]
    assert any(".px-boot-line" in b for b in rm_blocks)


# ── H6.4: Zweit-Theme "Blaupause" ─────────────────────────────────────────────

def test_blueprint_palette_has_same_keys_as_pixel():
    """Strukturelle Parität ist Pflicht — sonst crasht jede bestehende
    PALETTE["..."]-Zugriffsstelle im dashboard/-Baum unter blueprint."""
    assert set(theme.PALETTE_BLUEPRINT.keys()) == set(theme._PALETTE_PIXEL.keys())


def test_palette_resolves_to_blueprint_when_active(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "blueprint")
    assert theme.PALETTE["bg"] == theme.PALETTE_BLUEPRINT["bg"]
    assert theme.PALETTE["bg"] != theme._PALETTE_PIXEL["bg"]


def test_palette_resolves_to_pixel_by_default(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    assert theme.PALETTE["bg"] == theme._PALETTE_PIXEL["bg"]


def test_palette_supports_full_mapping_protocol(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "blueprint")
    assert dict(theme.PALETTE.items()) == theme.PALETTE_BLUEPRINT
    assert len(theme.PALETTE) == len(theme.PALETTE_BLUEPRINT)
    assert "bg" in theme.PALETTE
    assert theme.PALETTE.get("bg") == theme.PALETTE_BLUEPRINT["bg"]


def test_blueprint_is_enabled_stays_true_not_plain(monkeypatch):
    """blueprint ist ein drittes AKTIVES Theme, kein Alias für plain."""
    monkeypatch.setenv("DASHBOARD_THEME", "blueprint")
    assert theme.is_enabled() is True


def test_base_css_reflects_blueprint_hex_values(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "blueprint")
    css = theme._base_css()
    assert theme.PALETTE_BLUEPRINT["bg"] in css
    assert theme._PALETTE_PIXEL["bg"] not in css


def test_base_css_unchanged_for_pixel_regression(monkeypatch):
    """Regression: pixel-Modus darf durch H6.4 nicht verändert werden."""
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    css = theme._base_css()
    assert theme._PALETTE_PIXEL["bg"] in css
    assert theme.PALETTE_BLUEPRINT["bg"] not in css


def test_machine_status_color_reflects_blueprint(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "blueprint")
    from dashboard.factory.machines import _status_color
    assert _status_color("ok") == theme.PALETTE_BLUEPRINT["neon_green"]
    monkeypatch.setenv("DASHBOARD_THEME", "pixel")
    assert _status_color("ok") == theme._PALETTE_PIXEL["neon_green"]
