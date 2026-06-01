import json
from dataclasses import dataclass
from typing import List, Dict, Optional
import anthropic
from config import config
from analyzers.technical_indicators import TechnicalIndicators, TechnicalSnapshot
from analyzers.ollama_prescreener import OllamaPrescreener
from analyzers.api_cost_tracker import APICostTracker
from analyzers.news_credibility import NewsTrustFilter
from logger import get_logger

# Lazy import – avoids circular import at module load time
def _get_pattern_result_type():
    from analyzers.chart_patterns import PatternResult
    return PatternResult

_trust_filter = NewsTrustFilter()

log = get_logger(__name__)

# Singleton-Instanzen (einmal erstellen, wiederverwenden)
_prescreener = None
_cost_tracker: Optional[APICostTracker] = None
_prescreener_instance = None   # exposed for resource_manager model-switching


def _get_prescreener():
    """
    Returns the best available local LLM backend:
      1. MLX  (fastest on Apple Silicon, if MLX_ENABLED=true and server reachable)
      2. Ollama (if OLLAMA_ENABLED=true)
      3. None  (pure Claude)
    Result is cached as singleton for the process lifetime.
    """
    global _prescreener, _prescreener_instance
    if _prescreener is not None:
        return _prescreener

    if config.mlx_enabled:
        try:
            from analyzers.mlx_prescreener import MLXPrescreener
            mlx = MLXPrescreener(
                base_url=config.mlx_url,
                model=config.mlx_model,
                timeout=config.mlx_timeout,
            )
            if mlx.is_available():
                _prescreener = mlx
                _prescreener_instance = mlx
                log.info("Lokales LLM-Backend: MLX @ %s (%s)", config.mlx_url, config.mlx_model)
                return _prescreener
        except Exception as _e:
            log.debug("MLX-Init fehlgeschlagen: %s", _e)

    if config.ollama_enabled:
        _prescreener = OllamaPrescreener(
            base_url=config.ollama_url,
            model=config.ollama_model,
            timeout=config.ollama_timeout,
        )
        _prescreener_instance = _prescreener
        log.info("Lokales LLM-Backend: Ollama @ %s (%s)", config.ollama_url, config.ollama_model)
        return _prescreener

    return None


def _get_cost_tracker() -> APICostTracker:
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = APICostTracker()
    return _cost_tracker


@dataclass
class AnalysisResult:
    ticker: str
    sentiment_score: float          # 0.0 (stark bärisch) bis 1.0 (stark bullisch)
    direction: str                  # "BULLISH" | "NEUTRAL" | "BEARISH"
    confidence: str                 # "LOW" | "MEDIUM" | "HIGH"
    recommendation: str             # "BUY" | "HOLD" | "SELL" | "SKIP"
    entry_rationale: str
    risk_factors: List[str]
    key_catalysts: List[str]
    suggested_hold_days: int
    target_price: Optional[float]
    target_price_rationale: str
    # Thesis validation (only relevant when an open position exists)
    thesis_valid: Optional[bool]    # None = no open position to check
    thesis_break_reason: str        # Filled if thesis_valid == False
    sources_used: int
    raw_summary: str
    # Bullish/Bearish Debate
    bull_case: str = ""             # Stärkstes Argument FÜR den Trade
    bear_case: str = ""             # Stärkstes Argument GEGEN den Trade
    debate_winner: str = ""         # "BULL" | "BEAR" | "DRAW"
    # Verwandte Aktien (nur bei BUY): profitieren von derselben These
    related_tickers: List[str] = None
    # Bedingter Einstieg (nur bei SKIP mit bullischem Potential)
    entry_trigger_price: Optional[float] = None

    def __post_init__(self):
        if self.related_tickers is None:
            self.related_tickers = []


_SYSTEM_PROMPT = """Du bist ein erfahrener quantitativer Aktienanalyst mit Schwerpunkt auf Swing-Trading (Haltedauer 3–30 Tage).
Du analysierst Nachrichten und Social-Media-Sentiment zu Aktien und gibst strukturierte, konservative Handelsempfehlungen.
Du berücksichtigst sowohl aktuelle als auch historische Nachrichtenentwicklungen, um Trendwenden frühzeitig zu erkennen.
Beachte besonders Volumen-Anomalien (ungewöhnlich hohes/niedriges Handelsvolumen als Bestätigungssignal) und Short-Interest
(hoher Short-Float kann Short-Squeeze-Potential signalisieren oder auf institutionellen Pessimismus hinweisen).

Antworte IMMER ausschließlich mit einem validen JSON-Objekt ohne Markdown-Fences oder zusätzlichen Text.
"""

