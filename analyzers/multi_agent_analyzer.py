"""
Multi-Agent Analyzer – 3 Claude-Analysten parallel, Konsens-Entscheidung.

Bull-, Bear- und Risk-Analyst laufen gleichzeitig via ThreadPoolExecutor.
Empfehlung = BUY nur wenn >= 2 von 3 Analysten BUY ausgeben.
Bear-Analyst hat 1.3× Gewicht beim sentiment_score (konservative Vorsicht).

Aktivierung: MULTI_AGENT_ENABLED=true in .env  (default: false, 3× Kosten!)
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional

from analyzers import llm_client

from analyzers.api_cost_tracker import APICostTracker
from analyzers.llm_analyzer import AnalysisResult
from analyzers.technical_indicators import TechnicalSnapshot
from config import config
from logger import get_logger

log = get_logger(__name__)

_MULTI_AGENT_ENABLED = os.getenv("MULTI_AGENT_ENABLED", "false").lower() == "true"

# ── System-Prompts der drei Agenten ──────────────────────────────────────────

_BULL_SYSTEM = (
    "Du bist ein optimistischer Growth-Investor der Wachstumspotenzial sucht. "
    "Du gewichtest positive Katalysatoren, Umsatzwachstum und Momentum höher als Risiken. "
    "Antworte IMMER ausschließlich mit einem validen JSON-Objekt ohne Markdown-Fences."
)

_BEAR_SYSTEM = (
    "Du bist ein konservativer Value-Investor der primär Risiken und Überbewertungen identifiziert. "
    "Du gewichtest Bewertungsrisiken, Schuldenlast und Marktrisiken höher als Chancen. "
    "Im Zweifel empfiehlst du HOLD oder SELL statt BUY. "
    "Antworte IMMER ausschließlich mit einem validen JSON-Objekt ohne Markdown-Fences."
)

_RISK_SYSTEM = (
    "Du bist ein neutraler Risikomanager der ausschließlich die Wahrscheinlichkeit eines Verlustes bewertet. "
    "Du berücksichtigst Volatilität, Liquidität, Nachrichtenunsicherheit und Downside-Szenarien. "
    "Antworte IMMER ausschließlich mit einem validen JSON-Objekt ohne Markdown-Fences."
)

_AGENT_USER_TEMPLATE = """Analysiere folgende Aktie für einen Swing-Trade (3–30 Tage).

Ticker: {ticker}
{context_block}
=== MARKTDATEN ===
{price_data}

=== AKTUELLE NACHRICHTEN ({n_news} Artikel) ===
{news_text}

