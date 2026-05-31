"""
Ollama Pre-Screener – lokale KI-Vorfilterung (Linux/macOS, CPU oder GPU).

Funktionsweise:
  1. Ollama liest alle Nachrichtenartikel für einen Ticker
  2. Gibt Score 0–1 + Richtung + Konfidenz zurück
  3. Nur wenn Signal unklar ODER bullisch → Claude wird gerufen
  4. Klar bearisch/neutral mit hoher Konfidenz → Claude wird GESPART

Smart Frugal Mode (automatisch, modellabhängig):
  8b  (MINIMAL tier) : konservative Schwellen, Claude-Fallback bei Unsicherheit
  14b (BALANCED tier): mittlere Schwellen
  32b (PERFORMANCE)  : aggressivere Filterung, reicherer Prompt, höhere Eigenständigkeit

Empfohlene Modelle (Mac Mini M4):
  16 GB RAM : llama3.1:8b     (~5 GB,  ~30–60s pro Analyse)
  24 GB RAM : qwen2.5:14b     (~9 GB,  ~60–90s pro Analyse)
  32 GB RAM : qwen2.5:32b     (~20 GB, ~90–120s pro Analyse) ← beste Qualität
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

# Quellen die IMMER zu Claude gehen, unabhängig vom Modell
# (nur wirklich material-kritische Events)
_ALWAYS_CLAUDE_SOURCES = {
    "SEC 8-K",
    "Earnings Call Transcript",
}

# Zusätzliche Always-Claude-Quellen für schwache Modelle (8b/14b)
# Bei 32b-Modellen werden diese lokal verarbeitet
_WEAK_MODEL_CLAUDE_SOURCES = {
    "Analyst Rating",
    "Benzinga Analyst",
    "13F Institutional",
    "Short Interest",
}


def _model_capability(model_name: str) -> str:
    """Returns 'HIGH' (32b+), 'MEDIUM' (14b), or 'LOW' (≤8b) based on model name."""
    name = model_name.lower()
    # Check for explicit size indicators
    for size in ("70b", "72b", "65b", "34b", "32b", "30b"):
        if size in name:
            return "HIGH"
    for size in ("14b", "13b", "12b", "11b"):
        if size in name:
            return "MEDIUM"
    return "LOW"

# Komprimierungs-Prompt: News-Artikel → strukturiertes Kurzgutachten für Claude
_COMPRESS_PROMPT = """Summarize these {count} news articles about {ticker} for a financial analyst.

ARTICLES:
{articles}

Write a structured summary (max 350 words) with these sections:
KEY EVENTS: Most important developments with dates and specific numbers (3-5 bullets)
SENTIMENT: Overall market sentiment and main reason in 1-2 sentences
RISKS: Main risks or headwinds mentioned (2-3 bullets)
CATALYSTS: Potential positive triggers (2-3 bullets)

Be precise with numbers, dates, product names. Preserve source names for critical news."""

# Rich full analysis for 32b models – near-Claude quality, replaces Claude for most cases
_FULL_ANALYSIS_PROMPT_32B = """You are a senior portfolio manager running a swing trading strategy (3–21 day holds).
Analyze the news and price data below for {ticker}. Provide a rigorous trading decision.

PRICE DATA:
{price_block}

NEWS (most recent first):
{headlines}

Your analysis must weigh:
1. News materiality and credibility (earnings beats, guidance changes, M&A, regulatory)
2. Price action context (trend direction, distance from 52w highs, volume signals)
3. Risk/reward for a 3–21 day swing trade
4. Sector and macro headwinds implied by the news

BUY criteria: score ≥ 0.70, direction BULLISH, confidence HIGH or MEDIUM, clear catalyst
SKIP criteria: score < 0.42 or direction BEARISH with HIGH confidence
HOLD: everything else

Return ONLY valid JSON, no markdown:
{{"sentiment_score": <0.0-1.0>, "direction": "<BULLISH|NEUTRAL|BEARISH>", "confidence": "<HIGH|MEDIUM|LOW>", "recommendation": "<BUY|HOLD|SKIP>", "suggested_hold_days": <3-21>, "entry_rationale": "<max 100 chars – specific catalyst>", "stop_loss_note": "<key risk that would invalidate the thesis, 50 chars>", "risk_factors": ["<specific risk 1>", "<specific risk 2>"], "key_catalysts": ["<specific catalyst 1>", "<specific catalyst 2>"], "summary": "<max 120 chars>"}}"""

# Vollständige Analyse im Frugal-Modus – ersetzt Claude komplett für normale Aktien
_FULL_ANALYSIS_PROMPT = """You are a financial news analyst for swing trading (hold 3–21 days).
Analyze news and price data about {ticker}. Return ONLY valid JSON, no explanation.

CURRENT PRICE DATA:
{price_block}

NEWS HEADLINES:
{headlines}

Return ONLY this JSON:
{{"sentiment_score": <0.0-1.0>, "direction": "<BULLISH|NEUTRAL|BEARISH>", "confidence": "<HIGH|MEDIUM|LOW>", "recommendation": "<BUY|HOLD|SKIP>", "suggested_hold_days": <3-21>, "entry_rationale": "<max 80 chars>", "risk_factors": ["<risk1>", "<risk2>"], "key_catalysts": ["<cat1>", "<cat2>"], "summary": "<max 100 chars>"}}

Rules:
- BUY only if score >= 0.68, direction BULLISH, confidence HIGH or MEDIUM
- SKIP if score < 0.40 or direction BEARISH
- HOLD otherwise
- suggested_hold_days: 3-7 for event-driven, 7-14 for momentum, 14-21 for trend"""

# Rich screening prompt for 32b models – returns richer signal with reasoning
_PRESCREEN_PROMPT_32B = """You are a senior financial analyst specializing in swing trading (3–21 day holds).
Analyze the following news about {ticker} and assess the short-term trading signal.

NEWS:
{headlines}

Consider: earnings momentum, management credibility, sector tailwinds/headwinds,
institutional activity, and macro context implied by the headlines.

Return ONLY valid JSON, no markdown, no explanation:
{{"score": <0.0-1.0>, "direction": "<BULLISH|NEUTRAL|BEARISH>", "confidence": "<HIGH|MEDIUM|LOW>", "reason": "<25 words max>", "catalyst": "<primary driver in 10 words>", "risk": "<biggest risk in 10 words>"}}

Score guide: 0.0=strong sell, 0.35=bearish, 0.5=neutral, 0.65=mildly bullish, 0.80=strong buy signal.
HIGH confidence only when headlines are clear, consistent, and material."""

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
        bearish_skip_threshold: float = 0.38,
        neutral_skip_threshold: float = 0.65,
    ):
        self.base_url         = base_url.rstrip("/")
        self.model            = model
        self.timeout          = timeout
        self.bearish_skip     = bearish_skip_threshold
        self.neutral_skip     = neutral_skip_threshold
        self._available: Optional[bool] = None

    @property
    def capability(self) -> str:
        """Model capability tier: HIGH (32b+), MEDIUM (14b), LOW (8b)."""
        return _model_capability(self.model)

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
        always_sources = _ALWAYS_CLAUDE_SOURCES.copy()
        if self.capability in ("LOW", "MEDIUM"):
            always_sources |= _WEAK_MODEL_CLAUDE_SOURCES
        high_priority = [
            item for item in news_items
            if any(src in (item.get("source") or "") for src in always_sources)
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

        # Nachrichten auf Schlagzeilen reduzieren
        max_items = 25 if self.capability == "HIGH" else 15
        headlines = self._extract_headlines(news_items, max_items=max_items)
        if self.capability == "HIGH":
            prompt = _PRESCREEN_PROMPT_32B.format(ticker=ticker, headlines=headlines)
        else:
            prompt = _PRESCREEN_PROMPT.format(ticker=ticker, headlines=headlines)

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
        Schwellen skalieren mit Modellstärke (capability).
        """
        cap = self.capability

        if cap == "HIGH":
            # 32b-Modell: sehr aggressiv filtern – Claude nur für echte BUY-Kandidaten
            if confidence == "HIGH":
                if score < 0.45:
                    return False, f"32b HIGH BEARISH ({score:.2f}) – SKIP"
                if score < 0.72:
                    return False, f"32b HIGH NEUTRAL ({score:.2f}) – kein Trade-Signal"
                return True, ""   # bullisch mit hoher Konfidenz → Claude bestätigt Kauf
            if confidence == "MEDIUM":
                if score < 0.52:
                    return False, f"32b MEDIUM BEARISH/NEUTRAL ({score:.2f}) – SKIP"
                if score < 0.68:
                    return False, f"32b MEDIUM mäßig ({score:.2f}) – kein Kauf-Signal"
                return True, ""
            # LOW confidence: Claude außer klar bearisch
            if score < 0.38:
                return False, f"32b LOW BEARISH ({score:.2f}) – SKIP"
            return True, ""

        elif cap == "MEDIUM":
            # 14b-Modell: moderat vertrauen
            if confidence == "HIGH":
                if score < self.bearish_skip:
                    return False, f"14b HIGH BEARISH ({score:.2f}) – SKIP"
                if score < 0.67:
                    return False, f"14b HIGH NEUTRAL ({score:.2f}) – kein Trade-Signal"
                return True, ""
            if confidence == "MEDIUM":
                if score < 0.55:
                    return False, f"14b MEDIUM ({score:.2f}) – zu niedrig"
                return True, ""
            if score < 0.35:
                return False, f"14b LOW BEARISH ({score:.2f}) – SKIP"
            return True, ""

        else:
            # 8b-Modell: konservativ – im Zweifel immer Claude
            if confidence == "LOW":
                if score < 0.35:
                    return False, f"Ollama: klar BEARISH ({score:.2f}, LOW) – kein Trade möglich"
                return True, ""
            if confidence == "HIGH":
                if score < self.bearish_skip:
                    return False, f"Ollama: klar BEARISH ({score:.2f}, HIGH) – SKIP ohne Claude"
                if score < self.neutral_skip:
                    return False, f"Ollama: NEUTRAL ({score:.2f}, HIGH) – kein Trade-Signal"
                return True, ""
            # MEDIUM
            if score < 0.58:
                return False, f"Ollama: NEUTRAL/BEARISH ({score:.2f}, MEDIUM) – Score zu niedrig"
            return True, ""

    # ── Ollama API ────────────────────────────────────────────────────────────

    def _call_ollama(self, prompt: str, max_tokens: int = 120) -> Optional[str]:
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model":  self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": max_tokens,
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

        return {
            "score":     score,
            "direction": direction,
            "confidence":confidence,
            "reason":    reason,
            "catalyst":  str(data.get("catalyst", "")),
            "risk":      str(data.get("risk", "")),
        }

    def full_analysis(
        self,
        ticker: str,
        news_items: List[Dict],
        price_data: Optional[Dict] = None,
        buy_min_score: float = 0.68,
    ) -> Optional[Dict]:
        """
        Vollständige Swing-Trading-Analyse via Ollama (Frugal-Modus).
        Gibt ein dict zurück das direkt als AnalysisResult verwendet werden kann,
        oder None wenn Ollama nicht verfügbar oder Parsing fehlschlägt.
        """
        if not self.is_available() or not news_items:
            return None

        max_items = 30 if self.capability == "HIGH" else 20
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

        t0  = time.monotonic()
        raw = self._call_ollama(prompt, max_tokens=max_tokens)
        latency_ms = int((time.monotonic() - t0) * 1000)

        if raw is None:
            self.reset_availability_cache()
            return None

        data = self._parse_full_response(raw, buy_min_score)
        if data is None:
            log.warning("[%s] Ollama full_analysis: JSON-Parse fehlgeschlagen", ticker)
            return None

        log.info(
            "Ollama full_analysis [%s]: %s score=%.2f %s (%dms)",
            ticker, data["recommendation"], data["sentiment_score"],
            data["confidence"], latency_ms,
        )
        return data

    def _format_price_block(self, price_data: Dict) -> str:
        if not price_data.get("current_price"):
            return "No price data available."
        lines = [
            f"Price: ${price_data.get('current_price')}",
            f"1w change: {price_data.get('price_change_1w')}%",
            f"1m change: {price_data.get('price_change_1m')}%",
            f"Sector: {price_data.get('sector', 'N/A')}",
            f"P/E: {price_data.get('pe_ratio', 'N/A')}",
        ]
        return "  ".join(lines)

    def _parse_full_response(self, raw: str, buy_min_score: float) -> Optional[Dict]:
        """Parst und validiert die vollständige Ollama-Analyse."""
        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end])
                except json.JSONDecodeError:
                    return None

        if not data:
            return None

        try:
            score = float(data.get("sentiment_score", 0.5))
            score = max(0.0, min(1.0, score))
            direction = data.get("direction", "NEUTRAL").upper()
            if direction not in ("BULLISH", "NEUTRAL", "BEARISH"):
                direction = "NEUTRAL"
            confidence = data.get("confidence", "LOW").upper()
            if confidence not in ("HIGH", "MEDIUM", "LOW"):
                confidence = "LOW"
            rec = data.get("recommendation", "SKIP").upper()
            if rec not in ("BUY", "HOLD", "SKIP"):
                rec = "SKIP"
            # Safety: enforce BUY criteria (Ollama kann hier weniger präzise sein)
            if rec == "BUY" and (score < buy_min_score or direction != "BULLISH"):
                rec = "HOLD"
            hold = int(data.get("suggested_hold_days", 7))
            hold = max(3, min(21, hold))
            return {
                "sentiment_score":    score,
                "direction":          direction,
                "confidence":         confidence,
                "recommendation":     rec,
                "suggested_hold_days": hold,
                "entry_rationale":    str(data.get("entry_rationale", ""))[:100],
                "risk_factors":       [str(r) for r in data.get("risk_factors", [])[:3]],
                "key_catalysts":      [str(c) for c in data.get("key_catalysts", [])[:3]],
                "summary":            str(data.get("summary", ""))[:150],
            }
        except (TypeError, ValueError):
            return None

    def compress_news(self, ticker: str, news_items: List[Dict], max_items: int = 20) -> Optional[str]:
        """
        Fasst News-Artikel mit Ollama zusammen.
        Gibt kompakten Text zurück (für Claude-Prompt), oder None bei Fehler.
        """
        if not self.is_available() or not news_items:
            return None
        articles = self._format_articles_for_compression(news_items[:max_items])
        prompt = _COMPRESS_PROMPT.format(
            count=min(len(news_items), max_items),
            ticker=ticker,
            articles=articles,
        )
        t0 = time.monotonic()
        raw = self._call_ollama(prompt, max_tokens=500)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if raw:
            log.debug("Ollama compress [%s]: %d chars in %dms", ticker, len(raw), latency_ms)
            return raw.strip()
        return None

    def _format_articles_for_compression(self, news_items: List[Dict]) -> str:
        """Artikel mit Titel + Kurztext für Komprimierungs-Prompt."""
        lines = []
        for i, item in enumerate(news_items, 1):
            title  = (item.get("title") or "")[:120]
            text   = (item.get("text") or "")[:200]
            source = (item.get("source") or "")[:25]
            date   = (item.get("published_at") or "")[:10]
            prio   = " [WICHTIG]" if item.get("priority") == "HIGH" else ""
            lines.append(f"[{i}] {date} | {source}{prio}\n    {title}\n    {text}")
        return "\n\n".join(lines)

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
