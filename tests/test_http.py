"""
Tests für system/http.py – gemeinsame Session mit Retry/Backoff.
"""
import os

import system.http as http


def test_session_is_singleton():
    s1 = http.get_session()
    s2 = http.get_session()
    assert s1 is s2


def test_session_has_retry_adapter():
    s = http.get_session()
    adapter = s.get_adapter("https://example.com")
    retry = adapter.max_retries
    assert retry.total == http._RETRY_TOTAL
    assert 429 in retry.status_forcelist
    assert 503 in retry.status_forcelist


def test_http_get_sets_default_timeout(monkeypatch):
    captured = {}

    class _FakeSession:
        def get(self, url, **kwargs):
            captured.update(url=url, kwargs=kwargs)
            return "resp"

    monkeypatch.setattr(http, "get_session", lambda: _FakeSession())
    out = http.http_get("https://x.test")
    assert out == "resp"
    assert captured["kwargs"]["timeout"] == http._DEFAULT_TIMEOUT


def test_http_get_keeps_explicit_timeout(monkeypatch):
    captured = {}

    class _FakeSession:
        def get(self, url, **kwargs):
            captured.update(kwargs=kwargs)
            return "resp"

    monkeypatch.setattr(http, "get_session", lambda: _FakeSession())
    http.http_get("https://x.test", timeout=99)
    assert captured["kwargs"]["timeout"] == 99


def test_sec_user_agent_uses_env(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "me@real.com")
    assert "me@real.com" in http.sec_user_agent()


def test_sec_user_agent_placeholder_without_env(monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    ua = http.sec_user_agent()
    assert "AktienBot" in ua
