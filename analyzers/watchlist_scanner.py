"""
Watchlist scanner – finds candidates with unusual volume/momentum
from a larger universe and proposes them for the analysis queue.
"""
from typing import List, Dict
import yfinance as yf


# Curated universe (S&P 500 leaders + popular movers + EU); extend as desired.
DEFAULT_UNIVERSE = [
    # Mega caps tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "ADBE",
    "CRM", "AMD", "INTC", "QCOM", "CSCO", "TXN", "INTU", "NOW", "PANW", "SNOW",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "V", "MA", "AXP",
    # Healthcare / pharma
    "LLY", "NVO", "UNH", "JNJ", "MRK", "PFE", "ABBV", "TMO", "ABT",
    # Energy / industrial
    "XOM", "CVX", "COP", "CAT", "BA", "GE", "RTX", "HON",
    # Consumer
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "DIS", "NFLX",
    # Growth / momentum
    "PLTR", "COIN", "MSTR", "RIOT", "MARA", "CLSK", "SHOP", "UBER", "ABNB", "RIVN", "LCID", "SOFI", "HOOD",
    # Semiconductors
    "ASML", "TSM", "AMAT", "LRCX", "MU", "MRVL", "ARM",
    # EU – Deutschland (XETRA)
    "RHM.DE", "SAP.DE", "SIE.DE", "IFX.DE", "BMW.DE", "MBG.DE", "ALV.DE", "MTX.DE", "ENR.DE",
    # EU – Rüstung & Verteidigung (aktueller Megatrend)
    "AIR.PA", "BA.L", "BAESY",
    # EU – Energie / Industrie
    "SHEL.L", "TTE.PA", "NOVO-B.CO",
]


class WatchlistScanner:
    """Scans a universe for unusual volume + price momentum (hype detection)."""

    def __init__(
        self,
        universe: List[str] = None,
        min_volume_ratio: float = 2.0,
        min_price_change_pct: float = 2.0,
        max_picks: int = 8,
    ):
        self.universe = universe or DEFAULT_UNIVERSE
        self.min_volume_ratio = min_volume_ratio
        self.min_price_change_pct = min_price_change_pct
        self.max_picks = max_picks

    def scan(self, exclude: List[str] = None) -> List[Dict]:
        """Returns stocks with unusual BUYING momentum (volume up AND price up)."""
        exclude = set(exclude or [])
        candidates: List[Dict] = []
        for ticker in self.universe:
            if ticker in exclude:
                continue
            metrics = self._compute_metrics(ticker)
            if not metrics:
                continue
            # Both conditions must be true: real buying pressure (not just volatility)
            if (
                metrics["volume_ratio"] >= self.min_volume_ratio
                and metrics["change_pct"] >= self.min_price_change_pct
            ):
                candidates.append(metrics)

        # Rank by combined momentum score: volume × price move
        candidates.sort(
            key=lambda x: x["volume_ratio"] * (1 + x["change_pct"] / 10),
            reverse=True,
        )
        return candidates[: self.max_picks]

    def _compute_metrics(self, ticker: str) -> Dict:
        try:
            hist = yf.Ticker(ticker).history(period="1mo")
            if hist.empty or len(hist) < 5:
                return {}
            today = hist.iloc[-1]
            avg_volume = hist["Volume"].iloc[:-1].mean()
            if avg_volume == 0:
                return {}
            volume_ratio = float(today["Volume"]) / float(avg_volume)
            prev_close = float(hist["Close"].iloc[-2])
            change_pct = (float(today["Close"]) - prev_close) / prev_close * 100
            # 3-day streak: how many of last 3 days were positive
            streak = sum(
                1 for i in range(-3, 0)
                if len(hist) > abs(i) + 1
                and hist["Close"].iloc[i] > hist["Close"].iloc[i - 1]
            )
            return {
                "ticker": ticker,
                "price": round(float(today["Close"]), 2),
                "change_pct": round(change_pct, 2),
                "volume_ratio": round(volume_ratio, 2),
                "streak_days": streak,  # consecutive up-days (0-3)
            }
        except Exception:
            return {}
