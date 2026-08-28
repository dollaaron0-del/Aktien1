"""Tests für monitoring/status_api.py — der read-only HTTP-Status-Endpunkt.

Kernzusagen:
- build_status() wirft nie und liefert immer die erwarteten Top-Level-Keys.
- Ein kaputter Abschnitt landet in errors[], der Rest der Antwort bleibt.
- /health und /status antworten sauber, unbekannte Pfade → 404.
"""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import monitoring.status_api as api

TOP_LEVEL_KEYS = {
    "ok",
    "service",
    "generated_at",
    "runtime",
    "portfolio",
    "phase",
    "score",
    "risk",
    "recent_trades",
    "llm",
    "errors",
}


def test_build_status_shape_and_never_raises():
    s = api.build_status()
    assert TOP_LEVEL_KEYS.issubset(s.keys())
    assert s["service"] == "aktien-bot"
    assert isinstance(s["errors"], list)
    assert isinstance(s["recent_trades"], list)
    assert isinstance(s["runtime"], dict)
    # generated_at is an ISO-8601 UTC stamp
    assert s["generated_at"].endswith("+00:00")


def test_failing_section_is_isolated(monkeypatch):
    """A section that hits a real error must record it in errors[] and return
    empty, without taking the rest of the payload down."""

    def broken_score(errors):
        try:
            raise RuntimeError("db gone")
        except RuntimeError as exc:
            errors.append(f"score: {exc}")
            return {}

    monkeypatch.setattr(api, "_score_section", broken_score)
    s = api.build_status()
    assert any("score: db gone" in e for e in s["errors"])
    assert TOP_LEVEL_KEYS.issubset(s.keys())
    # other sections still populated from real data
    assert s["runtime"] != {} or s["portfolio"] != {}


def test_ok_false_only_when_nothing_readable(monkeypatch):
    monkeypatch.setattr(api, "_runtime_section", lambda errors: {})
    monkeypatch.setattr(api, "_portfolio_section", lambda errors: {})
    monkeypatch.setattr(api, "_score_section", lambda errors: {})
    s = api.build_status()
    assert s["ok"] is False


def test_ok_true_when_any_section_has_data(monkeypatch):
    monkeypatch.setattr(api, "_runtime_section", lambda errors: {"state": "idle"})
    monkeypatch.setattr(api, "_portfolio_section", lambda errors: {})
    monkeypatch.setattr(api, "_score_section", lambda errors: {})
    s = api.build_status()
    assert s["ok"] is True


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api._Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 (local only)
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_health_route(server):
    status, body = _get(f"{server}/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_status_route(server):
    status, body = _get(f"{server}/status")
    assert status == 200
    assert TOP_LEVEL_KEYS.issubset(body.keys())


def test_status_route_trailing_slash(server):
    status, _ = _get(f"{server}/status/")
    assert status == 200


def test_unknown_route_is_404(server):
    try:
        urllib.request.urlopen(f"{server}/nope", timeout=10)
        raise AssertionError("expected HTTPError 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
