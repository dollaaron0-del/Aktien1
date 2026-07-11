"""
Tests für den Collector-Abschalt-Mechanismus (Kill-Switch via
config.collectors_disabled): deaktivierte Quellen werden auf None gesetzt,
bleiben aber als 0-Einträge im sources_breakdown sichtbar.
"""
import types

from config import Config


def test_config_default_disables_nothing(monkeypatch):
    # Die früheren Default-Abschaltungen (reddit/patents/earn_transcripts/
    # aaii_sentiment, N3-Befund 5.7.2026) wurden im Juli 2026 komplett
    # entfernt — der Default ist seitdem eine leere Liste.
    monkeypatch.delenv("COLLECTORS_DISABLED", raising=False)
    c = Config()
    assert c.collectors_disabled == []


def test_config_env_override_and_normalization(monkeypatch):
    monkeypatch.setenv("COLLECTORS_DISABLED", "Yahoo, wire ,")
    c = Config()
    assert c.collectors_disabled == ["yahoo", "wire"]


def test_config_empty_disables_nothing(monkeypatch):
    monkeypatch.setenv("COLLECTORS_DISABLED", "")
    c = Config()
    assert c.collectors_disabled == []


def test_make_collectors_nulls_disabled_sources(monkeypatch):
    """_make_collectors setzt deaktivierte Quellen auf None (nicht entfernen),
    damit sie im sources_breakdown weiter mit 0 auftauchen."""
    import bot.runner as runner_mod

    class _DummyCollector:
        available = True

        def __init__(self, *a, **k):
            pass

        def collect(self, ticker, *a, **k):
            return []

    for name in dir(runner_mod):
        if name.endswith("Collector"):
            monkeypatch.setattr(runner_mod, name, _DummyCollector)
    monkeypatch.setattr(
        runner_mod.config, "collectors_disabled", ["newsapi", "wire"], raising=False
    )

    cols = runner_mod._make_collectors()
    assert cols["newsapi"] is None
    assert cols["wire"] is None
    assert cols["yahoo"] is not None
    assert cols["stocktwits"] is not None  # nicht in der Liste → aktiv
