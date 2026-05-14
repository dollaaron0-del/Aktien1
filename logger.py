"""
Zentrales Logging-Modul.
- Rotating File Handler: data/logs/bot.log (max 5 MB, 3 Backups)
- Console-Handler mit Rich-Formatting
- Alle Module importieren: from logger import get_logger; log = get_logger(__name__)
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

_LOG_DIR  = os.path.join(os.path.dirname(__file__), "data", "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "bot.log")
_FMT      = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def _init_root() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    os.makedirs(_LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # ── Rotating file handler (DEBUG and above) ──────────────────────────────
    fh = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    root.addHandler(fh)

    # ── Console handler (INFO and above) ─────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    root.addHandler(ch)

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "requests", "yfinance", "peewee", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a module-level logger, initializing root if needed."""
    _init_root()
    return logging.getLogger(name or "bot")
