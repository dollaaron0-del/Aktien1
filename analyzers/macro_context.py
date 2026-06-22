"""
analyzers/macro_context.py – Zentraler Makro-Kontext für Trading-Entscheidungen.

Bündelt die bereits vorhandenen Makro-Analyzer zu *einer* kompakten Sicht, die
an drei Stellen konsumiert wird:

  1. as_prompt_block()  → kompakter Text-Block für den KI-Analyse-Prompt
                          (jede Aktie wird damit im Makro-Kontext bewertet)
  2. bias_score()       → -1.0 … +1.0  (Risk-Off … Risk-On) für Score-Gewichtung
  3. size_modifier(t)   → Multiplikator für die Positionsgröße (VIX × Kalender × Sektor)

Zusammengeführte Quellen (alle bereits existierend, hier nur aggregiert):
  - MarketOverview     : VIX, Marktregime, Cross-Asset-Bias
  - MacroCalendar      : FOMC/CPI/NFP-Termine & Nähe
  - RecessionDetector  : Rezessionswahrscheinlichkeit (gecachter Read, kein API-Call)
  - SectorRotation     : Risk-On/Off-Modus & starke/schwache Sektoren

Bewusst leichtgewichtig und fehler-isoliert: jede Quelle ist einzeln gekapselt,
fällt eine aus, bleibt der Rest nutzbar. Ergebnis wird pro Lauf gecacht (TTL 30min).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from logger import get_logger

log = get_logger(__name__)

_CACHE: Dict = {}
_CACHED_AT: Optional[datetime] = None
_CACHE_TTL_MIN = 30


class MacroContext:
    """Aggregiert alle Makro-Signale zu einem konsumierbaren Gesamtbild."""

    def snapshot(self, force: bool = False) -> Dict:
        """Sammelt alle Makro-Signale und cached das Ergebnis (TTL 30min)."""
        global _CACHE, _CACHED_AT
        if not force and _CACHED_AT is not None and _CACHE:
            age_min = (datetime.utcnow() - _CACHED_AT).total_seconds() / 60
            if age_min < _CACHE_TTL_MIN:
                return _CACHE

        s: Dict = {"timestamp": datetime.utcnow().isoformat()}

        # ── Markt-Lage (VIX, Regime, Cross-Asset, Gesamttendenz) ─────────────
        try:
            from analyzers.market_overview import MarketOverview
            ov = MarketOverview().full_assessment()
            s["regime"]      = ov.get("regime")
            s["vix"]         = ov.get("vix")
            s["vix_label"]   = ov.get("vix_label")
            s["overall"]     = ov.get("overall")          # BULLISH | NEUTRAL | VORSICHT
            s["buy_blocked"] = ov.get("buy_blocked", False)
            s["vix_size_mod"] = ov.get("position_size_modifier", 1.0)
            ca = ov.get("cross_asset") or {}
            s["cross_asset_rec"]   = ca.get("recommendation")
            s["risk_appetite"]     = ca.get("risk_appetite_score")
            s["yield_curve_inv"]   = ca.get("yield_curve_inverted")
            s["yield_curve_spread"] = ca.get("yield_curve_spread")
        except Exception as e:
            log.warning("MacroContext: Markt-Lage fehlgeschlagen: %s", e)

        # ── Makro-Termine (FOMC/CPI/NFP) ─────────────────────────────────────
        try:
            from analyzers.macro_calendar import MacroCalendar
            cal = MacroCalendar()
            events = cal.get_upcoming_events(7)
            s["macro_events"] = [
                {"name": e.name, "days_until": e.days_until,
                 "impact": e.impact, "date": e.release_date.isoformat()}
                for e in events[:4]
            ]
            s["cal_size_mod"] = cal.get_position_size_modifier()
        except Exception as e:
            log.warning("MacroContext: Makro-Kalender fehlgeschlagen: %s", e)

        # ── Rezessions-Wahrscheinlichkeit (gecachter Read) ───────────────────
        try:
            from analyzers.recession_detector import RecessionDetector
            latest = RecessionDetector().get_latest()
            if latest:
                s["recession_score"]  = latest.get("recession_score")
                s["recession_regime"] = latest.get("regime")
        except Exception as e:
            log.warning("MacroContext: Rezessions-Read fehlgeschlagen: %s", e)

        # ── Sektor-Rotation (Risk-Modus + starke/schwache Sektoren) ──────────
        try:
            from analyzers.sector_rotation import SectorRotation
            sr = SectorRotation()
            mode, diff = sr.get_risk_mode()
            s["sector_risk_mode"] = mode
            s["sector_risk_diff"] = round(diff, 2)
            s["strong_sectors"] = [x.name for x in sr.get_strong_sectors(3)]
            s["weak_sectors"]   = [x.name for x in sr.get_weak_sectors(3)]
        except Exception as e:
            log.warning("MacroContext: Sektor-Rotation fehlgeschlagen: %s", e)

        # ── FRED-Risikoreihen (HY-Spread, Erstanträge) ──────────────────────
        try:
            from collectors.fred_collector import FREDCollector
            fred = FREDCollector().read()
            if fred:
                s["hy_oas"]       = fred.get("hy_oas")
                s["hy_oas_trend"] = fred.get("hy_oas_trend")
                s["claims_trend"] = fred.get("claims_trend")
                s["macro_stress"] = fred.get("stress_label")
        except Exception as e:
            log.warning("MacroContext: FRED fehlgeschlagen: %s", e)

        # ── EIA-Rohöl-Lagerbestände (Build/Draw → Öl-Bias) ──────────────────
        try:
            from collectors.eia_collector import EIACollector
            eia = EIACollector().read()
            if eia:
                s["oil_stocks_label"] = eia.get("label")
                s["oil_bias"]         = eia.get("oil_bias")
                s["oil_wow_mbbl"]     = eia.get("wow_change_mbbl")
        except Exception as e:
            log.warning("MacroContext: EIA fehlgeschlagen: %s", e)

        # ── ENTSO-E EU-Stromlast (Industrieaktivitäts-Proxy) ────────────────
        try:
            from collectors.entsoe_collector import ENTSOECollector
            eu = ENTSOECollector().read()
            if eu:
                s["eu_activity"]   = eu.get("activity_label")
                s["eu_load_ratio"] = eu.get("ratio")
        except Exception as e:
            log.warning("MacroContext: ENTSO-E fehlgeschlagen: %s", e)

        # ── Wetter-Energie-Nachfrage (Open-Meteo, HDD/CDD-Forecast-Proxy) ───
        try:
            from collectors.weather_collector import WeatherCollector
            wx = WeatherCollector().read()
            if wx:
                s["weather_demand"] = wx.get("demand_label")
                s["weather_ratio"]  = wx.get("ratio")
        except Exception as e:
            log.warning("MacroContext: Wetter fehlgeschlagen: %s", e)

        # ── Globales BIP-Momentum (DBnomics/OECD-G20, keylos) ───────────────
        try:
            from collectors.dbnomics_collector import DBnomicsCollector
            gl = DBnomicsCollector().read()
            if gl:
                s["global_momentum"]    = gl.get("momentum_label")
                s["global_contracting"] = gl.get("contracting")
                s["global_n"]           = gl.get("n")
                s["g20_gdp"]            = gl.get("g20_latest")
        except Exception as e:
            log.warning("MacroContext: DBnomics fehlgeschlagen: %s", e)

        # ── Tanker-Verkehr (experimentell, nur falls Daten vorliegen) ───────
        try:
            from collectors.tanker_flow_collector import get_latest as _tanker_latest
            tk = _tanker_latest()
            if tk and tk.get("signal"):
                s["tanker_signal"] = tk["signal"].get("label")
        except Exception as e:
            log.warning("MacroContext: Tanker-Read fehlgeschlagen: %s", e)

        _CACHE = s
        _CACHED_AT = datetime.utcnow()
        return s

    # ── Konsum-Schnittstellen ────────────────────────────────────────────────

    def as_prompt_block(self) -> str:
        """
        Kompakter Makro-Lagebericht als Text für den KI-Analyse-Prompt.
        Leerer String, wenn keine Daten vorliegen (Prompt bleibt dann unverändert).
        """
        s = self.snapshot()
        lines = []

        regime = s.get("regime")
        overall = s.get("overall")
        if regime or overall:
            bits = []
            if regime:
                bits.append(f"Regime {regime}")
            if overall:
                bits.append(f"Tendenz {overall}")
            lines.append("- Marktlage: " + ", ".join(bits))

        vix = s.get("vix")
        if vix is not None:
            lines.append(f"- VIX: {vix:.1f} ({s.get('vix_label', '?')})")

        rec = s.get("cross_asset_rec")
        if rec:
            extra = ""
            if s.get("yield_curve_inv"):
                sp = s.get("yield_curve_spread")
                extra = f", Zinskurve invertiert{f' ({sp:+.2f}%)' if sp is not None else ''}"
            lines.append(f"- Cross-Asset: {rec}{extra}")

        rscore = s.get("recession_score")
        if rscore is not None:
            lines.append(f"- Rezessionsrisiko: {rscore:.0%} ({s.get('recession_regime', '?')})")

        mode = s.get("sector_risk_mode")
        if mode:
            strong = ", ".join(s.get("strong_sectors", [])[:3])
            weak = ", ".join(s.get("weak_sectors", [])[:3])
            line = f"- Sektor-Rotation: {mode}"
            if strong:
                line += f" | stark: {strong}"
            if weak:
                line += f" | schwach: {weak}"
            lines.append(line)

        events = s.get("macro_events") or []
        if events:
            ev = []
            for e in events:
                d = e["days_until"]
                when = "heute" if d == 0 else (f"in {d}d" if d > 0 else f"vor {abs(d)}d")
                tag = "⚠" if e["impact"] == "HIGH" else ""
                ev.append(f"{e['name']} ({when}){tag}")
            lines.append("- Anstehende Makro-Termine: " + "; ".join(ev))

        stress = s.get("macro_stress")
        if stress and stress != "CALM":
            bits = []
            if s.get("hy_oas") is not None:
                bits.append(f"HY-Spread {s['hy_oas']:.2f}% ({s.get('hy_oas_trend', '?')})")
            if s.get("claims_trend"):
                bits.append(f"Erstanträge {s['claims_trend']}")
            line = f"- Makro-Stress (FRED): {stress}"
            if bits:
                line += " | " + ", ".join(bits)
            lines.append(line)

        gm = s.get("global_momentum")
        if gm and gm != "GLOBAL_NEUTRAL":
            arrow = "↑" if gm == "GLOBAL_ACCELERATING" else "↓"
            extra = ""
            contr, gn = s.get("global_contracting"), s.get("global_n")
            if isinstance(contr, int) and isinstance(gn, int) and contr:
                extra = f", {contr}/{gn} Blöcke schrumpfen"
            lines.append(f"- Globales BIP-Momentum (OECD-G20): {gm} {arrow}{extra}")

        oil = s.get("oil_stocks_label")
        if oil:
            wow = s.get("oil_wow_mbbl")
            wow_txt = f" {wow:+.1f} Mbbl" if isinstance(wow, (int, float)) else ""
            lines.append(f"- Öl-Lager (EIA): {oil}{wow_txt} → Öl-Bias {s.get('oil_bias', '?')}")

        eu = s.get("eu_activity")
        if eu and eu != "NORMAL":
            lines.append(f"- EU-Stromlast (ENTSO-E): {eu} (Industrieaktivitäts-Proxy)")

        wx = s.get("weather_demand")
        if wx and wx != "NORMAL":
            arrow = "↑" if wx == "ELEVATED" else "↓"
            lines.append(f"- Energie-Wetter (Open-Meteo): Nachfrage {wx} {arrow} "
                         f"(Heiz-/Kühl-Gradtage-Vorlauf)")

        tk = s.get("tanker_signal")
        if tk and tk not in ("INSUFFICIENT_DATA", "NORMAL"):
            lines.append(f"- Tanker-Verkehr an Öl-Chokepoints: {tk} (experimentell)")

        if not lines:
            return ""

        return "AKTUELLE MAKRO-LAGE (gilt für alle Titel):\n" + "\n".join(lines)

    def bias_score(self) -> float:
        """
        Gesamttendenz als Zahl in [-1.0, +1.0].
        Positiv = Rückenwind (Risk-On), negativ = Gegenwind (Risk-Off/Rezession).
        """
        s = self.snapshot()
        score = 0.0

        overall = s.get("overall")
        if overall == "BULLISH":
            score += 0.4
        elif overall == "VORSICHT":
            score -= 0.4

        rec = s.get("cross_asset_rec")
        if rec == "RISK_ON":
            score += 0.3
        elif rec == "RISK_OFF":
            score -= 0.3

        mode = s.get("sector_risk_mode")
        if mode == "RISK_ON":
            score += 0.2
        elif mode == "RISK_OFF":
            score -= 0.2

        rscore = s.get("recession_score")
        if rscore is not None:
            if rscore >= 0.65:
                score -= 0.3
            elif rscore >= 0.45:
                score -= 0.15

        if s.get("buy_blocked"):
            score -= 0.2

        # FRED-Makro-Stress als breiter Risk-Off-Beitrag (HY-Spread + Erstanträge)
        stress = s.get("macro_stress")
        if stress == "ELEVATED":
            score -= 0.2
        elif stress == "WATCH":
            score -= 0.1

        return max(-1.0, min(1.0, round(score, 3)))

    def size_modifier(self, ticker: str = "") -> float:
        """
        Positionsgrößen-Multiplikator aus den Makro-Faktoren.
        Kombiniert VIX-, Kalender- und Sektor-Modifikator, geklemmt auf [0.3, 1.15].
        """
        s = self.snapshot()
        mod = 1.0

        vix_mod = s.get("vix_size_mod")
        if isinstance(vix_mod, (int, float)) and vix_mod > 0:
            mod *= vix_mod

        cal_mod = s.get("cal_size_mod")
        if isinstance(cal_mod, (int, float)) and cal_mod > 0:
            mod *= cal_mod

        if ticker:
            try:
                from analyzers.sector_rotation import SectorRotation
                mod *= SectorRotation().get_position_size_modifier(ticker)
            except Exception:
                pass

        return max(0.3, min(1.15, round(mod, 3)))


# ── Modul-weiter Singleton + Komfort-Funktionen ───────────────────────────────
_INSTANCE: Optional[MacroContext] = None


def get_macro_context() -> MacroContext:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = MacroContext()
    return _INSTANCE


def get_macro_brief() -> str:
    """Kompakter Makro-Block für Prompts. Fehler-isoliert → liefert notfalls ''."""
    try:
        return get_macro_context().as_prompt_block()
    except Exception as e:
        log.warning("get_macro_brief fehlgeschlagen: %s", e)
        return ""
