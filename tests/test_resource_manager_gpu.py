"""
Tests für die GPU-Erkennung in system/resource_manager.py (Roadmap 6.5a).

Diese Erkennung ist der stille Umschalter für den geplanten Server-Umzug:
auf einem headless-Linux-CPU-Host laufen kleine Ollama-Modelle (aktuell
llama3.2:3b), sobald eine GPU erkannt wird, schaltet TIER_MODELS automatisch
auf die großen Defaults (qwen2.5:32b/14b) um — OHNE Codeänderung, rein über
_has_inference_gpu(). Bisher ungetestet, obwohl das genau die Stelle ist,
die beim Umzug "einfach funktionieren" soll. Kern-Zusagen: (1) Apple Silicon
(Darwin) gilt immer als GPU-fähig. (2) OLLAMA_FORCE_GPU erzwingt True, auch
ohne echte GPU. (3) Eine per nvidia-smi sichtbare NVIDIA-GPU gilt als
vorhanden. (4) Ohne all das (Linux, kein nvidia-smi, kein Override) → False,
damit CPU-Hosts nicht versehentlich ein zu großes Modell laden. Netzfrei,
subprocess/platform/env gemockt.
"""
import subprocess

from system.resource_manager import _has_inference_gpu


def test_darwin_always_gpu_capable(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    assert _has_inference_gpu() is True


def test_force_gpu_env_overrides_on_linux_without_nvidia(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("OLLAMA_FORCE_GPU", "1")

    def _fail(*a, **kw):
        raise FileNotFoundError("nvidia-smi nicht vorhanden")
    monkeypatch.setattr(subprocess, "check_output", _fail)
    assert _has_inference_gpu() is True


def test_nvidia_smi_success_means_gpu(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("OLLAMA_FORCE_GPU", raising=False)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"GPU 0: Tesla T4")
    assert _has_inference_gpu() is True


def test_linux_without_nvidia_or_override_is_cpu_only(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("OLLAMA_FORCE_GPU", raising=False)

    def _fail(*a, **kw):
        raise FileNotFoundError("nvidia-smi nicht vorhanden")
    monkeypatch.setattr(subprocess, "check_output", _fail)
    assert _has_inference_gpu() is False


def test_nvidia_smi_timeout_falls_back_to_false(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("OLLAMA_FORCE_GPU", raising=False)

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3)
    monkeypatch.setattr(subprocess, "check_output", _timeout)
    assert _has_inference_gpu() is False


def test_force_gpu_env_falsy_values_do_not_force(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("OLLAMA_FORCE_GPU", "false")

    def _fail(*a, **kw):
        raise FileNotFoundError("nvidia-smi nicht vorhanden")
    monkeypatch.setattr(subprocess, "check_output", _fail)
    assert _has_inference_gpu() is False
