"""
analyzers/market_overview.py – Aggregierter Markt-Lagebericht.

Bündelt VIX (Angst-Index), Marktregime und Cross-Asset-Makrosignale zu einer
kompakten Gesamteinschätzung. Genutzt vom Scheduler für:
  - 4-stündlichen Cache-Refresh (full_assessment)
  - täglichen Morgen-Lagebericht via Telegram (format_telegram)

Bewusst leichtgewichtig: aggregiert nur bereits vorhandene Analyzer, keine
neuen Datenquellen. Jede Quelle ist einzeln fehler-isoliert.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from logger import get_logger

log = get_logger(__name__)

# Modul-weiter Cache, damit Refresh-Job und Lagebericht-Job dieselbe Einschätzung teilen
_CACHE: Dict = {}
_CACHED_AT: Optional[datetime] = None
_CACHE_TTL_MIN = 240  # 4 Stunden


def _vix_label(vix: Optional[float]) -> str:
    if vix is None:
        return "unbekannt"
    if vix < 16:
        return "ruhig"
    if vix < 20:
        return "normal"
    if vix < 28:
        return "erhöht"
    return "Stress"


def _overall_bias(a: Dict) -> str:
    """Einfache Gesamttendenz aus Regime, Cross-Asset und VIX."""
    score = 0
    regime = a.get("regime", "")
    if regime == "BULL":
        score += 1
    elif regime in ("BEAR", "CRISIS"):
        score -= 1

    rec = (a.get("cross_asset") or {}).get("recommendation", "")
    if rec == "RISK_ON":
        score += 1
    elif rec == "RISK_OFF":
        score -= 1

    vix = a.get("vix")
    if vix is not None and vix >= 28:
        score -= 1
    if a.get("buy_blocked"):
        score -= 1

    if score >= 1:
        return "BULLISH"
    if score <= -1:
        return "VORSICHT"
    return "NEUTRAL"


class MarketOverview:
    """Aggregiert VIX, Regime und Cross-Asset-Signale zu einer Markteinschätzung."""

    def full_assessment(self, force: bool = False) -> Dict:
        """Sammelt alle Marktsignale und cached das Ergebnis (TTL 4h)."""
        global _CACHE, _CACHED_AT
        if not force and _CACHED_AT is not None and _CACHE:
            age_min = (datetime.now(timezone.utc).replace(tzinfo=None) - _CACHED_AT).total_seconds() / 60
            if age_min < _CACHE_TTL_MIN:
                return _CACHE

        a: Dict = {"timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}

        # ── VIX (Angst-Index) ────────────────────────────────────────────────
        try:
            from collectors.vix_monitor import (
                get_vix, get_position_size_modifier, should_block_buy,
            )
            vix = get_vix()
            a["vix"] = vix
            a["vix_label"] = _vix_label(vix)
            a["position_size_modifier"] = get_position_size_modifier()
            blocked, reason = should_block_buy()
            a["buy_blocked"] = blocked
            a["buy_block_reason"] = reason
        except Exception as e:
            log.warning("MarketOverview: VIX-Abruf fehlgeschlagen: %s", e)

        # ── Marktregime ──────────────────────────────────────────────────────
        try:
            from analyzers.regime_adaptive import (
                get_current_regime, get_regime_config, get_market_caution,
            )
            regime = get_current_regime()
            a["regime"] = regime
            try:
                a["regime_summary"] = get_regime_config().summary(regime)
            except Exception:
                a["regime_summary"] = ""
            caution = get_market_caution()
            if caution:
                a["market_caution"] = caution
        except Exception as e:
            log.warning("MarketOverview: Regime-Abruf fehlgeschlagen: %s", e)

        # ── Cross-Asset-Makrosignale ─────────────────────────────────────────
        try:
            from analyzers.cross_asset import CrossAssetSignals
            snap = CrossAssetSignals().fetch()
            a["cross_asset"] = {
                "recommendation":       snap.recommendation,
                "risk_appetite_score":  snap.risk_appetite_score,
                "yield_curve_inverted": snap.yield_curve_inverted,
                "yield_curve_spread":   snap.yield_curve_spread,
                "hyg_momentum":         snap.hyg_momentum,
                "dxy_momentum":         snap.dxy_momentum,
            }
        except Exception as e:
            log.warning("MarketOverview: Cross-Asset-Abruf fehlgeschlagen: %s", e)

        a["overall"] = _overall_bias(a)

        _CACHE = a
        _CACHED_AT = datetime.now(timezone.utc).replace(tzinfo=None)
        return a

    def get_cached(self) -> Dict:
        """Gibt die letzte Einschätzung zurück (oder erstellt eine neue)."""
        return _CACHE or self.full_assessment()

    def format_telegram(self) -> str:
        """Formatiert die Einschätzung als HTML-Telegram-Nachricht."""
        a = self.get_cached()
        bias = a.get("overall", "NEUTRAL")
        bias_icon = {"BULLISH": "🟢", "NEUTRAL": "🟡", "VORSICHT": "🔴"}.get(bias, "⚪")

        lines = [
            f"📊 <b>Markt-Lagebericht</b> – {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%d.%m.%Y')}",
            f"{bias_icon} Gesamttendenz: <b>{bias}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        vix = a.get("vix")
        if vix is not None:
            lines.append(f"😱 <b>VIX:</b> {vix:.1f} ({a.get('vix_label', '?')})")
            mod = a.get("position_size_modifier")
            if mod is not None and abs(mod - 1.0) > 0.001:
                lines.append(f"   → Positionsgröße ×{mod:.2f}")
            if a.get("buy_blocked"):
                lines.append(f"   ⚠️ Käufe blockiert: {a.get('buy_block_reason', '')}")

        regime = a.get("regime")
        if regime:
            r_icon = {"BULL": "📈", "NEUTRAL": "➡️", "BEAR": "📉", "CRISIS": "🆘"}.get(regime, "")
            lines.append(f"{r_icon} <b>Regime:</b> {regime}")
            if a.get("regime_summary"):
                lines.append(f"   {a['regime_summary']}")

        ca = a.get("cross_asset")
        if ca:
            rec = ca.get("recommendation", "?")
            ca_icon = {"RISK_ON": "🟢", "NEUTRAL": "🟡", "RISK_OFF": "🔴"}.get(rec, "")
            score = ca.get("risk_appetite_score")
            score_str = f" (Score {score:.2f})" if score is not None else ""
            lines.append(f"{ca_icon} <b>Cross-Asset:</b> {rec}{score_str}")
            if ca.get("yield_curve_inverted"):
                spread = ca.get("yield_curve_spread")
                spread_str = f" ({spread:+.2f}%)" if spread is not None else ""
                lines.append(f"   ⚠️ Zinskurve invertiert{spread_str}")

        caution = a.get("market_caution")
        if caution and isinstance(caution, dict) and caution.get("reason"):
            lines.append(f"⚠️ <b>Markt-Warnung:</b> {caution.get('reason')}")

        return "\n".join(lines)
