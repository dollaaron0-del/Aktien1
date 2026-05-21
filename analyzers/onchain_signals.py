"""
analyzers/onchain_signals.py – On-Chain Signal-Interpretation für Krypto.

Interpretiert OnChainMetrics (aus collectors/onchain_collector.py) in
handlungsrelevante Signale mit Score 0–100 und Prompt-Block für Claude.

Signal-Logik:
  NVT Ratio        – niedrig = Netzwerk wird viel genutzt relativ zur Bewertung (bullisch)
  Aktive Adressen  – über Durchschnitt = Adoption wächst (bullisch); BTC-spezifisch
  Hash Rate        – steigt = Miner-Vertrauen (bullisch); BTC-spezifisch
  Tx-Count         – über Normal = Netzwerk-Aktivität hoch (bullisch); BTC-spezifisch
  Vol/MarktKap     – hohes Verhältnis = erhöhte Handelsaktivität

NVT-Schwellenwerte:
  blockchain.info (echter On-Chain-NVT):  < 25 unterbewertet, > 65 teuer, > 150 Blase
  CoinGecko (MarktKap/Handelsvolumen):    < 5  hohe Aktivität, > 20 träge, > 50 überbewertet
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from logger import get_logger

log = get_logger(__name__)

_NVT_THRESHOLDS = {
    "blockchain.info": {"low": 25,  "fair_high": 65,  "bubble": 150},
    "coingecko":        {"low": 5,   "fair_high": 20,  "bubble": 50},
}

# BTC historical benchmarks
_BTC_ADDR_HIGH   = 1_100_000   # active addresses/day → above = bullish
_BTC_ADDR_LOW    =   700_000
_BTC_TX_HIGH     =   450_000   # tx/day → above = high activity
_BTC_TX_LOW      =   150_000
_BTC_HR_ATH_EHS  =   700       # EH/s all-time-high approx
_BTC_HR_STRONG   =   600
_BTC_HR_WEAK     =   300


@dataclass
class OnChainSnapshot:
    ticker: str
    signal: str                          # STRONG_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONG_BEARISH
    score: float                         # 0–100
    key_signals: List[str] = field(default_factory=list)
    source: str = ""

    def to_prompt_block(self) -> str:
        if not self.key_signals:
            return ""
        sig_emoji = {
            "STRONG_BULLISH": "🟢🟢",
            "BULLISH":        "🟢",
            "NEUTRAL":        "⚪",
            "BEARISH":        "🔴",
            "STRONG_BEARISH": "🔴🔴",
        }.get(self.signal, "")
        lines = [
            f"=== ON-CHAIN DATEN ({self.source}) ===",
            f"Signal: {sig_emoji} {self.signal}  |  Score: {self.score:.0f}/100",
            "Metriken:",
        ]
        for sig in self.key_signals:
            lines.append(f"  • {sig}")
        lines.append(
            "Interpretation: On-Chain Signale zeigen Netzwerk-Nutzung und Bewertung. "
            "Bestätigen oder widersprechen sie dem Chart-Signal?"
        )
        return "\n".join(lines)


class OnChainSignalAnalyzer:
    """Converts raw OnChainMetrics into an OnChainSnapshot."""

    def analyze(self, metrics) -> Optional[OnChainSnapshot]:
        """metrics: OnChainMetrics | None → OnChainSnapshot | None"""
        if metrics is None:
            return None

        score = 50.0
        key_signals: List[str] = []
        thresh = _NVT_THRESHOLDS.get(metrics.source, _NVT_THRESHOLDS["coingecko"])

        # ── NVT Ratio ──────────────────────────────────────────────────────────
        if metrics.nvt_ratio is not None:
            nvt = metrics.nvt_ratio
            if nvt < thresh["low"]:
                score += 15
                key_signals.append(
                    f"NVT {nvt:.0f} (< {thresh['low']}) → unterbewertet: "
                    "Netzwerknutzung hoch relativ zur Marktkapitalisierung"
                )
            elif nvt > thresh["bubble"]:
                score -= 20
                key_signals.append(
                    f"NVT {nvt:.0f} (> {thresh['bubble']}) → Überbewertungssignal: "
                    "Kurs weit über Netzwerkaktivität"
                )
            elif nvt > thresh["fair_high"]:
                score -= 8
                key_signals.append(f"NVT {nvt:.0f} – leicht erhöht, Bewertung gespannt")
            else:
                key_signals.append(f"NVT {nvt:.0f} – faire Bewertung")

        # ── Aktive Adressen (BTC, blockchain.info) ─────────────────────────────
        if metrics.active_addresses_24h:
            addr = metrics.active_addresses_24h
            if addr > _BTC_ADDR_HIGH:
                score += 10
                key_signals.append(
                    f"Aktive Adressen: {addr:,}/24h → überdurchschnittlich "
                    f"(> {_BTC_ADDR_HIGH:,}) – Adoption wächst"
                )
            elif addr < _BTC_ADDR_LOW:
                score -= 8
                key_signals.append(
                    f"Aktive Adressen: {addr:,}/24h → unterdurchschnittlich "
                    f"(< {_BTC_ADDR_LOW:,}) – geringe Netzwerknutzung"
                )
            else:
                key_signals.append(f"Aktive Adressen: {addr:,}/24h – normal")

        # ── Hash Rate (BTC) ────────────────────────────────────────────────────
        if metrics.hash_rate_ths:
            hr_ehs = metrics.hash_rate_ths / 1_000_000  # TH/s → EH/s
            if hr_ehs > _BTC_HR_STRONG:
                score += 8
                label = "Rekordniveau" if hr_ehs > _BTC_HR_ATH_EHS else "sehr stark"
                key_signals.append(
                    f"Hash Rate: {hr_ehs:.0f} EH/s – {label} → "
                    "Miner-Vertrauen hoch, Netzwerk-Sicherheit maximal"
                )
            elif hr_ehs < _BTC_HR_WEAK:
                score -= 6
                key_signals.append(
                    f"Hash Rate: {hr_ehs:.0f} EH/s → gesunken "
                    f"(< {_BTC_HR_WEAK} EH/s) – Miner-Abwanderung möglich"
                )
            else:
                key_signals.append(f"Hash Rate: {hr_ehs:.0f} EH/s – normal")

        # ── Transaktionsanzahl (BTC) ───────────────────────────────────────────
        if metrics.tx_count_24h:
            tx = metrics.tx_count_24h
            if tx > _BTC_TX_HIGH:
                score += 6
                key_signals.append(f"Transaktionen: {tx:,}/24h → hohes Netzwerk-Volumen")
            elif tx < _BTC_TX_LOW:
                score -= 5
                key_signals.append(f"Transaktionen: {tx:,}/24h → geringe Aktivität")
            else:
                key_signals.append(f"Transaktionen: {tx:,}/24h")

        # ── On-Chain Transaktionsvolumen (BTC) ─────────────────────────────────
        if metrics.tx_volume_usd_24h:
            vol_b = metrics.tx_volume_usd_24h / 1e9
            if vol_b > 10:
                score += 5
                key_signals.append(f"On-Chain-Volumen: ${vol_b:.1f}B/24h – sehr hoch")
            elif vol_b > 3:
                key_signals.append(f"On-Chain-Volumen: ${vol_b:.1f}B/24h")
            else:
                key_signals.append(f"On-Chain-Volumen: ${vol_b:.2f}B/24h – niedrig")

        # ── Handelsvolumen / Marktkapitalisierung ──────────────────────────────
        if metrics.volume_24h_usd and metrics.market_cap_usd:
            ratio = metrics.volume_24h_usd / metrics.market_cap_usd
            if ratio > 0.15:
                score += 5
                key_signals.append(
                    f"Vol/MarktKap: {ratio:.1%} → erhöhte Handelsaktivität (Interesse steigt)"
                )
            elif ratio < 0.02:
                score -= 3
                key_signals.append(
                    f"Vol/MarktKap: {ratio:.1%} → geringe Liquidität"
                )

        if not key_signals:
            return None

        score = max(0.0, min(100.0, score))
        if score >= 72:
            signal = "STRONG_BULLISH"
        elif score >= 58:
            signal = "BULLISH"
        elif score <= 28:
            signal = "STRONG_BEARISH"
        elif score <= 42:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        log.debug(
            "OnChain [%s] %s score=%.0f signals=%d",
            metrics.ticker, signal, score, len(key_signals),
        )

        return OnChainSnapshot(
            ticker=metrics.ticker,
            signal=signal,
            score=round(score, 1),
            key_signals=key_signals,
            source=metrics.source,
        )
