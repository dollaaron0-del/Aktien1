"""
CryptoScanner – scans the crypto universe and ranks coins by momentum,
volume, volatility, and Bitcoin correlation.

Activation: manually via CLI (e.g. python main.py --crypto-scan).
Never called automatically in the normal bot cycle.

Scoring (0–100 points):
  30 pts  7-day momentum      (positive = points, negative = deduction)
  25 pts  Price above MA7     (bullish structure)
  25 pts  Volume              (log-scaled, > $1 B = full points)
  20 pts  Low volatility      (< 60 % ann. = good, > 150 % = poor)

Recommendation thresholds:
  STRONG_BUY  score >= 70 and 7d > 0
  BUY         score >= 50
  HOLD        score >= 30
  AVOID       score <  30
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

# ── Universe ──────────────────────────────────────────────────────────────────

CRYPTO_UNIVERSE = {
    "BTC":  "Bitcoin – digitales Gold, Marktbarometer",
    "ETH":  "Ethereum – Smart Contract Plattform",
    "SOL":  "Solana – High-Speed Blockchain",
    "BNB":  "Binance Coin – Boersen-Token",
    "XRP":  "Ripple – Zahlungsnetzwerk",
    "ADA":  "Cardano – Proof-of-Stake",
    "AVAX": "Avalanche – DeFi Plattform",
    "DOT":  "Polkadot – Blockchain Interoperabilitaet",
    "MATIC":"Polygon – Ethereum Layer 2",
    "LINK": "Chainlink – Oracle Netzwerk",
    "UNI":  "Uniswap – DeFi DEX Token",
    "ATOM": "Cosmos – Blockchain Internet",
}

_MIN_VOLUME_24H_USD = 100_000_000   # $100 M minimum 24 h volume
_VOL_HIGH_THRESHOLD = 1_000_000_000  # $1 B — full volume score


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class CryptoCandidate:
    symbol:             str
    name:               str
    price_usd:          float
    change_24h_pct:     float
    change_7d_pct:      float
    volatility_ann_pct: float   # annualised volatility in %
    above_ma7:          bool
    btc_correlation:    float   # -1 to 1
    volume_24h_usd_m:   float   # in millions USD
    recommendation:     str     # STRONG_BUY | BUY | HOLD | AVOID
    score:              float   # 0–100
    risk_note:          str     # e.g. "High volatility – max. 5% portfolio"


@dataclass
class CryptoScanResult:
    scan_date:      str
    btc_trend:      str                          # BULL / BEAR / SIDEWAYS
    candidates:     List[CryptoCandidate] = field(default_factory=list)
    scan_duration_s: float = 0.0


# ── Scanner ───────────────────────────────────────────────────────────────────

class CryptoScanner:
    """
    Scans the crypto universe for momentum, volume, and risk signals.
    Only to be called manually — never in the automatic bot cycle.
    """

    def __init__(
        self,
        watchlist: List[str] = None,
        max_results: int = 8,
    ):
        self.watchlist   = watchlist or list(CRYPTO_UNIVERSE.keys())
        self.max_results = max_results

    # ── Public API ─────────────────────────────────────────────────────────────

    def scan(self) -> CryptoScanResult:
        """
        Loads 30-day price data for all coins via yfinance (symbol + '-USD').
        Calculates per coin: price, 24 h change, 7 d change, annualised
        volatility, momentum score, BTC correlation, and a recommendation.
        Filters out coins with 24 h volume < $100 M.
        Returns a CryptoScanResult sorted by score descending.
        """
        t0 = time.monotonic()
        log.info("CryptoScanner started for %d symbols …", len(self.watchlist))

        # Fetch BTC data first – needed for trend and correlation
        btc_hist = self._fetch_history("BTC")
        btc_trend = self._calc_btc_trend(btc_hist)
        btc_returns = self._daily_returns(btc_hist, days=7) if btc_hist is not None else None

        candidates: List[CryptoCandidate] = []

        for sym in self.watchlist:
            candidate = self._evaluate(sym, btc_returns)
            if candidate is not None:
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[: self.max_results]

        duration = round(time.monotonic() - t0, 1)
        log.info(
            "CryptoScanner finished: %d candidates in %.1f s (BTC trend: %s)",
            len(candidates), duration, btc_trend,
        )

        return CryptoScanResult(
            scan_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            btc_trend=btc_trend,
            candidates=candidates,
            scan_duration_s=duration,
        )

    # ── Evaluation ─────────────────────────────────────────────────────────────

    def _evaluate(
        self,
        symbol: str,
        btc_returns: Optional[List[float]],
    ) -> Optional[CryptoCandidate]:
        try:
            hist = self._fetch_history(symbol)
            if hist is None or len(hist) < 8:
                log.debug("CryptoScanner %s: insufficient history", symbol)
                return None

            close  = hist["Close"]
            volume = hist["Volume"]

            price       = float(close.iloc[-1])
            price_1d_ago = float(close.iloc[-2])
            price_7d_ago = float(close.iloc[-8]) if len(close) >= 8 else float(close.iloc[0])

            change_24h = round((price / price_1d_ago - 1) * 100, 2)
            change_7d  = round((price / price_7d_ago  - 1) * 100, 2)

            # 24 h volume filter (use most recent day's volume * price as proxy)
            vol_24h_usd = float(volume.iloc[-1]) * price
            if vol_24h_usd < _MIN_VOLUME_24H_USD:
                log.debug(
                    "CryptoScanner %s: volume $%.0f M below threshold, skipping",
                    symbol, vol_24h_usd / 1_000_000,
                )
                return None

            # MA7
            ma7 = float(close.tail(7).mean())
            above_ma7 = price > ma7

            # Annualised volatility from 14-day daily returns
            volatility_ann = self._calc_volatility(close, days=14)

            # BTC correlation (7-day daily returns)
            btc_corr = self._calc_btc_correlation(close, btc_returns)

            # Score
            score = self._score(
                change_7d=change_7d,
                above_ma7=above_ma7,
                vol_24h_usd=vol_24h_usd,
                volatility_ann_pct=volatility_ann,
            )

            recommendation = self._recommendation(score, change_7d)
            risk_note      = self._risk_note(volatility_ann)
            name           = CRYPTO_UNIVERSE.get(symbol, symbol)

            return CryptoCandidate(
                symbol=symbol,
                name=name,
                price_usd=round(price, 6),
                change_24h_pct=change_24h,
                change_7d_pct=change_7d,
                volatility_ann_pct=round(volatility_ann, 1),
                above_ma7=above_ma7,
                btc_correlation=round(btc_corr, 3),
                volume_24h_usd_m=round(vol_24h_usd / 1_000_000, 1),
                recommendation=recommendation,
                score=round(score, 1),
                risk_note=risk_note,
            )

        except Exception as e:
            log.debug("CryptoScanner %s: %s", symbol, e)
            return None

    # ── Score + helpers ────────────────────────────────────────────────────────

    def _score(
        self,
        change_7d: float,
        above_ma7: bool,
        vol_24h_usd: float,
        volatility_ann_pct: float,
    ) -> float:
        score = 0.0

        # 30 pts: 7-day momentum
        if change_7d > 0:
            score += min(change_7d / 20.0, 1.0) * 30
        else:
            score += max(1 + change_7d / 30.0, 0.0) * 10  # partial credit, max -10 deduction

        # 25 pts: price above MA7
        if above_ma7:
            score += 25

        # 25 pts: volume (log-scaled; > $1 B = full)
        if vol_24h_usd > 0:
            log_ratio = math.log10(vol_24h_usd) / math.log10(_VOL_HIGH_THRESHOLD)
            score += min(log_ratio, 1.0) * 25

        # 20 pts: low annualised volatility
        # < 60 % → full 20 pts; 60–150 % → linear decay; > 150 % → 0
        if volatility_ann_pct < 60:
            score += 20
        elif volatility_ann_pct <= 150:
            score += max((150 - volatility_ann_pct) / 90.0, 0.0) * 20
        # else 0

        return min(score, 100.0)

    @staticmethod
    def _recommendation(score: float, change_7d: float) -> str:
        if score >= 70 and change_7d > 0:
            return "STRONG_BUY"
        if score >= 50:
            return "BUY"
        if score >= 30:
            return "HOLD"
        return "AVOID"

    @staticmethod
    def _risk_note(volatility_ann_pct: float) -> str:
        if volatility_ann_pct > 150:
            return "Very high volatility – max. 2% portfolio"
        if volatility_ann_pct > 100:
            return "High volatility – max. 3% portfolio"
        if volatility_ann_pct > 60:
            return "Elevated volatility – max. 5% portfolio"
        return "Moderate volatility – max. 10% portfolio"

    # ── BTC trend ─────────────────────────────────────────────────────────────

    def _calc_btc_trend(self, btc_hist) -> str:
        """Determine BTC macro trend: BULL / BEAR / SIDEWAYS."""
        if btc_hist is None or len(btc_hist) < 8:
            return "SIDEWAYS"
        close = btc_hist["Close"]
        price     = float(close.iloc[-1])
        ma7       = float(close.tail(7).mean())
        price_7d  = float(close.iloc[-8]) if len(close) >= 8 else float(close.iloc[0])
        change_7d = (price / price_7d - 1) * 100

        if price > ma7 and change_7d > 0:
            return "BULL"
        if price < ma7 and change_7d < 0:
            return "BEAR"
        return "SIDEWAYS"

    # ── Data helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_history(symbol: str):
        """Download 30 days of OHLCV history from yfinance. Returns DataFrame or None."""
        try:
            yf_symbol = f"{symbol}-USD"
            hist = yf.Ticker(yf_symbol).history(period="30d")
            if hist.empty:
                return None
            return hist
        except Exception as e:
            log.debug("yfinance history %s-USD: %s", symbol, e)
            return None

    @staticmethod
    def _daily_returns(hist, days: int) -> List[float]:
        """Return the last `days` daily percentage returns as a list."""
        close = hist["Close"].tail(days + 1)
        returns = []
        for i in range(1, len(close)):
            ret = (float(close.iloc[i]) / float(close.iloc[i - 1]) - 1) * 100
            returns.append(ret)
        return returns

    @staticmethod
    def _calc_volatility(close, days: int) -> float:
        """Annualised volatility (%) from `days` daily returns. 0 if not enough data."""
        if len(close) < days + 1:
            return 0.0
        try:
            import statistics
            rets = []
            tail = close.tail(days + 1)
            for i in range(1, len(tail)):
                ret = float(tail.iloc[i]) / float(tail.iloc[i - 1]) - 1
                rets.append(ret)
            if len(rets) < 2:
                return 0.0
            std_daily = statistics.stdev(rets)
            return round(std_daily * math.sqrt(365) * 100, 2)
        except Exception:
            return 0.0

    @staticmethod
    def _calc_btc_correlation(close, btc_returns: Optional[List[float]]) -> float:
        """Pearson correlation between coin's 7-day returns and BTC's 7-day returns."""
        if btc_returns is None or len(btc_returns) < 2:
            return 0.0
        try:
            days = len(btc_returns)
            if len(close) < days + 1:
                return 0.0
            tail = close.tail(days + 1)
            coin_returns = [
                (float(tail.iloc[i]) / float(tail.iloc[i - 1]) - 1) * 100
                for i in range(1, len(tail))
            ]
            # Trim to same length
            n = min(len(coin_returns), len(btc_returns))
            if n < 2:
                return 0.0
            cx = coin_returns[-n:]
            bx = btc_returns[-n:]

            mean_c = sum(cx) / n
            mean_b = sum(bx) / n
            num    = sum((cx[i] - mean_c) * (bx[i] - mean_b) for i in range(n))
            den_c  = math.sqrt(sum((v - mean_c) ** 2 for v in cx))
            den_b  = math.sqrt(sum((v - mean_b) ** 2 for v in bx))
            if den_c == 0 or den_b == 0:
                return 0.0
            return round(num / (den_c * den_b), 3)
        except Exception:
            return 0.0