_GEO_CONTEXT_BLOCK = """
=== GEOPOLITISCHER AUSLÖSER (Bot-Frühsignal) ===
Kategorie:   {kategorie}
Schwere:     {schwere_label} (Stufe {schwere}/3)
Schlagzeile: {schlagzeile}
Quelle:      {quelle}
Marktthese:  {marktthese}

⚠️  Dieser Ticker wurde vom Geopolitischen Radar identifiziert – BEVOR der Mainstream reagiert hat.
Bewerte ob die geopolitische These den Kauf rechtfertigt. Falls technisch NICHT bestätigt
(RSI überkauft, kein Volumen-Anstieg, Kurs bereits stark gestiegen), bevorzuge SKIP.
=== ENDE GEOPOLITISCHER KONTEXT ===
"""

def _is_crypto(ticker: str) -> bool:
    try:
        from analyzers.crypto_universe import CRYPTO_UNIVERSE
        base = ticker.split("/")[0].upper()
        return base in CRYPTO_UNIVERSE or ticker.upper().endswith("-USD") or ticker.upper().endswith("/USD")
    except Exception:
        return False


_LESSONS_PREFIX = """

=== AKTUELLE LESSONS-LEARNED (aus eigenen Trades) ===
{memo}
=== ENDE LESSONS ===
Berücksichtige diese Erfahrungen aktiv bei deiner Empfehlung.
"""

# Used when there is NO open position – standard buy/skip evaluation
_USER_TEMPLATE_STANDARD = """Analysiere folgende Informationen zur Aktie {ticker}:

=== MARKTDATEN ===
{price_data}

{volume_block}
{tech_block}

{eu_market_block}

=== AKTUELLE NACHRICHTEN ({current_count} Artikel, letzte 24–48h) ===
{current_news}

{historical_block}

=== AUFGABE ===
Führe eine strukturierte Zwei-Seiten-Analyse für einen Swing-Trade (3–30 Tage) durch:

SCHRITT 1 – INTERNE DEBATTE:
Überlege zuerst intern (ohne separate Ausgabe):
  BULLISCH: Was sind die 2–3 stärksten Argumente FÜR einen Kauf?
  BÄRISCH:  Was sind die 2–3 stärksten Argumente GEGEN einen Kauf?
  URTEIL:   Welche Seite überwiegt klar – und warum?

Berücksichtige dabei:
- Kontinuität und Trendwenden zwischen historischen und aktuellen Nachrichten
- RSI-Extremwerte (>70 überkauft, <30 überverkauft), MACD-Kreuzungen,
  Bollinger-Band-Position und EMA-Trend als Signalbestätigung
- Congressional/Insider-Käufe als starke bullische Signale
- Analyst-Upgrades/-Downgrades als Bestätigung oder Warnung

SCHRITT 2 – AUSGABE als JSON:
{{
  "bull_case": "<1–2 Sätze: das stärkste Bullish-Argument>",
  "bear_case": "<1–2 Sätze: das stärkste Bearish-Argument>",
  "debate_winner": "<BULL|BEAR|DRAW>",
  "sentiment_score": <float 0.0–1.0>,
  "direction": "<BULLISH|NEUTRAL|BEARISH>",
  "confidence": "<LOW|MEDIUM|HIGH>",
  "recommendation": "<BUY|HOLD|SELL|SKIP>",
  "entry_rationale": "<1–2 Sätze: Kaufgrund oder warum skip/sell>",
  "risk_factors": ["<Risiko 1>", "<Risiko 2>"],
  "key_catalysts": ["<Katalysator 1>", "<Katalysator 2>"],
  "suggested_hold_days": <integer 3–30>,
  "target_price": <float Zielkurs USD, oder null>,
  "target_price_rationale": "<1 Satz Begründung>",
  "entry_trigger_price": <float oder null>,
  "thesis_valid": null,
  "thesis_break_reason": "",
  "related_tickers": [],
  "summary": "<3–5 Sätze Gesamtbewertung>"
}}

BUY nur wenn BULL-Seite klar überwiegt UND technische Signale bestätigen.
confidence-Regeln: HIGH wenn ≥3 unabhängige Quellen + technische UND fundamentale Signale
  übereinstimmen + score ≥ 0.72 + kein wesentlicher Widerspruch im Bear-Case.
  MEDIUM wenn 2 Quellen oder nur eines der Systeme bestätigt. LOW sonst.
entry_trigger_price: NUR bei SKIP mit bullischem Potential (score ≥ 0.50, direction BULLISH/NEUTRAL):
  Der Kurs-Level bei dem das Risk/Reward zu einem BUY kippt.
  Wichtig: Wähle das NÄCHSTE Unterstützungsniveau das max. 5% unter dem aktuellen Kurs liegt.
  Wenn EMA21 oder Bollinger-Band weiter als 5% entfernt sind, setze den Trigger trotzdem
  auf 2–4% unter aktuellem Kurs (leichter Pullback reicht als Bestätigung).
  Ziel: Trigger soll realistisch erreichbar sein, nicht der absolute Tiefpunkt.
  Null wenn klar BEARISH, zu unklar, oder offene Position vorhanden.
SKIP wenn DRAW oder wenn BEAR-Argumente das Risiko zu hoch machen.
Bei BUY: trage in "related_tickers" 2–3 Ticker-Symbole ein die von derselben These profitieren
(z.B. Zulieferer, Wettbewerber, Abnehmer). Leer lassen bei HOLD/SKIP/SELL.
"""

