"""
Adaptive Resource Manager — dynamisch RAM und Modellgröße anpassen.

Drei Ressourcentiers basierend auf macOS-Idle-Zeit und freiem RAM:

  PERFORMANCE  (Mac idle ≥ 5 Min + RAM frei ≥ 60%)
      → Größtes Ollama-Modell, maximale Caches, alle parallelen Jobs aktiv

  BALANCED     (Idle 1–5 Min oder RAM frei 30–60%)
      → Mittleres Modell, normale Caches

  MINIMAL      (Aktive Nutzung < 1 Min oder RAM frei < 30%)
      → Kleinstes Modell, Caches reduziert, weniger parallele Jobs

Idle-Erkennung (macOS):
    ioreg -c IOHIDSystem → HIDIdleTime in Nanosekunden

RAM-Erkennung:
    psutil.virtual_memory() → available / total

Ollama-Modell-Mapping (konfigurierbar via .env):
    PERFORMANCE → OLLAMA_MODEL_PERFORMANCE  (default: llama3.3:70b)
    BALANCED    → OLLAMA_MODEL_BALANCED     (default: qwen2.5:14b)
    MINIMAL     → OLLAMA_MODEL_MINIMAL      (default: llama3.1:8b)
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from logger import get_logger

log = get_logger(__name__)


class ResourceTier(str, Enum):
    PERFORMANCE = "PERFORMANCE"   # Mac idle, viel RAM frei
    BALANCED    = "BALANCED"      # Teils aktiv oder mittelmäßig RAM
    MINIMAL     = "MINIMAL"       # Mac aktiv genutzt oder RAM knapp


@dataclass
class ResourceState:
    tier:           ResourceTier
    idle_seconds:   float        # Sekunden seit letzter Nutzereingabe
    ram_free_pct:   float        # 0.0–1.0
    ram_free_gb:    float
    ram_total_gb:   float
    ollama_model:   str


# ── Schwellen ────────────────────────────────────────────────────────────────
_IDLE_PERFORMANCE_SEC  = 5 * 60    # 5 Minuten idle → PERFORMANCE
_IDLE_BALANCED_SEC     = 1 * 60    # 1 Minute idle → BALANCED
_RAM_PERFORMANCE_PCT   = 0.60      # ≥60% frei → PERFORMANCE
_RAM_MINIMAL_PCT       = 0.30      # <30% frei → MINIMAL

# Ollama-Modell je Tier (via .env überschreibbar)
_MODEL_PERFORMANCE = os.getenv("OLLAMA_MODEL_PERFORMANCE", "llama3.3:70b")
_MODEL_BALANCED    = os.getenv("OLLAMA_MODEL_BALANCED",    "qwen2.5:14b")
_MODEL_MINIMAL     = os.getenv("OLLAMA_MODEL_MINIMAL",     "llama3.1:8b")

TIER_MODELS = {
    ResourceTier.PERFORMANCE: _MODEL_PERFORMANCE,
    ResourceTier.BALANCED:    _MODEL_BALANCED,
    ResourceTier.MINIMAL:     _MODEL_MINIMAL,
}

# Cache-Größen je Tier (Anzahl Einträge in In-Memory Caches)
TIER_CACHE_SIZES = {
    ResourceTier.PERFORMANCE: 500,
    ResourceTier.BALANCED:    200,
    ResourceTier.MINIMAL:     80,
}

# Max gleichzeitige Analyse-Jobs je Tier
TIER_MAX_WORKERS = {
    ResourceTier.PERFORMANCE: 4,
    ResourceTier.BALANCED:    2,
    ResourceTier.MINIMAL:     1,
}


def _get_idle_seconds_macos() -> Optional[float]:
    """Reads HID idle time from IORegistry (macOS only)."""
    try:
        out = subprocess.check_output(
            ["ioreg", "-c", "IOHIDSystem"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode()
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                # format: "HIDIdleTime" = 12345678901
                ns = int(line.split("=")[-1].strip())
                return ns / 1e9   # nanoseconds → seconds
    except Exception:
        pass
    return None


def _get_idle_seconds_linux() -> Optional[float]:
    """Reads idle time from xprintidle (Linux, optional)."""
    try:
        out = subprocess.check_output(
            ["xprintidle"], stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
        return float(out) / 1000.0   # ms → seconds
    except Exception:
        pass
    return None


def get_idle_seconds() -> Optional[float]:
    """Returns seconds since last user input, or None if unavailable."""
    import platform
    if platform.system() == "Darwin":
        return _get_idle_seconds_macos()
    return _get_idle_seconds_linux()


def get_ram_state() -> tuple[float, float, float]:
    """Returns (free_pct, free_gb, total_gb)."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        free_pct  = vm.available / vm.total
        free_gb   = vm.available / (1024 ** 3)
        total_gb  = vm.total     / (1024 ** 3)
        return free_pct, free_gb, total_gb
    except Exception:
        return 0.5, 8.0, 16.0   # conservative fallback


