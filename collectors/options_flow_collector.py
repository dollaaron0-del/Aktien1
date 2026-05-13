"""
Options flow collector – detects unusual options activity from free Yahoo Finance data.
Heavy call volume relative to puts (or vice versa) is one of the strongest early signals,
since informed traders often hedge or speculate via options before news breaks.
"""
from datetime import datetime
from typing import List, Dict, Optional
import yfinance as yf


class OptionsFlowCollector:
    def __init__(self, min_volume_ratio: float = 2.0, min_total_volume: int = 500):
        self.min_volume_ratio = min_volume_ratio
        self.min_total_volume = min_total_volume

    def collect(self, ticker: str) -> List[Dict]:
        try:
            stock = yf.Ticker(ticker)
            expirations = stock.options
            if not expirations:
                return []
            # Use the nearest two expirations (most liquid + indicative of imminent moves)
            target_exps = expirations[:2]
        except Exception:
            return []

        total_call_vol = 0
        total_put_vol = 0
        large_strikes: List[Dict] = []

        for exp in target_exps:
            try:
                chain = yf.Ticker(ticker).option_chain(exp)
                calls = chain.calls
                puts = chain.puts
                if calls is not None and not calls.empty:
                    cv = int(calls["volume"].fillna(0).sum())
                    total_call_vol += cv
                    for _, row in calls.nlargest(3, "volume").iterrows():
                        v = int(row.get("volume", 0) or 0)
                        if v >= 200:
                            large_strikes.append({
                                "type": "CALL",
                                "strike": float(row["strike"]),
                                "volume": v,
                                "expiry": exp,
                            })
                if puts is not None and not puts.empty:
                    pv = int(puts["volume"].fillna(0).sum())
                    total_put_vol += pv
            except Exception:
                continue

        if total_call_vol + total_put_vol < self.min_total_volume:
            return []

        items: List[Dict] = []
        ratio = total_call_vol / max(total_put_vol, 1)
        inv_ratio = total_put_vol / max(total_call_vol, 1)

        if ratio >= self.min_volume_ratio:
            items.append({
                "title": f"Ungewöhnliche Call-Aktivität bei {ticker} – C/P-Ratio {ratio:.1f}",
                "summary": (
                    f"Calls: {total_call_vol:,} vs Puts: {total_put_vol:,}. "
                    f"Bullisches Optionsflow-Signal."
                ),
                "url": f"https://finance.yahoo.com/quote/{ticker}/options",
                "published": datetime.utcnow().isoformat(),
                "source": "options_flow",
                "signal": "BULLISCH",
            })
        elif inv_ratio >= self.min_volume_ratio:
            items.append({
                "title": f"Ungewöhnliche Put-Aktivität bei {ticker} – P/C-Ratio {inv_ratio:.1f}",
                "summary": (
                    f"Puts: {total_put_vol:,} vs Calls: {total_call_vol:,}. "
                    f"Bärisches Optionsflow-Signal / Absicherung."
                ),
                "url": f"https://finance.yahoo.com/quote/{ticker}/options",
                "published": datetime.utcnow().isoformat(),
                "source": "options_flow",
                "signal": "BÄRISCH",
            })

        # Large single-strike trades as separate signals
        for s in large_strikes[:3]:
            items.append({
                "title": f"Großer {s['type']}-Trade {ticker} ${s['strike']:.0f} ({s['expiry']})",
                "summary": f"Volumen: {s['volume']:,} Kontrakte.",
                "url": f"https://finance.yahoo.com/quote/{ticker}/options",
                "published": datetime.utcnow().isoformat(),
                "source": "options_flow",
                "signal": "BULLISCH" if s["type"] == "CALL" else "BÄRISCH",
            })
        return items
