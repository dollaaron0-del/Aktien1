"""
collectors/short_volume_collector.py – tägliches Short-Volumen (FINRA RegSHO).

short_interest ist nur ~2×/Monat und stark verzögert. FINRA veröffentlicht
dagegen TÄGLICH das konsolidierte Short-Sale-Volumen aller NMS-Aktien (frei, kein
Key). Ein hoher Short-Volumen-Anteil (ShortVolume/TotalVolume) zeigt frischen
Short-Druck – ambivalent (bärisch ODER Squeeze-Brennstoff), aber zeitnah.

Effizienz: eine Tagesdatei enthält ALLE Symbole → wird einmal pro Tag geladen
und im Prozess gecacht; collect(ticker) ist danach ein reiner Lookup.

Datei: https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
Format: Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

Konfig (.env):
  SHORT_VOL_MIN_RATIO   (Standard: 0.55)  ab welchem Anteil gemeldet wird
  SHORT_VOL_LOOKBACK    (Standard: 5)     wie viele Kalendertage rückwärts
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_URL_TMPL  = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
_MIN_RATIO = float(os.getenv("SHORT_VOL_MIN_RATIO", "0.55"))
_LOOKBACK  = int(os.getenv("SHORT_VOL_LOOKBACK", "5"))

# prozessweiter Tages-Cache: 'YYYYMMDD' → {symbol: (short_vol, total_vol)} | None
_DAY_CACHE: Dict[str, Optional[Dict[str, Tuple[int, int]]]] = {}


class ShortVolumeCollector:
    def __init__(self, lookback_days: int = _LOOKBACK, min_ratio: float = _MIN_RATIO):
        self.lookback_days = lookback_days
        self.min_ratio = min_ratio

    def collect(self, ticker: str) -> List[Dict]:
        try:
            maps = self._recent_maps()
            avg_ratio, n_days, avg_short = self._ratio_for(ticker.upper(), maps)
            if n_days == 0 or avg_ratio < self.min_ratio:
                return []
            return self._to_item(ticker, avg_ratio, n_days, avg_short)
        except Exception as e:
            log.debug("[%s] Short-Volumen: %s", ticker, e)
            return []

    # ── Signal ────────────────────────────────────────────────────────────────
    @staticmethod
    def _ratio_for(ticker: str, day_maps: List[Dict[str, Tuple[int, int]]]) -> Tuple[float, int, float]:
        ratios, shorts = [], []
        for m in day_maps:
            rec = m.get(ticker)
            if not rec:
                continue
            short, total = rec
            if total > 0:
                ratios.append(short / total)
                shorts.append(short)
        if not ratios:
            return 0.0, 0, 0.0
        return sum(ratios) / len(ratios), len(ratios), sum(shorts) / len(shorts)

    def _to_item(self, ticker: str, ratio: float, n_days: int, avg_short: float) -> List[Dict]:
        prio = "HIGH" if ratio >= 0.65 else "MEDIUM"
        return [{
            "source":       "FINRA/ShortVolume",
            "ticker":       ticker,
            "title":        f"Hoher Short-Volumen-Anteil: {ratio*100:.0f}% (Ø {n_days}T, ~{avg_short:,.0f} Stk/Tag)",
            "text":         (f"FINRA-Tages-Short-Volumen im Schnitt {ratio*100:.0f}% des Gesamtvolumens "
                             f"über {n_days} Tage. Starker Short-Druck – bärisch ODER Squeeze-Brennstoff."),
            "published_at": datetime.utcnow().isoformat(),
            "priority":     prio,
        }]

    # ── FINRA-Tagesdateien (geteilt über alle Ticker) ─────────────────────────
    def _recent_maps(self) -> List[Dict[str, Tuple[int, int]]]:
        maps = []
        day = datetime.utcnow().date()
        checked = 0
        # bis zu lookback*2 Kalendertage rückwärts, um Wochenenden/Feiertage zu überspringen
        while len(maps) < self.lookback_days and checked < self.lookback_days * 2 + 3:
            ymd = day.strftime("%Y%m%d")
            m = self._day_map(ymd)
            if m:
                maps.append(m)
            day -= timedelta(days=1)
            checked += 1
        return maps

    def _day_map(self, ymd: str) -> Optional[Dict[str, Tuple[int, int]]]:
        if ymd in _DAY_CACHE:
            return _DAY_CACHE[ymd]
        try:
            resp = http_get(_URL_TMPL.format(ymd=ymd), timeout=15)
            parsed = self._parse(resp.text) if resp.status_code == 200 else None
        except Exception as e:
            log.debug("FINRA %s: %s", ymd, e)
            parsed = None
        _DAY_CACHE[ymd] = parsed
        return parsed

    @staticmethod
    def _parse(text: str) -> Dict[str, Tuple[int, int]]:
        out: Dict[str, Tuple[int, int]] = {}
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) < 5 or parts[0] in ("Date", "") or parts[1] == "Symbol":
                continue
            sym = parts[1].strip().upper()
            try:
                short = int(parts[2]); total = int(parts[4])
            except (ValueError, IndexError):
                continue
            if sym and total > 0:
                out[sym] = (short, total)
        return out
