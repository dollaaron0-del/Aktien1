"""
Options Flow Collector — erkennt ungewöhnliche Options-Aktivität via Yahoo Finance (kostenlos).

Drei Signaltypen:
  1. Put/Call-Ratio  — hohes Call-Volumen = bullischer Ausblick, hohes Put-Volumen = bearish/Absicherung
  2. Volume/OI-Ratio — Volumen >> Open Interest deutet auf neue Positionen hin (keine Schließungen)
  3. OTM-Sweeps      — große Lots weit aus dem Geld = spekulative Wetten, oft vor Nachrichten

Warum Volume/OI wichtig ist:
  Wenn das Volumen eines Strikes das Open Interest übersteigt, werden neue Positionen eröffnet,
  nicht nur bestehende gerollt. Das ist der klarste Hinweis auf informierte Händler die auf
  eine bevorstehende Kursbewegung wetten.
"""
from datetime import datetime, timezone
from math import isnan
from typing import List, Dict, Optional
import yfinance as yf


def _safe_num(value, default=0.0) -> float:
    """Wandelt einen Wert robust in float um – fängt None und NaN ab.
    yfinance liefert für leere Options-Zellen NaN (truthy!), weshalb
    `int(x or 0)` bei NaN crasht (`cannot convert float NaN to integer`)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if isnan(v) else v


class OptionsFlowCollector:
    def __init__(
        self,
        min_volume_ratio:  float = 2.0,   # min C/P oder P/C-Verhältnis
        min_total_volume:  int   = 500,    # mind. Gesamtvolumen beide Seiten
        min_vol_oi_ratio:  float = 1.5,    # Volumen muss mind. 1.5× Open Interest sein
        min_sweep_volume:  int   = 300,    # Min. Kontrakte für OTM-Sweep-Signal
        otm_threshold_pct: float = 0.05,   # Strikes >5% aus dem Geld gelten als OTM
    ):
        self.min_volume_ratio  = min_volume_ratio
        self.min_total_volume  = min_total_volume
        self.min_vol_oi_ratio  = min_vol_oi_ratio
        self.min_sweep_volume  = min_sweep_volume
        self.otm_threshold_pct = otm_threshold_pct

    def collect(self, ticker: str) -> List[Dict]:
        try:
            stock = yf.Ticker(ticker)
            expirations = stock.options
            if not expirations:
                return []
            target_exps = expirations[:2]
            current_price = self._get_price(stock)
        except Exception:
            return []

        total_call_vol = 0
        total_put_vol  = 0
        vol_oi_signals: List[Dict] = []
        otm_sweeps:     List[Dict] = []

        for exp in target_exps:
            try:
                chain = yf.Ticker(ticker).option_chain(exp)
                calls = chain.calls
                puts  = chain.puts
            except Exception:
                continue

            if calls is not None and not calls.empty:
                cv = int(calls["volume"].fillna(0).sum())
                total_call_vol += cv
                # Volume/OI ratio + OTM sweep detection for calls
                for _, row in calls.iterrows():
                    vol = int(_safe_num(row.get("volume", 0)))
                    oi  = int(_safe_num(row.get("openInterest", 0)))
                    strike = _safe_num(row.get("strike", 0))
                    if vol < 100:
                        continue
                    # Volume/OI: new money entering
                    if oi > 0 and vol / oi >= self.min_vol_oi_ratio and vol >= self.min_sweep_volume:
                        vol_oi_signals.append({
                            "type": "CALL", "strike": strike, "volume": vol,
                            "oi": oi, "ratio": round(vol / oi, 1), "expiry": exp,
                            "is_otm": current_price and strike > current_price * (1 + self.otm_threshold_pct),
                        })
                    # OTM sweep
                    if (current_price and strike > current_price * (1 + self.otm_threshold_pct)
                            and vol >= self.min_sweep_volume):
                        otm_sweeps.append({
                            "type": "CALL", "strike": strike, "volume": vol,
                            "expiry": exp, "otm_pct": round((strike / current_price - 1) * 100, 1),
                        })

            if puts is not None and not puts.empty:
                pv = int(puts["volume"].fillna(0).sum())
                total_put_vol += pv
                for _, row in puts.iterrows():
                    vol = int(_safe_num(row.get("volume", 0)))
                    oi  = int(_safe_num(row.get("openInterest", 0)))
                    strike = _safe_num(row.get("strike", 0))
                    if vol < 100:
                        continue
                    if oi > 0 and vol / oi >= self.min_vol_oi_ratio and vol >= self.min_sweep_volume:
                        vol_oi_signals.append({
                            "type": "PUT", "strike": strike, "volume": vol,
                            "oi": oi, "ratio": round(vol / oi, 1), "expiry": exp,
                            "is_otm": current_price and strike < current_price * (1 - self.otm_threshold_pct),
                        })
                    if (current_price and strike < current_price * (1 - self.otm_threshold_pct)
                            and vol >= self.min_sweep_volume):
                        otm_sweeps.append({
                            "type": "PUT", "strike": strike, "volume": vol,
                            "expiry": exp, "otm_pct": round((1 - strike / current_price) * 100, 1),
                        })

        if total_call_vol + total_put_vol < self.min_total_volume:
            return []

        items: List[Dict] = []
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        url = f"https://finance.yahoo.com/quote/{ticker}/options"

        # ── Signal 1: Put/Call-Ratio ──────────────────────────────────────────
        ratio     = total_call_vol / max(total_put_vol, 1)
        inv_ratio = total_put_vol  / max(total_call_vol, 1)

        if ratio >= self.min_volume_ratio:
            items.append({
                "title":     f"Ungewöhnliche Call-Aktivität bei {ticker} – C/P-Ratio {ratio:.1f}",
                "summary":   (
                    f"Calls: {total_call_vol:,} vs Puts: {total_put_vol:,} "
                    f"(Ratio {ratio:.1f}). Bullisches Optionsflow-Signal – "
                    f"deutlich mehr Call- als Put-Volumen deutet auf bullische Erwartungshaltung."
                ),
                "url":       url,
                "published": now,
                "source":    "options_flow",
                "signal":    "BULLISCH",
            })
        elif inv_ratio >= self.min_volume_ratio:
            items.append({
                "title":     f"Ungewöhnliche Put-Aktivität bei {ticker} – P/C-Ratio {inv_ratio:.1f}",
                "summary":   (
                    f"Puts: {total_put_vol:,} vs Calls: {total_call_vol:,} "
                    f"(Ratio {inv_ratio:.1f}). Bärisches Optionsflow-Signal oder Absicherung – "
                    f"Put-Käufe können auf institutionelle Hedge-Positionen hinweisen."
                ),
                "url":       url,
                "published": now,
                "source":    "options_flow",
                "signal":    "BÄRISCH",
            })

        # ── Signal 2: Volume/OI-Ratio — neue Positionen ───────────────────────
        # Sortiere nach ratio, nimm die überzeugendsten Signale
        vol_oi_signals.sort(key=lambda x: -x["ratio"])
        for s in vol_oi_signals[:3]:
            signal  = "BULLISCH" if s["type"] == "CALL" else "BÄRISCH"
            otm_tag = " (OTM-Spekulation)" if s.get("is_otm") else ""
            items.append({
                "title": (
                    f"Neue {s['type']}-Positionen {ticker} ${s['strike']:.0f} ({s['expiry']})"
                    f"{otm_tag} – Vol/OI {s['ratio']:.1f}×"
                ),
                "summary": (
                    f"Volumen {s['volume']:,} Kontrakte bei Open Interest {s['oi']:,} "
                    f"(Vol/OI-Ratio: {s['ratio']:.1f}). "
                    f"Volumen >> Open Interest deutet auf neue Positionen hin, nicht nur Rollovers. "
                    f"Dies signalisiert frisches Interesse informierter Händler."
                ),
                "url":       url,
                "published": now,
                "source":    "options_flow",
                "signal":    signal,
            })

        # ── Signal 3: OTM-Sweeps ─────────────────────────────────────────────
        otm_sweeps.sort(key=lambda x: -x["volume"])
        for s in otm_sweeps[:2]:
            signal  = "BULLISCH" if s["type"] == "CALL" else "BÄRISCH"
            items.append({
                "title": (
                    f"OTM-Sweep {ticker}: Großer {s['type']}-Kauf ${s['strike']:.0f} "
                    f"({s['otm_pct']:.0f}% OTM, {s['expiry']})"
                ),
                "summary": (
                    f"Volumen: {s['volume']:,} Kontrakte auf Strike ${s['strike']:.0f} "
                    f"({s['otm_pct']:.1f}% aus dem Geld, Verfall {s['expiry']}). "
                    f"Weit OTM-Käufe mit hohem Volumen sind oft spekulative Wetten "
                    f"kurz vor erwarteten Kursbewegungen (Earnings, FDA-Beschlüsse, M&A)."
                ),
                "url":       url,
                "published": now,
                "source":    "options_flow",
                "signal":    signal,
            })

        return items

    @staticmethod
    def _get_price(stock) -> Optional[float]:
        try:
            info = stock.fast_info
            return float(info.last_price or 0) or None
        except Exception:
            return None
