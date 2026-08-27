from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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
    # Verarbeitungs-Trace (Roadmap 1.4c) — von analyze() direkt vor jedem
    # return gesetzt, NICHT von den Bau-Helfern (_thesis_check etc.): so
    # bleibt an EINER Stelle nachvollziehbar, welcher Pfad tatsächlich
    # gewonnen hat, ohne jede Hilfsmethode einzeln anzufassen.
    model_route: str = ""            # z.B. "ollama_frugal" | "claude" | "claude_dedup_cache"
    frugal_reason: str = ""          # Klartext-Begründung für die Routing-Entscheidung
    # KI-Prompt-Archiv (Roadmap 1.4d) — nur bei einem ECHTEN Claude-Aufruf
    # gesetzt (_claude_analysis/_thesis_check), sonst leer. Basis für
    # Entscheidungs-Replay (Roadmap 4.5). Bewusst NICHT im Dedup-Cache
    # persistiert (_result_cache_store entfernt diese Felder vor dem
    # Schreiben) — ein Cache-Hit ist kein neuer Aufruf und würde sonst den
    # alten Prompt fälschlich unter einer neuen analysis_id archivieren.
    raw_model: str = ""
    raw_system_prompt: str = ""
    raw_user_prompt: str = ""
    raw_response: str = ""


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Du bist ein erfahrener Aktienanalyst spezialisiert auf kurzfristige Swing-Trades (1–4 Wochen).
Deine Aufgabe: Analysiere News-Artikel und liefere eine strukturierte Kaufentscheidung.
Wichtig: Gewichte die aktuelle Makro-Lage (Marktregime, VIX, Risk-On/Off, Rezessionsrisiko,
anstehende FOMC/CPI-Termine) in deiner Einschätzung – ein bullishes Einzelsignal bei
Risk-Off-Marktumfeld oder kurz vor einem FOMC-Termin verdient mehr Vorsicht (niedrigere
Konfidenz / konservativeres Kursziel).
WICHTIG zur recommendation: Makro-Vorsicht (z.B. ein FOMC/CPI-Termin in einigen Tagen)
senkt confidence und target_price, ist aber für sich genommen KEIN Grund, ein ansonsten
überzeugendes Kaufsignal von BUY auf HOLD herabzustufen. Ein anstehender Kalendertermin
allein rechtfertigt kein HOLD. Das Risiko-Overlay (Positionsgröße, Kaufschwelle) wird vom
Handelssystem separat angewandt – deine recommendation soll die fundamentale/technische
Stärke des Titels widerspiegeln: starkes bullishes Signal (hoher Score, klare Katalysatoren)
→ BUY; gib HOLD nur bei echtem Risk-Off-Regime, gemischtem Signal oder titelspezifischem
Gegenwind (z.B. Earnings des Titels selbst innerhalb der Haltedauer).
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


def _stamp_route(result: AnalysisResult, route: str, reason: str) -> AnalysisResult:
    """Setzt den Verarbeitungs-Trace (Roadmap 1.4c) auf ein bereits gebautes
    AnalysisResult und gibt es unverändert sonst zurück – ein einziger,
    zentraler Stempel-Punkt in analyze() statt jede Bau-Methode einzeln
    anzufassen."""
    result.model_route = route
    result.frugal_reason = reason
    return result