# Used when an open position EXISTS – Claude must validate the original thesis
_USER_TEMPLATE_THESIS_CHECK = """Analysiere folgende Informationen zur Aktie {ticker}:

=== MARKTDATEN ===
{price_data}

{tech_block}

=== OFFENE POSITION ===
Einstiegspreis: ${entry_price:.2f} | Einstieg: {entry_date} | Halteziel: {hold_days}d
Ursprüngliche Kaufthese: {thesis}
Ursprüngliche Katalysatoren: {catalysts}

=== AKTUELLE NACHRICHTEN ({current_count} Artikel, letzte 24–48h) ===
{current_news}

{historical_block}

=== AUFGABE ===
SCHRITT 1 – INTERNE DEBATTE:
  HALTEN:   Was spricht dafür die Position zu behalten? (These noch intakt, Katalysatoren aktiv?)
  VERKAUFEN: Was spricht für einen Ausstieg? (These gebrochen, neue Risiken, technische Warnsignale?)
  Eine These gilt als GEBROCHEN wenn: ursprüngliche Treiber weggefallen, stark negative neue
  Nachrichten den Kaufgrund widerlegen, Sentiment sich fundamental umgekehrt hat,
  oder technische Warnsignale: RSI >75, MACD-Negativkreuzung, Kurs unter EMA21.

SCHRITT 2 – AUSGABE als JSON:
{{
  "bull_case": "<1–2 Sätze: stärkstes Argument die Position zu halten>",
  "bear_case": "<1–2 Sätze: stärkstes Argument für Ausstieg>",
  "debate_winner": "<BULL|BEAR|DRAW>",
  "sentiment_score": <float 0.0–1.0>,
  "direction": "<BULLISH|NEUTRAL|BEARISH>",
  "confidence": "<LOW|MEDIUM|HIGH>",
  "recommendation": "<HOLD|SELL|SKIP>",
  "entry_rationale": "<1–2 Sätze aktuelle Lage>",
  "risk_factors": ["<Risiko 1>", "<Risiko 2>"],
  "key_catalysts": ["<noch aktiver Katalysator>"],
  "suggested_hold_days": <integer 3–30>,
  "target_price": <float oder null>,
  "target_price_rationale": "<1 Satz>",
  "thesis_valid": <true | false>,
  "thesis_break_reason": "<Wenn false: konkreter Grund warum die These gebrochen ist, sonst leer>",
  "summary": "<3–5 Sätze>"
}}
"""


