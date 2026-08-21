"""
Tests für dashboard/report.py (Ausbau-Roadmap H5.1 — Wochen-Report-Export).
"""
from dashboard.report import build_weekly_html


def test_build_weekly_html_is_self_contained_document():
    out = build_weekly_html("2026-07-15")
    assert out.startswith("<!doctype html>")
    assert "<html" in out and "</html>" in out
    # Selbstständigkeits-Check: keine ECHTEN externen Ressourcen-Referenzen
    # (CDN/Skript/Bild/Stylesheet) — die SVG-xmlns-Deklaration
    # ("http://www.w3.org/2000/svg") ist ein reiner Namensraum-Bezeichner,
    # löst nie einen Netzwerk-Request aus, und ist bewusst KEIN Treffer.
    assert "<link " not in out
    assert "<script src" not in out
    assert "@import" not in out
    assert "url(http" not in out
    import re
    urls = re.findall(r'(?:src|href)="(https?://[^"]*)"', out)
    assert urls == []


def test_build_weekly_html_contains_kpi_and_sections():
    out = build_weekly_html("2026-07-15")
    assert "Kennzahlen" in out
    assert "Fabrik-Szene" in out
    assert "Letzte Entscheidungen" in out
    assert "<svg" in out  # Fabrik-Szene ist eine echte Momentaufnahme


def test_build_weekly_html_default_end_day_is_today():
    from datetime import date
    out_default = build_weekly_html()
    assert "<h1>Wochen-Report" in out_default
    assert date.today().isoformat() in out_default


def test_build_weekly_html_escapes_injected_decision_reason(monkeypatch):
    class _FakeDecisionLog:
        def get_recent(self, limit=10):
            return [{
                "decided_at": "2026-07-15T09:00:00", "ticker": "<script>x</script>",
                "action": "SKIP", "reason": "<img onerror=alert(1)>",
            }]

    monkeypatch.setattr("analyzers.decision_log.DecisionLog", _FakeDecisionLog)
    out = build_weekly_html("2026-07-15")
    assert "<script>x</script>" not in out
    assert "<img onerror=alert(1)>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;img onerror=alert(1)&gt;" in out


def test_build_weekly_html_fail_open_when_week_stats_broken(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr("dashboard.report.week_stats", _boom)
    out = build_weekly_html("2026-07-15")
    assert "<!doctype html>" in out
    assert "Entscheidungen: 0" in out


def test_build_weekly_html_fail_open_when_scene_broken(monkeypatch):
    def _boom():
        raise RuntimeError("kaputt")

    monkeypatch.setattr("dashboard.factory.render_scene", _boom)
    out = build_weekly_html("2026-07-15")
    assert "Fabrik-Szene nicht verfügbar" in out


def test_build_weekly_html_no_decisions_shows_hint(monkeypatch):
    class _EmptyDecisionLog:
        def get_recent(self, limit=10):
            return []

    monkeypatch.setattr("analyzers.decision_log.DecisionLog", _EmptyDecisionLog)
    out = build_weekly_html("2026-07-15")
    assert "Keine Entscheidungen protokolliert" in out