class ResourceManager:
    """
    Singleton-artiger Manager — einmal instanziieren, dann überall nutzen.

    Beispiel::
        rm = get_resource_manager()
        state = rm.update()
        print(state.tier, state.ollama_model)
    """

    def __init__(self) -> None:
        self._current_tier: Optional[ResourceTier] = None
        self._last_check    = 0.0
        self._check_interval = 60   # Sekunden zwischen Updates

    def update(self, force: bool = False) -> ResourceState:
        """Recomputes resource tier. Returns current state."""
        now = time.time()
        if not force and (now - self._last_check) < self._check_interval:
            # Return cached tier without re-reading
            tier = self._current_tier or ResourceTier.BALANCED
            free_pct, free_gb, total_gb = get_ram_state()
            return ResourceState(
                tier=tier,
                idle_seconds=0,
                ram_free_pct=free_pct,
                ram_free_gb=free_gb,
                ram_total_gb=total_gb,
                ollama_model=TIER_MODELS[tier],
            )

        self._last_check = now
        idle  = get_idle_seconds()
        free_pct, free_gb, total_gb = get_ram_state()

        # Determine tier
        if idle is None:
            # Can't detect idle → assume user is active
            idle = 0.0

        if idle >= _IDLE_PERFORMANCE_SEC and free_pct >= _RAM_PERFORMANCE_PCT:
            tier = ResourceTier.PERFORMANCE
        elif idle < _IDLE_BALANCED_SEC or free_pct < _RAM_MINIMAL_PCT:
            tier = ResourceTier.MINIMAL
        else:
            tier = ResourceTier.BALANCED

        prev = self._current_tier
        self._current_tier = tier

        if prev != tier:
            log.info(
                "ResourceTier: %s → %s | idle=%.0fs | RAM frei %.0f%% (%.1fGB / %.1fGB)",
                prev, tier, idle, free_pct * 100, free_gb, total_gb,
            )

        return ResourceState(
            tier=tier,
            idle_seconds=idle,
            ram_free_pct=free_pct,
            ram_free_gb=free_gb,
            ram_total_gb=total_gb,
            ollama_model=TIER_MODELS[tier],
        )

    @property
    def current_tier(self) -> ResourceTier:
        return self._current_tier or ResourceTier.BALANCED

    def tier_changed(self, new_state: ResourceState) -> bool:
        return new_state.tier != (self._current_tier or ResourceTier.BALANCED)

    def apply_to_ollama(self, prescreener) -> None:
        """
        Updates the Ollama prescreener's active model to match current tier.
        Call after update() when tier changed.
        """
        state = self.update()
        if hasattr(prescreener, "model"):
            old = prescreener.model
            prescreener.model = state.ollama_model
            prescreener._available = None   # force availability re-check
            if old != state.ollama_model:
                log.info("Ollama-Modell gewechselt: %s → %s", old, state.ollama_model)

    def apply_to_caches(self) -> None:
        """
        Adjusts global in-memory caches to match current tier.
        Trims oversized caches — does not expand already-populated ones.
        """
        state = self.update()
        max_size = TIER_CACHE_SIZES[state.tier]
        trimmed = 0
        try:
            from collectors.adhoc_collector import _CACHE as _adhoc_cache
            _trim_dict_cache(_adhoc_cache, max_size)
            trimmed += 1
        except Exception:
            pass
        try:
            from collectors.reddit_hype_scanner import RedditHypeScanner  # noqa: F401 — just import to check
        except Exception:
            pass
        if trimmed:
            log.debug("Caches auf max_size=%d getrimmt (Tier=%s)", max_size, state.tier)

    def max_workers(self) -> int:
        return TIER_MAX_WORKERS[self.current_tier]


def _trim_dict_cache(cache: dict, max_size: int) -> None:
    """Remove oldest entries if cache exceeds max_size."""
    while len(cache) > max_size:
        try:
            oldest = next(iter(cache))
            del cache[oldest]
        except (StopIteration, RuntimeError):
            break


# Module-level singleton
_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    global _manager
    if _manager is None:
        _manager = ResourceManager()
    return _manager
