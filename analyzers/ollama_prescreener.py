"""
Ollama Pre-Screener – lokale KI-Vorfilterung (Linux/macOS, CPU oder GPU).

Funktionsweise:
  1. Ollama liest alle Nachrichtenartikel für einen Ticker
  2. Gibt Score 0–1 + Richtung + Konfidenz zurück
  3. Nur wenn Signal unklar ODER bullisch → Claude wird gerufen
  4. Klar bearisch/neutral mit hoher Konfidenz → Claude wird GESPART

Empfohlene Modelle:
  CPU-only, < 16 GB RAM : llama3.2:3b     (~2 GB,  ~5–15s pro Analyse)
  CPU-only, 16–24 GB    : llama3.1:8b     (~5 GB,  ~30–60s pro Analyse)
  CPU-only, 24+ GB      : qwen2.5:14b     (~9 GB,  ~60–90s pro Analyse)
  GPU (8+ GB VRAM)      : llama3.3:70b    (~40 GB, ~5–10s pro Analyse)

Fallback:
  - Ollama offline / Timeout → Claude übernimmt automatisch
  - Kein Qualitätsverlust durch Ausfall
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

import requests

from logger import get_logger

log = get_logger(__name__)

# Quellen die IMMER zu Claude gehen – zu wichtig für lokale Vorfilterung
_ALWAYS_CLAUDE_SOURCES = {
    "SEC 8-K",
    "Analyst Rating",
    "Benzinga Analyst",
    "Earnings Call Transcript",
    "13F Institutional",
    "Short Interest",
}

# Einfacher, zuverlässiger Prompt – kurze Antwort für kleinere Modelle
_PRESCREEN_PROMPT = """You are a financial news analyst. Analyze these news headlines about {ticker}.

NEWS:
{headlines}

Return ONLY this JSON (no explanation, no markdown):
{{"score": <0.0-1.0>, "direction": "<BULLISH|NEUTRAL|BEARISH>", "confidence": "<HIGH|MEDIUM|LOW>", "reason": "<10 words max>"}}

