"""
Tests für ResourceManager.force_tier()/clear_forced_tier().

Grund: Die automatische Idle-/RAM-Tier-Erkennung erreicht PERFORMANCE auf
einem headless Linux-Server nie (get_idle_seconds() liefert ohne
Desktop-Session immer None, wird als 0 behandelt → _IDLE_PERFORMANCE_SEC=300
kann nie erreicht werden). Für die geplanten Analysezyklen (bot/runner.py::
safe_run_analysis_cycle) wird PERFORMANCE deshalb explizit erzwungen. Diese
Tests decken nur den Override-Mechanismus selbst ab (nicht die Aufrufstelle
in runner.py, die reale Ollama-Instanzen/laufende Zyklen bräuchte).
"""
from system.resource_manager import ResourceManager, ResourceTier, TIER_MODELS


def test_force_tier_overrides_result_regardless_of_idle_ram(monkeypatch):
    # idle=0 (wie auf einem headless Server) und wenig freies RAM würden ohne
    # force_tier() zu MINIMAL führen – force_tier() muss das übersteuern.
    monkeypatch.setattr(
        "system.resource_manager.get_idle_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        "system.resource_manager.get_ram_state", lambda: (0.10, 1.0, 16.0)
    )
    rm = ResourceManager()
    rm.force_tier(ResourceTier.PERFORMANCE)
    state = rm.update(force=True)
    assert state.tier == ResourceTier.PERFORMANCE
    assert state.ollama_model == TIER_MODELS[ResourceTier.PERFORMANCE]


def test_clear_forced_tier_restores_normal_logic(monkeypatch):
    monkeypatch.setattr(
        "system.resource_manager.get_idle_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        "system.resource_manager.get_ram_state", lambda: (0.10, 1.0, 16.0)
    )
    rm = ResourceManager()
    rm.force_tier(ResourceTier.PERFORMANCE)
    rm.clear_forced_tier()
    state = rm.update(force=True)
    # idle=0 + wenig RAM frei → normale Logik ergibt MINIMAL, nicht PERFORMANCE
    assert state.tier == ResourceTier.MINIMAL


def test_current_tier_reflects_forced_tier_immediately(monkeypatch):
    # force_tier() setzt _current_tier sofort, ohne auf update() zu warten –
    # relevant weil runner.py apply_to_ollama() direkt danach aufruft.
    rm = ResourceManager()
    rm.force_tier(ResourceTier.PERFORMANCE)
    assert rm.current_tier == ResourceTier.PERFORMANCE
