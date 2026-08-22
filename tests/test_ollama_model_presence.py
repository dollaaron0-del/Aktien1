"""
Tests für die Modell-Präsenz-Prüfung des OllamaPrescreeners.

Hintergrund (22.8.2026): is_available() prüfte nur, ob der Ollama-SERVER auf
/api/tags antwortet — nie, ob das konfigurierte Modell dort installiert ist.
Folge im Live-Bot: "Ollama verfügbar" im Log, danach ~700 stille HTTP-404 pro
Tag über 5 Tage (17.–22.8.), die lokale Analyse war faktisch tot.

Netzfrei: requests.get/post gemockt.
"""
import requests

from analyzers.ollama_prescreener import (
    OllamaPrescreener,
    _model_installed,
    _parse_installed_models,
)


def _tags_resp(names, status: int = 200):
    class _R:
        status_code = status

        def json(self):
            return {"models": [{"name": n} for n in names]}
    return _R()


def _broken_resp(status: int = 200):
    class _R:
        status_code = status

        def json(self):
            raise ValueError("kein JSON")
    return _R()


# ── _model_installed: Tag-Vergleich ────────────────────────────────────────

def test_exact_tag_counts_as_installed():
    assert _model_installed("llama3.2:3b", {"llama3.2:3b", "bge-m3:latest"})


def test_missing_tag_is_not_installed():
    assert not _model_installed("llama3.2:3b", {"bge-m3:latest"})


def test_name_without_colon_means_latest():
    assert _model_installed("bge-m3", {"bge-m3:latest"})
    assert not _model_installed("bge-m3", {"bge-m3:v2"})


def test_quantized_variant_is_a_different_tag():
    """Der konkrete Auslöser des Live-Ausfalls: qwen2.5:14b war konfiguriert,
    installiert war nur qwen2.5:14b-instruct-q6_K. Präfix-Matching hätte das
    fälschlich für vorhanden gehalten."""
    assert not _model_installed("qwen2.5:14b", {"qwen2.5:14b-instruct-q6_K"})


def test_empty_installed_set_is_not_installed():
    assert not _model_installed("llama3.2:3b", set())


# ── _parse_installed_models: robustes Auswerten ────────────────────────────

def test_parse_extracts_names():
    assert _parse_installed_models(_tags_resp(["a:1", "b:2"])) == {"a:1", "b:2"}


def test_parse_returns_none_on_unparsable_response():
    """None = 'weiß nicht' → Aufrufer bleibt fail-open."""
    assert _parse_installed_models(_broken_resp()) is None


def test_parse_skips_malformed_entries():
    class _R:
        status_code = 200

        def json(self):
            return {"models": [{"name": "ok:1"}, {"noname": True}, "kaputt", None]}
    assert _parse_installed_models(_R()) == {"ok:1"}


def test_parse_returns_none_when_models_not_a_list():
    class _R:
        status_code = 200

        def json(self):
            return {"models": "kaputt"}
    assert _parse_installed_models(_R()) is None


# ── is_available: Server läuft, Modell fehlt ───────────────────────────────

def test_available_when_model_is_installed(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _tags_resp(["llama3.2:3b"]))
    assert OllamaPrescreener(model="llama3.2:3b").is_available() is True


def test_not_available_when_model_missing(monkeypatch):
    """Kern-Regression: Server antwortet, Modell fehlt → NICHT verfügbar,
    statt 'verfügbar' zu melden und dann in 404er zu laufen."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: _tags_resp(["etwas:anderes"]))
    assert OllamaPrescreener(model="llama3.2:3b").is_available() is False


def test_unparsable_tags_response_stays_fail_open(monkeypatch):
    """Antwort nicht auswertbar → lieber verfügbar lassen als die Engine
    grundlos abschalten (Fail-open-Muster des Projekts)."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: _broken_resp())
    assert OllamaPrescreener(model="llama3.2:3b").is_available() is True


def test_server_down_still_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "get", _boom)
    assert OllamaPrescreener(model="llama3.2:3b").is_available() is False


def test_availability_result_is_cached(monkeypatch):
    calls = []

    def _get(*a, **k):
        calls.append(1)
        return _tags_resp(["llama3.2:3b"])
    monkeypatch.setattr(requests, "get", _get)
    p = OllamaPrescreener(model="llama3.2:3b")
    assert p.is_available() and p.is_available()
    assert len(calls) == 1


# ── _call_ollama: 404 schaltet die Engine ab ───────────────────────────────

def test_404_disables_engine_for_the_run(monkeypatch):
    """Ein fehlendes Modell heilt sich im Lauf nicht — nach dem ersten 404
    darf der Prescreener nicht als verfügbar weiterlaufen."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _tags_resp([], status=404))
    p = OllamaPrescreener(model="fehlt:1b")
    p._available = True

    assert p._call_ollama("prompt") is None
    assert p._available is False


def test_other_http_errors_do_not_disable_engine(monkeypatch):
    """Ein 500er ist typischerweise vorübergehend — die Engine bleibt an,
    anders als beim strukturellen 404."""
    monkeypatch.setattr(requests, "post", lambda *a, **k: _tags_resp([], status=500))
    p = OllamaPrescreener(model="da:1b")
    p._available = True

    assert p._call_ollama("prompt") is None
    assert p._available is True
