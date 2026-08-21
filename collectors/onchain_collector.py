"""
collectors/onchain_collector.py – On-Chain Rohdaten für Krypto-Assets.

Quellen (alle kostenlos, kein API-Key):
  - blockchain.info/stats  → BTC: Transaktionen, aktive Adressen, Hash Rate, On-Chain-Volumen
  - CoinGecko API v3       → alle anderen Coins: Market Cap, Volume, Circulating Supply

Caching: 1 Stunde (On-Chain-Daten ändern sich träge).
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 12
_CACHE_TTL_SECONDS = 3600
_cache: Dict[str, tuple] = {}

_COINGECKO_IDS: Dict[str, str] = {
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "SOL":   "solana",
    "BNB":   "binancecoin",
    "XRP":   "ripple",
    "ADA":   "cardano",
    "AVAX":  "avalanche-2",
    "DOT":   "polkadot",
    "MATIC": "matic-network",
    "LINK":  "chainlink",
    "UNI":   "uniswap",
    "ATOM":  "cosmos",
}

# Approximate BTC circulating supply (updated periodically)
_BTC_CIRC_SUPPLY = 19_730_000.0


@dataclass
class OnChainMetrics:
    ticker: str
    active_addresses_24h: Optional[int]    # Unique active addresses (BTC only)
    tx_count_24h: Optional[int]            # Transaction count/24h (BTC only)
    tx_volume_usd_24h: Optional[float]     # On-chain transaction volume USD (BTC only)
    market_cap_usd: Optional[float]
    nvt_ratio: Optional[float]             # Market cap / Tx volume (low = undervalued)
    hash_rate_ths: Optional[float]         # Mining hash rate in TH/s (BTC only)
    circulating_supply: Optional[float]
    volume_24h_usd: Optional[float]        # Exchange trading volume
    source: str                            # "blockchain.info" | "coingecko"


class OnChainCollector:
    """Fetches on-chain metrics from free public APIs."""

    def collect(self, ticker: str) -> Optional[OnChainMetrics]:
        """
        Returns OnChainMetrics for the given crypto ticker or None on failure.
        ticker: "BTC", "ETH", "SOL", … (without "-USD" suffix)
        """
        base = ticker.split("/")[0].upper().removesuffix("-USD")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cached = _cache.get(base)
        if cached and (now - cached[0]).total_seconds() < _CACHE_TTL_SECONDS:
            return cached[1]

        metrics = None
        if base == "BTC":
            metrics = self._fetch_btc()
        if metrics is None:
            metrics = self._fetch_coingecko(base)

        if metrics:
            _cache[base] = (now, metrics)
        return metrics

    # ── BTC via blockchain.info ───────────────────────────────────────────────

    def _fetch_btc(self) -> Optional[OnChainMetrics]:
        try:
            body = self._get("https://api.blockchain.info/stats")
            if not body:
                return None
            data = json.loads(body)

            price      = float(data.get("market_price_usd") or 0)
            n_tx       = int(data.get("n_tx") or 0)
            n_addr     = int(data.get("n_unique_addresses") or 0)
            tx_vol     = float(data.get("estimated_transaction_volume_usd") or 0)
            hash_rate  = float(data.get("hash_rate") or 0)
            trade_vol  = float(data.get("trade_volume_usd") or 0)

            mkt_cap = price * _BTC_CIRC_SUPPLY
            nvt = round(mkt_cap / tx_vol, 1) if tx_vol > 0 else None

            return OnChainMetrics(
                ticker="BTC",
                active_addresses_24h=n_addr or None,
                tx_count_24h=n_tx or None,
                tx_volume_usd_24h=tx_vol or None,
                market_cap_usd=mkt_cap or None,
                nvt_ratio=nvt,
                hash_rate_ths=round(hash_rate, 0) or None,
                circulating_supply=_BTC_CIRC_SUPPLY,
                volume_24h_usd=trade_vol or None,
                source="blockchain.info",
            )
        except Exception as e:
            log.debug("OnChainCollector BTC blockchain.info: %s", e)
            return None

    # ── All coins via CoinGecko ───────────────────────────────────────────────

    def _fetch_coingecko(self, ticker: str) -> Optional[OnChainMetrics]:
        cg_id = _COINGECKO_IDS.get(ticker)
        if not cg_id:
            return None
        try:
            url = (
                f"https://api.coingecko.com/api/v3/coins/{cg_id}"
                "?localization=false&tickers=false&market_data=true"
                "&community_data=false&developer_data=false&sparkline=false"
            )
            body = self._get(url)
            if not body:
                return None
            data = json.loads(body)
            md = data.get("market_data") or {}

            def _usd(d) -> float:
                return float((d or {}).get("usd") or 0)

            price      = _usd(md.get("current_price"))
            mkt_cap    = _usd(md.get("market_cap"))
            vol_24h    = _usd(md.get("total_volume"))
            circ       = float(md.get("circulating_supply") or 0)

            # NVT proxy: market_cap / 24h exchange volume
            nvt_proxy = round(mkt_cap / vol_24h, 1) if vol_24h > 0 else None

            return OnChainMetrics(
                ticker=ticker,
                active_addresses_24h=None,
                tx_count_24h=None,
                tx_volume_usd_24h=None,
                market_cap_usd=mkt_cap or None,
                nvt_ratio=nvt_proxy,
                hash_rate_ths=None,
                circulating_supply=circ or None,
                volume_24h_usd=vol_24h or None,
                source="coingecko",
            )
        except Exception as e:
            log.debug("OnChainCollector CoinGecko %s: %s", ticker, e)
            return None

    # ── HTTP helper ───────────────────────────────────────────────────────────

    @staticmethod
    def _get(url: str) -> Optional[bytes]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "TradingBot/1.0 (research)"}
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.read()
        except Exception as e:
            log.debug("OnChainCollector HTTP %s: %s", url, e)
            return None
