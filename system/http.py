"""
system/http.py – gemeinsame HTTP-Session mit Retry/Backoff + Connection-Pooling.

Bisher baute jeder Collector eigene `requests.get/post`-Aufrufe ohne Retry:
ein einzelner 429/Timeout/5xx ließ die Quelle für den Ticker still `[]` liefern.
Diese zentrale Session fängt transiente Fehler automatisch ab (Retry mit
exponentiellem Backoff, respektiert Retry-After) und teilt einen Connection-Pool
über alle Collector.

Verwendung (Drop-in für requests.get/post):

    from system.http import http_get, http_post
    resp = http_get(url, headers=..., params=..., timeout=...)

`timeout` wird auf einen Default gesetzt, falls nicht angegeben – ein Request
ohne Timeout könnte den ganzen Collector-Thread blockieren.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

try:                                                  # urllib3 ≥ 1.26
    from urllib3.util.retry import Retry
except Exception:                                     # pragma: no cover - alte urllib3
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

_DEFAULT_TIMEOUT = 15          # Sekunden – Schutz gegen hängende Collector-Threads
_RETRY_TOTAL     = 3
_BACKOFF_FACTOR  = 0.6         # Wartezeiten: 0.6s, 1.2s, 2.4s
_STATUS_FORCELIST = (429, 500, 502, 503, 504)

_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _build_retry() -> Retry:
    """Retry-Strategie – kompatibel zu alten (method_whitelist) und neuen
    (allowed_methods) urllib3-Versionen."""
    common = dict(
        total=_RETRY_TOTAL,
        connect=_RETRY_TOTAL,
        read=_RETRY_TOTAL,
        backoff_factor=_BACKOFF_FACTOR,
        status_forcelist=_STATUS_FORCELIST,
        respect_retry_after_header=True,
        raise_on_status=False,     # finalen Status zurückgeben statt zu werfen
    )
    methods = frozenset(["GET", "POST"])  # POSTs hier sind Lese-Queries (idempotent)
    try:
        return Retry(allowed_methods=methods, **common)
    except TypeError:              # ältere urllib3
        return Retry(method_whitelist=methods, **common)


def get_session() -> requests.Session:
    """Liefert die gemeinsame, retry-fähige Session (lazy, thread-safe)."""
    global _session
    if _session is not None:
        return _session
    with _lock:
        if _session is None:
            s = requests.Session()
            adapter = HTTPAdapter(
                max_retries=_build_retry(),
                pool_connections=20,
                pool_maxsize=20,
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
    return _session


def http_get(url: str, **kwargs) -> requests.Response:
    """Drop-in für requests.get mit Retry/Backoff + Pooling."""
    kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
    return get_session().get(url, **kwargs)


def http_post(url: str, **kwargs) -> requests.Response:
    """Drop-in für requests.post mit Retry/Backoff + Pooling."""
    kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
    return get_session().post(url, **kwargs)


def sec_user_agent() -> str:
    """User-Agent für SEC-EDGAR-Anfragen. Die SEC verlangt einen echten Kontakt
    (Name + E-Mail), sonst drohen Drosselung/IP-Block. Per `SEC_CONTACT_EMAIL`
    in der .env setzen – der Platzhalter-Default ist nur eine Notlösung."""
    contact = os.getenv("SEC_CONTACT_EMAIL", "").strip()
    if not contact:
        contact = "set-SEC_CONTACT_EMAIL@example.com"
    return f"AktienBot/1.0 ({contact})"