# Crypto: technical signals PRIMARY, on-chain SECONDARY, news TERTIARY context
_USER_TEMPLATE_CRYPTO = """Analysiere folgende Informationen zum Krypto-Asset {ticker}:

=== MARKTDATEN ===
{price_data}

{tech_block}

{pattern_block}

{onchain_block}

=== AUFGABE (TECHNISCHE ANALYSE ZUERST) ===
Kryptowährungen reagieren primär auf technische Chart-Signale – Nachrichten sind sekundärer Kontext.

SCHRITT 1 – TECHNISCHE ANALYSE (Hauptsignal):
Bewerte die Chart-Muster, Moving Averages, RSI und Volumen-Trends oben.
  - Golden Cross / Death Cross (MA50 vs MA200) als Trendbestätigung
  - Double Bottom / Bull Flag → starke Kaufsignale
  - Double Top / Bear Flag / Head & Shoulders → Warnsignale
  - RSI-Divergenz: bullische Divergenz (neues Preis-Tief, RSI steigt) = Trendwende
  - Volumen-Bestätigung: Ausbrüche MÜSSEN mit erhöhtem Volumen kommen
  Vergib einen technischen Score 0–100 und leite daraus die primäre Richtung ab.

SCHRITT 2 – ON-CHAIN DATEN (Bestätigung):
Falls On-Chain-Daten vorhanden: prüfe ob NVT, aktive Adressen und Hash Rate
das technische Signal bestätigen oder widersprechen.
  - Niedriger NVT + steigendes technisches Signal → starke Konvergenz (bullisch)
  - Hoher NVT trotz Kursanstieg → Vorsicht (Überbewertung möglich)
  - Sinkende aktive Adressen trotz Kursanstieg → Distribution, kein echtes Kaufinteresse

SCHRITT 3 – NEWS ALS KONTEXT (tertiär):
=== AKTUELLE NACHRICHTEN ({current_count} Artikel, letzte 24–48h) ===
{current_news}

{historical_block}

Nutze die Nachrichten nur zur Bestätigung oder als Warnsignal bei extremen Entwicklungen
(z.B. Regulierungsverbote, Exchange-Zusammenbrüche). Einzelne bullische News ohne technische
Bestätigung sind KEIN Kaufsignal.

SCHRITT 4 – INTERNE DEBATTE:
  BULLISCH: Technische Signale + On-Chain-Bestätigung + ergänzende News
  BÄRISCH:  Technische Warnsignale + On-Chain-Schwäche + News-Risiken
  Ohne technische UND On-Chain-Bestätigung: KEIN BUY, maximal HOLD.

SCHRITT 5 – AUSGABE als JSON:
{{
  "bull_case": "<1–2 Sätze: stärkstes technisches + News-Argument FÜR>",
  "bear_case": "<1–2 Sätze: stärkstes technisches + News-Argument GEGEN>",
  "debate_winner": "<BULL|BEAR|DRAW>",
  "sentiment_score": <float 0.0–1.0>,
  "direction": "<BULLISH|NEUTRAL|BEARISH>",
  "confidence": "<LOW|MEDIUM|HIGH>",
  "recommendation": "<BUY|HOLD|SELL|SKIP>",
  "entry_rationale": "<1–2 Sätze: technischer Kaufgrund oder warum skip/sell>",
  "risk_factors": ["<Risiko 1>", "<Risiko 2>"],
  "key_catalysts": ["<technischer Katalysator 1>", "<Katalysator 2>"],
  "suggested_hold_days": <integer 1–14>,
  "target_price": <float Zielkurs USD, oder null>,
  "target_price_rationale": "<1 Satz: technisches Kursziel (z.B. Widerstand, Fib-Level)>",
  "thesis_valid": null,
  "thesis_break_reason": "",
  "summary": "<3–5 Sätze: technische Gesamtbewertung, News als Kontext>"
}}

BUY nur wenn Chart-Muster UND technische Indikatoren eindeutig bullisch sind.
SKIP/SELL wenn keine klare technische Bestätigung vorliegt.
"""