Gib NUR dieses JSON zurück:
{{
  "recommendation": "BUY"|"HOLD"|"SELL"|"SKIP",
  "sentiment_score": <0.0–1.0>,
  "rationale": "<1 klarer Satz>",
  "confidence": "LOW"|"MEDIUM"|"HIGH"
}}"""

# Gewichte pro Agent (Bear 1.3× für Vorsicht)
_WEIGHTS: Dict[str, float] = {"bull": 1.0, "bear": 1.3, "risk": 1.0}


@dataclass
class _AgentResult:
    agent: str
    recommendation: str
    sentiment_score: float
    rationale: str
    confidence: str


class MultiAgentAnalyzer:
    """Drei Claude-Agenten parallel – Konsens-Analyse."""

    def __init__(self) -> None:
        self._client = llm_client.client_or_none()
        self._cost_tracker = APICostTracker()

    # ── Public API (gleiche Signatur wie ClaudeAnalyzer.analyze) ─────────────

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
        macro_brief: str = "",
        geo_context=None,
        **kwargs,
    ) -> AnalysisResult:
        # **kwargs schluckt zusätzliche Runner-Argumente (pattern_result,
        # onchain_snapshot, eu_market_snapshot, force_claude …) ohne TypeError.
        if not _MULTI_AGENT_ENABLED:
            log.warning("[%s] MultiAgentAnalyzer deaktiviert (MULTI_AGENT_ENABLED=false)", ticker)
            return self._empty_result(ticker, "MULTI_AGENT_ENABLED=false")

        if not news_items:
            return self._empty_result(ticker, "Keine Nachrichtenartikel verfügbar")

        cost_ok, cost_reason = self._cost_tracker.check_daily_limit()
        if not cost_ok:
            log.warning("[%s] Kostenlimit erreicht: %s", ticker, cost_reason)
            return self._empty_result(ticker, cost_reason)

        price_text = self._format_price(price_data or {})
        news_text = self._format_news(news_items)
        context_block = self._build_context_block(macro_brief, geo_context)

        agents = {
            "bull": _BULL_SYSTEM,
            "bear": _BEAR_SYSTEM,
            "risk": _RISK_SYSTEM,
        }

        results: List[_AgentResult] = []

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(
                    self._call_agent,
                    name,
                    system_prompt,
                    ticker,
                    price_text,
                    news_text,
                    len(news_items),
                    context_block,
                ): name
                for name, system_prompt in agents.items()
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    result = fut.result()
                    results.append(result)
                    log.info(
                        "[%s] Agent %s → %s (score=%.2f, conf=%s)",
                        ticker, name, result.recommendation, result.sentiment_score, result.confidence,
                    )
                except Exception as exc:
                    log.error("[%s] Agent %s fehlgeschlagen: %s", ticker, name, exc)

        if not results:
            return self._empty_result(ticker, "Alle Agenten fehlgeschlagen")

        return self._build_consensus(ticker, results, len(news_items))

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_context_block(macro_brief: str = "", geo_context=None) -> str:
        """Makro- + Geopolitik-Block für den Agenten-Prompt (leer wenn nichts da)."""
        parts = []
        if macro_brief:
            parts.append(macro_brief.strip())
        if geo_context:
            geo_txt = geo_context if isinstance(geo_context, str) else str(geo_context)
            geo_txt = geo_txt.strip()
            if geo_txt:
                parts.append("=== GEOPOLITIK ===\n" + geo_txt)
        if not parts:
            return ""
        return "\n" + "\n\n".join(parts) + "\n"

    def _call_agent(
        self,
        name: str,
        system_prompt: str,
        ticker: str,
        price_text: str,
        news_text: str,
        n_news: int,
        context_block: str = "",
    ) -> _AgentResult:
        system_blocks = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        user_msg = _AGENT_USER_TEMPLATE.format(
            ticker=ticker,
            price_data=price_text,
            news_text=news_text,
            n_news=n_news,
            context_block=context_block,
        )
        message = llm_client.create_message(
            model=config.claude_model,
            max_tokens=600,
            system=system_blocks,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        return _AgentResult(
            agent=name,
            recommendation=str(data.get("recommendation", "SKIP")).upper(),
            sentiment_score=float(data.get("sentiment_score", 0.5)),
            rationale=str(data.get("rationale", "")),
            confidence=str(data.get("confidence", "LOW")).upper(),
        )

    def _build_consensus(
        self, ticker: str, results: List[_AgentResult], n_sources: int
    ) -> AnalysisResult:
        buy_votes = sum(1 for r in results if r.recommendation == "BUY")
        sell_votes = sum(1 for r in results if r.recommendation == "SELL")

        # Konsens-Regel: BUY nur bei >= 2 Agenten
        if buy_votes >= 2:
            recommendation = "BUY"
        elif sell_votes >= 2:
            recommendation = "SELL"
        else:
            recommendation = "HOLD"

        # Gewichteter sentiment_score
        total_weight = sum(_WEIGHTS.get(r.agent, 1.0) for r in results)
        weighted_score = sum(
            r.sentiment_score * _WEIGHTS.get(r.agent, 1.0) for r in results
        ) / total_weight

        # Richtung ableiten
        if weighted_score >= 0.65:
            direction = "BULLISH"
        elif weighted_score <= 0.40:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        # Konfidenz: HIGH nur wenn alle drei einig; MEDIUM bei 2/3
        if buy_votes == 3 or sell_votes == 3:
            confidence = "HIGH"
        elif buy_votes >= 2 or sell_votes >= 2:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        rationales = " | ".join(
            f"{r.agent.upper()}: {r.rationale}" for r in results
        )
        bull_r = next((r for r in results if r.agent == "bull"), None)
        bear_r = next((r for r in results if r.agent == "bear"), None)

        return AnalysisResult(
            ticker=ticker,
            sentiment_score=round(weighted_score, 3),
            direction=direction,
            confidence=confidence,
            recommendation=recommendation,
            entry_rationale=rationales,
            risk_factors=[r.rationale for r in results if r.recommendation in ("SELL", "HOLD")],
            key_catalysts=[r.rationale for r in results if r.recommendation == "BUY"],
            suggested_hold_days=14,
            target_price=None,
            target_price_rationale="Multi-Agent-Konsens ohne Kursziel",
            thesis_valid=None,
            thesis_break_reason="",
            sources_used=n_sources,
            raw_summary=f"Votes: BUY={buy_votes}, SELL={sell_votes}, score={weighted_score:.3f}",
            bull_case=bull_r.rationale if bull_r else "",
            bear_case=bear_r.rationale if bear_r else "",
            debate_winner=(
                "BULL" if buy_votes > sell_votes
                else "BEAR" if sell_votes > buy_votes
                else "DRAW"
            ),
        )

    def _format_price(self, price_data: Dict) -> str:
        if not price_data:
            return "Keine Marktdaten verfügbar."
        lines = []
        for k, v in price_data.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def _format_news(self, items: List[Dict], max_items: int = 10) -> str:
        parts = []
        for i, item in enumerate(items[:max_items], 1):
            source = item.get("source", "Unbekannt")
            title = item.get("title", "")
            date = (item.get("published_at") or "")[:10]
            parts.append(f"[{i}] {date} | {source}: {title}")
        return "\n".join(parts)

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
