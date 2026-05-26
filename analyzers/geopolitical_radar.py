"""
GeopoliticalRadar – Erkennt geopolitische Frühsignale und leitet
Marktauswirkungen ab, bevor der Mainstream reagiert.

Beispiele:
  • Flugzeugträger im Persischen Golf   → Öl-Produzenten BUY, Airlines WARN
  • Chinesische Kriegsschiffe bei Taiwan → TSMC/Halbleiter SELL-Warnung, Rüstung BUY
  • Russland-Eskalation                  → Gas/Energie BUY, EU-Industrie WARN
  • Sanktionen gegen Land X              → Rohstoffe aus Region prüfen

Datenquellen: RSS-Feeds (Reuters World, AP, BBC, Al Jazeera, Defense News,
USNI News) – kein API-Key nötig.

Ablauf:
  1. Stündlich: Weltpolitik-Schlagzeilen laden
  2. Regexbasierte Event-Kategorisierung (13 Kategorien)
  3. Event → Marktauswirkung mappen (Sektor + Ticker + Richtung)
  4. Starke Signale → BenchList + Telegram-Sofortalert
  5. Ticker werden beim nächsten Claude-Zyklus vollständig analysiert
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests

from logger import get_logger

log = get_logger(__name__)

_STATE_FILE = os.path.join("data", "geopolitical_radar_state.json")
_TIMEOUT    = 12
_HEADERS    = {"User-Agent": "Mozilla/5.0 StockSentimentBot/1.0 (geopolitical research)"}

# ── RSS-Quellen für Weltpolitik & Verteidigung ────────────────────────────────

_GEO_RSS_FEEDS = [
    ("Reuters World",    "https://feeds.reuters.com/reuters/worldNews"),
    ("AP Top News",      "https://rsshub.app/ap/topics/apf-intlnews"),
    ("BBC World",        "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera",       "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Defense News",     "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("USNI News",        "https://news.usni.org/feed"),
    ("Reuters Commodities", "https://feeds.reuters.com/reuters/USenergyNews"),
    ("FT World",         "https://www.ft.com/world?format=rss"),
]

# ── Geopolitische Event-Kategorien ───────────────────────────────────────────
# Jede Kategorie hat: keywords, market_impacts, severity (1-3)
# severity 3 = sofortiger Telegram-Alert

@dataclass
class MarketImpact:
    tickers:   List[str]
    direction: str        # BUY | SELL | WATCH
    reason:    str
    score:     float      # 0.0–1.0 für BenchList

@dataclass
class GeoEvent:
    category:    str
    severity:    int       # 1=niedrig, 2=mittel, 3=kritisch
    headline:    str
    source:      str
    impacts:     List[MarketImpact]
    detected_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


_GEO_CATEGORIES: List[Dict] = [
    {
        "name": "MIDDLE_EAST_CONFLICT",
        "label": "Nahost-Konflikt",
        "severity": 3,
        "keywords": [
            "strait of hormuz", "persian gulf", "iran attack", "iran strike",
            "iran war", "israel iran", "houthi", "red sea attack",
            "oil tanker attack", "gulf escalation", "carrier strike group gulf",
        ],
        "impacts": [
            MarketImpact(["XOM", "CVX", "OXY", "COP", "PSX"],          "BUY",  "Öl-Angebotsschock Persischer Golf",   0.85),
            MarketImpact(["LMT", "RTX", "NOC", "GD", "RHM.DE"],        "BUY",  "Rüstungsnachfrage Nahost",             0.82),
            MarketImpact(["GLD", "SLV"],                                "BUY",  "Safe-Haven Gold bei Kriegsrisiko",     0.75),
            MarketImpact(["DAL", "UAL", "LUF.DE", "AF.PA"],            "SELL", "Luftraum-Sperrung, Kerosin-Kosten",    0.30),
            MarketImpact(["ZIM", "MAERSK.CO", "DSV.CO"],               "WATCH","Suezkanal-Route gefährdet",            0.55),
        ],
    },
    {
        "name": "TAIWAN_STRAIT_TENSION",
        "label": "Taiwan-Straße Eskalation",
        "severity": 3,
        "keywords": [
            "taiwan strait", "taiwan invasion", "pla navy taiwan",
            "chinese warships taiwan", "taiwan blockade", "pla exercise taiwan",
            "china taiwan military", "taiwan crisis", "cross-strait tension",
        ],
        "impacts": [
            MarketImpact(["TSM", "NVDA", "AMD", "AMAT", "LRCX", "KLAC"], "SELL", "TSMC-Risiko, Halbleiter-Lieferkette",  0.25),
            MarketImpact(["LMT", "RTX", "NOC", "HII", "GD"],             "BUY",  "US-Pazifik-Verteidigung",              0.85),
            MarketImpact(["RHM.DE", "AIR.PA", "BAES.L"],                 "BUY",  "NATO-Rüstung Reaktion",                0.78),
            MarketImpact(["GLD"],                                          "BUY",  "Safe-Haven",                           0.72),
            MarketImpact(["AAPL", "MSFT"],                                "WATCH","Lieferketten-Exposition China",        0.45),
        ],
    },
    {
        "name": "RUSSIA_UKRAINE_ESCALATION",
        "label": "Russland-Ukraine Eskalation",
        "severity": 3,
        "keywords": [
            "russia ukraine", "russia nato", "russia nuclear",
            "russia mobiliz", "ukraine offensive", "russia attack",
            "russia launch", "russia strike", "russia missile",
            "putin escalat", "russian troops", "russian forces ukraine",
        ],
        "impacts": [
            MarketImpact(["RHM.DE", "AIR.PA", "BAES.L", "LMT", "RTX"],  "BUY",  "Europäische Rüstung, NATO-Aufrüstung", 0.88),
            MarketImpact(["ENGI.PA", "RWE.DE", "NESTE.HE"],              "BUY",  "Europäischer Energie-Umbau",           0.75),
            MarketImpact(["XOM", "CVX", "SLB"],                           "BUY",  "Gas/Öl Versorgungssorgen",             0.78),
            MarketImpact(["GLD"],                                          "BUY",  "Safe-Haven Gold",                      0.72),
            MarketImpact(["SIE.DE", "BASF.DE", "BMW.DE"],                "SELL", "Europäische Industrie-Exposition",     0.30),
        ],
    },
    {
        "name": "NORTH_KOREA_PROVOCATION",
        "label": "Nordkorea Provokation",
        "severity": 2,
        "keywords": [
            "north korea missile", "north korea nuclear", "north korea icbm",
            "north korea launch", "north korea fires", "dprk launch",
            "dprk missile", "pyongyang test", "pyongyang launch",
            "kim jong un provoc", "korean peninsula tension",
        ],
        "impacts": [
            MarketImpact(["LMT", "RTX", "NOC", "HII"],                   "BUY",  "US-Pazifik-Abwehr",                    0.75),
            MarketImpact(["005930.KS", "Samsung", "SK Hynix"],            "SELL", "Südkorea-Markt unter Druck",           0.35),
            MarketImpact(["GLD"],                                          "BUY",  "Safe-Haven",                           0.65),
        ],
    },
    {
        "name": "OIL_SUPPLY_SHOCK",
        "label": "Öl-Angebotsschock",
        "severity": 2,
        "keywords": [
            "opec cut", "opec+ cut", "opec production cut", "opec surprise",
            "opec+ announces", "million barrels cut", "barrels per day cut",
            "oil supply disruption", "pipeline attack", "oil field strike",
            "saudi aramco attack", "oil embargo", "strategic reserve release",
        ],
        "impacts": [
            MarketImpact(["XOM", "CVX", "OXY", "COP", "PSX", "VLO"],    "BUY",  "Direkt von Ölpreis-Anstieg",           0.82),
            MarketImpact(["SLB", "HAL", "BKR"],                           "BUY",  "Öl-Services profitieren",              0.75),
            MarketImpact(["DAL", "UAL", "RYA.L"],                         "SELL", "Kerosin-Kosten steigen",               0.30),
        ],
    },
    {
        "name": "CHINA_SANCTIONS",
        "label": "China Sanktionen / Handelskrieg",
        "severity": 2,
        "keywords": [
            "china sanctions", "china tariffs", "us china trade war",
            "chip export ban china", "semiconductor export control",
            "huawei ban", "china decoupling", "chip ban china",
        ],
        "impacts": [
            MarketImpact(["NVDA", "AMD", "QCOM", "INTC"],                 "SELL", "Chip-Export-Beschränkungen",           0.30),
            MarketImpact(["AMAT", "LRCX", "KLAC"],                        "SELL", "Ausrüstungs-Export-Verbote",           0.28),
            MarketImpact(["SMCI", "DELL", "HPE"],                         "BUY",  "US-Alternativ-Rechenzentren",          0.68),
            MarketImpact(["LMT", "RTX"],                                   "BUY",  "Geopolitik-Premium",                   0.70),
        ],
    },
    {
        "name": "EUROPE_ENERGY_CRISIS",
        "label": "Europäische Energiekrise",
        "severity": 2,
        "keywords": [
            "europe gas crisis", "gas shortage europe", "energy crisis europe",
            "european gas prices", "lng europe", "energy rationing europe",
        ],
        "impacts": [
            MarketImpact(["ENPH", "FSLR", "NEE", "BEP"],                  "BUY",  "Erneuerbare als Alternative",          0.78),
            MarketImpact(["RWE.DE", "ENGI.PA"],                            "BUY",  "Versorger profitieren",                0.72),
            MarketImpact(["NESTE.HE", "TTE.PA"],                           "BUY",  "Europäische Energiekonzerne",          0.75),
        ],
    },
    {
        "name": "GLOBAL_FLEET_MOVEMENT",
        "label": "Flottenverlegung / Militäraufmarsch",
        "severity": 2,
        "keywords": [
            "carrier strike group deployed", "naval fleet deployed",
            "warships dispatched", "military buildup", "troops massed",
            "aircraft carrier deployed", "naval exercise near",
            "fleet movement", "military convoy", "troop buildup",
        ],
        "impacts": [
            MarketImpact(["LMT", "RTX", "NOC", "GD", "HII"],             "BUY",  "Militäraufmarsch = Rüstungs-Signal",   0.78),
            MarketImpact(["GLD"],                                           "BUY",  "Unsicherheitsprämie",                  0.68),
            MarketImpact(["XOM", "CVX"],                                   "WATCH","Öl-Region prüfen",                     0.60),
        ],
    },
    {
        "name": "PANDEMIC_BIOLOGICAL",
        "label": "Pandemie / Biologisches Risiko",
        "severity": 3,
        "keywords": [
            "pandemic declared", "who emergency", "novel virus outbreak",
            "disease outbreak spreading", "global health emergency",
            "new pathogen", "quarantine international",
        ],
        "impacts": [
            MarketImpact(["MRNA", "BNTX", "PFE", "JNJ"],                  "BUY",  "Impfstoff-Entwickler",                 0.88),
            MarketImpact(["REGN", "VRTX", "ABBV"],                         "BUY",  "Biotech Behandlungen",                 0.80),
            MarketImpact(["ZM", "MSFT", "GOOGL"],                          "BUY",  "Remote-Work-Profiteure",               0.72),
            MarketImpact(["DAL", "UAL", "MAR", "HLT"],                    "SELL", "Reise & Tourismus",                    0.25),
        ],
    },
    {
        "name": "NUCLEAR_THREAT",
        "label": "Nukleares Risiko",
        "severity": 3,
        "keywords": [
            "nuclear threat", "nuclear weapon", "nuclear strike",
            "tactical nuclear", "dirty bomb threat", "nuclear alert",
            "defcon", "nuclear posture",
        ],
        "impacts": [
            MarketImpact(["GLD", "SLV"],                                   "BUY",  "Maximales Safe-Haven",                 0.92),
            MarketImpact(["LMT", "RTX", "NOC"],                           "BUY",  "Rüstung Extremszenario",               0.85),
        ],
    },
    {
        "name": "STRAIT_BLOCKADE",
        "label": "Meeresstraßen-Sperrung",
        "severity": 3,
        "keywords": [
            "strait of hormuz closed", "hormuz blockade", "bab el-mandeb",
            "malacca strait blocked", "suez canal closed", "panama canal",
            "shipping lane blocked", "naval blockade",
        ],
        "impacts": [
            MarketImpact(["XOM", "CVX", "OXY"],                            "BUY",  "Ölversorgung gefährdet",               0.88),
            MarketImpact(["ZIM", "DSV.CO", "DPW.DE"],                      "BUY",  "Umwegkosten steigen",                  0.75),
            MarketImpact(["DAL", "UAL"],                                    "SELL", "Frachtkosten, Kerosin",                0.30),
        ],
    },
    {
        "name": "COMMODITY_SUPPLY_DISRUPTION",
        "label": "Rohstoff-Lieferausfall",
        "severity": 2,
        "keywords": [
            "mine strike", "mine shutdown", "commodity export ban",
            "rare earth export ban", "lithium shortage", "cobalt disruption",
            "copper mine", "grain export ban", "wheat export ban",
        ],
        "impacts": [
            MarketImpact(["FCX", "VALE", "BHP", "RIO"],                   "BUY",  "Kupfer/Metall-Produzenten",            0.78),
            MarketImpact(["ALB", "SQM", "LAC"],                            "BUY",  "Lithium-Produzenten",                  0.80),
            MarketImpact(["NEM", "GOLD", "AEM"],                           "BUY",  "Gold/Edelmetall-Minen",                0.72),
        ],
    },
    {
        "name": "COUP_REGIME_CHANGE",
        "label": "Staatsstreich / Regimewechsel",
        "severity": 2,
        "keywords": [
            "military coup", "coup attempt", "government overthrown",
            "regime change", "president ousted", "military takeover",
        ],
        "impacts": [
            MarketImpact(["GLD"],                                           "BUY",  "Politische Unsicherheitsprämie",       0.72),
            MarketImpact(["XOM", "CVX"],                                   "WATCH","Öl-Produktion in betroffener Region",  0.60),
        ],
    },
]

_MIN_SEVERITY_FOR_BENCH  = 1   # Ab Severity 1 → BenchList
_MIN_SEVERITY_FOR_NOTIFY = 2   # Ab Severity 2 → Telegram


class GeopoliticalRadar:
    """
    Scannt Weltpolitik-Nachrichten auf Frühsignale und leitet
    Marktauswirkungen automatisch ab.
    """

    def __init__(self, state_path: str = _STATE_FILE):
        self._state_path = state_path
        os.makedirs("data", exist_ok=True)

    def scan(self) -> List[GeoEvent]:
        """Lädt Weltpolitik-Nachrichten und gibt erkannte Events zurück."""
        headlines = self._fetch_headlines()
        if not headlines:
            return []

        seen = self._load_seen()
        events: List[GeoEvent] = []

        for title, source in headlines:
            key = title.lower()[:90]
            if key in seen:
                continue
            seen.add(key)

            event = self._classify(title, source)
            if event:
                events.append(event)

        self._save_seen(seen)

        if events:
            log.info(
                "GeopoliticalRadar: %d neue Events erkannt: %s",
                len(events),
                ", ".join(e.category for e in events),
            )
        return events

    def process_events(
        self,
        events: List[GeoEvent],
        notify_fn=None,
    ) -> List[str]:
        """
        Verarbeitet Events: BUY-Impacts → BenchList, Severity≥2 → Telegram.
        Gibt Liste der hinzugefügten Ticker zurück.
        """
        if not events:
            return []

        from analyzers.bench_list import BenchList
        bench = BenchList()
        added: List[str] = []
        alerts: List[str] = []

        for event in events:
            buy_tickers = []
            sell_tickers = []

            for impact in event.impacts:
                if impact.direction == "BUY":
                    for t in impact.tickers:
                        if t not in added:
                            bench.add(
                                t,
                                score=impact.score,
                                reason=f"Geo:{event.category} – {impact.reason}",
                            )
                            added.append(t)
                            buy_tickers.append(t)
                elif impact.direction == "SELL":
                    sell_tickers.extend(impact.tickers)

            if event.severity >= _MIN_SEVERITY_FOR_NOTIFY and notify_fn:
                severity_emoji = {1: "🌐", 2: "⚠️", 3: "🚨"}.get(event.severity, "📡")
                buy_str  = f"BUY:  {', '.join(buy_tickers[:5])}"  if buy_tickers  else ""
                sell_str = f"SELL: {', '.join(sell_tickers[:5])}" if sell_tickers else ""
                impact_str = "\n".join(filter(None, [buy_str, sell_str]))

                alerts.append(
                    f"{severity_emoji} <b>GEOPOLITIK: {event.category}</b>\n"
                    f"<i>{event.headline[:140]}</i>\n"
                    f"Quelle: {event.source}\n\n"
                    f"{impact_str}"
                )

        if alerts and notify_fn:
            notify_fn(
                "🌍 <b>Geopolitischer Radar – Marktrelevante Ereignisse</b>\n\n"
                + "\n\n".join(alerts[:4])
            )

        return added

    # ── Headlines laden ───────────────────────────────────────────────────────

    def _fetch_headlines(self) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        for feed_name, url in _GEO_RSS_FEEDS:
            try:
                resp = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
                if resp.status_code != 200:
                    continue
                items = self._parse_rss(resp.text, feed_name)
                results.extend(items)
            except Exception as e:
                log.debug("GeoRadar RSS %s fehlgeschlagen: %s", feed_name, e)
        log.debug("GeopoliticalRadar: %d Schlagzeilen geladen", len(results))
        return results

    def _parse_rss(self, xml_text: str, source: str) -> List[Tuple[str, str]]:
        import xml.etree.ElementTree as ET
        results = []
        try:
            root = ET.fromstring(xml_text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for item in root.iter("item"):
                el = item.find("title")
                desc = item.find("description")
                # Titel + erste Zeile der Beschreibung kombinieren für besseren Kontext
                text = ""
                if el is not None and el.text:
                    text = el.text.strip()
                if desc is not None and desc.text:
                    text += " " + desc.text[:200]
                if text:
                    results.append((text.strip(), source))
            for entry in root.findall(".//atom:entry", ns):
                el = entry.find("atom:title", ns)
                if el is not None and el.text:
                    results.append((el.text.strip(), source))
        except Exception:
            pass
        return results

    # ── Klassifizierung ───────────────────────────────────────────────────────

    def _classify(self, headline: str, source: str) -> Optional[GeoEvent]:
        lower = headline.lower()
        best_match = None
        best_hits  = 0

        for cat in _GEO_CATEGORIES:
            hits = sum(1 for kw in cat["keywords"] if kw in lower)
            if hits > best_hits:
                best_hits  = hits
                best_match = cat

        if not best_match or best_hits == 0:
            return None

        return GeoEvent(
            category=best_match["name"],
            severity=best_match["severity"],
            headline=headline[:200],
            source=source,
            impacts=best_match["impacts"],
        )

    # ── State-Persistenz ──────────────────────────────────────────────────────

    def _load_seen(self) -> set:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                data = json.load(f)
            cutoff = (datetime.utcnow() - timedelta(hours=48)).isoformat()
            return {k for k, v in data.items() if v >= cutoff}
        except Exception:
            return set()

    def _save_seen(self, seen: set) -> None:
        dirpath = os.path.dirname(self._state_path) or "."
        os.makedirs(dirpath, exist_ok=True)
        now = datetime.utcnow().isoformat()
        try:
            fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: now for k in seen}, f)
            os.replace(tmp, self._state_path)
        except Exception as e:
            log.debug("GeoRadar: State speichern fehlgeschlagen: %s", e)
