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
    suggested_hold_days: int        # Geschätzter Haltezeitraum in Tagen
    sources_used: int
    raw_summary: str


_SYSTEM_PROMPT = """Du bist ein erfahrener quantitativer Aktienanalyst mit Schwerpunkt auf Swing-Trading (Haltedauer 3–30 Tage).
Du analysierst Nachrichten und Social-Media-Sentiment zu Aktien und gibst strukturierte, konservative Handelsempfehlungen.

Antworte IMMER ausschließlich mit einem validen JSON-Objekt ohne Markdown-Fences oder zusätzlichen Text.
"""

_USER_TEMPLATE = """Analysiere folgende Informationen zur Aktie {ticker}:

=== MARKTDATEN ===
{price_data}

=== NACHRICHTENARTIKEL UND MEINUNGEN ({count} Quellen) ===
{news_text}

=== AUFGABE ===
Bewerte das Gesamt-Sentiment und die Handlungsoption für einen Swing-Trade (3–30 Tage Haltedauer).
Berücksichtige: Nachrichtenlage, Momentum, Risiken, Liquidität.

Antworte mit folgendem JSON (keine anderen Texte):
{{
  "sentiment_score": <float 0.0–1.0, wobei 0=stark bärisch, 0.5=neutral, 1=stark bullisch>,
  "direction": "<BULLISH|NEUTRAL|BEARISH>",
  "confidence": "<LOW|MEDIUM|HIGH>",
  "recommendation": "<BUY|HOLD|SELL|SKIP>",
  "entry_rationale": "<1–2 Sätze: Warum kaufen/verkaufen/halten?>",
  "risk_factors": ["<Risiko 1>", "<Risiko 2>"],
  "key_catalysts": ["<Katalysator 1>", "<Katalysator 2>"],
  "suggested_hold_days": <integer 3–30>,
  "summary": "<3–5 Sätze Gesamtbewertung>"
}}

Vergib BUY nur bei starker, konsistenter Nachrichtenlage. Vergib SKIP wenn zu wenige oder widersprüchliche Informationen vorliegen.
"""


class ClaudeAnalyzer:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def analyze(
        self,
        ticker: str,
        news_items: List[Dict],
        price_data: Optional[Dict] = None,
    ) -> AnalysisResult:
        if not news_items:
            return self._empty_result(ticker, "Keine Nachrichtenartikel verfügbar")

        news_text = self._format_news(news_items)
        price_text = self._format_price(price_data or {})

        prompt = _USER_TEMPLATE.format(
            ticker=ticker,
            price_data=price_text,
            news_text=news_text,
            count=len(news_items),
        )

        message = self._client.messages.create(
            model=config.claude_model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        return self._parse_response(ticker, raw, len(news_items))

    def _format_news(self, items: List[Dict]) -> str:
        parts = []
        for i, item in enumerate(items[:25], 1):
            source = item.get("source", "Unbekannt")
            title = item.get("title", "")
            text = item.get("text", "")
            date = item.get("published_at", "")[:10]
            parts.append(f"[{i}] {date} | {source}\n    {title}\n    {text[:300]}")
        return "\n\n".join(parts)

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
            sources_used=0,
            raw_summary=reason,
        )
