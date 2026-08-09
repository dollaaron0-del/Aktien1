"""
Tests für die VRAM-bewusste Modellwahl in system/resource_manager.py.

Grund: _has_inference_gpu() prüft nur Präsenz, nicht Größe — eine 8GB-Karte
(z.B. RTX 2070) galt bisher als "GPU vorhanden" und bekam denselben
qwen2.5:32b-Default wie ein Mac Mini M4 mit 32GB Unified Memory. Das Modell
ist weder lokal vorhanden noch VRAM-mäßig ladbar → reale 60s-Timeouts im
Live-Betrieb (Fund 9.8.2026, bot.log ab 30.7. nach dem Server-Umzug).

_select_default_models() ist bewusst als reine Funktion (keine I/O, keine
Modul-globals) herausgezogen — direkt parametrisiert testbar, ohne
importlib.reload(). Ein früherer Anlauf mit Modul-Reload hat andere
Tests verschmutzt: ResourceManager.update() löst TIER_MODELS über die
__globals__ des Moduls dynamisch auf, ein reload() in einer Testdatei
wirkt sich dadurch auf bereits importierte ResourceManager-Instanzen in
ANDEREN Testdateien aus (test_resource_manager_tier_override.py schlug
danach fehl). Reine Funktionen ohne globalen Zustand vermeiden das.
_detect_nvidia_vram_gb() bleibt separat getestet (reine subprocess-Parsing-
Funktion, kein globaler Zustand). Netzfrei, subprocess gemockt.
"""
import subprocess

from system.resource_manager import _detect_nvidia_vram_gb, _select_default_models


def _fail(*a, **kw):
    raise FileNotFoundError("nvidia-smi nicht vorhanden")


def test_detect_vram_parses_single_gpu(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"8192\n")
    assert _detect_nvidia_vram_gb() == 8.0


def test_detect_vram_takes_min_of_multiple_gpus(monkeypatch):
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **kw: b"8192\n24576\n"
    )
    assert _detect_nvidia_vram_gb() == 8.0


def test_detect_vram_returns_zero_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", _fail)
    assert _detect_nvidia_vram_gb() == 0.0


def test_detect_vram_returns_zero_on_empty_output(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"")
    assert _detect_nvidia_vram_gb() == 0.0


def test_detect_vram_returns_zero_on_garbage_output(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"N/A\n")
    assert _detect_nvidia_vram_gb() == 0.0


def test_cpu_only_ignores_darwin_and_vram():
    result = _select_default_models(
        cpu_only=True, is_darwin=True, vram_gb=48.0, model_cpu="llama3.2:3b"
    )
    assert result == ("llama3.2:3b", "llama3.2:3b", "llama3.2:3b")


def test_darwin_keeps_large_defaults_regardless_of_vram():
    result = _select_default_models(
        cpu_only=False, is_darwin=True, vram_gb=0.0, model_cpu="llama3.2:3b"
    )
    assert result == ("qwen2.5:32b", "qwen2.5:14b", "llama3.1:8b")


def test_8gb_card_gets_14b_not_32b():
    # RTX 2070 8GB — der konkrete Produktionsfall vom 9.8.2026
    result = _select_default_models(
        cpu_only=False, is_darwin=False, vram_gb=8.0, model_cpu="llama3.2:3b"
    )
    assert result == ("qwen2.5:14b", "qwen2.5:14b", "llama3.1:8b")


def test_24gb_card_gets_32b():
    result = _select_default_models(
        cpu_only=False, is_darwin=False, vram_gb=24.0, model_cpu="llama3.2:3b"
    )
    assert result == ("qwen2.5:32b", "qwen2.5:14b", "llama3.1:8b")


def test_boundary_exactly_20gb_gets_32b():
    result = _select_default_models(
        cpu_only=False, is_darwin=False, vram_gb=20.0, model_cpu="llama3.2:3b"
    )
    assert result[0] == "qwen2.5:32b"


def test_boundary_just_under_20gb_gets_14b():
    result = _select_default_models(
        cpu_only=False, is_darwin=False, vram_gb=19.9, model_cpu="llama3.2:3b"
    )
    assert result[0] == "qwen2.5:14b"


def test_boundary_exactly_6gb_gets_14b():
    result = _select_default_models(
        cpu_only=False, is_darwin=False, vram_gb=6.0, model_cpu="llama3.2:3b"
    )
    assert result[0] == "qwen2.5:14b"


def test_gpu_present_but_vram_unreadable_falls_back_to_cpu_model():
    # nvidia-smi erkannt (GPU vorhanden), aber --query-gpu liefert nichts
    # Verwertbares (z.B. alte Treiberversion) → 0.0GB → sichere CPU-Seite
    result = _select_default_models(
        cpu_only=False, is_darwin=False, vram_gb=0.0, model_cpu="llama3.2:3b"
    )
    assert result == ("llama3.2:3b", "llama3.2:3b", "llama3.2:3b")
