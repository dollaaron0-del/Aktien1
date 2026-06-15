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
    target_price_rationale: str = ""
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
Wichtig: Gewichte die aktuelle Makro-Lage (Marktregime, VIX, Risk-On/Off, Rezessionsrisiko,
anstehende FOMC/CPI-Termine) in deiner Einschätzung – ein bullishes Einzelsignal bei
Risk-Off-Marktumfeld oder kurz vor einem FOMC-Termin verdient mehr Vorsicht (niedrigere
Konfidenz / konservativeres Kursziel).
Antworte IMMER mit validem JSON. Kein Text vor oder nach dem JSON."""

_USER_TEMPLATE_STANDARD = """
Analysiere folgende News für {ticker} (aktueller Preis: ${price:.2f}):
{context_block}
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
{context_block}
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
{context_block}
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
        self.ollama_ratio = float(os.environ.get("OLLAMA_RATIO", 0.6))
        # Lokales Ollama-Modell für die Vorfilterung. Muss ein tatsächlich
        # geladenes Modell sein (siehe `ollama list`), sonst schlägt jeder
        # Prescreen fehl und es wird immer auf Claude zurückgefallen.
        self.ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
        self.ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self.ollama_timeout = int(os.environ.get("OLLAMA_TIMEOUT", "60"))
        self.model = model
        self._trust_filter = NewsTrustFilter()
        self._call_count = 0
        self._total_tokens = 0
        self._local_count = 0          # via Ollama/MLX abgehandelt (kein Claude)
        self._compress_count = 0       # News lokal komprimiert vor Claude
        self._prescreener = None       # lazy: OllamaPrescreener oder MLXPrescreener

    def analyze(
        self,
        ticker: str,
        news: List[Dict] = None,
        current_price: float = None,
        existing_position=None,
        is_crypto: bool = False,
        use_ollama: bool = False,
        news_items=None,
        price_data=None,
        macro_brief: str = "",
        geo_context=None,
        **kwargs,
    ) -> AnalysisResult:
        """
        Main analysis entry point.
        - Filters and ranks news by trust
        - Tries Ollama pre-screen if enabled
        - Falls back to Claude if needed

        macro_brief / geo_context fließen als Kontext-Block in den Prompt, damit
        jede Aktie im aktuellen Makro- und geopolitischen Umfeld bewertet wird.
        """
        if news is None:
            news = news_items or []
        context_block = self._build_context_block(macro_brief, geo_context)
        if current_price is None and price_data is not None:
            current_price = price_data if isinstance(price_data, (int, float)) else (price_data or {}).get("price") or (price_data or {}).get("close") or 0.0
        if not news:
            return self._empty_result(ticker)

        # Filter news
        filtered = self._trust_filter.filter_and_rank(news, min_score=0.3)
        if not filtered:
            filtered = news[:10]

        force_claude = bool(kwargs.get("force_claude"))
        news_text = self._format_news(filtered[:15])

        # ── Frugal-Modus (Daten-Sammel-Modus) ────────────────────────────────
        # Ollama/MLX übernimmt deterministisch ALLE Analysen – auch Thesis-/Exit-
        # Checks offener Positionen (Paper-Trading: kein echtes Geld, der Bot
        # sammelt Erfahrung; gelegentlich unscharfe Analysen sind akzeptabel).
        # Claude nur noch bei echten Katalysatoren (SEC 8-K / Earnings) oder
        # explizitem Zwang (force_claude / Dashboard). KEIN Zufalls-Gate, KEIN
        # pauschaler Reuters/Bloomberg-Fallback – das war der Grund, warum der
        # Modus „schnell auf Claude zurückfiel".
        from config import config as _cfg
        if _cfg.frugal_mode and not force_claude and not self._has_catalyst_source(news):
            if existing_position is not None:
                local = self._frugal_thesis_check(
                    ticker, news_text, current_price, existing_position, context_block
                )
            else:
                local = self._frugal_local_analysis(
                    ticker, news, price_data, current_price, is_crypto
                )
            if local is not None:
                return local
            # Lokale Engine offline/Parsing-Fehler → Claude als Sicherheitsnetz.

        import random
        ollama_enabled = os.environ.get("OLLAMA_ENABLED", "true").lower() in ("true", "1", "yes")
        # Zufalls-Gate nur außerhalb des Frugal-Modus (Legacy-Verhalten).
        use_ollama = use_ollama or (
            not _cfg.frugal_mode
            and ollama_enabled and bool(self.api_key)
            and random.random() < self.ollama_ratio
        )

        # Ollama pre-screen (Legacy-Pfad, nur außerhalb Frugal oder ohne API-Key)
        if use_ollama or not self.api_key:
            ollama_result = self._try_ollama(ticker, news_text, current_price, is_crypto, context_block)
            if ollama_result is not None:
                # If Ollama gives strong signal, skip Claude
                if (
                    not self.api_key
                    or not self._has_hard_priority_sources(filtered)
                ):
                    return ollama_result

        # Claude übernimmt jetzt die finale Analyse. Im Frugal-Modus lässt Ollama
        # die News vorher lokal zu einem kompakten Briefing eindampfen → weniger
        # Claude-Input-Tokens (günstiger), fokussierterer Prompt.
        claude_news = self._claude_news_text(ticker, news, news_text)

        # Thesis check for existing position
        if existing_position is not None:
            return self._thesis_check(ticker, claude_news, current_price, existing_position, context_block)

        # Full Claude analysis
        return self._claude_analysis(ticker, claude_news, current_price, is_crypto, context_block)

    # ── Frugal-Modus: lokale Engine (Ollama/MLX) ─────────────────────────────

    def _get_prescreener(self):
        """Lazy: liefert die lokale Analyse-Engine. MLX wenn aktiviert, sonst
        Ollama – beide bieten dieselbe full_analysis()-Schnittstelle. Wird
        gecacht; bei Init-Fehler None (→ Claude-Fallback)."""
        if self._prescreener is not None:
            return self._prescreener
        from config import config as _cfg
        try:
            if _cfg.mlx_enabled:
                from analyzers.mlx_prescreener import MLXPrescreener
                self._prescreener = MLXPrescreener(
                    base_url=_cfg.mlx_url, model=_cfg.mlx_model, timeout=_cfg.mlx_timeout,
                )
            else:
                from analyzers.ollama_prescreener import OllamaPrescreener
                self._prescreener = OllamaPrescreener(
                    base_url=_cfg.ollama_url, model=_cfg.ollama_model, timeout=_cfg.ollama_timeout,
                )
            # Für den Resource-Manager-Hook (Modellwechsel zur Laufzeit).
            import analyzers.ollama_prescreener as _opmod
            _opmod._prescreener_instance = self._prescreener
        except Exception as e:
            log.warning("Prescreener-Init fehlgeschlagen: %s", e)
            self._prescreener = None
        return self._prescreener

    @staticmethod
    def _has_catalyst_source(articles: List[Dict]) -> bool:
        """True wenn ein echter Katalysator dabei ist (SEC 8-K / Earnings-
        Transcript) – nur diese gehen im Frugal-Modus noch an Claude."""
        try:
            from analyzers.ollama_prescreener import _ALWAYS_CLAUDE_SOURCES
        except Exception:
            _ALWAYS_CLAUDE_SOURCES = {"SEC 8-K", "Earnings Call Transcript"}
        for a in articles or []:
            src = a.get("source") or ""
            if any(c in src for c in _ALWAYS_CLAUDE_SOURCES):
                return True
        return False

    def _frugal_local_analysis(
        self, ticker: str, news: List[Dict], price_data, current_price, is_crypto: bool,
    ) -> Optional[AnalysisResult]:
        """Vollanalyse über die lokale Engine (Ollama/MLX). None wenn die Engine
        nicht verfügbar ist oder das Parsing scheitert (→ Claude-Fallback)."""
        ps = self._get_prescreener()
        if ps is None or not ps.is_available():
            return None
        from config import config as _cfg
        pdata = price_data if isinstance(price_data, dict) else {"current_price": current_price}
        try:
            data = ps.full_analysis(
                ticker=ticker, news_items=news, price_data=pdata,
                buy_min_score=_cfg.frugal_buy_min_score,
            )
        except Exception as e:
            log.warning("[%s] Lokale Vollanalyse fehlgeschlagen: %s", ticker, e)
            return None
        if data is None:
            return None
        self._local_count += 1
        return self._ollama_full_to_result(ticker, data)

    def _claude_news_text(self, ticker: str, news: List[Dict], raw_text: str) -> str:
        """Im Frugal-Modus: Ollama/MLX komprimiert die News lokal zu einem
        Briefing, bevor Claude analysiert (spart Claude-Tokens). Fällt auf die
        rohen Schlagzeilen zurück, wenn die lokale Engine offline ist oder die
        Komprimierung scheitert."""
        from config import config as _cfg
        if not _cfg.frugal_mode:
            return raw_text
        ps = self._get_prescreener()
        if ps is None or not ps.is_available():
            return raw_text
        try:
            compressed = ps.compress_news(ticker, news)
        except Exception as e:
            log.debug("[%s] News-Komprimierung fehlgeschlagen: %s", ticker, e)
            return raw_text
        if compressed:
            self._compress_count += 1
            return "[Lokales News-Briefing (Ollama-komprimiert)]\n" + compressed
        return raw_text

    def _frugal_thesis_check(
        self, ticker: str, news_text: str, current_price: float, position,
        context_block: str = "",
    ) -> Optional[AnalysisResult]:
        """Thesis-/Exit-Check offener Positionen über die lokale Engine
        (Ollama/MLX). Gleicher Prompt wie der Claude-Thesis-Check. None wenn die
        Engine offline ist oder das Parsing scheitert (→ Claude-Fallback)."""
        ps = self._get_prescreener()
        if ps is None or not ps.is_available():
            return None
        try:
            entry = float(getattr(position, "entry_price", 0) or 0)
            gain_pct = (current_price - entry) / entry * 100 if entry else 0.0
            prompt = _USER_TEMPLATE_THESIS_CHECK.format(
                ticker=ticker,
                entry_price=entry,
                current_price=current_price or 0.0,
                gain_pct=gain_pct,
                original_rationale=(getattr(position, "rationale", "") or "")[:300],
                news_text=news_text,
                context_block=context_block,
            )
            raw = ps.generate(prompt, max_tokens=300)
        except Exception as e:
            log.warning("[%s] Lokaler Thesis-Check fehlgeschlagen: %s", ticker, e)
            return None
        if not raw:
            return None
        data = self._safe_json(raw)
        if not data:
            return None
        self._local_count += 1
        result = self._empty_result(ticker)
        result.thesis_valid = bool(data.get("thesis_valid", True))
        result.thesis_break_reason = str(data.get("thesis_break_reason", ""))
        try:
            result.sentiment_score = float(data.get("sentiment_score", 0.5))
        except (TypeError, ValueError):
            result.sentiment_score = 0.5
        result.recommendation = str(data.get("recommendation", "HOLD")).upper()
        return result

    @staticmethod
    def _ollama_full_to_result(ticker: str, d: Dict) -> AnalysisResult:
        """Mappt das full_analysis()-Dict der lokalen Engine auf AnalysisResult."""
        return AnalysisResult(
            ticker=ticker,
            sentiment_score=float(d.get("sentiment_score", 0.5)),
            direction=d.get("direction", "NEUTRAL"),
            confidence=d.get("confidence", "LOW"),
            recommendation=d.get("recommendation", "SKIP"),
            entry_rationale=d.get("entry_rationale", "") or d.get("summary", ""),
            risk_factors=list(d.get("risk_factors", []) or []),
            key_catalysts=list(d.get("key_catalysts", []) or []),
            suggested_hold_days=int(d.get("suggested_hold_days", 14)),
            bull_case=d.get("summary", ""),
        )

    @staticmethod
    def _build_context_block(macro_brief: str = "", geo_context=None) -> str:
        """Baut den optionalen Kontext-Block (Makro + Geopolitik) für den Prompt."""
        parts = []
        if macro_brief:
            parts.append(macro_brief.strip())
        if geo_context:
            geo_txt = geo_context if isinstance(geo_context, str) else str(geo_context)
            geo_txt = geo_txt.strip()
            if geo_txt:
                parts.append("GEOPOLITIK: " + geo_txt)
        if not parts:
            return ""
        return "\n" + "\n\n".join(parts) + "\n"

    def _claude_analysis(
        self, ticker: str, news_text: str, price: float, is_crypto: bool,
        context_block: str = "",
    ) -> AnalysisResult:
        template = _USER_TEMPLATE_CRYPTO if is_crypto else _USER_TEMPLATE_STANDARD
        prompt = template.format(
            ticker=ticker, price=price, news_text=news_text, context_block=context_block
        )

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
        self, ticker: str, news_text: str, current_price: float, position,
        context_block: str = "",
    ) -> AnalysisResult:
        gain_pct = (current_price - position.entry_price) / position.entry_price * 100
        prompt = _USER_TEMPLATE_THESIS_CHECK.format(
            ticker=ticker,
            entry_price=position.entry_price,
            current_price=current_price,
            gain_pct=gain_pct,
            original_rationale=position.rationale[:300],
            news_text=news_text,
            context_block=context_block,
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
        self, ticker: str, news_text: str, price: float, is_crypto: bool,
        context_block: str = "",
    ) -> Optional[AnalysisResult]:
        """Versucht lokale Ollama-Analyse (kostenfrei)."""
        try:
            import requests
            prompt = _USER_TEMPLATE_STANDARD.format(
                ticker=ticker, price=price, news_text=news_text[:3000],
                context_block=context_block,
            )
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={"model": self.ollama_model, "prompt": prompt, "stream": False},
                timeout=self.ollama_timeout,
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
                target_price_rationale=data.get("target_price_rationale", ""),
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
