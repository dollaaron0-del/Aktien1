import json
from dataclasses import dataclass
from typing import List, Dict, Optional
import anthropic
from config import config


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


_SYSTEM_PROMPT = """Du bist ein erfahrener quantitativer Aktienanalyst mit Schwerpunkt auf Swing-Trading (Haltedauer 3–30 Tage).
Du analysierst Nachrichten und Social-Media-Sentiment zu Aktien und gibst strukturierte, konservative Handelsempfehlungen.
Du berücksichtigst sowohl aktuelle als auch historische Nachrichtenentwicklungen, um Trendwenden frühzeitig zu erkennen.

Antworte IMMER ausschließlich mit einem validen JSON-Objekt ohne Markdown-Fences oder zusätzlichen Text.
"""

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

=== AKTUELLE NACHRICHTEN ({current_count} Artikel, letzte 24–48h) ===
{current_news}

{historical_block}

=== AUFGABE ===
Bewerte das Gesamt-Sentiment für einen Swing-Trade (3–30 Tage).
Berücksichtige Kontinuität und Trendwenden zwischen historischen und aktuellen Nachrichten.

Antworte mit diesem JSON:
{{
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
  "thesis_valid": null,
  "thesis_break_reason": "",
  "summary": "<3–5 Sätze Gesamtbewertung>"
}}

BUY nur bei starker, konsistenter Nachrichtenlage. SKIP wenn zu wenige oder widersprüchliche Informationen.
"""

# Used when an open position EXISTS – Claude must validate the original thesis
_USER_TEMPLATE_THESIS_CHECK = """Analysiere folgende Informationen zur Aktie {ticker}:

=== MARKTDATEN ===
{price_data}

=== OFFENE POSITION ===
Einstiegspreis: ${entry_price:.2f} | Einstieg: {entry_date} | Halteziel: {hold_days}d
Ursprüngliche Kaufthese: {thesis}
Ursprüngliche Katalysatoren: {catalysts}

=== AKTUELLE NACHRICHTEN ({current_count} Artikel, letzte 24–48h) ===
{current_news}

{historical_block}

=== AUFGABE ===
1. Beurteile ob die ursprüngliche Kaufthese noch gültig ist.
   → Sind die Katalysatoren noch intakt? Hat sich die Nachrichtenlage fundamental verändert?
   → Eine These gilt als GEBROCHEN wenn: die ursprünglichen Treiber weggefallen sind, neue stark negative
     Nachrichten den Kaufgrund widerlegen, oder das Sentiment sich stark umgekehrt hat.
2. Gib eine aktuelle Handlungsempfehlung.

Antworte mit diesem JSON:
{{
  "sentiment_score": <float 0.0–1.0>,
  "direction": "<BULLISH|NEUTRAL|BEARISH>",
  "confidence": "<LOW|MEDIUM|HIGH>",
  "recommendation": "<BUY|HOLD|SELL|SKIP>",
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


class ClaudeAnalyzer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def analyze(
        self,
        ticker: str,
        news_items: List[Dict],
        price_data: Optional[Dict] = None,
        historical_news: Optional[List[Dict]] = None,
        open_position: Optional[Dict] = None,  # dict with entry_price, entry_date, hold_days, thesis, catalysts
        lessons_memo: Optional[str] = None,    # active reflection memo to inject
    ) -> AnalysisResult:
        if not news_items:
            return self._empty_result(ticker, "Keine Nachrichtenartikel verfügbar")

        current_news_text = self._format_news(news_items, label="aktuell")
        historical_block = self._format_historical_block(historical_news or [], news_items)
        price_text = self._format_price(price_data or {})

        if open_position:
            prompt = _USER_TEMPLATE_THESIS_CHECK.format(
                ticker=ticker,
                price_data=price_text,
                entry_price=open_position.get("entry_price", 0),
                entry_date=open_position.get("entry_date", "")[:10],
                hold_days=open_position.get("hold_days", "?"),
                thesis=open_position.get("thesis", "Keine These gespeichert"),
                catalysts=", ".join(open_position.get("catalysts", [])) or "–",
                current_count=len(news_items),
                current_news=current_news_text,
                historical_block=historical_block,
            )
        else:
            prompt = _USER_TEMPLATE_STANDARD.format(
                ticker=ticker,
                price_data=price_text,
                current_count=len(news_items),
                current_news=current_news_text,
                historical_block=historical_block,
            )

        system_prompt = _SYSTEM_PROMPT
        if lessons_memo:
            system_prompt = system_prompt + _LESSONS_PREFIX.format(memo=lessons_memo)

        message = self._client.messages.create(
            model=config.claude_model,
            max_tokens=1200,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        return self._parse_response(ticker, raw, len(news_items))

    def _format_news(self, items: List[Dict], label: str = "", max_items: int = 20) -> str:
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
        return "\n".join(lines)

    def _parse_response(self, ticker: str, raw: str, sources: int) -> AnalysisResult:
        try:
            data = json.loads(raw)
            raw_target = data.get("target_price")
            target_price = float(raw_target) if raw_target is not None else None
            thesis_valid_raw = data.get("thesis_valid")
            thesis_valid = (
                bool(thesis_valid_raw) if thesis_valid_raw is not None else None
            )
            return AnalysisResult(
                ticker=ticker,
                sentiment_score=float(data.get("sentiment_score", 0.5)),
                direction=data.get("direction", "NEUTRAL"),
                confidence=data.get("confidence", "LOW"),
                recommendation=data.get("recommendation", "SKIP"),
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
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return self._empty_result(ticker, f"Parse-Fehler: {raw[:200]}")

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
