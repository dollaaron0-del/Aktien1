"""
Test: claude_analyzer._try_ollama nutzt das KONFIGURIERTE Ollama-Modell
(nicht mehr das hartcodierte, nicht existierende "llama3").
"""
from unittest.mock import patch, MagicMock

from analyzers.claude_analyzer import ClaudeAnalyzer


def test_try_ollama_uses_configured_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "45")
    az = ClaudeAnalyzer(api_key="dummy")

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["model"] = (json or {}).get("model")
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"response": ""}
        return resp

    with patch("requests.post", side_effect=fake_post):
        az._try_ollama("AAPL", "some news", 150.0, False, "")

    assert captured["model"] == "llama3.2:3b"   # nicht "llama3"
    assert captured["timeout"] == 45
    assert captured["url"].endswith("/api/generate")
