"""
Recession / Market Regime Detector

Berechnet einen Rezessions-Score (0–1) aus quantitativen Signalen:
  - VIX (Angst-Index)
  - Zinskurveninversion (2y vs 10y Treasury)
  - S&P 500 Trendstatus (200-Tage-Linie)
  - Marktbreite (wie viele Sektoren im Abwärtstrend)
  - High-Yield Credit Spread (HYG vs IEI)
  - Claude-Makroanalyse (qualitatives Signal)

Regime-Schwellen:
  Score < 0.25  → BULL    (normal, Long-Strategie)
  0.25–0.45     → NEUTRAL (vorsichtiger, höhere Kauf-Schwellen)
  0.45–0.65     → BEAR    (defensiv, kleine Hedge-Position)
  > 0.65        → CRISIS  (größere Absicherung, weniger Longs)
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "recession_detector.db")

# Regime labels
BULL    = "BULL"
NEUTRAL = "NEUTRAL"
BEAR    = "BEAR"
CRISIS  = "CRISIS"

# Sector ETFs used for breadth analysis
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC"]

# Inverse ETF options per scenario
# Keys map to macro_theme → (conservative 1x, aggressive 2x/3x)
INVERSE_ETF_MAP: Dict[str, Dict] = {
    "broad_market": {
        "conservative": "SH",    # ProShares Short S&P500 (1×)
        "aggressive":   "SDS",   # ProShares UltraShort S&P500 (2×)
        "description":  "Breiter Markt (S&P 500)",
    },
    "tech_bubble": {
        "conservative": "PSQ",   # ProShares Short QQQ (1×)
        "aggressive":   "SQQQ",  # ProShares UltraPro Short QQQ (3×)
        "description":  "Technologie / NASDAQ",
    },
    "energy_crash": {
        "conservative": "DDG",   # ProShares Short Oil & Gas
        "aggressive":   "ERY",   # Direxion Daily Energy Bear 2×
        "description":  "Energie-Sektor",
    },
    "finance_crisis": {
        "conservative": "SEF",   # ProShares Short Financials
        "aggressive":   "FAZ",   # Direxion Daily Financial Bear 3×
        "description":  "Finanz-Sektor",
    },
    "real_estate_bubble": {
        "conservative": "REK",   # ProShares Short Real Estate
        "aggressive":   "DRV",   # Direxion Daily Real Estate Bear 3×
        "description":  "Immobilien",
    },
}


class RecessionDetector:
    def __init__(self, anthropic_api_key: str = ""):
        self.api_key = anthropic_api_key
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS regime_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at     TEXT NOT NULL,
                recession_score REAL NOT NULL,
                regime          TEXT NOT NULL,
                vix             REAL,
                yield_spread    REAL,
                sp500_vs_ma200  REAL,
                sector_bear_pct REAL,
                credit_spread   REAL,
                claude_score    REAL,
                components      TEXT,   -- JSON breakdown
                macro_summary   TEXT    -- Claude's qualitative take
            );
        """)
        self._conn.commit()

    # ── Main entry point ─────────────────────────────────────────────────────

    def analyze(self, macro_news: Optional[List[Dict]] = None) -> Dict:
        """
        Runs full regime analysis. Returns:
          {regime, recession_score, components, macro_summary,
           recommended_hedges, hedge_intensity}
        """
        components = {}

        # 1. VIX
        vix, vix_score = self._vix_signal()
        components["vix"] = {"value": vix, "score": round(vix_score, 3), "label": self._vix_label(vix)}

        # 2. Yield curve (2y vs 10y)
        spread, yc_score = self._yield_curve_signal()
        components["yield_curve"] = {
            "spread_pct": round(spread, 3) if spread is not None else None,
            "score": round(yc_score, 3),
            "label": "Invertiert ⚠" if spread is not None and spread < 0 else "Normal",
        }

        # 3. S&P 500 vs 200-day MA
        sp500_gap, ma_score = self._sp500_ma_signal()
        components["sp500_ma200"] = {
            "gap_pct": round(sp500_gap, 2) if sp500_gap is not None else None,
            "score": round(ma_score, 3),
            "label": "Unter 200-MA ⚠" if sp500_gap is not None and sp500_gap < 0 else "Über 200-MA",
        }

        # 4. Sector breadth
        bear_pct, breadth_score = self._sector_breadth_signal()
        components["sector_breadth"] = {
            "bear_sectors_pct": round(bear_pct * 100, 1),
            "score": round(breadth_score, 3),
            "label": f"{bear_pct*100:.0f}% der Sektoren im Abwärtstrend",
        }

        # 5. Credit spread (HYG vs IEI)
        cs, credit_score = self._credit_spread_signal()
        components["credit_spread"] = {
            "ratio": round(cs, 4) if cs is not None else None,
            "score": round(credit_score, 3),
        }

        # Weighted quantitative score (before Claude)
        quant_score = (
            vix_score     * 0.30 +
            yc_score      * 0.25 +
            ma_score      * 0.20 +
            breadth_score * 0.15 +
            credit_score  * 0.10
        )

        # 6. Optional Claude qualitative signal
        claude_score = 0.0
        macro_summary = ""
        if macro_news and self.api_key:
            claude_score, macro_summary = self._claude_macro_signal(macro_news, quant_score)
            components["claude_macro"] = {"score": round(claude_score, 3), "summary": macro_summary[:300]}

        # Final score: 80% quant + 20% Claude (if available)
        if macro_news and self.api_key:
            recession_score = quant_score * 0.80 + claude_score * 0.20
        else:
            recession_score = quant_score

        recession_score = round(min(max(recession_score, 0.0), 1.0), 3)
        regime = self._score_to_regime(recession_score)

        # Determine which hedges to recommend
        recommended_hedges, hedge_intensity = self._recommend_hedges(
            regime, components, macro_summary
        )

        result = {
            "regime": regime,
            "recession_score": recession_score,
            "components": components,
            "macro_summary": macro_summary,
            "recommended_hedges": recommended_hedges,
            "hedge_intensity": hedge_intensity,  # "conservative" or "aggressive"
            "vix": vix,
            "yield_spread": spread,
        }

        self._persist(result)
        return result

    # ── Quantitative signals ─────────────────────────────────────────────────

    def _vix_signal(self) -> Tuple[Optional[float], float]:
        try:
            hist = yf.Ticker("^VIX").history(period="2d")
            if not hist.empty:
                vix = float(hist["Close"].iloc[-1])
                # 0 at VIX=12, 1 at VIX=45
                score = max(0.0, min(1.0, (vix - 12) / (45 - 12)))
                return vix, score
        except Exception:
            pass
        return None, 0.3  # Neutral fallback

    def _yield_curve_signal(self) -> Tuple[Optional[float], float]:
        """2yr vs 10yr Treasury: negative spread = inversion = recession warning."""
        try:
            t2  = yf.Ticker("^IRX").history(period="2d")   # 13-week, proxy for short end
            t10 = yf.Ticker("^TNX").history(period="2d")   # 10-year
            if not t2.empty and not t10.empty:
                r2  = float(t2["Close"].iloc[-1])
                r10 = float(t10["Close"].iloc[-1])
                spread = r10 - r2  # positive = normal, negative = inverted
                # Score: 0 at spread=+2, 1 at spread=-1
                score = max(0.0, min(1.0, (-spread + 0.5) / 1.5))
                return spread, score
        except Exception:
            pass
        return None, 0.2

    def _sp500_ma_signal(self) -> Tuple[Optional[float], float]:
        """How far S&P 500 is from its 200-day moving average."""
        try:
            hist = yf.Ticker("^GSPC").history(period="1y")
            if len(hist) >= 200:
                current = float(hist["Close"].iloc[-1])
                ma200   = float(hist["Close"].tail(200).mean())
                gap_pct = (current - ma200) / ma200 * 100
                # 0 at gap=+10%, 1 at gap=-15%
                score = max(0.0, min(1.0, (-gap_pct + 5) / 20))
                return gap_pct, score
        except Exception:
            pass
        return None, 0.2

    def _sector_breadth_signal(self) -> Tuple[float, float]:
        """Fraction of sector ETFs in a downtrend (below 50-day MA)."""
        bear_count = 0
        checked = 0
        for sym in SECTOR_ETFS:
            try:
                hist = yf.Ticker(sym).history(period="3mo")
                if len(hist) >= 50:
                    current = float(hist["Close"].iloc[-1])
                    ma50    = float(hist["Close"].tail(50).mean())
                    if current < ma50:
                        bear_count += 1
                    checked += 1
            except Exception:
                pass
        if checked == 0:
            return 0.3, 0.3
        bear_pct = bear_count / checked
        # 0 at 0% bear, 1 at 80%+ bear
        score = min(1.0, bear_pct / 0.8)
        return bear_pct, score

    def _credit_spread_signal(self) -> Tuple[Optional[float], float]:
        """HYG (high yield) vs IEI (7-10yr treasury) – flight to safety indicator."""
        try:
            hyg  = yf.Ticker("HYG").history(period="2d")
            iei  = yf.Ticker("IEI").history(period="2d")
            if not hyg.empty and not iei.empty:
                ratio = float(hyg["Close"].iloc[-1]) / float(iei["Close"].iloc[-1])
                # Ratio falls in stress (HYG drops, IEI rises)
                # Approximate normal ~1.0, stressed ~0.85
                score = max(0.0, min(1.0, (1.05 - ratio) / 0.20))
                return ratio, score
        except Exception:
            pass
        return None, 0.2

    # ── Claude qualitative signal ─────────────────────────────────────────────

    def _claude_macro_signal(
        self, macro_news: List[Dict], quant_score: float
    ) -> Tuple[float, str]:
        news_text = "\n".join(
            f"- {n.get('title', '')} ({n.get('source', '')})"
            for n in macro_news[:10]
        )
        prompt = f"""Aktueller quantitativer Rezessions-Score: {quant_score:.2f} (0=bullisch, 1=Krise)

Aktuelle Makro-Nachrichten:
{news_text}

Bewerte das Rezessionsrisiko anhand der Nachrichten.
Antworte NUR mit diesem JSON (kein Text davor/dahinter):
{{
  "macro_recession_score": <float 0.0–1.0>,
  "regime_signal": "<BULL|NEUTRAL|BEAR|CRISIS>",
  "primary_risk": "<1 Satz: größtes Risiko>",
  "bubble_sectors": ["<Sektor 1>", "<Sektor 2>"],
  "summary": "<2–3 Sätze Gesamtbewertung>"
}}"""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            data  = json.loads(raw[start:end])
            return float(data.get("macro_recession_score", 0.3)), data.get("summary", "")
        except Exception:
            return 0.3, ""

    # ── Hedge recommendations ─────────────────────────────────────────────────

    def _recommend_hedges(
        self, regime: str, components: Dict, macro_summary: str
    ) -> Tuple[List[Dict], str]:
        if regime == BULL:
            return [], "conservative"

        intensity = "aggressive" if regime == CRISIS else "conservative"
        hedges = []

        # Always include broad market hedge in BEAR/CRISIS
        if regime in (BEAR, CRISIS):
            broad = INVERSE_ETF_MAP["broad_market"]
            hedges.append({
                "ticker": broad[intensity],
                "theme": "broad_market",
                "description": broad["description"],
                "reason": f"Breiter Marktabschwung erkannt (Score: {components.get('sp500_ma200', {}).get('gap_pct', '?')}% vs 200-MA)",
                "allocation_pct": 0.15 if regime == CRISIS else 0.08,
            })

        # Tech/AI bubble detection
        tech_score = components.get("sector_breadth", {}).get("score", 0)
        sp_gap     = components.get("sp500_ma200", {}).get("gap_pct") or 0
        bubble_keywords = {"tech", "ai", "ki", "bubble", "overvalued", "nvidia", "semiconductor"}
        summary_lower   = macro_summary.lower()
        if any(k in summary_lower for k in bubble_keywords) or (tech_score > 0.5 and regime != BULL):
            tech = INVERSE_ETF_MAP["tech_bubble"]
            hedges.append({
                "ticker": tech[intensity],
                "theme": "tech_bubble",
                "description": tech["description"],
                "reason": "Technologie/KI Überbewertungsrisiko erkannt",
                "allocation_pct": 0.10 if regime == CRISIS else 0.05,
            })

        # Finance sector stress
        credit_score = components.get("credit_spread", {}).get("score", 0)
        if credit_score > 0.6 or "bank" in summary_lower or "credit" in summary_lower:
            fin = INVERSE_ETF_MAP["finance_crisis"]
            hedges.append({
                "ticker": fin[intensity],
                "theme": "finance_crisis",
                "description": fin["description"],
                "reason": "Erhöhte Credit-Spreads / Bankenstress",
                "allocation_pct": 0.05,
            })

        return hedges, intensity

    # ── Persistence & history ─────────────────────────────────────────────────

    def _persist(self, result: Dict):
        c = result["components"]
        self._conn.execute(
            """INSERT INTO regime_snapshots
               (recorded_at, recession_score, regime, vix, yield_spread,
                sp500_vs_ma200, sector_bear_pct, credit_spread, claude_score,
                components, macro_summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.utcnow().isoformat(),
                result["recession_score"],
                result["regime"],
                result.get("vix"),
                result.get("yield_spread"),
                c.get("sp500_ma200", {}).get("gap_pct"),
                c.get("sector_breadth", {}).get("bear_sectors_pct"),
                c.get("credit_spread", {}).get("ratio"),
                c.get("claude_macro", {}).get("score"),
                json.dumps(c),
                result.get("macro_summary", ""),
            ),
        )
        self._conn.commit()

    def get_latest(self) -> Optional[Dict]:
        row = self._conn.execute(
            "SELECT * FROM regime_snapshots ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["components"] = json.loads(d.get("components") or "{}")
        return d

    def get_history(self, days: int = 30) -> List[Dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """SELECT recorded_at, recession_score, regime, vix, yield_spread
               FROM regime_snapshots WHERE recorded_at > ? ORDER BY recorded_at DESC""",
            (cutoff,),
        )
        return [dict(r) for r in cursor.fetchall()]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _score_to_regime(score: float) -> str:
        if score < 0.25:
            return BULL
        if score < 0.45:
            return NEUTRAL
        if score < 0.65:
            return BEAR
        return CRISIS

    @staticmethod
    def _vix_label(vix: Optional[float]) -> str:
        if vix is None:
            return "Unbekannt"
        if vix < 15:  return "Sehr ruhig"
        if vix < 20:  return "Normal"
        if vix < 25:  return "Erhöht"
        if vix < 30:  return "Nervös"
        if vix < 40:  return "Angst ⚠"
        return "Panik 🚨"
