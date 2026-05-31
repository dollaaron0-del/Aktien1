"""
MLX Prescreener — lokale LLM-Analyse via mlx-lm (Apple Silicon optimiert).

mlx-lm läuft 2–4× schneller als Ollama auf M-Series Chips weil es direkt
den Neural Engine und Unified Memory von Apple Silicon nutzt.

Setup auf dem Mac Mini:
    pip install mlx-lm
    mlx_lm.server --model mlx-community/Qwen2.5-32B-Instruct-4bit --port 8080

mlx-lm stellt einen OpenAI-kompatiblen API-Endpunkt bereit:
    POST http://localhost:8080/v1/chat/completions

Dieselbe Logik wie OllamaPrescreener — gleiche Methoden, gleiche Prompts,
gleiche Capability-Tiers — nur schneller auf Apple Silicon.

Aktivierung (.env):
    MLX_ENABLED=true
    MLX_URL=http://localhost:8080
    MLX_MODEL=qwen2.5:32b    # nur für Logging / capability detection
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

import requests

from analyzers.ollama_prescreener import (
    PrescreenResult,
    _ALWAYS_CLAUDE_SOURCES,
    _WEAK_MODEL_CLAUDE_SOURCES,
    _FULL_ANALYSIS_PROMPT,
    _FULL_ANALYSIS_PROMPT_32B,
    _PRESCREEN_PROMPT,
    _PRESCREEN_PROMPT_32B,
    _COMPRESS_PROMPT,
    _model_capability,
)
from logger import get_logger

log = get_logger(__name__)

_TIMEOUT_DEFAULT = 120   # MLX-Modelle brauchen beim ersten Aufruf länger


class MLXPrescreener:
    """
    Drop-in-Ersatz für OllamaPrescreener mit mlx-lm als Backend.
    Selbe öffentliche Schnittstelle: prescreen(), full_analysis(), compress_news(),
    is_available(), capability.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model: str = "qwen2.5:32b",
        timeout: int = _TIMEOUT_DEFAULT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model    = model
        self.timeout  = timeout
        self._available: Optional[bool] = None

    # ── Capability ────────────────────────────────────────────────────────────

    @property
    def capability(self) -> str:
        return _model_capability(self.model)

    # ── Verfügbarkeit ─────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=4)
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        if self._available:
            log.info("MLX-Server verfügbar @ %s (Modell: %s)", self.base_url, self.model)
        else:
            log.info("MLX-Server nicht erreichbar – Ollama/Claude als Fallback")
        return self._available

    def reset_availability_cache(self) -> None:
        self._available = None

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def prescreen(
        self,
        ticker: str,
        news_items: List[Dict],
        has_open_position: bool = False,
    ) -> PrescreenResult:
        if has_open_position:
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW", reason="",
                send_to_claude=True, skip_reason="", ollama_used=False, latency_ms=0,
            )

        always_sources = _ALWAYS_CLAUDE_SOURCES.copy()
        if self.capability in ("LOW", "MEDIUM"):
            always_sources |= _WEAK_MODEL_CLAUDE_SOURCES
        if any(
            any(src in (item.get("source") or "") for src in always_sources)
            for item in news_items
        ):
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW", reason="",
                send_to_claude=True, skip_reason="", ollama_used=False, latency_ms=0,
            )

        if not self.is_available():
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW",
                reason="MLX offline", send_to_claude=True, skip_reason="",
                ollama_used=False, latency_ms=0,
            )

        max_items = 25 if self.capability == "HIGH" else 15
        headlines = self._extract_headlines(news_items, max_items=max_items)
        prompt    = (
            _PRESCREEN_PROMPT_32B.format(ticker=ticker, headlines=headlines)
            if self.capability == "HIGH"
            else _PRESCREEN_PROMPT.format(ticker=ticker, headlines=headlines)
        )

        t0  = time.monotonic()
        raw = self._call(prompt, max_tokens=180)
        latency_ms = int((time.monotonic() - t0) * 1000)

        if raw is None:
            self.reset_availability_cache()
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW",
                reason="MLX-Fehler", send_to_claude=True, skip_reason="",
                ollama_used=False, latency_ms=latency_ms,
            )

        parsed = self._parse_json(raw, required=["score", "direction", "confidence"])
        if parsed is None:
            log.warning("MLX: ungültiges JSON für %s", ticker)
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW",
                reason="JSON-Fehler", send_to_claude=True, skip_reason="",
                ollama_used=True, latency_ms=latency_ms,
            )

        score      = float(parsed["score"])
        direction  = parsed["direction"].upper()
        confidence = parsed["confidence"].upper()
        reason     = str(parsed.get("reason", ""))

        if direction not in ("BULLISH", "NEUTRAL", "BEARISH"):
            direction = "NEUTRAL"
        if confidence not in ("HIGH", "MEDIUM", "LOW"):
            confidence = "LOW"
        score = max(0.0, min(1.0, score))

        send_to_claude, skip_reason = self._decide(score, direction, confidence)

        log.debug(
            "MLX [%s]: score=%.2f %s %s → %s (%dms)",
            ticker, score, direction, confidence,
            "→Claude" if send_to_claude else "SKIP", latency_ms,
        )
        return PrescreenResult(
            score=score, direction=direction, confidence=confidence,
            reason=reason, send_to_claude=send_to_claude, skip_reason=skip_reason,
            ollama_used=True, latency_ms=latency_ms,
        )

    def full_analysis(
        self,
        ticker: str,
        news_items: List[Dict],
        price_data: Optional[Dict] = None,
        buy_min_score: float = 0.70,
    ) -> Optional[Dict]:
        if not self.is_available() or not news_items:
            return None

        max_items   = 30 if self.capability == "HIGH" else 20
        headlines   = self._extract_headlines(news_items, max_items=max_items)
        price_block = self._format_price_block(price_data or {})

        if self.capability == "HIGH":
            prompt     = _FULL_ANALYSIS_PROMPT_32B.format(
                ticker=ticker, price_block=price_block, headlines=headlines)
            max_tokens = 500
        else:
            prompt     = _FULL_ANALYSIS_PROMPT.format(
                ticker=ticker, price_block=price_block, headlines=headlines)
            max_tokens = 350

        t0         = time.monotonic()
        raw        = self._call(prompt, max_tokens=max_tokens)
        latency_ms = int((time.monotonic() - t0) * 1000)

        if raw is None:
            self.reset_availability_cache()
            return None

        data = self._parse_full_response(raw, buy_min_score)
        if data is None:
            log.warning("[%s] MLX full_analysis: JSON-Parse fehlgeschlagen", ticker)
            return None

        log.info(
            "MLX full_analysis [%s]: %s score=%.2f %s (%dms)",
            ticker, data["recommendation"], data["sentiment_score"],
            data["confidence"], latency_ms,
        )
        return data

    def compress_news(
        self, ticker: str, news_items: List[Dict], max_items: int = 20
    ) -> Optional[str]:
        if not self.is_available() or not news_items:
            return None
        articles = self._format_articles(news_items[:max_items])
        prompt   = _COMPRESS_PROMPT.format(
            count=min(len(news_items), max_items),
            ticker=ticker, articles=articles,
        )
        t0  = time.monotonic()
        raw = self._call(prompt, max_tokens=500)
        log.debug("MLX compress [%s]: %d chars in %dms",
                  ticker, len(raw or ""), int((time.monotonic() - t0) * 1000))
        return raw if raw and len(raw) > 50 else None

    # ── Interne Hilfsmethoden ─────────────────────────────────────────────────

    def _call(self, prompt: str, max_tokens: int = 200) -> Optional[str]:
        """POST to mlx-lm OpenAI-compatible chat completions endpoint."""
        try:
            resp = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model":       self.model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  max_tokens,
                    "temperature": 0.1,
                    "stream":      False,
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                log.debug("MLX HTTP %d: %s", resp.status_code, resp.text[:200])
                return None
            data    = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip() if content else None
        except requests.Timeout:
            log.debug("MLX timeout (%ds) – erhöhe MLX_TIMEOUT in .env", self.timeout)
            return None
        except Exception as e:
            log.debug("MLX Fehler: %s", e)
            return None

    def _extract_headlines(self, news_items: List[Dict], max_items: int = 15) -> str:
        lines = []
        for item in news_items[:max_items]:
            src   = item.get("source", "")
            title = item.get("title") or item.get("text", "")[:80]
            lines.append(f"- [{src}] {title}")
        return "\n".join(lines)

    def _format_price_block(self, price_data: Dict) -> str:
        if not price_data.get("current_price"):
            return "No price data available."
        return "  ".join([
            f"Price: ${price_data.get('current_price')}",
            f"1w change: {price_data.get('price_change_1w')}%",
            f"1m change: {price_data.get('price_change_1m')}%",
            f"Sector: {price_data.get('sector', 'N/A')}",
            f"P/E: {price_data.get('pe_ratio', 'N/A')}",
        ])

    def _format_articles(self, news_items: List[Dict]) -> str:
        parts = []
        for i, item in enumerate(news_items, 1):
            title = item.get("title", "")
            text  = item.get("text", "")[:300]
            src   = item.get("source", "")
            parts.append(f"[{i}] {src}: {title}\n{text}")
        return "\n\n".join(parts)

    def _parse_json(self, raw: str, required: List[str]) -> Optional[Dict]:
        for attempt in (raw, raw[raw.find("{"):raw.rfind("}") + 1]):
            try:
                data = json.loads(attempt)
                if all(k in data for k in required):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    def _parse_full_response(self, raw: str, buy_min_score: float) -> Optional[Dict]:
        data = self._parse_json(raw, ["sentiment_score", "direction", "confidence"])
        if not data:
            return None
        try:
            score     = max(0.0, min(1.0, float(data.get("sentiment_score", 0.5))))
            direction = data.get("direction", "NEUTRAL").upper()
            if direction not in ("BULLISH", "NEUTRAL", "BEARISH"):
                direction = "NEUTRAL"
            confidence = data.get("confidence", "LOW").upper()
            if confidence not in ("HIGH", "MEDIUM", "LOW"):
                confidence = "LOW"
            rec = data.get("recommendation", "SKIP").upper()
            if rec not in ("BUY", "HOLD", "SKIP"):
                rec = "SKIP"
            if rec == "BUY" and (score < buy_min_score or direction != "BULLISH"):
                rec = "HOLD"
            hold = max(3, min(21, int(data.get("suggested_hold_days", 7))))
            return {
                "sentiment_score":     score,
                "direction":           direction,
                "confidence":          confidence,
                "recommendation":      rec,
                "suggested_hold_days": hold,
                "entry_rationale":     str(data.get("entry_rationale", ""))[:100],
                "stop_loss_note":      str(data.get("stop_loss_note", ""))[:80],
                "risk_factors":        [str(r) for r in data.get("risk_factors", [])[:3]],
                "key_catalysts":       [str(c) for c in data.get("key_catalysts", [])[:3]],
                "summary":             str(data.get("summary", ""))[:150],
            }
        except (TypeError, ValueError):
            return None

    def _decide(self, score: float, direction: str, confidence: str):
        """Same threshold logic as OllamaPrescreener._decide() for the capability tier."""
        cap = self.capability
        if cap == "HIGH":
            if confidence == "HIGH":
                if score < 0.45: return False, f"MLX 32b HIGH BEARISH ({score:.2f})"
                if score < 0.72: return False, f"MLX 32b HIGH NEUTRAL ({score:.2f})"
                return True, ""
            if confidence == "MEDIUM":
                if score < 0.52: return False, f"MLX 32b MEDIUM bearish ({score:.2f})"
                if score < 0.68: return False, f"MLX 32b MEDIUM mäßig ({score:.2f})"
                return True, ""
            if score < 0.38: return False, f"MLX 32b LOW bearish ({score:.2f})"
            return True, ""
        elif cap == "MEDIUM":
            if confidence == "HIGH":
                if score < 0.40: return False, f"MLX 14b HIGH BEARISH ({score:.2f})"
                if score < 0.67: return False, f"MLX 14b HIGH NEUTRAL ({score:.2f})"
                return True, ""
            if confidence == "MEDIUM":
                if score < 0.55: return False, f"MLX 14b MEDIUM ({score:.2f})"
                return True, ""
            if score < 0.35: return False, f"MLX 14b LOW bearish ({score:.2f})"
            return True, ""
        else:
            if confidence == "HIGH":
                if score < 0.38: return False, f"MLX 8b HIGH BEARISH ({score:.2f})"
                if score < 0.65: return False, f"MLX 8b HIGH NEUTRAL ({score:.2f})"
                return True, ""
            if confidence == "MEDIUM":
                if score < 0.58: return False, f"MLX 8b MEDIUM ({score:.2f})"
                return True, ""
            if score < 0.35: return False, f"MLX 8b LOW bearish ({score:.2f})"
            return True, ""
