"""Zentraler LLM-Zugang für den Bot – ein Umschalter zwischen Anthropic (Claude)
und Google Gemini.

Warum: Bis hierher rief jedes Modul `anthropic.Anthropic().messages.create(...)`
direkt. Um den teuren Claude-API-Verbrauch auf Gemini zu verlagern (ohne jede
Aufrufstelle einzeln umzuschreiben), geht ab jetzt alles über
`create_message(...)`. Die Rückgabe ahmt die Form einer Anthropic-Message nach
(`resp.content[0].text`, `resp.usage.input_tokens/output_tokens/
cache_read_input_tokens`), damit der bestehende Auswerte-/Kosten-Code
unverändert weiterläuft.

Schalter: `LLM_PROVIDER` in der .env.
  - "anthropic" (Default) → identisches Verhalten wie vorher, kein Risiko.
  - "gemini"              → Google Gemini via google-genai.

Modell-Mapping (nur im Gemini-Pfad): ein Claude-Modellname mit "haiku" darin
→ GEMINI_MODEL_LIGHT, sonst → GEMINI_MODEL. So bleibt das bestehende
Sonnet/Haiku-Tiering (config.claude_model / claude_model_light) erhalten.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from logger import get_logger

log = get_logger(__name__)

SystemArg = Union[str, List[Dict[str, Any]], None]


# ── Anthropic-kompatible Antwort-Hülle ───────────────────────────────────────

@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class _Msg:
    """Quakt wie eine anthropic.types.Message, soweit der Bot sie anfasst."""

    def __init__(self, text: str, usage: _Usage, model: str):
        self.content: List[_TextBlock] = [_TextBlock(text or "")]
        self.usage = usage
        self.model = model
        self.stop_reason = "end_turn"


# ── Provider-Auswahl ────────────────────────────────────────────────────────

def _provider() -> str:
    try:
        from config import config as _cfg
        p = getattr(_cfg, "llm_provider", "") or os.getenv("LLM_PROVIDER", "anthropic")
    except Exception:
        p = os.getenv("LLM_PROVIDER", "anthropic")
    return (p or "anthropic").strip().lower()


def available() -> bool:
    """True, wenn der aktive Provider einen Key hat. Ersetzt die bisherigen
    `bool(config.anthropic_api_key)`-Checks, damit die Verfügbarkeits-Logik
    auch im Gemini-Betrieb stimmt."""
    if _provider() == "gemini":
        return bool(_gemini_key())
    return bool(os.getenv("ANTHROPIC_API_KEY", "") or _cfg_attr("anthropic_api_key"))


def client_or_none() -> Optional["object"]:
    """Für Module, die bisher `self._client = anthropic.Anthropic(...) if key
    else None` gemacht haben: gibt ein truthy Sentinel zurück, wenn der aktive
    Provider nutzbar ist, sonst None."""
    import sys
    return sys.modules[__name__] if available() else None


def _cfg_attr(name: str, default: str = "") -> str:
    try:
        from config import config as _cfg
        return getattr(_cfg, name, default) or default
    except Exception:
        return default


def _gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "") or _cfg_attr("gemini_api_key")


def _map_model(claude_model: str) -> str:
    m = (claude_model or "").lower()
    if "haiku" in m or "flash" in m or "light" in m:
        return _cfg_attr("gemini_model_light", "gemini-3.1-flash-lite")
    return _cfg_attr("gemini_model", "gemini-3.1-pro-preview")


def _flatten_system(system: SystemArg) -> Optional[str]:
    if not system:
        return None
    if isinstance(system, str):
        return system
    # Anthropic-Blockliste [{"type": "text", "text": "...", "cache_control": ...}]
    parts = [str(b.get("text", "")) for b in system if isinstance(b, dict) and b.get("text")]
    return "\n\n".join(p for p in parts if p) or None


def _user_text(messages: List[Dict[str, Any]]) -> str:
    """Der Bot schickt durchweg genau eine User-Nachricht mit String-Inhalt."""
    parts: List[str] = []
    for msg in messages or []:
        if msg.get("role") not in (None, "user"):
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("text")
            )
    return "\n\n".join(p for p in parts if p)


# ── Öffentlicher Aufruf ─────────────────────────────────────────────────────

def create_message(
    *,
    model: str,
    max_tokens: int,
    messages: List[Dict[str, Any]],
    system: SystemArg = None,
    api_key: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
    **_ignored: Any,
) -> _Msg:
    """Ein LLM-Turn. Signatur bewusst wie `client.messages.create(...)`.

    Im Anthropic-Pfad wird 1:1 durchgereicht (identisches Verhalten wie vorher).
    Im Gemini-Pfad übersetzt.
    """
    if _provider() == "gemini":
        return _create_gemini(
            model=model, max_tokens=max_tokens, messages=messages, system=system
        )
    return _create_anthropic(
        model=model, max_tokens=max_tokens, messages=messages, system=system,
        api_key=api_key, extra_headers=extra_headers,
    )


def _create_anthropic(
    *, model, max_tokens, messages, system, api_key, extra_headers,
) -> _Msg:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY", ""))
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system is not None:
        kwargs["system"] = system
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    resp = client.messages.create(**kwargs)
    # Native anthropic-Antwort hat schon die richtige Form – direkt
    # zurückgeben spart eine Umkopie und hält alle Randfelder erhalten.
    return resp  # type: ignore[return-value]


def _create_gemini(*, model, max_tokens, messages, system) -> _Msg:
    from google import genai
    from google.genai import types

    key = _gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY fehlt – LLM_PROVIDER=gemini gesetzt, aber kein Key.")

    gmodel = _map_model(model)
    client = genai.Client(api_key=key)
    cfg = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        system_instruction=_flatten_system(system),
    )
    resp = client.models.generate_content(
        model=gmodel,
        contents=_user_text(messages),
        config=cfg,
    )

    text = getattr(resp, "text", "") or ""
    um = getattr(resp, "usage_metadata", None)
    usage = _Usage(
        input_tokens=int(getattr(um, "prompt_token_count", 0) or 0),
        output_tokens=int(getattr(um, "candidates_token_count", 0) or 0),
        cache_read_input_tokens=int(getattr(um, "cached_content_token_count", 0) or 0),
    )
    if not text.strip():
        # Gleiche Signal-Form wie ein leerer Claude-Content: Aufrufer werfen
        # dann ihren "keine Antwort"-Fehler.
        log.warning("Gemini (%s) lieferte leeren Text zurück", gmodel)
    return _Msg(text=text, usage=usage, model=gmodel)