class ClaudeAnalyzer:
    """Analysiert Aktien-News mittels Claude API."""

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        # Kein hartcodiertes Hauptmodell mehr: leer → config.claude_model (Default
        # Haiku 4.5). So greift CLAUDE_MODEL auch auf dem Hauptpfad (früher lief die
        # Katalysator-Analyse fest auf Sonnet 4.5, an der Config vorbei).
        if not model:
            from config import config as _cfg
            model = _cfg.claude_model
        # Läuft der Bot auf Gemini (LLM_PROVIDER=gemini), ist ANTHROPIC_API_KEY
        # oft leer – dann würde jeder `bool(self.api_key)`-Check unten fälschlich
        # "kein LLM verfügbar → nur Ollama" ergeben. Verfügbarkeit am aktiven
        # Provider festmachen.
        from config import config as _cfg
        if getattr(_cfg, "llm_provider", "anthropic") == "gemini" and not self.api_key:
            self.api_key = getattr(_cfg, "gemini_api_key", "") or "gemini"
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
        self._last_usage = None         # (in, out, cache_read) des letzten Claude-Calls
        self._last_model = model         # Modell des letzten Calls (für Kostenabrechnung)
        self._local_count = 0          # via Ollama/MLX abgehandelt (kein Claude)
        self._compress_count = 0       # News lokal komprimiert vor Claude
        self._prescreener = None       # lazy: OllamaPrescreener oder MLXPrescreener
        self._claude_blocked = False   # True nach erkanntem Guthaben-/Auth-Fehler
        try:
            from analyzers.api_cost_tracker import APICostTracker
            self._cost_tracker = APICostTracker()
        except Exception:
            self._cost_tracker = None  # Kosten-Bremse optional (nie blockieren bei Fehler)

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
        context_block = self._build_context_block(
            macro_brief, geo_context, kwargs.get("mechanical_brief", ""))
        if current_price is None and price_data is not None:
            current_price = price_data if isinstance(price_data, (int, float)) else (price_data or {}).get("price") or (price_data or {}).get("close") or 0.0
        if not news:
            return _stamp_route(self._empty_result(ticker), "empty", "keine News-Artikel")

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
        _has_catalyst = self._has_catalyst_source(news)
        if _cfg.frugal_mode and not force_claude and not _has_catalyst:
            if existing_position is not None:
                local = self._frugal_thesis_check(
                    ticker, news_text, current_price, existing_position, context_block
                )
                _frugal_route = "ollama_frugal_thesis"
            else:
                local = self._frugal_local_analysis(
                    ticker, news, price_data, current_price, is_crypto
                )
                _frugal_route = "ollama_frugal_full"
            if local is not None:
                if self._cost_tracker is not None:
                    self._cost_tracker.record(claude_called=False, ollama_used=True)
                return _stamp_route(
                    local, _frugal_route,
                    "frugal_mode: kein Katalysator, lokale Engine übernimmt",
                )
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
                    return _stamp_route(
                        ollama_result, "ollama_legacy",
                        "kein API-Key" if not self.api_key else "Zufalls-Gate (Legacy-Pfad)",
                    )

        # Claude verfügbar? Drei harte Sperren – greift EINE, übernimmt Ollama
        # ausnahmslos alles (auch Katalysatoren & force_claude):
        #   1. kein API-Key
        #   2. Guthaben-/Auth-Fehler heute schon erkannt (_claude_blocked)
        #   3. Tages-Budget MAX_DAILY_COST_USD erreicht
        claude_ok = bool(self.api_key) and not self._claude_blocked
        _claude_block_reason = (
            "kein API-Key" if not self.api_key
            else "Claude zuvor gesperrt (Guthaben/Auth)" if self._claude_blocked
            else ""
        )
        if claude_ok and self._cost_tracker is not None:
            allowed, reason = self._cost_tracker.check_daily_limit()
            if not allowed:
                log.warning("[%s] Claude-Budget: %s", ticker, reason)
                claude_ok = False
                _claude_block_reason = f"Tagesbudget erschöpft: {reason}"

        if not claude_ok:
            return _stamp_route(
                self._local_fallback(
                    ticker, news, news_text, price_data, current_price,
                    is_crypto, existing_position, context_block,
                ),
                "ollama_fallback", _claude_block_reason or "Claude nicht verfügbar",
            )

        # Dedup: bei Neueinstiegen identische News (gleicher Fingerprint) für
        # denselben Ticker innerhalb der Cache-TTL nicht erneut an Claude geben –
        # das letzte Vollergebnis wird wiederverwendet (Thesis-Checks hängen am
        # Live-Preis und werden bewusst nicht gecacht).
        if existing_position is None:
            cached = self._result_cache_get(ticker, news)
            if cached is not None:
                log.info("[%s] Dedup-Treffer: identische News < Cache-TTL → Claude übersprungen", ticker)
                if self._cost_tracker is not None:
                    self._cost_tracker.record(claude_called=False, ollama_used=False)
                return _stamp_route(
                    cached, "claude_dedup_cache",
                    "identische News < Cache-TTL, letztes Claude-Ergebnis wiederverwendet",
                )

        # Claude übernimmt jetzt die finale Analyse. Im Frugal-Modus lässt Ollama
        # die News vorher lokal zu einem kompakten Briefing eindampfen → weniger
        # Claude-Input-Tokens (günstiger), fokussierterer Prompt.
        claude_news = self._claude_news_text(ticker, news, news_text)

        if existing_position is not None:
            result = self._thesis_check(ticker, claude_news, current_price, existing_position, context_block)
        else:
            result = self._claude_analysis(ticker, claude_news, current_price, is_crypto, context_block)

        # None = Claude hart nicht verfügbar (Guthaben/Auth) → für den Rest des
        # Prozesses sperren und sofort lokal ausweichen.
        if result is None:
            self._claude_blocked = True
            log.warning("[%s] Claude nicht verfügbar (Guthaben/Auth) – Ollama übernimmt ab jetzt alles", ticker)
            return _stamp_route(
                self._local_fallback(
                    ticker, news, news_text, price_data, current_price,
                    is_crypto, existing_position, context_block,
                ),
                "ollama_fallback", "Claude Guthaben-/Auth-Fehler während des Calls",
            )

        if self._cost_tracker is not None:
            usage = getattr(self, "_last_usage", None)
            if usage is not None:
                from analyzers.api_cost_tracker import cost_eur_from_usage
                self._cost_tracker.record(
                    claude_called=True, ollama_used=False,
                    actual_cost_eur=cost_eur_from_usage(
                        getattr(self, "_last_model", self.model), *usage),
                )
                self._last_usage = None
            else:
                self._cost_tracker.record(claude_called=True, ollama_used=False)

        # Neueinstiegs-Ergebnis für identische News cachen (Dedup-Skip oben).
        if existing_position is None:
            self._result_cache_store(ticker, news, result)

        _route_reason = (
            "force_claude gesetzt" if force_claude
            else "Katalysator gefunden (SEC 8-K/Earnings)" if _cfg.frugal_mode and _has_catalyst
            else "frugal_mode aus" if not _cfg.frugal_mode
            else "Claude (Default-Pfad)"
        )
        result = _stamp_route(result, "claude", _route_reason)
        return result

    def _local_fallback(
        self, ticker, news, news_text, price_data, current_price,
        is_crypto, existing_position, context_block,
    ) -> AnalysisResult:
        """Ollama übernimmt vollständig (Budget erschöpft / kein Guthaben /
        force-unabhängig). Liefert lokale Analyse bzw. Thesis-Check; nur wenn die
        lokale Engine ebenfalls ausfällt → leeres Ergebnis (SKIP)."""
        local = (
            self._frugal_thesis_check(ticker, news_text, current_price, existing_position, context_block)
            if existing_position is not None
            else self._frugal_local_analysis(ticker, news, price_data, current_price, is_crypto)
        )
        if local is not None:
            if self._cost_tracker is not None:
                self._cost_tracker.record(claude_called=False, ollama_used=True)
            return local
        return self._empty_result(ticker)

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
        return ClaudeAnalyzer._enforce_buy_floor(AnalysisResult(
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
        ))

    @staticmethod
    def _build_context_block(macro_brief: str = "", geo_context=None,
                             mechanical_brief: str = "") -> str:
        """Baut den optionalen Kontext-Block (Makro + Geopolitik + mechanische
        Konviktion aus strategy_lab) für den Prompt."""
        parts = []
        if macro_brief:
            parts.append(macro_brief.strip())
        if geo_context:
            geo_txt = geo_context if isinstance(geo_context, str) else str(geo_context)
            geo_txt = geo_txt.strip()
            if geo_txt:
                parts.append("GEOPOLITIK: " + geo_txt)
        if mechanical_brief and str(mechanical_brief).strip():
            parts.append(str(mechanical_brief).strip())
        if not parts:
            return ""
        return "\n" + "\n\n".join(parts) + "\n"

    # ── Zentraler Claude-Aufruf (Caching + Modell-Tiering + Usage-Tracking) ──

    def _light_model(self) -> str:
        """Leichtes Modell für Nebenaufrufe (Thesis-Check). Leere Config → das
        Hauptmodell (kein Tiering)."""
        from config import config as _cfg
        return getattr(_cfg, "claude_model_light", "") or self.model

    def _cache_control(self) -> Dict:
        """cache_control für gecachte System-Blöcke. ttl="1h" hält den Cache über
        den ganzen (langsamen) Zyklus warm statt nur 5 min."""
        from config import config as _cfg
        cc: Dict = {"type": "ephemeral"}
        ttl = (getattr(_cfg, "claude_cache_ttl", "5m") or "5m").lower()
        if ttl in ("1h", "60m"):
            cc["ttl"] = "1h"
        return cc

    def _system_blocks(self, context_block: str = "") -> List[Dict]:
        """System-Prompt + (pro Zyklus konstanter) Makro/Geo-Kontext als gecachte
        Blöcke. Der Kontext ist für ALLE Ticker eines Zyklus identisch → einmal
        statt pro Ticker als Input bezahlt."""
        cc = self._cache_control()
        blocks = [{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": cc}]
        ctx = (context_block or "").strip()
        if ctx:
            blocks.append({"type": "text", "text": "AKTUELLER MARKTKONTEXT:\n" + ctx,
                           "cache_control": cc})
        return blocks

    def _system_prompt_text(self, context_block: str) -> str:
        """Reine Text-Fassung von _system_blocks() ohne cache_control – fürs
        KI-Prompt-Archiv (Roadmap 1.4d), spiegelt exakt das, was tatsächlich
        an Claude ging."""
        return "\n\n".join(b["text"] for b in self._system_blocks(context_block))

    def _call_claude(self, user_prompt: str, context_block: str, model: str,
                     max_tokens: int) -> str:
        """Einziger Pfad zu client.messages.create – setzt Caching/TTL, das
        gewählte Modell und erfasst Usage+Modell für die Kostenabrechnung.
        Gibt den Antworttext zurück; wirft bei Fehler (Aufrufer behandelt)."""
        from analyzers import llm_client
        extra_headers = {}
        if self._cache_control().get("ttl") == "1h":
            extra_headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"
        resp = llm_client.create_message(
            model=model,
            max_tokens=max_tokens,
            system=self._system_blocks(context_block),
            messages=[{"role": "user", "content": user_prompt}],
            api_key=self.api_key,
            extra_headers=extra_headers or None,
        )
        self._call_count += 1
        self._total_tokens += resp.usage.input_tokens + resp.usage.output_tokens
        self._last_usage = (
            resp.usage.input_tokens, resp.usage.output_tokens,
            int(getattr(resp.usage, "cache_read_input_tokens", 0) or 0),
        )
        self._last_model = model
        return resp.content[0].text

    def _claude_analysis(
        self, ticker: str, news_text: str, price: float, is_crypto: bool,
        context_block: str = "",
    ) -> AnalysisResult:
        # Kontext steckt im gecachten System-Block (s. _system_blocks) → nicht
        # zusätzlich in den User-Prompt (sonst doppelt bezahlt).
        template = _USER_TEMPLATE_CRYPTO if is_crypto else _USER_TEMPLATE_STANDARD
        prompt = template.format(
            ticker=ticker, price=price, news_text=news_text, context_block=""
        )
        try:
            text = self._call_claude(prompt, context_block, self.model, max_tokens=1000)
            result = self._parse_response(ticker, text)
            result.raw_model = self.model
            result.raw_system_prompt = self._system_prompt_text(context_block)
            result.raw_user_prompt = prompt
            result.raw_response = text
            return result
        except Exception as e:
            log.warning("Claude-Analyse fehlgeschlagen für %s: %s", ticker, e)
            # Guthaben-/Auth-Fehler → None signalisieren (Aufrufer sperrt Claude
            # und weicht auf Ollama aus). Soft-Fehler → leeres Ergebnis.
            if self._is_claude_unavailable_error(e):
                return None
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
            context_block="",
        )
        try:
            # Nebenaufruf → leichtes Modell (Haiku): ~1/3 der Sonnet-Kosten.
            text = self._call_claude(
                prompt, context_block, self._light_model(), max_tokens=400
            )
            data = self._safe_json(text)
            result = self._empty_result(ticker)
            result.thesis_valid = data.get("thesis_valid", True)
            result.thesis_break_reason = data.get("thesis_break_reason", "")
            result.sentiment_score = float(data.get("sentiment_score", 0.5))
            result.recommendation = data.get("recommendation", "HOLD")
            result.raw_model = self._light_model()
            result.raw_system_prompt = self._system_prompt_text(context_block)
            result.raw_user_prompt = prompt
            result.raw_response = text
            return result
        except Exception as e:
            log.warning("Thesis-Check fehlgeschlagen für %s: %s", ticker, e)
            if self._is_claude_unavailable_error(e):
                return None
            result = self._empty_result(ticker)
            result.thesis_valid = True
            return result

    @staticmethod
    def _is_claude_unavailable_error(e: Exception) -> bool:
        """Erkennt harte Nicht-Verfügbarkeit (Guthaben leer / Auth) – im
        Unterschied zu transienten Fehlern. Dann übernimmt Ollama ausnahmslos."""
        s = str(e).lower()
        return any(k in s for k in (
            "credit balance", "too low", "billing", "insufficient",
            "authentication", "invalid x-api-key", "401", "403", "permission",
            # Gemini-Gegenstücke (LLM_PROVIDER=gemini)
            "api key not valid", "api_key_invalid", "permission_denied",
            "resource_exhausted", "quota",
        ))

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
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    # Analyst-Leitlinien (u.a. „Kalendertermin allein → kein HOLD")
                    # gelten auch im Frugal-/Ollama-Pfad – sonst kippt das lokale
                    # Modell vor jedem FOMC alle Signale auf HOLD (Funnel zu).
                    "system": _SYSTEM_PROMPT,
                    "stream": False,
                },
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
            return self._enforce_buy_floor(AnalysisResult(
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
            ))
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
    def _enforce_buy_floor(r: AnalysisResult) -> AnalysisResult:
        """Deterministischer BUY-Boden für Neueinstiege.

        Ein überzeugendes bullishes Signal (Richtung BULLISH, Score >= Kaufschwelle,
        Konfidenz MEDIUM/HIGH) darf nicht still auf HOLD/SKIP hängenbleiben. Das
        Modell stuft vor FOMC/CPI-Terminen gern pauschal herab (Doppelzählung der
        Makro-Vorsicht) – darauf darf sich der Funnel nicht verlassen. Das eigentliche
        Risiko-Overlay (Kaufschwelle inkl. Makro-Aufschlag, Quellen-Boden, Korrelation,
        Liquidität, Sizing) wendet die Strategie-Schicht separat an, daher ist ein
        erzwungenes BUY hier risikolos – es passiert weiterhin alle Downstream-Gates.
        """
        try:
            from config import config as _cfg
            thr = float(getattr(_cfg, "buy_threshold", 0.65))
        except Exception:
            thr = 0.65
        try:
            score = float(r.sentiment_score)
        except (TypeError, ValueError):
            return r
        if (
            str(r.direction).upper() == "BULLISH"
            and str(r.recommendation).upper() in ("HOLD", "SKIP")
            and str(r.confidence).upper() in ("MEDIUM", "HIGH")
            and score >= thr
        ):
            log.info(
                "[%s] BUY-Boden: %s/%s @ %.2f → BUY erzwungen (Makro-Overlay in Strategie)",
                r.ticker, r.recommendation, r.confidence, score,
            )
            r.recommendation = "BUY"
        return r

    @staticmethod
    def _empty_result(ticker: str) -> AnalysisResult:
        return AnalysisResult(
            ticker=ticker,
            sentiment_score=0.5,
            direction="NEUTRAL",
            confidence="LOW",
            recommendation="SKIP",
        )

    # ── Dedup-Ergebnis-Cache (identische News → Claude überspringen) ──────────

    _RESULT_CACHE_FILE = os.path.join(
        os.path.dirname(__file__), "..", "data", "claude_result_cache.json"
    )

    @staticmethod
    def _news_fingerprint(ticker: str, news: List[Dict]) -> str:
        """Stabiler Fingerprint der News-Lage (Titel+Quelle, reihenfolgeunabhängig).
        Gleiche News → gleicher Key → derselbe Cache-Eintrag."""
        parts = sorted(
            f"{(a.get('title') or '')[:200]}|{a.get('source') or ''}"
            for a in (news or [])
        )
        raw = ticker.upper() + "\n" + "\n".join(parts)
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def _result_cache_ttl_hours(self) -> float:
        try:
            from config import config as _cfg
            return float(getattr(_cfg, "claude_result_cache_hours", 0) or 0)
        except Exception:
            return 0.0

    def _result_cache_get(self, ticker: str, news: List[Dict]) -> Optional[AnalysisResult]:
        ttl = self._result_cache_ttl_hours()
        if ttl <= 0 or not news:
            return None
        key = self._news_fingerprint(ticker, news)
        try:
            with open(self._RESULT_CACHE_FILE) as f:
                store = json.load(f)
        except Exception:
            return None
        entry = store.get(key)
        if not entry:
            return None
        try:
            age = datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(entry["stored_at"])
            if age > timedelta(hours=ttl):
                return None
            return AnalysisResult(**entry["result"])
        except Exception:
            return None

    def _result_cache_store(self, ticker: str, news: List[Dict], result: AnalysisResult) -> None:
        ttl = self._result_cache_ttl_hours()
        if ttl <= 0 or not news or result is None:
            return
        # Leeres Platzhalter-Ergebnis (Parse-/Engine-Fehler) nicht cachen – sonst
        # würde es eine spätere echte Analyse derselben News unterdrücken.
        if (str(result.recommendation).upper() == "SKIP"
                and str(result.confidence).upper() == "LOW"
                and str(result.direction).upper() == "NEUTRAL"
                and 0.43 <= float(result.sentiment_score or 0.5) <= 0.57):
            return
        key = self._news_fingerprint(ticker, news)
        try:
            with open(self._RESULT_CACHE_FILE) as f:
                store = json.load(f)
        except Exception:
            store = {}
        # raw_*-Felder (Roadmap 1.4d) NICHT in den Dedup-Cache übernehmen: ein
        # späterer Cache-Hit ist kein neuer Claude-Aufruf und würde sonst den
        # alten Prompt fälschlich unter einer neuen analysis_id archivieren
        # (siehe model_route "claude_dedup_cache" in cycle_analysis.py).
        _cached_result = asdict(result)
        for _f in ("raw_model", "raw_system_prompt", "raw_user_prompt", "raw_response"):
            _cached_result.pop(_f, None)
        store[key] = {"stored_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "result": _cached_result}
        # Abgelaufene Einträge beim Schreiben aufräumen (Datei klein halten).
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max(ttl, 1.0))
        for k in [k for k, v in store.items()
                  if self._stored_before(v, cutoff)]:
            store.pop(k, None)
        try:
            os.makedirs(os.path.dirname(self._RESULT_CACHE_FILE), exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=os.path.dirname(self._RESULT_CACHE_FILE),
                suffix=".tmp", delete=False,
            ) as tmp:
                json.dump(store, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self._RESULT_CACHE_FILE)
        except Exception as e:
            log.debug("Result-Cache Speicherfehler: %s", e)

    @staticmethod
    def _stored_before(entry: Dict, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(entry["stored_at"]) < cutoff
        except Exception:
            return True

    def get_stats(self) -> Dict:
        return {
            "calls": self._call_count,
            "total_tokens": self._total_tokens,
        }