Score guide: 0.0=very bearish, 0.5=neutral, 1.0=very bullish.
HIGH confidence only when signal is clear and consistent."""


@dataclass
class PrescreenResult:
    score: float
    direction: str          # BULLISH | NEUTRAL | BEARISH
    confidence: str         # HIGH | MEDIUM | LOW
    reason: str
    send_to_claude: bool    # True = Claude needed, False = skip Claude
    skip_reason: str        # Warum Claude übersprungen wird (für Logging)
    ollama_used: bool       # False wenn Ollama offline (Fallback)
    latency_ms: int


class OllamaPrescreener:
    """
    Lokaler KI-Vorfilter auf Apple Silicon.
    Entscheidet ob Claude für einen Ticker gerufen werden muss.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        timeout: int = 30,
        # Schwellen für "Claude überspringen"
        bearish_skip_threshold: float = 0.35,   # Score unter diesem Wert = klar bearish
        neutral_skip_threshold: float = 0.55,   # Score unter diesem Wert = neutral
    ):
        self.base_url         = base_url.rstrip("/")
        self.model            = model
        self.timeout          = timeout
        self.bearish_skip     = bearish_skip_threshold
        self.neutral_skip     = neutral_skip_threshold
        self._available: Optional[bool] = None   # Cache, einmal prüfen

    # ── Verfügbarkeit ─────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Prüft ob Ollama läuft. Ergebnis wird gecacht."""
        if self._available is not None:
            return self._available
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        if not self._available:
            log.info("Ollama nicht erreichbar – Claude übernimmt alle Analysen.")
        else:
            log.info("Ollama verfügbar: %s @ %s", self.model, self.base_url)
        return self._available

    def reset_availability_cache(self) -> None:
        """Nach Verbindungsfehler zurücksetzen – nächster Aufruf prüft neu."""
        self._available = None

    # ── Haupt-Methode ─────────────────────────────────────────────────────────

    def prescreen(
        self,
        ticker: str,
        news_items: List[Dict],
        has_open_position: bool = False,
    ) -> PrescreenResult:
        """
        Analysiert Nachrichten lokal.
        Gibt PrescreenResult zurück mit send_to_claude=True/False.
        """
        t0 = time.monotonic()

        # Offene Positionen IMMER zu Claude – Thesis-Check ist kritisch
        if has_open_position:
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW", reason="",
                send_to_claude=True,
                skip_reason="",
                ollama_used=False,
                latency_ms=0,
            )

        # Hochprioritäre Quellen IMMER zu Claude
        high_priority = [
            item for item in news_items
            if any(src in (item.get("source") or "") for src in _ALWAYS_CLAUDE_SOURCES)
        ]
        if high_priority:
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW", reason="",
                send_to_claude=True,
                skip_reason="",
                ollama_used=False,
                latency_ms=0,
            )

        # Ollama verfügbar?
        if not self.is_available():
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW",
                reason="Ollama offline",
                send_to_claude=True,
                skip_reason="",
                ollama_used=False,
                latency_ms=0,
            )

        # Nachrichten auf Schlagzeilen reduzieren (Modell-Token sparen)
        headlines = self._extract_headlines(news_items)
        prompt    = _PRESCREEN_PROMPT.format(ticker=ticker, headlines=headlines)

        raw = self._call_ollama(prompt)
        latency_ms = int((time.monotonic() - t0) * 1000)

        if raw is None:
            # Ollama-Aufruf fehlgeschlagen → Claude übernimmt
            self.reset_availability_cache()
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW",
                reason="Ollama-Fehler",
                send_to_claude=True,
                skip_reason="",
                ollama_used=False,
                latency_ms=latency_ms,
            )

        parsed = self._parse_ollama_response(raw)
        if parsed is None:
            log.warning("Ollama: ungültiges JSON für %s – Claude übernimmt", ticker)
            return PrescreenResult(
                score=0.5, direction="NEUTRAL", confidence="LOW",
                reason="JSON-Fehler",
                send_to_claude=True,
                skip_reason="",
                ollama_used=True,
                latency_ms=latency_ms,
            )

        score      = parsed["score"]
        direction  = parsed["direction"]
        confidence = parsed["confidence"]
        reason     = parsed["reason"]

        send_to_claude, skip_reason = self._decide(score, direction, confidence)

        log.debug(
            "Ollama [%s]: score=%.2f %s %s → %s (%dms)",
            ticker, score, direction, confidence,
            "→Claude" if send_to_claude else "SKIP", latency_ms,
        )

        return PrescreenResult(
            score=score,
            direction=direction,
            confidence=confidence,
            reason=reason,
            send_to_claude=send_to_claude,
            skip_reason=skip_reason,
            ollama_used=True,
            latency_ms=latency_ms,
        )

    # ── Entscheidungslogik ────────────────────────────────────────────────────

    def _decide(self, score: float, direction: str, confidence: str) -> Tuple[bool, str]:
        """
        Entscheidet ob Claude gerufen werden soll.
        Konservativ: Im Zweifel immer Claude.
        """
        # Niedrige Konfidenz → Claude (sicherer)
        if confidence == "LOW":
            return True, ""

        # Klar bearisch mit hoher/mittlerer Konfidenz → Claude sparen
        if score < self.bearish_skip and confidence in ("HIGH", "MEDIUM"):
            return False, f"Ollama: klar BEARISH ({score:.2f}) – HOLD/SKIP ohne Claude"

        # Klar neutral mit hoher Konfidenz → Claude sparen
        if score < self.neutral_skip and confidence == "HIGH":
            return False, f"Ollama: NEUTRAL ({score:.2f}, HIGH) – kein Trade-Signal"

        # Alles andere → Claude (bullische Signale, Unsicherheit)
        return True, ""

    # ── Ollama API ────────────────────────────────────────────────────────────

    def _call_ollama(self, prompt: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model":  self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,    # Deterministisch für JSON
                        "num_predict": 120,    # Kurze Antwort reicht
                    },
                },
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
            log.warning("Ollama HTTP %d für Analyse", resp.status_code)
            return None
        except requests.Timeout:
            log.warning("Ollama Timeout nach %ds", self.timeout)
            return None
        except Exception as e:
            log.warning("Ollama Fehler: %s", e)
            return None

    def _parse_ollama_response(self, raw: str) -> Optional[Dict]:
        """JSON aus Ollama-Antwort extrahieren – robust gegen Formatfehler."""
        # Direktes JSON
        try:
            data = json.loads(raw)
            return self._validate_fields(data)
        except json.JSONDecodeError:
            pass

        # JSON aus Text extrahieren (Modell hat manchmal Präfix-Text)
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start:end])
                return self._validate_fields(data)
            except (json.JSONDecodeError, KeyError):
                pass

        return None

    def _validate_fields(self, data: Dict) -> Optional[Dict]:
        score     = float(data.get("score", -1))
        direction = data.get("direction", "").upper()
        confidence= data.get("confidence", "").upper()
        reason    = str(data.get("reason", ""))

        if not (0.0 <= score <= 1.0):
            return None
        if direction not in ("BULLISH", "NEUTRAL", "BEARISH"):
            direction = "NEUTRAL"
        if confidence not in ("HIGH", "MEDIUM", "LOW"):
            confidence = "LOW"

        return {"score": score, "direction": direction,
                "confidence": confidence, "reason": reason}

    def _extract_headlines(self, news_items: List[Dict], max_items: int = 15) -> str:
        """Kompakte Schlagzeilen für Ollama-Prompt."""
        lines = []
        for item in news_items[:max_items]:
            title  = (item.get("title") or "")[:100]
            source = (item.get("source") or "")[:30]
            date   = (item.get("published_at") or "")[:10]
            prio   = " [HIGH]" if item.get("priority") == "HIGH" else ""
            lines.append(f"- [{date}] {source}{prio}: {title}")
        return "\n".join(lines)

    # ── Modell-Empfehlungen ───────────────────────────────────────────────────

    @staticmethod
    def recommended_model(ram_gb: int) -> str:
        """Gibt empfohlenes Ollama-Modell basierend auf verfügbarem RAM zurück."""
        if ram_gb >= 32:
            return "llama3.3:70b"    # Near-Claude Qualität, ~40 GB
        if ram_gb >= 24:
            return "qwen2.5:14b"     # Sehr gut, ~9 GB
        if ram_gb >= 16:
            return "llama3.1:8b"     # Gut, ~5 GB
        return "llama3.2:3b"         # Minimal, ~2 GB