class ClaudeAnalyzer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def analyze(
        self,
        ticker: str,
        news_items: List[Dict],
        price_data: Optional[Dict] = None,
        historical_news: Optional[List[Dict]] = None,
        open_position: Optional[Dict] = None,
        lessons_memo: Optional[str] = None,
        weekly_briefing: Optional[str] = None,
        technical: Optional[TechnicalSnapshot] = None,
        pattern_result=None,    # Optional[PatternResult] – lazy-imported to avoid circular dep
        onchain_snapshot=None,  # Optional[OnChainSnapshot]
        eu_market_snapshot=None,  # Optional[EUMarketSnapshot]
        geo_context: Optional[Dict] = None,  # Geopolitischer Auslöser aus GeopoliticalRadar
        force_claude: bool = False,  # True = Ollama-Prescreen + Budget-Check überspringen (manuelle Anfragen)
    ) -> AnalysisResult:
        if not news_items:
            return self._empty_result(ticker, "Keine Nachrichtenartikel verfügbar")

        # ── Ollama Pre-Screening ───────────────────────────────────────────────
        prescreener  = _get_prescreener()
        cost_tracker = _get_cost_tracker()

        # ── News-Glaubwürdigkeits-Filter ────────────────────────────────────────
        news_items, trust_report = _trust_filter.filter(news_items)
        if trust_report.get("dropped", 0) > 0:
            log.info(
                "[%s] NewsTrust: %d/%d Artikel entfernt (avg_trust=%.2f, koordiniert: %s)",
                ticker,
                trust_report["dropped"],
                trust_report["total"],
                trust_report["avg_trust"],
                trust_report.get("coordinated") or "keine",
            )
        if not news_items:
            return self._empty_result(ticker, "Alle Artikel nach Glaubwürdigkeits-Filter entfernt")

        # ── Frugal-Modus: Ollama übernimmt vollständige Analyse ─────────────────
        from analyzers.ollama_prescreener import (
            _ALWAYS_CLAUDE_SOURCES, _WEAK_MODEL_CLAUDE_SOURCES
        )
        _high_priority_sources = _ALWAYS_CLAUDE_SOURCES.copy()
        if not prescreener or prescreener.capability in ("LOW", "MEDIUM"):
            _high_priority_sources |= _WEAK_MODEL_CLAUDE_SOURCES
        _has_high_priority = any(
            any(src in (item.get("source") or "") for src in _high_priority_sources)
            for item in news_items
        )
        # Frugal Full-Analysis: nur harte Quellen (SEC 8-K, Earnings Transcript)
        # blockieren den Ollama-Pfad — schwache Quellen (Analyst Rating, Short Interest)
        # werden von Ollama genauso gut verarbeitet, kein Grund für Claude-Eskalation.
        _has_hard_priority = any(
            any(src in (item.get("source") or "") for src in _ALWAYS_CLAUDE_SOURCES)
            for item in news_items
        )
        # Smart buy threshold: 32b gets 0.70, smaller models get configured value
        _buy_min = config.frugal_buy_min_score
        if config.frugal_smart_mode and prescreener and prescreener.capability == "HIGH":
            _buy_min = max(_buy_min, 0.70)
        if (
            config.frugal_mode
            and prescreener
            and not force_claude
            and not open_position
            and not _has_hard_priority  # nur SEC 8-K / Earnings blockieren; schwache Quellen OK
        ):
            result_data = prescreener.full_analysis(
                ticker=ticker,
                news_items=news_items,
                price_data=price_data,
                buy_min_score=_buy_min,
            )
            if result_data is not None:
                cost_tracker.record(claude_called=False, ollama_used=True)
                return AnalysisResult(
                    ticker=ticker,
                    sentiment_score=result_data["sentiment_score"],
                    direction=result_data["direction"],
                    confidence=result_data["confidence"],
                    recommendation=result_data["recommendation"],
                    entry_rationale=f"[Frugal/Ollama] {result_data['entry_rationale']}",
                    risk_factors=result_data["risk_factors"],
                    key_catalysts=result_data["key_catalysts"],
                    suggested_hold_days=result_data["suggested_hold_days"],
                    target_price=None,
                    target_price_rationale="",
                    thesis_valid=None,
                    thesis_break_reason="",
                    sources_used=len(news_items),
                    raw_summary=f"[Frugal] {result_data['summary']}",
                )
            # Ollama fehlgeschlagen → Claude als Fallback
            log.info("[%s] Frugal-Modus: Ollama fehlgeschlagen – Claude als Fallback", ticker)

        # ── API-Kosten-Limit (übersprungen bei manueller Anfrage) ───────────────
        if not force_claude:
            cost_ok, cost_reason = cost_tracker.check_daily_limit()
            if not cost_ok:
                log.warning("[%s] %s", ticker, cost_reason)
                return self._empty_result(ticker, cost_reason)

        if prescreener and not force_claude:
            prescreen = prescreener.prescreen(
                ticker=ticker,
                news_items=news_items,
                has_open_position=bool(open_position),
            )

            if not prescreen.send_to_claude:
                # Ollama sagt: kein Claude-Aufruf nötig
                log.info(
                    "Ollama SKIP [%s]: score=%.2f %s – %s",
                    ticker, prescreen.score, prescreen.direction, prescreen.skip_reason,
                )
                cost_tracker.record(claude_called=False, ollama_used=True)
                # Gib direktes Ergebnis zurück basierend auf Ollama-Score
                return self._result_from_prescreen(ticker, prescreen, len(news_items))

            if not prescreen.ollama_used:
                cost_tracker.record_fallback()

        # Auto-calculate technicals if not provided
        if technical is None:
            try:
                technical = TechnicalIndicators().calculate(ticker)
            except Exception:
                technical = None

        # Ollama-Komprimierung: fasst News zusammen → ~50% weniger Claude-Tokens
        # Erst ab 8 Artikeln sinnvoll – bei weniger Artikeln ist der Komprimierungs-Overhead
        # größer als der Gewinn durch kürzere Claude-Prompts.
        _comp_prescreener = _get_prescreener()
        if _comp_prescreener and len(news_items) >= 8:
            compressed = _comp_prescreener.compress_news(ticker, news_items)
            if compressed:
                current_news_text = (
                    f"[Ollama-Zusammenfassung aus {len(news_items)} Artikeln]\n\n"
                    + compressed
                )
                log.debug("[%s] News komprimiert: %d → %d chars", ticker, sum(
                    len(x.get("text") or "") for x in news_items), len(compressed))
            else:
                current_news_text = self._format_news(news_items, label="aktuell")
        else:
            current_news_text = self._format_news(news_items, label="aktuell")
        historical_block = self._format_historical_block(historical_news or [], news_items)
        price_text = self._format_price(price_data or {})
        volume_block  = self._format_volume_block(price_data or {})
        tech_block    = technical.to_prompt_block() if technical else ""
        pattern_block = pattern_result.to_prompt_block() if pattern_result else ""
        onchain_block = onchain_snapshot.to_prompt_block() if onchain_snapshot else ""
        # EU market block: only for EU tickers, include BoE note for .L tickers
        include_boe = ticker.upper().endswith(".L")
        eu_market_block = (
            eu_market_snapshot.to_prompt_block(include_boe=include_boe)
            if eu_market_snapshot else ""
        )

        if open_position:
            prompt = _USER_TEMPLATE_THESIS_CHECK.format(
                ticker=ticker,
                price_data=price_text,
                tech_block=tech_block,
                entry_price=open_position.get("entry_price", 0),
                entry_date=open_position.get("entry_date", "")[:10],
                hold_days=open_position.get("hold_days", "?"),
                thesis=open_position.get("thesis", "Keine These gespeichert"),
                catalysts=", ".join(open_position.get("catalysts", [])) or "–",
                current_count=len(news_items),
                current_news=current_news_text,
                historical_block=historical_block,
            )
        elif _is_crypto(ticker):
            prompt = _USER_TEMPLATE_CRYPTO.format(
                ticker=ticker,
                price_data=price_text,
                tech_block=tech_block,
                pattern_block=pattern_block,
                onchain_block=onchain_block,
                current_count=len(news_items),
                current_news=current_news_text,
                historical_block=historical_block,
            )
        else:
            prompt = _USER_TEMPLATE_STANDARD.format(
                ticker=ticker,
                price_data=price_text,
                volume_block=volume_block,
                tech_block=tech_block,
                eu_market_block=eu_market_block,
                current_count=len(news_items),
                current_news=current_news_text,
                historical_block=historical_block,
            )

        system_prompt = _SYSTEM_PROMPT
        if lessons_memo:
            system_prompt = system_prompt + _LESSONS_PREFIX.format(memo=lessons_memo)
        if weekly_briefing:
            system_prompt = system_prompt + (
                "\n\n=== WOCHENBRIEFING (Marktkontext diese Woche) ===\n"
                + weekly_briefing
                + "\n=== ENDE WOCHENBRIEFING ===\n"
                "Berücksichtige diesen Wochenkontext (Earnings-Risiken, Sektorstimmung, Makro) "
                "bei deiner Analyse und Empfehlung.\n"
            )
        if geo_context:
            _schwere = geo_context.get("schwere", 1)
            _schwere_label = {1: "Gering", 2: "Mittel", 3: "Kritisch"}.get(_schwere, "Unbekannt")
            system_prompt = system_prompt + _GEO_CONTEXT_BLOCK.format(
                kategorie=geo_context.get("kategorie", "Unbekannt"),
                schwere=_schwere,
                schwere_label=_schwere_label,
                schlagzeile=geo_context.get("schlagzeile", "–"),
                quelle=geo_context.get("quelle", "–"),
                marktthese=geo_context.get("marktthese", "–"),
            )

        # Prompt caching: system_prompt is identical across all tickers in a cycle.
        # Marking it ephemeral caches it for ~5 min → ~85% savings on system-prompt tokens.
        system_blocks = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]

        message = self._client.messages.create(
            model=config.claude_model,
            max_tokens=1500,
            system=system_blocks,
            messages=[{"role": "user", "content": prompt}],
        )

        cost_tracker = _get_cost_tracker()
        usage = message.usage
        cache_hit = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost_tracker.record(
            claude_called=True,
            ollama_used=bool(prescreener),
            cache_hit_tokens=cache_hit,
        )

        raw = message.content[0].text.strip()
        return self._parse_response(ticker, raw, len(news_items))

    def _format_news(self, items: List[Dict], label: str = "", max_items: int = 15) -> str:
        parts = []
        for i, item in enumerate(items[:max_items], 1):
            source = item.get("source", "Unbekannt")
            title = item.get("title", "")
            text = (item.get("text") or "")[:300]
            date = (item.get("published_at") or "")[:10]
            parts.append(f"[{i}] {date} | {source}\n    {title}\n    {text}")
        return "\n\n".join(parts)

    def _format_historical_block(
        self, historical: List[Dict], current: List[Dict]
    ) -> str:
        if not historical:
            return ""
        # Exclude titles already in current news
        current_titles = {item.get("title", "") for item in current}
        filtered = [h for h in historical if h.get("title", "") not in current_titles]
        if not filtered:
            return ""
        text = self._format_news(filtered[:30], max_items=30)
        return (
            f"=== HISTORISCHE NACHRICHTEN (letzte 30 Tage, {len(filtered)} Artikel) ===\n"
            f"Hinweis: Diese Artikel zeigen den Nachrichtenverlauf. Erkenne Trendwenden und Stimmungsveränderungen.\n\n"
            f"{text}"
        )

    def _format_price(self, data: Dict) -> str:
        if not data.get("current_price"):
            return "Keine Marktdaten verfügbar."
        lines = [
            f"Aktueller Kurs: ${data.get('current_price')}",
            f"Änderung 1 Woche: {data.get('price_change_1w')}%",
            f"Änderung 1 Monat: {data.get('price_change_1m')}%",
            f"Sektor: {data.get('sector', 'N/A')}",
            f"KGV: {data.get('pe_ratio', 'N/A')}",
            f"52W-Hoch: ${data.get('52w_high', 'N/A')} / 52W-Tief: ${data.get('52w_low', 'N/A')}",
        ]
        # Volumen-Daten hinzufügen wenn verfügbar
        volume_today = data.get("volume_today")
        volume_avg   = data.get("volume_avg")
        short_interest = data.get("short_interest_pct")
        if volume_today is not None:
            lines.append(f"Volumen heute: {int(volume_today):,}")
        if volume_avg is not None:
            lines.append(f"Ø Volumen (30d): {int(volume_avg):,}")
        if short_interest is not None:
            lines.append(f"Short Interest: {short_interest}%")
        return "\n".join(lines)

    def _format_volume_block(self, data: Dict) -> str:
        """Erstellt einen separaten Volume-Block für den Prompt."""
        volume_today   = data.get("volume_today")
        volume_avg     = data.get("volume_avg")
        short_interest = data.get("short_interest_pct")
        if not any([volume_today is not None, volume_avg is not None, short_interest is not None]):
            return ""
        lines = ["=== VOLUMEN & SHORT-INTEREST ==="]
        if volume_today is not None:
            lines.append(f"Volumen heute: {int(volume_today):,}")
        if volume_avg is not None:
            lines.append(f"Ø Volumen (30d): {int(volume_avg):,}")
            if volume_today is not None and volume_avg > 0:
                ratio = volume_today / volume_avg
                lines.append(f"Volumen-Ratio (heute/30d-Ø): {ratio:.2f}x")
        if short_interest is not None:
            lines.append(f"Short Interest: {short_interest}%")
        return "\n".join(lines)

    def _parse_response(self, ticker: str, raw: str, sources: int) -> AnalysisResult:
        def _try_parse(text: str) -> Optional[dict]:
            """Versucht JSON zu parsen, gibt None bei Fehler zurück."""
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return None

        # Erster Versuch: direkt parsen
        data = _try_parse(raw)

        # Zweiter Versuch: Markdown-Fences entfernen
        if data is None:
            cleaned = raw.strip().strip("```json").strip("```").strip()
            data = _try_parse(cleaned)

        # Dritter Versuch: JSON-Block extrahieren (erstes { bis letztes })
        if data is None:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = _try_parse(raw[start:end])

        if data is None:
            log.warning("[%s] JSON-Parse fehlgeschlagen: %s", ticker, raw[:200])
            return self._empty_result(ticker, f"Parse-Fehler: {raw[:200]}")

        try:
            raw_target = data.get("target_price")
            target_price = float(raw_target) if raw_target is not None else None
            raw_trigger = data.get("entry_trigger_price")
            entry_trigger_price = float(raw_trigger) if raw_trigger is not None else None
            thesis_valid_raw = data.get("thesis_valid")
            thesis_valid = (
                bool(thesis_valid_raw) if thesis_valid_raw is not None else None
            )
            rec = data.get("recommendation", "SKIP")
            # Crypto safety: prompt instructs KEIN BUY but model can still output BUY
            if _is_crypto(ticker) and rec == "BUY":
                log.warning("[%s] Crypto BUY → HOLD override (post-parse safety)", ticker)
                rec = "HOLD"
            return AnalysisResult(
                ticker=ticker,
                sentiment_score=float(data.get("sentiment_score", 0.5)),
                direction=data.get("direction", "NEUTRAL"),
                confidence=data.get("confidence", "LOW"),
                recommendation=rec,
                entry_rationale=data.get("entry_rationale", ""),
                risk_factors=data.get("risk_factors", []),
                key_catalysts=data.get("key_catalysts", []),
                suggested_hold_days=int(data.get("suggested_hold_days", 7)),
                target_price=target_price,
                target_price_rationale=data.get("target_price_rationale", ""),
                thesis_valid=thesis_valid,
                thesis_break_reason=data.get("thesis_break_reason", ""),
                sources_used=sources,
                raw_summary=data.get("summary", ""),
                bull_case=data.get("bull_case", ""),
                bear_case=data.get("bear_case", ""),
                debate_winner=data.get("debate_winner", ""),
                related_tickers=[
                    t.strip().upper() for t in data.get("related_tickers", [])
                    if isinstance(t, str) and t.strip()
                ],
                entry_trigger_price=entry_trigger_price,
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("[%s] AnalysisResult-Aufbau fehlgeschlagen: %s", ticker, exc)
            return self._empty_result(ticker, f"Parse-Fehler: {raw[:200]}")

    def _result_from_prescreen(self, ticker: str, prescreen, sources: int) -> AnalysisResult:
        """Erzeugt AnalysisResult direkt aus Ollama-Score (kein Claude-Aufruf).

        Ollama entscheidet NIE SELL — nur SKIP oder HOLD.
        SELL erfordert immer Claude (firmenspezifische Analyse).
        """
        score      = prescreen.score
        direction  = prescreen.direction
        confidence = prescreen.confidence

        # Ollama darf nur SKIP oder HOLD empfehlen, niemals SELL oder BUY
        recommendation = "SKIP" if score < 0.35 else "HOLD"

        return AnalysisResult(
            ticker=ticker,
            sentiment_score=score,
            direction=direction,
            confidence=confidence,
            recommendation=recommendation,
            entry_rationale=f"[Ollama] {prescreen.reason}",
            risk_factors=[],
            key_catalysts=[],
            suggested_hold_days=7,
            target_price=None,
            target_price_rationale="",
            thesis_valid=None,
            thesis_break_reason="",
            sources_used=sources,
            raw_summary=f"Ollama Pre-Screen: {direction} ({score:.2f}) – Claude nicht gerufen.",
        )

    def _empty_result(self, ticker: str, reason: str) -> AnalysisResult:
        return AnalysisResult(
            ticker=ticker,
            sentiment_score=0.5,
            direction="NEUTRAL",
            confidence="LOW",
            recommendation="SKIP",
            entry_rationale=reason,
            risk_factors=[],
            key_catalysts=[],
            suggested_hold_days=0,
            target_price=None,
            target_price_rationale="",
            thesis_valid=None,
            thesis_break_reason="",
            sources_used=0,
            raw_summary=reason,
        )
