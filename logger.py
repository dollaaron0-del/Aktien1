"""
Structured Logging – JSON-Format für Produktion, Human-readable für Dev.

Konfiguration via .env:
  LOG_LEVEL=INFO        (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  LOG_FORMAT=json       (json | text, default: text)
  LOG_FILE=logs/bot.log (optional: zusätzlich in Datei loggen)

JSON-Format Beispiel:
  {"ts": "2026-05-19T10:23:45.123Z", "level": "INFO", "logger": "main",
   "msg": "Trade executed", "ticker": "AAPL", "latency_ms": 245}
"""
import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_LOG_LEVEL  = os.getenv("LOG_LEVEL",  "INFO").upper()
_LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()  # "json" | "text"
_LOG_FILE   = os.getenv("LOG_FILE",   "")


class StructuredJsonFormatter(logging.Formatter):
    """Formatiert Log-Records als JSON-Zeilen (JSON Lines / NDJSON)."""

    def format(self, record: logging.LogRecord) -> str:
        # Basis-Felder
        payload: Dict[str, Any] = {
            "ts":      datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        # Exception-Info
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Beliebige Extra-Felder (z.B. ticker=, latency_ms=)
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_") \
                    and key not in ("args", "msg", "message", "exc_info", "exc_text",
                                    "stack_info", "lineno", "funcName", "filename",
                                    "module", "created", "msecs", "relativeCreated",
                                    "thread", "threadName", "processName", "process",
                                    "levelname", "levelno", "name", "pathname"):
                payload[key] = val
        return json.dumps(payload, ensure_ascii=False, default=str)


class TimedLogger:
    """
    Context-Manager für latency-tracking:

    with timed_log(log, "Claude API call", ticker="AAPL") as t:
        result = analyzer.analyze(...)
    # Loggt automatisch: "Claude API call" mit latency_ms=245
    """
    def __init__(self, logger: logging.Logger, operation: str, **extra):
        self._log   = logger
        self._op    = operation
        self._extra = extra
        self._start = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = round((time.monotonic() - self._start) * 1000)
        level = logging.ERROR if exc_type else logging.INFO
        self._log.log(level, self._op, extra={**self._extra, "latency_ms": elapsed_ms})
        return False  # Exceptions nicht unterdrücken


def timed_log(logger: logging.Logger, operation: str, **extra) -> TimedLogger:
    """Shortcut für TimedLogger Context-Manager."""
    return TimedLogger(logger, operation, **extra)


def get_logger(name: str) -> logging.Logger:
    """
    Gibt einen Logger zurück. Einmalige Root-Konfiguration beim ersten Aufruf.
    Unterstützt LOG_FORMAT=json für strukturiertes Logging.
    """
    logger = logging.getLogger(name)

    # Root-Logger nur einmal konfigurieren
    root = logging.getLogger()
    if root.handlers:
        return logger

    level = getattr(logging, _LOG_LEVEL, logging.INFO)
    root.setLevel(level)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    if _LOG_FORMAT == "json":
        console_handler.setFormatter(StructuredJsonFormatter())
    else:
        fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
        console_handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

    root.addHandler(console_handler)

    # Optionaler File Handler
    if _LOG_FILE:
        try:
            os.makedirs(os.path.dirname(_LOG_FILE) or ".", exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(StructuredJsonFormatter())  # Datei immer JSON
            root.addHandler(file_handler)
        except Exception as e:
            root.warning("Logger: File-Handler fehlgeschlagen – %s", e)

    # Noisy third-party logger dämpfen
    for noisy in ("yfinance", "urllib3", "requests", "httpx", "praw"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger
