from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)


@dataclass
class AnalysisResult:
    ticker: str
    sentiment_score: float          # 0.0 – 1.0
    direction: str                  # BULLISH | BEARISH | NEUTRAL
    confidence: str                 # HIGH | MEDIUM | LOW
    recommendation: str             # BUY | HOLD | SKIP
    entry_rationale: str = ""
    risk_factors: List[str] = field(default_factory=list)
    key_catalysts: List[str] = field(default_factory=list)
    suggested_hold_days: int = 14
    target_price: Optional[float] = None
    thesis_valid: Optional[bool] = None
    thesis_break_reason: str = ""
    sources_used: Dict[str, int] = field(default_factory=dict)
    bull_case: str = ""
    bear_case: str = ""
    debate_winner: str = ""          # BULL | BEAR | DRAW
    related_tickers: List[str] = field(default_factory=list)
    entry_trigger_price: Optional[float] = None


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Du bist ein erfahrener Aktienanalyst spezialisiert auf kurzfristige Swing-Trades (1–4 Wochen).
Deine Aufgabe: Analysiere News-Artikel und liefere eine strukturierte Kaufentscheidung.
Antworte IMMER mit validem JSON. Kein Text vor oder nach dem JSON."""

_USER_TEMPLATE_STANDARD = """
Analysiere folgende News für {ticker} (aktueller Preis: ${price:.2f}):

{news_text}

Antworte NUR mit diesem JSON-Format:
{{
  "sentiment_score": <float 0.0-1.0>,
  "direction": "<BULLISH|BEARISH|NEUTRAL>",
  "confidence": "<HIGH|MEDIUM|LOW>",
  "recommendation": "<BUY|HOLD|SKIP>",
  "entry_rationale": "<1-2 Sätze Kaufbegründung>",
  "risk_factors": ["<Risiko 1>", "<Risiko 2>"],
  "key_catalysts": ["<Katalysator 1>", "<Katalysator 2>"],
  "suggested_hold_days": <int>,
  "target_price": <float oder null>,
  "bull_case": "<1 Satz Bull-Szenario>",
  "bear_case": "<1 Satz Bear-Szenario>",
  "debate_winner": "<BULL|BEAR|DRAW>",
  "related_tickers": ["<Ticker1>"],
  "entry_trigger_price": <float oder null>
}}"""

_USER_TEMPLATE_THESIS_CHECK = """
Du hast {ticker} gekauft bei ${entry_price:.2f} (jetzt: ${current_price:.2f}, Gewinn: {gain_pct:+.1f}%).
Ursprüngliche Kaufbegründung: {original_rationale}

Aktuelle News:
{news_text}

Ist die Kaufthese noch intakt? Antworte NUR mit JSON:
{{
  "thesis_valid": <true|false>,
  "thesis_break_reason": "<leer wenn valid, sonst Grund>",
  "sentiment_score": <float 0.0-1.0>,
  "recommendation": "<HOLD|SELL>"
}}"""

_USER_TEMPLATE_CRYPTO = """
Analysiere folgende Krypto-News für {ticker} (aktueller Preis: ${price:.2f}):

{news_text}

Berücksichtige: On-Chain-Daten, Marktstruktur, regulatorische Risiken, Whale-Bewegungen.
Antworte NUR mit JSON (gleiches Format wie Standard-Analyse)."""


class NewsTrustFilter:
    """Filtert News nach Quelle und Qualität."""

    LOW_TRUST_DOMAINS = {
        "zerohedge.com", "seekingalpha.com", "motleyfool.com",
        "investorplace.com", "marketwatch.com",  # nicht blockiert, aber reduziertes Gewicht
    }
    HIGH_TRUST_DOMAINS = {
        "reuters.com", "bloomberg.com", "wsj.com", "ft.com",
        "sec.gov", "ir.company",
    }

    def score_article(self, article: Dict) -> float:
        """Returns trust score 0.0-1.0."""
        source = (article.get("source") or "").lower()
        if any(d in source for d in self.HIGH_TRUST_DOMAINS):
            return 1.0
        if any(d in source for d in self.LOW_TRUST_DOMAINS):
            return 0.4
        return 0.7

    def filter_and_rank(self, articles: List[Dict], min_score: float = 0.3) -> List[Dict]:
        scored = [(a, self.score_article(a)) for a in articles]
        scored = [(a, s) for a, s in scored if s >= min_score]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [a for a, _ in scored]


class ClaudeAnalyzer:
    """Analysiert Aktien-News mittels Claude API."""

    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-5"):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model
        self._trust_filter = NewsTrustFilter()
        self._call_count = 0
        self._total_tokens = 0

    def analyze(
        self,
        ticker: str,
        news: List[Dict],
        current_price: float,
        existing_position=None,
        is_crypto: bool = False,
        use_ollama: bool = False,
    ) -> AnalysisResult:
        """
        Main analysis entry point.
        - Filters and ranks news by trust
        - Tries Ollama pre-screen if enabled
        - Falls back to Claude if needed
        """
        if not news:
            return self._empty_result(ticker)

        # Filter news
        filtered = self._trust_filter.filter_and_rank(news, min_score=0.3)
        if not filtered:
            filtered = news[:10]

        # Format news text
        news_text = self._format_news(filtered[:15])

        # Ollama pre-screen
        if use_ollama or not self.api_key:
            ollama_result = self._try_ollama(ticker, news_text, current_price, is_crypto)
            if ollama_result is not None:
                # If Ollama gives strong signal, skip Claude
                if (
                    not self.api_key
                    or (ollama_result.confidence != "HIGH" and not self._has_hard_priority_sources(filtered))
                ):
                    return ollama_result

        # Thesis check for existing position
        if existing_position is not None:
            return self._thesis_check(ticker, news_text, current_price, existing_position)

        # Full Claude analysis
        return self._claude_analysis(ticker, news_text, current_price, is_crypto)

    def _claude_analysis(
        self, ticker: str, news_text: str, price: float, is_crypto: bool
    ) -> AnalysisResult:
        template = _USER_TEMPLATE_CRYPTO if is_crypto else _USER_TEMPLATE_STANDARD
        prompt = template.format(ticker=ticker, price=price, news_text=news_text)

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            self._call_count += 1
            self._total_tokens += resp.usage.input_tokens + resp.usage.output_tokens
            return self._parse_response(ticker, resp.content[0].text)
        except Exception as e:
            log.warning("Claude-Analyse fehlgeschlagen für %s: %s", ticker, e)
            return self._empty_result(ticker)

    def _thesis_check(
        self, ticker: str, news_text: str, current_price: float, position
    ) -> AnalysisResult:
        gain_pct = (current_price - position.entry_price) / position.entry_price * 100
        prompt = _USER_TEMPLATE_THESIS_CHECK.format(
            ticker=ticker,
            entry_price=position.entry_price,
            current_price=current_price,
            gain_pct=gain_pct,
            original_rationale=position.rationale[:300],
            news_text=news_text,
        )
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model=self.model,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            self._call_count += 1
            data = self._safe_json(resp.content[0].text)
            result = self._empty_result(ticker)
            result.thesis_valid = data.get("thesis_valid", True)
            result.thesis_break_reason = data.get("thesis_break_reason", "")
            result.sentiment_score = float(data.get("sentiment_score", 0.5))
            result.recommendation = data.get("recommendation", "HOLD")
            return result
        except Exception as e:
            log.warning("Thesis-Check fehlgeschlagen für %s: %s", ticker, e)
            result = self._empty_result(ticker)
            result.thesis_valid = True
            return result

    def _try_ollama(
        self, ticker: str, news_text: str, price: float, is_crypto: bool
    ) -> Optional[AnalysisResult]:
        """Versucht lokale Ollama-Analyse (kostenfrei)."""
        try:
            import requests
            prompt = _USER_TEMPLATE_STANDARD.format(
                ticker=ticker, price=price, news_text=news_text[:3000]
            )
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "llama3", "prompt": prompt, "stream": False},
                timeout=30,
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "")
                return self._parse_response(ticker, text)
        except Exception:
            pass
        return None

    def _has_hard_priority_sources(self, articles: List[Dict]) -> bool:
        """Prüft ob hochwertige Quellen (SEC, Reuters, Bloomberg) vorhanden sind."""
        for a in articles:
            source = (a.get("source") or "").lower()
            if any(d in source for d in {"sec.gov", "reuters", "bloomberg", "wsj", "ft.com"}):
                return True
        return False

    def _parse_response(self, ticker: str, text: str) -> AnalysisResult:
        data = self._safe_json(text)
        if not data:
            return self._empty_result(ticker)
        try:
            return AnalysisResult(
                ticker=ticker,
                sentiment_score=float(data.get("sentiment_score", 0.5)),
                direction=data.get("direction", "NEUTRAL"),
                confidence=data.get("confidence", "LOW"),
                recommendation=data.get("recommendation", "SKIP"),
                entry_rationale=data.get("entry_rationale", ""),
                risk_factors=data.get("risk_factors", []),
                key_catalysts=data.get("key_catalysts", []),
                suggested_hold_days=int(data.get("suggested_hold_days", 14)),
                target_price=data.get("target_price"),
                bull_case=data.get("bull_case", ""),
                bear_case=data.get("bear_case", ""),
                debate_winner=data.get("debate_winner", "DRAW"),
                related_tickers=data.get("related_tickers", []),
                entry_trigger_price=data.get("entry_trigger_price"),
            )
        except Exception as e:
            log.warning("Parse-Fehler für %s: %s", ticker, e)
            return self._empty_result(ticker)

    @staticmethod
    def _safe_json(text: str) -> Dict:
        try:
            start = text.find("{")
            end   = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            pass
        return {}

    @staticmethod
    def _format_news(articles: List[Dict]) -> str:
        lines = []
        for i, a in enumerate(articles, 1):
            title   = a.get("title", "")[:200]
            source  = a.get("source", "")
            summary = a.get("summary", a.get("content", ""))[:300]
            lines.append(f"{i}. [{source}] {title}")
            if summary:
                lines.append(f"   {summary}")
        return "\n".join(lines)

    @staticmethod
    def _empty_result(ticker: str) -> AnalysisResult:
        return AnalysisResult(
            ticker=ticker,
            sentiment_score=0.5,
            direction="NEUTRAL",
            confidence="LOW",
            recommendation="SKIP",
        )

    def get_stats(self) -> Dict:
        return {
            "calls": self._call_count,
            "total_tokens": self._total_tokens,
        }
