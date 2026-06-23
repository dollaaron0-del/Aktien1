"""
Universums-Bau für die Strategie-Suche — Survivorship-Bias mildern (Roadmap b).

PROBLEM: Bisher backtesten wir nur `config.watchlist` — ausschließlich *heutige*
Überlebende (AAPL/NVDA/MSFT/…). Wer pleiteging oder delistet wurde, fehlt. Damit
sind ALLE Kennzahlen systematisch nach oben verzerrt (Survivorship-Bias): die
Strategie wird nie an Titeln getestet, die gegen 0 liefen.

GEGENMITTEL (ehrlich, aber nicht perfekt): ein kuratierter "Graveyard" bekannter
Pleiten/Delistings/Übernahmen-zum-Tiefstkurs. Das ist eine *Stichprobe* berühmter
Fehlschläge — KEINE punkt-in-Zeit-vollständige Index-Rekonstruktion. Es entfernt
den Bias nicht restlos, reduziert ihn aber materiell, weil der Backtest jetzt auch
Titel sieht, die wertlos wurden.

GRENZEN, die bestehen bleiben:
  • Auswahl-Bias der Kuratierung: wir kennen die Fehlschläge im Nachhinein.
  • Kein Look-Ahead-Problem fürs Backtesting selbst — die Toten werden mit ihrer
    echten (oft kurzen) Historie geladen; lab.evaluate nutzt die reale Spanne.

⚠️ EMPIRISCHER BEFUND (23.6.2026) — DIE FREIEN QUELLEN LIEFERN ES NICHT:
  • yfinance/Yahoo PURGT delistete Symbole: von 34 Graveyard-Tickern lieferten
    nur 2 (SBNY, FRCB) verwertbare Historie. Alle `Q`-Bankrott-Ticker und älteren
    Delistings (LEHMQ, ENRNQ, BBBYQ, …) → leer.
  • stooq.com (frei/keylos, behält theoretisch Delistings) sperrt den CSV-Endpoint
    inzwischen hinter eine JS-Anti-Bot-Challenge → vom Server nicht abrufbar.
  • FAZIT: echte Survivorship-Korrektur braucht eine BEZAHL-Quelle mit
    Point-in-Time/Delisting-Historie (Norgate, Sharadar/Nasdaq-Data-Link,
    EOD Historical Data). Das ist eine Kosten-Entscheidung des Users.
  • Diese Mechanik ist bewusst trotzdem da: sie ist korrekt, opt-in (--include-delisted),
    rührt den Default-Lauf NICHT an, zieht die 2 verfügbaren Toten mit, und `coverage()`/
    `survivorship_note` machen die magere Abdeckung in JEDEM Lauf transparent statt sie
    zu verschweigen. Sobald eine bessere Quelle als Loader injiziert wird, wirkt sie sofort.

Reine Datenschicht, kein Live-Eingriff. Der Loader ist injizierbar — eine künftige
Bezahl-/Alternativquelle ersetzt nur die `loader`-Callable, der Rest bleibt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class Delisted:
    """Ein gestorbener/delisteter Titel mit Kontext fürs Backtest-Universum."""
    ticker: str          # Symbol, unter dem yfinance am ehesten noch Historie hat
    name: str            # Klarname
    fate: str            # was passierte (Bankrott / Übernahme-Tiefstkurs / Dilution→0)
    year: int            # ungefähres Jahr des Endes/Delistings


# Kuratierter Graveyard. Bewusst breit über Ären/Sektoren gestreut, damit der
# Backtest verschiedene Regime von Fehlschlägen sieht (Dotcom, GFC 2008, jüngste
# Pleitewelle 2022–2025, SPAC/Meme-Blowups). `Q`-Suffix = OTC-Pink-Sheets nach
# Chapter 11; manche liefern bei yfinance noch Daten, andere nicht (→ übersprungen).
GRAVEYARD: List[Delisted] = [
    # --- Globale Finanzkrise 2008 ---
    Delisted("LEHMQ", "Lehman Brothers", "Bankrott", 2008),
    Delisted("WAMUQ", "Washington Mutual", "Bankrott (größte Bankpleite US)", 2008),
    Delisted("BSC", "Bear Stearns", "Notübernahme JPM ~$10", 2008),
    Delisted("ABKFQ", "Ambac Financial", "Bankrott (Monoliner)", 2010),
    Delisted("CCTYQ", "Circuit City", "Liquidation", 2009),
    # --- Bilanz-/Buchhaltungsskandale & Dotcom ---
    Delisted("ENRNQ", "Enron", "Bilanzskandal-Bankrott", 2001),
    Delisted("WCOEQ", "WorldCom", "Bilanzskandal-Bankrott", 2002),
    Delisted("NRTLQ", "Nortel Networks", "Bankrott (Telecom-Bust)", 2009),
    # --- Strukturwandel / Disruption ---
    Delisted("BLOAQ", "Blockbuster", "Bankrott (Streaming-Disruption)", 2010),
    Delisted("SHLDQ", "Sears Holdings", "Bankrott (Retail-Verfall)", 2018),
    Delisted("JCPNQ", "JC Penney", "Bankrott", 2020),
    Delisted("TOYS", "Toys R Us", "Liquidation", 2018),
    Delisted("EKDKQ", "Eastman Kodak", "Bankrott (alt, Reorg 2013)", 2012),
    Delisted("SUNEQ", "SunEdison", "Bankrott (Solar-Überdehnung)", 2016),
    Delisted("GTATQ", "GT Advanced Tech", "Überraschungs-Bankrott", 2014),
    Delisted("DRYS", "DryShips", "Dilution gegen 0 (Reverse-Splits)", 2019),
    # --- Pleitewelle 2020 (COVID-Schock) ---
    Delisted("HTZGQ", "Hertz", "Bankrott 2020 (kam später zurück)", 2020),
    Delisted("WLLBQ", "Whiting Petroleum", "Öl-Crash-Bankrott", 2020),
    Delisted("CHKAQ", "Chesapeake Energy", "Schiefergas-Bankrott", 2020),
    # --- Jüngste Welle 2022–2025 (Zins-Schock / Geschäftsmodelle) ---
    Delisted("BBBYQ", "Bed Bath & Beyond", "Bankrott (Meme→0)", 2023),
    Delisted("SIVBQ", "SVB Financial", "Bank-Run-Kollaps", 2023),
    Delisted("SBNY", "Signature Bank", "Schließung durch Regulierer", 2023),
    Delisted("FRCB", "First Republic Bank", "Beschlagnahmt, an JPM verkauft", 2023),
    Delisted("WEWKQ", "WeWork", "Bankrott (SPAC-Hype→0)", 2023),
    Delisted("RADCQ", "Rite Aid", "Bankrott", 2023),
    Delisted("PRTYQ", "Party City", "Bankrott", 2023),
    Delisted("REVRQ", "Revlon", "Bankrott", 2022),
    Delisted("VORBQ", "Virgin Orbit", "Bankrott (Raketenstart)", 2023),
    Delisted("RIDE", "Lordstown Motors", "Bankrott (EV-SPAC)", 2023),
    Delisted("NKLAQ", "Nikola", "Bankrott (EV-SPAC, Betrugsvorwürfe)", 2025),
    Delisted("GOEV", "Canoo", "Bankrott (EV-SPAC)", 2025),
    # --- SPAC/Meme-Blowups (überleben evtl. als Penny, aber ~99% Wertverlust) ---
    Delisted("WISH", "ContextLogic (Wish)", "−99% nach IPO", 2021),
    Delisted("FFIE", "Faraday Future", "Near-Zero, Reverse-Splits", 2024),
    Delisted("MULN", "Mullen Automotive", "Extreme Dilution gegen 0", 2024),
]


def graveyard_tickers() -> List[str]:
    """Nur die Symbole des kuratierten Graveyards."""
    return [d.ticker for d in GRAVEYARD]


def extended_universe(base: List[str]) -> List[str]:
    """`base` (Überlebende) + Graveyard, dedupliziert, Reihenfolge stabil.

    Survivors zuerst, dann die Toten — so bleibt die Reihenfolge bestehender
    Läufe/Karten oben stabil und der Graveyard wird angehängt.
    """
    seen = set()
    out: List[str] = []
    for t in list(base) + graveyard_tickers():
        u = t.upper()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def coverage(
    tickers: List[str],
    loader: Callable[[str, int], object],
    years: int = 20,
) -> Dict[str, bool]:
    """Pro Ticker: lieferte der Loader überhaupt verwertbare Historie?

    Macht die echte Daten-Abdeckung transparent (delistete Ticker haben bei
    yfinance oft keine/lückenhafte Daten). loader injizierbar (netzfreie Tests).
    """
    result: Dict[str, bool] = {}
    for t in tickers:
        try:
            df = loader(t, years)
            result[t] = df is not None and len(df) >= 60
        except Exception:
            result[t] = False
    return result


def build(
    base: List[str],
    include_delisted: bool,
    loader: Optional[Callable[[str, int], object]] = None,
    years: int = 20,
) -> "UniverseInfo":
    """Baut das Universum und (falls Loader gegeben) misst die Graveyard-Abdeckung.

    Ohne Loader wird nicht geladen — nur die Ticker-Liste zusammengesetzt.
    """
    if not include_delisted:
        return UniverseInfo(tickers=[t.upper() for t in base],
                            survivors=len(base), graveyard_requested=0,
                            graveyard_with_data=0)

    full = extended_universe(base)
    gy = graveyard_tickers()
    with_data = 0
    if loader is not None:
        cov = coverage(gy, loader, years=years)
        with_data = sum(1 for ok in cov.values() if ok)
        # Tote ohne Daten gleich rauswerfen — sie würden ohnehin übersprungen,
        # aber so ist die zurückgegebene Liste ehrlich.
        full = [t for t in full if t in {b.upper() for b in base} or cov.get(t, False)]
    return UniverseInfo(tickers=full, survivors=len(base),
                        graveyard_requested=len(gy), graveyard_with_data=with_data)


@dataclass
class UniverseInfo:
    """Ergebnis von build(): die Ticker plus Transparenz über die Abdeckung."""
    tickers: List[str]
    survivors: int
    graveyard_requested: int
    graveyard_with_data: int

    @property
    def survivorship_note(self) -> str:
        if self.graveyard_requested == 0:
            return ("NUR Überlebende — Survivorship-Bias NICHT gemildert "
                    "(Kennzahlen nach oben verzerrt).")
        return (f"Survivorship gemildert: {self.survivors} Überlebende + "
                f"{self.graveyard_with_data}/{self.graveyard_requested} Graveyard-Titel "
                f"mit Daten. Rest delistet ohne yfinance-Historie (übersprungen).")
