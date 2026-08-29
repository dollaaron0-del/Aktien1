"""
Tests für validate_config() — speziell die provider-abhängige LLM-Key-Prüfung.

Vor dem Fix verlangte der Start-Check IMMER ANTHROPIC_API_KEY, auch wenn der
Bot auf LLM_PROVIDER=gemini läuft (und den Anthropic-Key gar nicht nutzt).
Ergebnis: der stale Anthropic-Key musste in der .env stehen bleiben, sonst
startete der Bot nicht.
"""
import pytest

import config as config_mod


def _run_validate(monkeypatch, *, provider, anthropic_key, gemini_key):
    """validate_config() gegen die drei relevanten Felder, alle Wertebereich-
    Checks unangetastet (die echte .env liefert dort gültige Werte)."""
    monkeypatch.setattr(config_mod.config, "llm_provider", provider, raising=False)
    monkeypatch.setattr(config_mod.config, "anthropic_api_key", anthropic_key, raising=False)
    monkeypatch.setattr(config_mod.config, "gemini_api_key", gemini_key, raising=False)
    config_mod.validate_config()


def test_gemini_provider_does_not_require_anthropic_key(monkeypatch):
    # Kein Anthropic-Key, aber Gemini-Key vorhanden → muss durchlaufen.
    _run_validate(monkeypatch, provider="gemini", anthropic_key="", gemini_key="AIza-test")


def test_gemini_provider_requires_a_gemini_key(monkeypatch):
    with pytest.raises(SystemExit):
        _run_validate(monkeypatch, provider="gemini", anthropic_key="", gemini_key="")


def test_anthropic_provider_still_requires_anthropic_key(monkeypatch):
    with pytest.raises(SystemExit):
        _run_validate(monkeypatch, provider="anthropic", anthropic_key="", gemini_key="AIza-test")


def test_anthropic_provider_happy_path(monkeypatch):
    _run_validate(monkeypatch, provider="anthropic", anthropic_key="sk-ant-test", gemini_key="")
