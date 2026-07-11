"""
bot/runner.py – Analysis cycle, news collection, and related helpers.
"""

import math
import os
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timezone
from typing import List, Dict, Optional

from rich.console import Console

from config import config
from logger import get_logger
from collectors import (
    RedditCollector, YahooCollector, NewsAPICollector,
    InsiderCollector, USASpendingCollector,
    SECEdgarCollector, StockTwitsCollector, WireCollector,
    OptionsFlowCollector, EuropeanNewsCollector, TwitterCollector,
    SEC8KCollector, ShortInterestCollector, InstitutionalCollector,
    AnalystCollector, EarningsTranscriptCollector, PatentCollector,
    JobListingsCollector, CEOInterviewCollector, EURegulationCollector,
    ChineseMediaCollector, WebTrafficCollector, GermanMediaCollector,
    InternationalMediaCollector, QuiverCollector,
    EconomicCalendarCollector, AAIISentimentCollector, AdhocCollector,
    FDACalendarCollector, EstimateRevisionsCollector, ShortVolumeCollector,
    GoogleTrendsCollector, WikipediaViewsCollector, OpenFDACollector,
    NHTSARecallsCollector, SECActivistCollector,
)
from collectors.news_archive import NewsArchive
from collectors.crypto_news_collector import CryptoNewsCollector
from analyzers import ClaudeAnalyzer, AnalysisResult
from analyzers.chart_patterns import ChartPatternAnalyzer
from analyzers.onchain_signals import OnChainSignalAnalyzer
from analyzers.eu_market_context import EUMarketContext
from collectors.onchain_collector import OnChainCollector
from analyzers.reflection_engine import ReflectionEngine
from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from analyzers.news_velocity import NewsVelocityAnalyzer
from analyzers.multi_timeframe_sentiment import MultiTimeframeSentiment
from analyzers.reentry_tracker import ReEntryTracker
from analyzers.analysis_cache import AnalysisCache
from analyzers.analysis_log import AnalysisLog
import analyzers.user_request_queue as _urq
from analyzers.rl_agent import RLAgent
from analyzers.earnings_predictor import EarningsPredictor
from analyzers.multi_agent_analyzer import MultiAgentAnalyzer
from broker.paper_broker import PaperBroker
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from strategy import SwingStrategy
from notifier.telegram_notifier import TelegramNotifier
from collectors.tradingview_webhook import get_pending_sells, get_pending_macro_events

console = Console()
log = get_logger(__name__)

# ── Experience-Store (Selbstlern-Datensatz, lazy Singleton, fail-open) ──
_experience_store = None
_experience_store_tried = False


def _get_experience_store():
    """Lädt den ExperienceStore einmalig; None wenn nicht verfügbar (fail-open)."""
    global _experience_store, _experience_store_tried
    if _experience_store_tried:
        return _experience_store
    _experience_store_tried = True
    try:
        from analyzers.experience_store import ExperienceStore
        _experience_store = ExperienceStore()
    except Exception as _e:  # pragma: no cover - defensiv
        log.debug("ExperienceStore nicht verfügbar: %s", _e)
        _experience_store = None
    return _experience_store


def _valid_price(p) -> bool:
    """True nur für eine echte, positive Zahl. Yahoo/yfinance liefern bei
    neuen/illiquiden Titeln (z.B. frische IPOs wie SPCX) NaN als current_price –
    und NaN ist truthy, sodass `if price` / `not price` es durchrutschen lassen.
    Ein solcher NaN-BUY läuft dann bis zur Kauf-Empfehlung (inkl. Telegram-
    Nachricht), scheitert aber zwangsläufig an der Kurs-Schranke vor der Order.
    Zentral abgesichert (vgl. yfinance-NaN-Score-Falle)."""
    try:
        return p is not None and math.isfinite(float(p)) and float(p) > 0
    except (TypeError, ValueError):
        return False


def _ensure_current_price(ticker: str, price_data: Optional[dict], broker) -> dict:
    """Sorgt für einen gültigen current_price in price_data. Liefert die primäre
    Quelle (Yahoo) keinen (NaN/None/0 – z.B. neue/illiquide IPOs wie SPCX), wird
    der Broker als Fallback befragt: IBKRBroker.get_price liefert dank Delayed-
    Marktdaten (IBKR_MARKET_DATA_TYPE=3) einen Kurs und fällt seinerseits auf
    yfinance zurück. Erst danach greift die NaN-Schranke.

    NUR aus dem seriellen Pfad aufrufen – ib_insync ist nicht thread-safe und der
    Broker hat keinen Lock; im parallelen Prefetch-Pool wäre das unsicher."""
    if price_data is None:
        price_data = {"ticker": ticker}
    if _valid_price(price_data.get("current_price")):
        return price_data
    getter = getattr(broker, "get_price", None)
    if not callable(getter):
        return price_data
    try:
        bp = getter(ticker)
    except Exception as e:
        log.debug("[%s] Broker-Preis-Fallback fehlgeschlagen: %s", ticker, e)
        return price_data
    if _valid_price(bp):
        price_data["current_price"] = round(float(bp), 4)
        log.info("[%s] Kurs via Broker-Fallback (Primärquelle leer): $%.4f", ticker, float(bp))
    return price_data

import threading as _threading
_cycle_lock = _threading.Lock()
_last_cycle_start: Optional[datetime] = None
_MIN_CYCLE_GAP_MINUTES = int(os.getenv("MIN_CYCLE_GAP_MINUTES", "20"))

_dynamic_watchlist  = DynamicWatchlist(max_picks=config.scan_max_picks or 12) if config.auto_scan_watchlist else None
_rl_agent           = RLAgent()
_earnings_predictor = EarningsPredictor()
_signal_expander    = SignalDrivenExpander()
_analysis_cache     = AnalysisCache()
_analysis_log       = AnalysisLog()

# Semantic dedup – einmal laden, alle Ticker eines Zyklus nutzen dieselbe Instanz
try:
    from analyzers.semantic_dedup import SemanticDeduplicator as _SemDedup
    _semantic_dedup = _SemDedup() if config.ollama_enabled else None
except Exception:
    _semantic_dedup = None

_collect_log = get_logger("collectors")

# ── Tages-Aktionen-Speicher ────────────────────────────────────────────────
# Die Tages-Zusammenfassung wird NICHT mehr pro Zyklus gesendet (das war die
# Telegram-Flut: 1× je Markt-Slot/Trigger/Watchdog). Stattdessen sammeln alle
# Zyklen ihre Käufe/Verkäufe hier; die Abend-Summary (_daily_summary_job im
# Scheduler) liest sie einmal täglich gebündelt aus.
_DAILY_ACTIONS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "daily_actions.json")


def record_daily_actions(actions: List[str]) -> None:
    """Hängt die Aktionen eines Zyklus an den heutigen Tagesspeicher an."""
    if not actions:
        return
    import json as _json
    today = datetime.now().date().isoformat()
    try:
        data: Dict = {}
        if os.path.exists(_DAILY_ACTIONS_PATH):
            with open(_DAILY_ACTIONS_PATH, "r", encoding="utf-8") as f:
                data = _json.load(f) or {}
        if data.get("date") != today:
            data = {"date": today, "actions": []}
        data["actions"].extend(actions)
        with open(_DAILY_ACTIONS_PATH, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False)
    except Exception as _e:
        log.debug("record_daily_actions fehlgeschlagen: %s", _e)


def pop_daily_actions() -> List[str]:
    """Liest die heutigen Aktionen und leert den Speicher (für die Abend-Summary)."""
    import json as _json
    today = datetime.now().date().isoformat()
    try:
        if not os.path.exists(_DAILY_ACTIONS_PATH):
            return []
        with open(_DAILY_ACTIONS_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f) or {}
        actions = data.get("actions", []) if data.get("date") == today else []
        os.remove(_DAILY_ACTIONS_PATH)
        return actions
    except Exception as _e:
        log.debug("pop_daily_actions fehlgeschlagen: %s", _e)
        return []


def _is_crypto(ticker: str) -> bool:
    """True wenn der Ticker ein Krypto-Asset ist (z.B. 'BTC', 'ETH/USD')."""
    from analyzers.crypto_universe import CRYPTO_UNIVERSE
    base = ticker.split("/")[0].upper()
    return base in CRYPTO_UNIVERSE or ticker.endswith("/USD")


_EU_SUFFIXES = {
    ".DE", ".F", ".MU", ".PA", ".AS", ".MI", ".MC",
    ".BR", ".BE", ".VI", ".L", ".SW", ".CO", ".ST", ".HE", ".OL",
}


# Bekannte falsche Ticker → korrekter Yahoo-Finance-Ticker
_TICKER_CORRECTIONS: Dict[str, str] = {
    "LVMH.PA":  "MC.PA",    # LVMH Moët Hennessy
    "LVMH":     "MC.PA",
    "VOLKSWAGEN.DE": "VOW3.DE",
    "VW.DE":    "VOW3.DE",
    "DAIMLER.DE": "MBG.DE",  # Mercedes-Benz
}


def _normalize_ticker(ticker: str) -> str:
    """Korrigiert bekannte falsche Ticker-Symbole."""
    return _TICKER_CORRECTIONS.get(ticker.upper(), ticker)


def _is_eu_stock(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper.endswith(s.upper()) for s in _EU_SUFFIXES)


def _base_symbol(ticker: str) -> str:
    """Strip exchange suffix so ASML and ASML.AS map to the same base."""
    upper = ticker.upper()
    for s in sorted(_EU_SUFFIXES, key=len, reverse=True):
        if upper.endswith(s.upper()):
            return upper[: -len(s)]
    return upper


def _get_watchlist(portfolio: Portfolio) -> tuple:
    """Returns watchlist: dynamic/static US + optional EU + optional Crypto."""
    if _dynamic_watchlist:
        active = list(portfolio.all_positions().keys())
        base = _dynamic_watchlist.get_watchlist(active_tickers=active)
    else:
        base = list(config.watchlist)

    # EU-Aktien anhängen wenn aktiviert
    if config.eu_stocks_enabled:
        eu_tickers = list(config.eu_watchlist)
        if not eu_tickers:
            # Keine manuelle Watchlist → EU-Scanner automatisch ausführen (Top 6)
            try:
                from analyzers.eu_stock_scanner import EUStockScanner
                scan = EUStockScanner(max_results=6).scan()
                eu_tickers = [c.ticker for c in scan.candidates]
                if eu_tickers:
                    log.info(
                        "EU-Auto-Scan: %d Kandidaten gefunden: %s",
                        len(eu_tickers), ", ".join(eu_tickers),
                    )
            except Exception as e:
                log.warning("EU-Auto-Scan fehlgeschlagen: %s", e)
        _base_symbols_in_list = {_base_symbol(x) for x in base}
        for t in eu_tickers:
            if t not in base and _base_symbol(t) not in _base_symbols_in_list:
                base.append(t)
                _base_symbols_in_list.add(_base_symbol(t))
            elif _base_symbol(t) in _base_symbols_in_list and t not in base:
                log.debug("EU-Ticker %s übersprungen (Basisymbol bereits als US-Ticker vorhanden)", t)

    # Krypto anhängen wenn aktiviert
    if config.crypto_enabled and config.crypto_watchlist:
        for t in config.crypto_watchlist:
            if t not in base:
                base.append(t)

    # Opportunity-Scan: bei ≥50% freien Slots nach weiteren Kandidaten suchen
    _opportunity_scan(portfolio, base)

    # Verwandte Aktien: für jeden BUY/HOLD-Ticker die bekannten Verbindungen einbeziehen
    _bench_picks = int(os.getenv("ANALYSIS_BENCH_PICKS", "3"))
    try:
        from analyzers.stock_relations import StockRelations
        from analyzers.analysis_cache import AnalysisCache
        _relations = StockRelations()
        _cache = AnalysisCache()
        _active_buys = [t for t in base if _cache.get(t) and _cache.get(t).get("recommendation") in ("BUY", "HOLD")]
        _related_added = []
        for _bt in _active_buys:
            for _rt in _relations.get_related(_bt):
                if _rt not in base and len(_related_added) < 4:
                    base.append(_rt)
                    _related_added.append(_rt)
        if _related_added:
            console.print(
                f"  [cyan]🔗 Verwandte Ticker aus BUY/HOLD-Netz: "
                f"{', '.join(_related_added)}[/cyan]"
            )
    except Exception as e:
        log.debug("Verwandte-Ticker-Lookup fehlgeschlagen: %s", e)

    # BenchList + Sektor-Sampler: im Frugal-Modus deaktiviert (nur Kern-Watchlist)
    _bench_geo_contexts: dict = {}
    if not config.frugal_mode:
        try:
            from analyzers.bench_list import BenchList
            _bench = BenchList()
            _bench.cleanup()
            _bench_candidates = _bench.pop_candidates(_bench_picks, exclude=base)
            for _bt in _bench_candidates:
                base.append(_bt)
                ctx = _bench.get_context(_bt)
                if ctx and ctx.get("geo_context"):
                    _bench_geo_contexts[_bt] = ctx["geo_context"]
            if _bench_candidates:
                geo_flagged = [t for t in _bench_candidates if t in _bench_geo_contexts]
                flag_str = f" (🌍 Geo: {', '.join(geo_flagged)})" if geo_flagged else ""
                console.print(
                    f"  [magenta]📋 BenchList → Analyse: {', '.join(_bench_candidates)}{flag_str}[/magenta]"
                )
        except Exception as e:
            log.debug("BenchList-Analyse-Pull fehlgeschlagen: %s", e)

        try:
            from analyzers.sector_sampler import SectorSampler
            sector_name, sample = SectorSampler().get_sample(exclude=base)
            added_sample = [t for t in sample if t not in base]
            for t in added_sample:
                base.append(t)
            if added_sample:
                console.print(
                    f"  [blue]🔬 Sektor-Stichprobe ({sector_name}): "
                    f"{', '.join(added_sample)}[/blue]"
                )
        except Exception as e:
            log.debug("Sektor-Sampler fehlgeschlagen: %s", e)
    else:
        log.debug("Frugal-Modus: BenchList + Sektor-Sampler übersprungen")

    log.info("Analyse-Watchlist: %d Aktien → %s", len(base), ", ".join(base[:15]))
    return base, _bench_geo_contexts


def _opportunity_scan(portfolio: "Portfolio", base: List[str]) -> None:
    """
    Erweitert die Watchlist wenn mehr als die Hälfte der Positions-Slots frei ist.
    Verhindert verschwendetes Kapital wenn alle Watchlist-Aktien kein Signal liefern.
    """
    try:
        from portfolio.focus_mode import get_scaling
        prices = {t: pos.entry_price for t, pos in portfolio.all_positions().items()}
        portfolio_value = portfolio.cash + sum(
            pos.shares * pos.entry_price for pos in portfolio.all_positions().values()
        )
        max_pos, _ = get_scaling(portfolio_value)
        open_count = len(portfolio.all_positions())
        free_slots = max_pos - open_count

        if free_slots < max(2, max_pos // 2):
            return  # Portfolio ist gut gefüllt – kein Scan nötig

        extra_needed = min(free_slots, 8)
        active = list(portfolio.all_positions().keys())

        log.info(
            "Opportunity-Scan: %d/%d Slots frei – suche bis zu %d neue Kandidaten",
            free_slots, max_pos, extra_needed,
        )

        added = []

        # 1. Zuerst aus der BenchList schöpfen (vom Bot bereits entdeckte Kandidaten)
        try:
            from analyzers.bench_list import BenchList
            bench_picks = BenchList().pop_candidates(extra_needed, exclude=base)
            for t in bench_picks:
                base.append(t)
                added.append(t)
            if bench_picks:
                log.info("BenchList: %d Kandidaten geholt: %s", len(bench_picks), ", ".join(bench_picks))
        except Exception as e:
            log.debug("BenchList-Abruf fehlgeschlagen: %s", e)

        # 2. US-Momentum Scan wenn noch Slots offen
        remaining = extra_needed - len(added)
        if remaining > 0:
            try:
                from analyzers.dynamic_watchlist import DynamicWatchlist as _DWL
                scan_tickers = _DWL(max_picks=remaining).get_watchlist(active_tickers=active)
                for t in scan_tickers:
                    if t not in base:
                        base.append(t)
                        added.append(t)
            except Exception as e:
                log.debug("Opportunity US-Scan fehlgeschlagen: %s", e)

        # 3. EU-Scan wenn noch Slots frei
        remaining = extra_needed - len(added)
        if remaining > 0:
            try:
                from analyzers.eu_stock_scanner import EUStockScanner
                eu_scan = EUStockScanner(max_results=min(remaining, 4)).scan()
                for c in eu_scan.candidates:
                    if c.ticker not in base:
                        base.append(c.ticker)
                        added.append(c.ticker)
            except Exception as e:
                log.debug("Opportunity EU-Scan fehlgeschlagen: %s", e)

        if added:
            console.print(
                f"  [magenta]🔍 Opportunity-Scan: {len(added)} neue Kandidaten → "
                f"{', '.join(added[:6])}{'...' if len(added) > 6 else ''}[/magenta]"
            )
    except Exception as e:
        log.debug("Opportunity-Scan Fehler: %s", e)


def _make_phase_ctrl() -> PhaseController:
    return PhaseController(
        initial_capital=config.initial_capital,
        growth_target_multiple=config.growth_target_multiple,
        monthly_target_eur=config.monthly_distribution_eur,
        buffer_months=config.distribution_buffer_months,
    )


def _make_focus_ctrl():
    from portfolio.focus_mode import FocusController
    return FocusController(
        mode=config.focus_mode,
        target_amount=config.target_goal_amount or None,
        target_date=config.target_goal_date or None,
        initial_capital=config.initial_capital,
    )


def _safe_collect(collector_name: str, fn, *args, **kwargs) -> List[Dict]:
    """Ruft einen Collector auf und loggt Fehler – gibt bei Ausnahme [] zurück."""
    try:
        return fn(*args, **kwargs) or []
    except Exception as e:
        _collect_log.warning("Collector %s fehlgeschlagen: %s", collector_name, e)
        try:
            from analyzers.source_monitor import get_monitor
            get_monitor().note_error(collector_name)
        except Exception:
            pass
        return []


def _make_collectors() -> Dict:
    """Build all collector instances once per analysis cycle. Failed inits become None."""
    _log = get_logger(__name__)

    def _safe(name, fn):
        try:
            return fn()
        except Exception as e:
            _log.warning("Collector '%s' konnte nicht initialisiert werden: %s", name, e)
            return None

    _twitter = _safe("twitter", TwitterCollector)
    out = {
        "yahoo":             _safe("yahoo",           YahooCollector),
        "reddit":            _safe("reddit",          RedditCollector),
        "newsapi":           _safe("newsapi",         NewsAPICollector),
        "insider":           _safe("insider",         lambda: InsiderCollector(lookback_days=90)),
        "usaspending":       _safe("usaspending",     lambda: USASpendingCollector(lookback_days=180, min_award_usd=1_000_000)),
        "sec_edgar":         _safe("sec_edgar",       lambda: SECEdgarCollector(lookback_days=30)),
        "stocktwits":        _safe("stocktwits",      lambda: StockTwitsCollector(lookback_hours=48)),
        "wire":              _safe("wire",            lambda: WireCollector(lookback_days=7)),
        "options_flow":      _safe("options_flow",    OptionsFlowCollector),
        "european_news":     _safe("european_news",   lambda: EuropeanNewsCollector(lookback_hours=72)),
        "twitter":           _twitter if (_twitter and _twitter.available) else None,
        "sec_8k":            _safe("sec_8k",          SEC8KCollector),
        "short_interest":    _safe("short_interest",  ShortInterestCollector),
        "institutional_13f": _safe("institutional",   InstitutionalCollector),
        "analyst_ratings":   _safe("analyst",         AnalystCollector),
        "earn_transcripts":  _safe("transcripts",     EarningsTranscriptCollector),
        "patents":           _safe("patents",         PatentCollector),
        "job_listings":      _safe("jobs",            JobListingsCollector),
        "ceo_interviews":    _safe("ceo",             CEOInterviewCollector),
        "eu_regulation":     _safe("eu_reg",          EURegulationCollector),
        "chinese_media":     _safe("chinese",         ChineseMediaCollector),
        "web_traffic":       _safe("web_traffic",     WebTrafficCollector),
        "crypto_news":       _safe("crypto_news",     CryptoNewsCollector),
        "german_media":      _safe("german",          lambda: GermanMediaCollector(lookback_hours=48)),
        "intl_media":        _safe("intl",            lambda: InternationalMediaCollector(lookback_hours=48)),
        "quiver":            _safe("quiver",          lambda: QuiverCollector(lookback_days=90)),
        "econ_calendar":     _safe("econ_cal",        lambda: EconomicCalendarCollector(lookahead_days=14)),
        "aaii_sentiment":    _safe("aaii",            AAIISentimentCollector),
        "adhoc_de":          _safe("adhoc",           AdhocCollector),
        "fda_calendar":      _safe("fda_calendar",    lambda: FDACalendarCollector(lookahead_days=120, lookback_days=21)),
        "estimate_revisions":_safe("est_revisions",   EstimateRevisionsCollector),
        "short_volume":      _safe("short_volume",    ShortVolumeCollector),
        "google_trends":     _safe("google_trends",   GoogleTrendsCollector),
        "wikipedia_views":   _safe("wikipedia_views", WikipediaViewsCollector),
        "openfda":           _safe("openfda",         lambda: OpenFDACollector(lookback_days=30)),
        "nhtsa_recalls":     _safe("nhtsa_recalls",   lambda: NHTSARecallsCollector(lookback_days=30)),
        "sec_activist":      _safe("sec_activist",    lambda: SECActivistCollector(lookback_days=21)),
    }
    # Abgeschaltete Quellen (config.collectors_disabled, N3-Befund): auf None
    # setzen statt entfernen — sie erscheinen weiter mit 0 im sources_breakdown
    # und bleiben so im Source-Health-Report als "deaktiviert" sichtbar.
    _disabled = {c.strip().lower() for c in getattr(config, "collectors_disabled", [])}
    for _name in out:
        if _name.lower() in _disabled and out[_name] is not None:
            _log.info("Collector '%s' per Konfiguration deaktiviert", _name)
            out[_name] = None
    return out


def collect_news(ticker: str, archive: NewsArchive, collectors: Dict) -> tuple:
    """
    Collects fresh news from all sources, stores in archive,
    and returns (deduplicated_items, sources_breakdown).
    collectors: pre-built dict from _make_collectors() – reused across tickers.
    """
    all_items: List[Dict] = []
    sources_breakdown: Dict[str, int] = {}

    is_crypto = _is_crypto(ticker)
    # Collectors that make sense for crypto assets; stock-specific ones are skipped.
    _CRYPTO_ALLOWED = {
        "yahoo", "reddit", "newsapi", "wire", "stocktwits",
        "twitter", "crypto_news", "econ_calendar", "aaii_sentiment",
    }

    active_collectors = {
        name: col for name, col in collectors.items()
        if col is not None and (not is_crypto or name in _CRYPTO_ALLOWED)
    }
    for name in collectors:
        if name not in active_collectors:
            sources_breakdown[name] = 0

    _max_workers = min(8, len(active_collectors))
    with ThreadPoolExecutor(max_workers=_max_workers) as _pool:
        futures = {
            _pool.submit(_safe_collect, name, col.collect, ticker): name
            for name, col in active_collectors.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                items = future.result()
            except Exception:
                items = []
            sources_breakdown[name] = len(items)
            all_items.extend(items)

    # Live-Quellen-Health: Artikel-Counts je Quelle für die Zyklus-Auswertung
    # melden (nur tatsächlich aktive Collector – nicht konfigurierte zählen nicht).
    try:
        from analyzers.source_monitor import get_monitor
        _mon = get_monitor()
        for _name in active_collectors:
            _mon.note_result(_name, sources_breakdown.get(_name, 0))
    except Exception:
        pass

    archive.store(ticker, all_items)

    try:
        NewsVelocityAnalyzer().record_articles(ticker, all_items)
    except Exception:
        pass

    if _semantic_dedup is not None:
        unique = _semantic_dedup.deduplicate(all_items)
    else:
        seen: set = set()
        unique = []
        for item in all_items:
            key = (item.get("title") or "").lower()[:80]
            if key and key not in seen:
                seen.add(key)
                unique.append(item)

    return unique, sources_breakdown


def _print_analysis(a: AnalysisResult):
    color = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(a.direction, "white")
    conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(a.confidence, "white")
    rec_color = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow", "SKIP": "dim"}.get(
        a.recommendation, "white"
    )

    console.print(
        f"  Sentiment: [{color}]{a.direction}[/{color}] "
        f"(Score: {a.sentiment_score:.2f}) | "
        f"Konfidenz: [{conf_color}]{a.confidence}[/{conf_color}] | "
        f"Empfehlung: [{rec_color}]{a.recommendation}[/{rec_color}]"
    )
    if a.bull_case or a.bear_case:
        winner_color = {"BULL": "green", "BEAR": "red", "DRAW": "yellow"}.get(a.debate_winner, "white")
        winner_icon  = {"BULL": "🟢", "BEAR": "🔴", "DRAW": "⚖️"}.get(a.debate_winner, "")
        console.print(f"  {winner_icon} Debatte: [{winner_color}]{a.debate_winner}[/{winner_color}]")
        if a.bull_case:
            console.print(f"  [green]▲ Bull:[/green] [italic]{a.bull_case}[/italic]")
        if a.bear_case:
            console.print(f"  [red]▼ Bear:[/red] [italic]{a.bear_case}[/italic]")
    if a.entry_rationale:
        console.print(f"  Begründung: [italic]{a.entry_rationale}[/italic]")
    if a.target_price:
        console.print(f"  [bold]Zielkurs: ${a.target_price:.2f}[/bold] – {a.target_price_rationale}")
    if a.thesis_valid is False:
        reason = a.thesis_break_reason or "Ursprüngliche Kaufkatalysatoren nicht mehr gültig"
        console.print(f"  [bold red]⚠ THESE GEBROCHEN: {reason}[/bold red]")
    elif a.thesis_valid is True:
        console.print(f"  [green]✓ Kaufthese weiterhin gültig[/green]")
    if a.key_catalysts:
        console.print(f"  Katalysatoren: {', '.join(a.key_catalysts[:3])}")
    if a.risk_factors:
        console.print(f"  Risiken: {', '.join(a.risk_factors[:3])}")


def _print_portfolio_summary(portfolio: Portfolio, broker, phase_ctrl: PhaseController):
    from rich.table import Table
    from rich import box
    from rich.panel import Panel

    positions = portfolio.all_positions()
    prices = broker.get_prices(list(positions.keys())) if positions else {}
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)

    table = Table(title="Portfolio-Übersicht", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Stück", justify="right")
    table.add_column("Einstieg", justify="right")
    table.add_column("Aktuell", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("Tage", justify="right")

    for ticker, pos in positions.items():
        price = prices.get(ticker, pos.entry_price)
        pnl = (price - pos.entry_price) * pos.shares
        days = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
        pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.2f}[/red]"
        table.add_row(
            ticker, f"{pos.shares:.2f}", f"${pos.entry_price:.2f}",
            f"${price:.2f}", pnl_str, f"${pos.stop_loss:.2f}", f"${pos.take_profit:.2f}", str(days),
        )

    console.print()
    console.print(table)

    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"
    progress_bar = _progress_bar(phase_info["progress_pct"])
    summary_lines = [
        f"Cash: [bold]${portfolio.cash:,.2f}[/bold]  |  Gesamtwert: [bold]${total:,.2f}[/bold]",
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}]  |  "
        f"Ziel: ${phase_info['growth_target']:,.0f}  |  "
        f"Fortschritt: {progress_bar} {phase_info['progress_pct']:.1f}%",
    ]
    if phase_info["phase"] == "DISTRIBUTION":
        summary_lines.append(
            f"[bold magenta]Monatliche Ausschüttung: ${phase_info.get('monthly_distribution', 0):,.2f} "
            f"(Ziel: ${phase_info['monthly_target']:,.2f})[/bold magenta]"
        )
    else:
        summary_lines.append(f"Noch ${phase_info['remaining_to_goal']:,.2f} bis zur Ausschüttungsphase")

    console.print(Panel("\n".join(summary_lines), title="Kapital & Phase", border_style=phase_color))

    # ── Portfolio Risk Panel ───────────────────────────────────────────────
    if positions:
        try:
            from portfolio.risk_monitor import PortfolioRiskMonitor
            _pos_values = {t: p.shares * (prices.get(t) or p.entry_price)
                           for t, p in positions.items()}
            risk = PortfolioRiskMonitor().compute(_pos_values, total)
            if risk:
                _risk_colors = {"LOW": "green", "MEDIUM": "yellow",
                                "HIGH": "red", "CRITICAL": "bold red"}
                rc = _risk_colors.get(risk.risk_label, "white")
                risk_lines = [
                    f"Risiko-Score: [{rc}]{risk.risk_score:.0f}/100 – {risk.risk_label}[/{rc}]"
                    f"  |  VaR(1T,95%): [yellow]${risk.var_1d_dollar:,.0f}"
                    f" ({risk.var_1d_pct*100:.1f}%)[/yellow]",
                    f"Beta: [cyan]{risk.portfolio_beta:.2f}[/cyan]"
                    f"  |  Konzentration (HHI): [cyan]{risk.concentration:.2f}[/cyan]"
                    f"  |  Ø Korrelation: [cyan]{risk.avg_correlation:.2f}[/cyan]",
                ]
                for pos_r in sorted(risk.positions, key=lambda x: -x.weight):
                    risk_lines.append(
                        f"  [dim]{pos_r.ticker:6}[/dim]"
                        f" Gewicht={pos_r.weight*100:.0f}%"
                        f"  Vola={pos_r.vol_daily*100:.1f}%/T"
                        f"  Beta={pos_r.beta:.2f}"
                        f"  VaR=${pos_r.var_1d:,.0f}"
                    )
                for w in risk.warnings:
                    risk_lines.append(f"  [bold yellow]⚠ {w}[/bold yellow]")
                border = _risk_colors.get(risk.risk_label, "white").replace("bold ", "")
                console.print(Panel("\n".join(risk_lines), title="Portfolio-Risiko", border_style=border))
        except Exception as _re:
            log.debug("Risk panel error: %s", _re)


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def safe_run_analysis_cycle(*args, **kwargs) -> None:
    """
    Fehler-sicherer Wrapper um run_analysis_cycle.
    Fängt alle unbehandelten Exceptions, loggt den vollen Traceback
    und schickt ihn per Telegram — der Bot-Loop läuft weiter.
    Enthält einen Lock + Mindestabstand (MIN_CYCLE_GAP_MINUTES, Standard 20 Min)
    um gleichzeitige und zu schnell aufeinanderfolgende Zyklen zu verhindern.
    Alle Aufruforte in scheduler.py sollten diesen Wrapper verwenden.
    """
    global _last_cycle_start
    now = datetime.now()

    if not _cycle_lock.acquire(blocking=False):
        log.info("safe_run_analysis_cycle: Zyklus läuft bereits – Aufruf übersprungen.")
        return

    try:
        if _last_cycle_start is not None:
            elapsed_min = (now - _last_cycle_start).total_seconds() / 60
            if elapsed_min < _MIN_CYCLE_GAP_MINUTES:
                log.info(
                    "safe_run_analysis_cycle: letzter Zyklus vor %.0f Min – "
                    "Mindestabstand %d Min nicht erreicht, übersprungen.",
                    elapsed_min, _MIN_CYCLE_GAP_MINUTES,
                )
                return
        _last_cycle_start = now
        try:
            run_analysis_cycle(*args, **kwargs)
        except Exception as _fatal:
            _tb = traceback.format_exc()
            log.error("Analyse-Zyklus FATAL – ungefangene Exception:\n%s", _tb)
            try:
                TelegramNotifier().send(
                    f"❌ <b>Analyse-Zyklus abgebrochen</b>\n\n"
                    f"Fehler: <code>{str(_fatal)[:400]}</code>\n\n"
                    f"Details: <code>journalctl -u aktien_bot -n 80</code>"
                )
            except Exception:
                pass
    finally:
        _cycle_lock.release()


def run_analysis_cycle(
    portfolio: Portfolio,
    broker,
    strategy,
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    archive: NewsArchive,
    reflection: Optional[ReflectionEngine] = None,
    weekend_prep=None,
    hedge_strategy=None,
    earnings_strategy=None,
    announce_start: bool = False,
    only_tickers: Optional[List[str]] = None,
):
    rule_suffix = "  [bold yellow][EXPLORATION][/bold yellow]" if config.exploration_mode else ""
    _cycle_ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    console.rule(f"[bold blue]Analyse-Zyklus – {_cycle_ts}{rule_suffix}")

    # Live-Sichtbarkeit (Roadmap 1.5): Status-Zeile + Aktivitätsfeed. Alle
    # Funktionen sind fail-open (werfen nie) — nur der Import wird geschützt.
    try:
        from system import live_status as _live
    except Exception:
        class _live:  # noqa: N801 — Null-Objekt, hält die Aufrufstellen schlank
            set_phase = set_idle = feed_emit = staticmethod(lambda *a, **k: None)
    _live.set_phase("Start")
    _live.feed_emit("cycle_start", detail=_cycle_ts)

    # Makro-Lagebericht einmal pro Lauf bauen – fließt als Kontext in jede
    # Einzelanalyse und wird hier transparent auf Konsole/Log/Telegram ausgegeben.
    _macro_brief = ""
    try:
        from analyzers.macro_context import get_macro_brief
        _macro_brief = get_macro_brief()
    except Exception as _mc_err:
        log.warning("Makro-Kontext konnte nicht gebaut werden: %s", _mc_err)
    if _macro_brief:
        log.info("Makro-Kontext:\n%s", _macro_brief)
        console.print(f"[cyan]{_macro_brief}[/cyan]")

    # Start-Nachricht nur für die geplante (vorbörsliche) Hauptanalyse – nicht für
    # jeden intraday/getriggerten Zyklus. "Analyse gestartet"-Spam war zu viel;
    # die relevanten Nachrichten sind Trades und gefundene Titel (Digest am Ende).
    if announce_start:
        try:
            _start_msg = f"🔄 <b>Vorbörsliche Analyse gestartet</b> – {_cycle_ts}"
            if _macro_brief:
                # Erste Zeile ist die Überschrift des Briefs → durch fette Telegram-
                # Überschrift ersetzen, Rest (Bullet-Zeilen) unverändert anhängen.
                _body = _macro_brief.split("\n", 1)[1] if "\n" in _macro_brief else _macro_brief
                _start_msg += f"\n\n📊 <b>Makro-Lage</b>\n{_body}"
            TelegramNotifier().send(_start_msg)
        except Exception:
            pass

    # Multi-Agent Konsens wenn aktiviert, sonst Standard-Analyzer
    _multi_agent_enabled = os.getenv("MULTI_AGENT_ENABLED", "false").lower() in ("1", "true", "yes")
    try:
        analyzer = MultiAgentAnalyzer() if _multi_agent_enabled else ClaudeAnalyzer()
    except Exception as _az_err:
        log.error("Analyzer-Initialisierung fehlgeschlagen: %s", _az_err, exc_info=True)
        TelegramNotifier().send(f"❌ <b>Analyse-Fehler</b>\nAnalyzer-Init fehlgeschlagen: <code>{_az_err}</code>", level="critical")
        return
    if _multi_agent_enabled:
        console.print("  [bold magenta]🤝 Multi-Agent Konsens aktiv[/bold magenta] (3 Claude-Analysten)")
    try:
        collectors = _make_collectors()
    except Exception as _col_err:
        log.error("Collector-Initialisierung fehlgeschlagen: %s", _col_err, exc_info=True)
        TelegramNotifier().send(f"❌ <b>Analyse-Fehler</b>\nCollector-Init fehlgeschlagen: <code>{_col_err}</code>", level="critical")
        return

    # Live-Quellen-Health: Zyklus-Erfassung zurücksetzen (Counts/Fehler je Quelle).
    try:
        from analyzers.source_monitor import get_monitor
        get_monitor().start_cycle()
    except Exception:
        pass

    # Inject continuous learning memo into Claude's system prompt
    lessons_memo = reflection.get_active_memo() if reflection else None
    if lessons_memo:
        console.print(f"  [dim]📚 Lessons-Memo aktiv ({len(lessons_memo)} Zeichen)[/dim]")

    # Inject weekly briefing as additional context
    weekly_briefing = weekend_prep.get_current_briefing() if weekend_prep else None
    if weekly_briefing:
        console.print(f"  [dim]📋 Wochenbriefing aktiv ({len(weekly_briefing)} Zeichen)[/dim]")

    # Track all material trade actions for the daily summary
    cycle_actions: List[str] = []

    cycle_log = get_logger(__name__)

    # Führt die StrategyResult-Entscheidungen der (reinen) SwingStrategy aus.
    from strategy.executor import TradeExecutor
    from strategy.swing_strategy import StrategyResult
    executor = TradeExecutor(portfolio, broker, getattr(strategy, "journal", None), strategy=strategy)

    # Regime-Default, falls kein Hedge aktiv ist oder der Regime-Check scheitert –
    # sonst NameError weiter unten (check_exits / evaluate brauchen regime).
    regime = "NEUTRAL"

    # ── Regime check + hedge evaluation ──────────────────────────────────────
    if hedge_strategy:
        macro_news_for_regime = []
        try:
            macro_news_for_regime = NewsAPICollector().collect_general("market recession economy", max_results=10)
        except Exception as e:
            cycle_log.warning("Hedge-Regime: Macro-News konnten nicht geladen werden – %s", e)
        try:
            regime, hedge_actions = hedge_strategy.evaluate_regime(macro_news_for_regime or None)
            regime_color = {"BULL": "green", "NEUTRAL": "yellow", "BEAR": "red", "CRISIS": "bold red"}.get(regime, "white")
            latest = hedge_strategy.regime_summary()
            score_str = f" (Score: {latest['recession_score']:.2f})" if latest else ""
            console.print(f"  Marktregime: [{regime_color}]{regime}[/{regime_color}]{score_str}")
            for action in hedge_actions:
                console.print(f"  [magenta]{action}[/magenta]")
                cycle_actions.append(action)
        except Exception as _reg_err:
            cycle_log.error("Regime-Check fehlgeschlagen – fahre ohne Hedge fort: %s", _reg_err, exc_info=True)
            console.print(f"  [dim red]⚠ Regime-Check fehlgeschlagen: {_reg_err}[/dim red]")

    # ── Makro-Events aus Webhook anzeigen ────────────────────────────────────
    if config.tradingview_webhook_enabled:
        try:
            macro_events = get_pending_macro_events(since_hours=24)
            for me in macro_events:
                surprise_color = "red" if me.get("surprise") == "ABOVE" and me.get("event") in ("CPI", "PPI") \
                                 else "green" if me.get("surprise") == "BELOW" else "yellow"
                console.print(
                    f"  [{surprise_color}]📣 Makro: {me['event']} "
                    f"(Surprise={me.get('surprise','?')}, Impact={me.get('impact','?')})[/{surprise_color}]"
                )
        except Exception:
            pass

    # Force-exit pre-earnings positions before the report
    if earnings_strategy:
        try:
            for _ea in earnings_strategy.check_pre_earnings_exits():
                console.print(f"  [bold yellow]{_ea}[/bold yellow]")
                cycle_actions.append(_ea)
        except Exception as _ee:
            log.debug("Earnings pre-exit check failed: %s", _ee)

    # Check stop-loss / take-profit first (no Claude needed)
    _live.set_phase("Exits prüfen")
    try:
        _open_tickers = list(portfolio.all_positions().keys())
        _exit_prices = broker.get_prices(_open_tickers) if _open_tickers else {}
        for _res in strategy.check_exits(_exit_prices, regime):
            _p = portfolio.get_position(_res.ticker)
            _dh = 0
            if _p:
                try:
                    _dh = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(_p.entry_date)).days
                except Exception:
                    _dh = 0
            action = executor.execute(_res, days_held=_dh)
            if action:
                console.print(f"  [yellow]{action}[/yellow]")
                cycle_actions.append(action)
    except Exception as _sl_err:
        cycle_log.error("SL/TP-Check fehlgeschlagen: %s", _sl_err, exc_info=True)
        console.print(f"  [dim red]⚠ SL/TP-Check fehlgeschlagen: {_sl_err}[/dim red]")

    # TradingView SELL-Signale verarbeiten (Short/Bearish Engulfing)
    if config.tradingview_webhook_enabled:
        tv_sells = get_pending_sells()
        for sig in tv_sells:
            tv_ticker = sig["ticker"]
            pos = portfolio.get_position(tv_ticker)
            if pos:
                price = broker.get_price(tv_ticker) or pos.entry_price
                _dh = 0
                try:
                    _dh = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
                except Exception:
                    _dh = 0
                action = executor.execute(
                    StrategyResult(
                        "SELL", tv_ticker,
                        f"TradingView SELL-Signal ({sig.get('strategy', 'TV')})",
                        shares=pos.shares, price=price,
                    ),
                    days_held=_dh,
                )
                console.print(f"  [bold red]{action}[/bold red]")
                if action:
                    cycle_actions.append(action)
            else:
                console.print(
                    f"  [dim]TradingView SELL [{tv_ticker}]: keine offene Position[/dim]"
                )

    # EU Marktbarometer einmal pro Zyklus laden (cached 2h)
    _eu_market_ctx = None
    # Vom Dashboard / Headline-Signal angeforderte Ticker einsammeln, BEVOR die
    # Watchlist eingefroren wird – force_claude und headline_meta gelten pro Zyklus.
    # Bei Fokus-/Einzel-Läufen die Queue NICHT leeren (gehört dem nächsten
    # geplanten Zyklus) und nichts erzwingen – das Potenzial-Gate hat schon entschieden.
    _requested = [] if only_tickers else _urq.consume_all()  # List[(ticker, meta)]
    _force_claude_tickers: set = set()
    _headline_meta: Dict[str, dict] = {}
    # Ergebnisse der Headline-Signal-Ticker werden gesammelt und am Zyklus-Ende
    # in EINER Digest-Nachricht gemeldet – statt einer Einzelnachricht pro Aktie
    # (sonst Telegram-Flut bei mehreren getriggerten Titeln).
    _headline_results: List[str] = []
    active_watchlist, _bench_geo_contexts = _get_watchlist(portfolio)

    # Einzel-Aktien-/Fokus-Lauf: getriggerte Eskalation analysiert NUR die
    # übergebenen Ticker (plus deren ggf. offene Position), nicht die ganze
    # Watchlist. Spart Claude-Calls und hält den Bot „vor der Welle" agil.
    if only_tickers:
        _focus = [_normalize_ticker(t) for t in only_tickers]
        active_watchlist = list(dict.fromkeys(_focus))
        _bench_geo_contexts = {
            k: v for k, v in (_bench_geo_contexts or {}).items() if k in active_watchlist
        }

    for _t, _meta in _requested:
        if _t not in active_watchlist:
            log.info("Nutzeranfrage: %s wird in diesem Zyklus analysiert", _t)
            active_watchlist.append(_t)
        # Claude nur bei EXPLIZITER Anfrage erzwingen (Dashboard = leere Meta).
        # Auto-Signale der Hintergrund-Scanner (Meta gesetzt) laufen durchs
        # Frugal-Gate: Ollama prüft vor, nur echte Katalysatoren erreichen Claude.
        if not _meta:
            _force_claude_tickers.add(_t)
        if _meta.get("from_headline"):
            _headline_meta[_t] = _meta

    if any(_is_eu_stock(t) for t in active_watchlist):
        try:
            _eu_market_ctx = EUMarketContext().get_snapshot()
            if _eu_market_ctx:
                sig_color = {"RISK_ON": "green", "RISK_OFF": "red"}.get(
                    _eu_market_ctx.signal, "yellow"
                )
                dax = _eu_market_ctx.indices.get("DAX")
                stoxx = _eu_market_ctx.indices.get("STOXX50")
                dax_str  = f"DAX {dax.change_5d_pct:+.1f}%"  if dax   else ""
                stx_str  = f"STOXX50 {stoxx.change_5d_pct:+.1f}%" if stoxx else ""
                console.print(
                    f"  [{sig_color}]🇪🇺 EU-Markt: {_eu_market_ctx.signal} "
                    f"| {dax_str} | {stx_str}[/{sig_color}]"
                )
                if _eu_market_ctx.ecb_note:
                    console.print(f"  [yellow]{_eu_market_ctx.ecb_note}[/yellow]")
        except Exception as e:
            log.debug("EU Marktkontext fehlgeschlagen: %s", e)

    # Analyzer singletons — created once, reused across all tickers in this cycle
    try:
        _vel_analyzer   = NewsVelocityAnalyzer()
        _mtf_sentiment  = MultiTimeframeSentiment()
        _reentry_tracker = ReEntryTracker()
        _chart_analyzer = ChartPatternAnalyzer()
    except Exception:
        _vel_analyzer = _mtf_sentiment = _reentry_tracker = _chart_analyzer = None

    _frugal_cache_hours = 8  # Frugal-Modus: Ticker < 8h alt überspringen

    # Pre-fetch news + price data for ALL tickers in parallel before the analysis loop.
    # This eliminates sequential waiting: all 12 tickers fetch their 30 collectors simultaneously.
    _normalized_watchlist = [_normalize_ticker(t) for t in active_watchlist]
    _prefetch_news:  Dict[str, tuple] = {}
    _prefetch_price: Dict[str, dict]  = {}

    # ── strategy_lab Live-Bridge (Roadmap d) – STANDARD AUS, flaggengeschützt ──
    # Liefert nur bei gesetztem STRATEGY_LAB_LIVE eine mechanische Konviktion je
    # Ticker (additiver Analyse-Kontext, kein Auto-Trade). Komplett defensiv:
    # ein Fehler hier darf den Zyklus nie reißen.
    _mech_conv: Dict[str, dict] = {}
    _mech_brief_fn = lambda _t, _m, _r=None: ""   # immer definiert; "" = kein Zusatzkontext
    try:
        from strategy_lab import live_bridge as _live_bridge
        if _live_bridge.is_enabled():
            _mech_conv = _live_bridge.conviction_map(_normalized_watchlist)
            _mech_brief_fn = _live_bridge.brief_for
            if _mech_conv:
                log.info("strategy_lab Live-Bridge aktiv: mechanische Konviktion für %d Ticker",
                         len(_mech_conv))
    except Exception as _lbe:
        log.debug("Live-Bridge übersprungen: %s", _lbe)
        _mech_conv = {}

    def _prefetch_ticker(t: str):
        news_result  = collect_news(t, archive, collectors)
        if _is_crypto(t):
            price = {"current_price": broker.get_crypto_price(t), "volume": 0}
        else:
            price = collectors["yahoo"].get_price_data(t)
        return t, news_result, price

    _live.set_phase("Vorladen", total=len(_normalized_watchlist))
    _pf_workers = min(len(_normalized_watchlist), int(os.getenv("PREFETCH_WORKERS", "8")))  # cap: avoid overwhelming APIs
    if _pf_workers > 1:
        with ThreadPoolExecutor(max_workers=_pf_workers) as _pf_pool:
            _pf_futures = {_pf_pool.submit(_prefetch_ticker, t): t for t in _normalized_watchlist}
            for _pf_fut in as_completed(_pf_futures):
                try:
                    _t, _nr, _pr = _pf_fut.result()
                    _prefetch_news[_t]  = _nr
                    _prefetch_price[_t] = _pr
                except Exception as _pfe:
                    _t = _pf_futures[_pf_fut]
                    log.debug("Prefetch fehlgeschlagen für %s: %s", _t, _pfe)
    else:
        for _t in _normalized_watchlist:
            try:
                _, _nr, _pr = _prefetch_ticker(_t)
                _prefetch_news[_t]  = _nr
                _prefetch_price[_t] = _pr
            except Exception:
                pass

    # ── Parallele Analyse-Vorberechnung ──────────────────────────────────────
    # Der teuerste Teil (Claude-Call + Chart + History + FinBERT) ist read-only
    # und thread-safe → vorab im Pool berechnen. Alle JSON-Schreiber (velocity,
    # earnings, signal_expander, cache, log) UND Trades bleiben seriell in der
    # Schleife. Per ENV abschaltbar (PARALLEL_ANALYSIS=false) als Kill-Switch.
    _prefetch_analysis: Dict[str, dict] = {}

    def _precompute_analysis(t: str) -> Optional[dict]:
        _pf = _prefetch_news.get(t)
        if _pf:
            _news, _sb = _pf
        else:
            _news, _sb = collect_news(t, archive, collectors)
        _price = _prefetch_price.get(t) or (
            {"current_price": broker.get_crypto_price(t), "volume": 0}
            if _is_crypto(t) else collectors["yahoo"].get_price_data(t)
        )
        if not _is_crypto(t) and not _valid_price(_price.get("current_price")):
            return None  # kein (gültiger, NaN-freier) Kurs → Schleife überspringt ohnehin
        # FinBERT-Signal voranstellen (read-only)
        try:
            from analyzers.finbert_analyzer import FinBERTAnalyzer
            _fb_an = FinBERTAnalyzer()
            if _fb_an.is_available() and _news:
                _hl = [(it.get("title") or it.get("text") or "")[:120]
                       for it in _news if it.get("title") or it.get("text")]
                if _hl:
                    _fb_item = _fb_an.build_signal_item(t, _fb_an.analyze_headlines(_hl))
                    if _fb_item:
                        _news = [_fb_item] + _news
        except Exception:
            pass
        if not _news:
            return {"news": _news, "sources_breakdown": _sb, "price_data": _price,
                    "analysis": None, "pattern_result": None, "onchain": None}
        _cur_titles = {it.get("title") or "" for it in _news}
        _hist = archive.get_history(t, days=30, exclude_titles=_cur_titles)
        _opc = strategy.build_open_position_context(t)
        _pat = None
        try:
            _pat = (_chart_analyzer or ChartPatternAnalyzer()).analyze(t)
        except Exception:
            pass
        _oc = None
        if _is_crypto(t):
            try:
                _base = t.split("/")[0].upper().removesuffix("-USD")
                _oc = OnChainSignalAnalyzer().analyze(OnChainCollector().collect(_base))
            except Exception:
                pass
        try:
            _an = analyzer.analyze(
                ticker=t, news_items=_news, price_data=_price,
                historical_news=_hist if _hist else None, open_position=_opc,
                lessons_memo=lessons_memo, weekly_briefing=weekly_briefing,
                pattern_result=_pat, onchain_snapshot=_oc,
                eu_market_snapshot=_eu_market_ctx if _is_eu_stock(t) else None,
                geo_context=_bench_geo_contexts.get(t), macro_brief=_macro_brief,
                mechanical_brief=_mech_brief_fn(t, _mech_conv, regime),
                force_claude=t in _force_claude_tickers,
            )
        except Exception as _ae:
            log.debug("Analyse-Prefetch analyze(%s) fehlgeschlagen: %s", t, _ae)
            _an = None
        return {"news": _news, "sources_breakdown": _sb, "price_data": _price,
                "analysis": _an, "pattern_result": _pat, "onchain": _oc}

    _parallel_analysis = os.getenv("PARALLEL_ANALYSIS", "true").lower() in ("1", "true", "yes")
    _an_workers = min(len(_normalized_watchlist), int(os.getenv("ANALYSIS_WORKERS", "4")))
    # Auf reiner CPU zieht EINE lokale Ollama-Analyse bereits ~4–5 Kerne. Mehrere
    # parallele Analysen übersubskribieren die Kerne → jede Generierung kriecht in
    # ihr Timeout → Circuit Breaker schaltet Ollama ab → alles fällt auf das
    # budgetgedeckelte Claude → leere SKIP-Analysen ohne Kaufsignale (Befund 18.6.,
    # 0 Trades seit 15.6.). Daher die Analyse-Worker an den Ressourcen-Tier koppeln
    # (MINIMAL/CPU = 1 Worker), sofern ANALYSIS_WORKERS nicht explizit gesetzt ist.
    if "ANALYSIS_WORKERS" not in os.environ:
        try:
            from system.resource_manager import get_resource_manager
            _rm_for_workers = get_resource_manager()
            _rm_for_workers.update()
            _an_workers = max(1, min(_an_workers, _rm_for_workers.max_workers()))
        except Exception as _rmw_err:
            log.debug("Worker-Cap via Resource-Manager übersprungen: %s", _rmw_err)
    if _parallel_analysis and _an_workers > 1 and not _multi_agent_enabled:
        log.info("Analyse-Prefetch: %d Titel mit %d Workern", len(_normalized_watchlist), _an_workers)
        with ThreadPoolExecutor(max_workers=_an_workers) as _an_pool:
            _an_futs = {_an_pool.submit(_precompute_analysis, t): t for t in _normalized_watchlist}
            for _an_fut in as_completed(_an_futs):
                _t = _an_futs[_an_fut]
                try:
                    _ctx_r = _an_fut.result()
                    if _ctx_r:
                        _prefetch_analysis[_t] = _ctx_r
                except Exception as _ace:
                    log.debug("Analyse-Prefetch fehlgeschlagen für %s: %s", _t, _ace)

    _wl_total = len(active_watchlist)
    _hb_every = max(0, int(os.getenv("HEARTBEAT_EVERY", "20")))  # 0 = Heartbeat aus
    for _wl_idx, ticker in enumerate(active_watchlist, start=1):
        ticker = _normalize_ticker(ticker)
        _live.set_phase("Analyse", ticker=ticker, idx=_wl_idx, total=_wl_total)

        # Heartbeat: periodisches Lebenszeichen während des langen Zyklus. Nur bei
        # der angekündigten Hauptanalyse (announce_start) – intraday/getriggerte
        # Zyklen laufen still, dort ist "Analyse läuft"-Spam unerwünscht.
        if announce_start and _hb_every and _wl_idx > 1 and (_wl_idx - 1) % _hb_every == 0:
            try:
                _done = _wl_idx - 1
                _pct = int(_done / _wl_total * 100) if _wl_total else 0
                _trades = sum(1 for a in cycle_actions if "GEKAUFT" in a or "VERKAUFT" in a)
                TelegramNotifier().send(
                    f"⏳ <b>Analyse läuft</b> – {_done}/{_wl_total} Titel ({_pct}%)"
                    + (f"\n💼 {_trades} Trade(s) bisher" if _trades else "")
                )
            except Exception:
                pass

        # Frugal-Modus: frisch gecachte Ticker überspringen (spart ~50% Ollama-Calls)
        if config.frugal_mode and not portfolio.get_position(ticker):
            cached = _analysis_cache.get(ticker)
            if cached:
                from datetime import timezone
                _cached_at = cached.get("updated_at", "")
                if _cached_at:
                    try:
                        _age_h = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(_cached_at.replace("Z", ""))).total_seconds() / 3600
                        if _age_h < _frugal_cache_hours:
                            log.debug("[%s] Frugal: Cache %dh alt – übersprungen", ticker, int(_age_h))
                            continue
                    except Exception:
                        pass

        console.print(f"\n[cyan]Analysiere {ticker}...[/cyan]")

        _ctx = _prefetch_analysis.get(ticker)
        if _ctx is not None:
            news = _ctx["news"]
            sources_breakdown = _ctx["sources_breakdown"]
            price_data = _ctx["price_data"]
        else:
            _pf = _prefetch_news.get(ticker)
            if _pf:
                news, sources_breakdown = _pf
            else:
                news, sources_breakdown = collect_news(ticker, archive, collectors)
            price_data = _prefetch_price.get(ticker) or (
                {"current_price": broker.get_crypto_price(ticker), "volume": 0}
                if _is_crypto(ticker) else collectors["yahoo"].get_price_data(ticker)
            )

        # Yahoo leer? → IBKR-Delayed (+yfinance) als Fallback, BEVOR die Schranke
        # greift. Macht Titel handelbar, die die Primärquelle nicht quotet.
        # Serieller Pfad → broker-Aufruf ist hier thread-safe (s. _ensure_current_price).
        if not _is_crypto(ticker):
            price_data = _ensure_current_price(ticker, price_data, broker)

        # Kurs-Check vor Claude: kein (gültiger) Kurs → Claude-Aufruf sparen.
        # _valid_price fängt auch NaN ab – sonst rutscht ein Titel mit
        # current_price=NaN (z.B. neue/illiquide IPOs) hier durch, Claude
        # erzwingt einen BUY und es feuert eine "Kauf nicht ausgeführt"-Nachricht,
        # obwohl die Order mangels Kurs nie platziert werden kann.
        if not _is_crypto(ticker) and not _valid_price(price_data.get("current_price")):
            console.print(f"  [dim]Kein Kurs für {ticker} verfügbar – übersprungen[/dim]")
            continue

        # ── FinBERT lokales Sentiment ─────────────────────────────────────────
        try:
            from analyzers.finbert_analyzer import FinBERTAnalyzer
            _finbert = FinBERTAnalyzer()
            if _ctx is None and _finbert.is_available() and news:
                _headlines = [
                    (item.get("title") or item.get("text") or "")[:120]
                    for item in news if item.get("title") or item.get("text")
                ]
                if _headlines:
                    _fb = _finbert.analyze_headlines(_headlines)
                    _fb_color = {"POSITIVE": "green", "NEGATIVE": "red", "NEUTRAL": "yellow"}[_fb["label"]]
                    console.print(
                        f"  [{_fb_color}]🤖 FinBERT: {_fb['label']} "
                        f"(Score {_fb['score']:.2f}, {_fb['confidence']}) "
                        f"| +{_fb['pos_pct']}% / -{_fb['neg_pct']}%[/{_fb_color}]"
                    )
                    _fb_item = _finbert.build_signal_item(ticker, _fb)
                    if _fb_item:
                        news = [_fb_item] + news  # prepend so Claude sees it first
        except Exception as _fbe:
            log.debug("FinBERT fehlgeschlagen: %s", _fbe)

        # ── Insider-Cluster-Detection ─────────────────────────────────────────
        try:
            _insider_items = [
                i for i in news
                if (i.get("source") or "").startswith("SEC-Form4")
                and "gekauft" in (i.get("title") or "").lower()
            ]
            if len(_insider_items) >= 2:
                _names = [i.get("person") or "Insider" for i in _insider_items[:4]]
                console.print(
                    f"  [bold green]👥 Insider-Cluster: {len(_insider_items)} Käufe "
                    f"({', '.join(_names[:3])}{'...' if len(_names) > 3 else ''})[/bold green]"
                )
        except Exception:
            pass

        # Feed news items to signal expander – detects unknown small-cap tickers
        new_signal_tickers = _signal_expander.process_news_items(news)
        if new_signal_tickers:
            console.print(f"  [magenta]📡 Neue Signal-Ticker entdeckt: {', '.join(new_signal_tickers)}[/magenta]")

        # Load 30-day history, excluding articles already in current batch
        current_titles = {item.get("title") or "" for item in news}
        historical = archive.get_history(ticker, days=30, exclude_titles=current_titles)

        src = sources_breakdown
        console.print(
            f"  [bold]{len(news)}[/bold] Artikel total | {len(historical)} historisch | "
            f"Yahoo:{src['yahoo']} Reddit:{src['reddit']} NewsAPI:{src['newsapi']} "
            f"SEC:{src['sec_edgar']} Wire:{src['wire']} "
            f"Twits:{src['stocktwits']} Twitter:{src.get('twitter', 0)} Insider:{src['insider']} "
            f"Contracts:{src['usaspending']} OptFlow:{src['options_flow']} "
            f"EU:{src['european_news']} | "
            f"Kurs: ${price_data.get('current_price', 'N/A')}"
        )

        if not news:
            console.print("  [dim]Keine Nachrichten – übersprungen[/dim]")
            continue

        # News-Geschwindigkeit anzeigen
        try:
            vel = (_vel_analyzer or NewsVelocityAnalyzer()).analyze(ticker)
            if vel.acceleration in ("SPIKE", "HIGH"):
                boost_str = f" (Signal-Boost ×{vel.signal_boost:.2f})" if vel.signal_boost > 1.0 else ""
                color = "bold yellow" if vel.acceleration == "SPIKE" else "yellow"
                console.print(
                    f"  [{color}]📡 Nachrichten-{vel.acceleration}: "
                    f"{vel.articles_24h} Artikel/24h (Basis: {vel.baseline_per_day:.0f}/Tag){boost_str}[/{color}]"
                )
        except Exception:
            pass

        # Multi-Zeitrahmen-Sentiment (1d/7d/30d)
        try:
            hist_30d = archive.get_history(ticker, days=30)
            by_date: dict = defaultdict(list)
            for item in hist_30d:
                pub = (item.get("published") or item.get("timestamp") or "")[:10]
                if pub and item.get("sentiment_score") is not None:
                    by_date[pub].append(item)
            for item in news:
                today_key = date.today().isoformat()
                if item.get("sentiment_score") is not None:
                    by_date[today_key].append(item)
            if len(by_date) >= 2:
                _mtf = _mtf_sentiment or MultiTimeframeSentiment()
                mtf_result = _mtf.analyze(ticker, dict(by_date))
                mtf_line = _mtf.to_text(mtf_result)
                if mtf_result.trend in ("UPTREND", "DOWNTREND"):
                    t_color = "green" if mtf_result.trend == "UPTREND" else "red"
                    console.print(f"  [{t_color}]📈 {mtf_line}[/{t_color}]")
        except Exception:
            pass

        # Re-Entry-Tracker: Preise aktualisieren
        try:
            rt = _reentry_tracker or ReEntryTracker()
            tickers_watched = [c.ticker for c in rt.get_all_watched()]
            if tickers_watched:
                watch_prices = broker.get_prices(tickers_watched)
                rt.update_prices(watch_prices)
        except Exception:
            pass

        # Build open-position context for thesis check
        open_position_ctx = strategy.build_open_position_context(ticker)
        if open_position_ctx:
            console.print(f"  [yellow]Offene Position – prüfe Kaufthese...[/yellow]")

        # Earnings Surprise Predictor (Stufe 2) – vor Claude-Analyse
        try:
            ep = _earnings_predictor.predict(ticker)
            if ep.get("prediction") in ("BEAT", "MISS"):
                ep_color = "green" if ep["prediction"] == "BEAT" else "red"
                console.print(
                    f"  [{ep_color}]🔮 Earnings-Prognose: {ep['prediction']} "
                    f"(Konfidenz: {ep.get('confidence','LOW')}, "
                    f"Score: {ep.get('score', 0):.2f})[/{ep_color}]"
                )
        except Exception:
            pass

        # Chart-Muster-Analyse (für Krypto primär, für Aktien als Bestätigung)
        pattern_result = None
        try:
            pattern_result = (
                _ctx["pattern_result"] if _ctx is not None
                else (_chart_analyzer or ChartPatternAnalyzer()).analyze(ticker)
            )
            if pattern_result:
                sig_color = {"STRONG_BUY": "bold green", "BUY": "green",
                             "STRONG_SELL": "bold red", "SELL": "red"}.get(
                    pattern_result.primary_signal, "yellow"
                )
                patterns_found = ", ".join(p.name for p in pattern_result.patterns) if pattern_result.patterns else "–"
                console.print(
                    f"  [{sig_color}]📊 Chart-Signal: {pattern_result.primary_signal} "
                    f"(Score: {pattern_result.score:.0f}) | Muster: {patterns_found}[/{sig_color}]"
                )
        except Exception as e:
            log.debug("[%s] Chart-Muster-Analyse fehlgeschlagen: %s", ticker, e)

        # On-Chain-Analyse (nur für Krypto-Assets)
        onchain_snapshot = None
        if _ctx is not None:
            onchain_snapshot = _ctx.get("onchain")
        elif _is_crypto(ticker):
            try:
                base = ticker.split("/")[0].upper().removesuffix("-USD")
                metrics = OnChainCollector().collect(base)
                onchain_snapshot = OnChainSignalAnalyzer().analyze(metrics)
                if onchain_snapshot:
                    oc_color = {
                        "STRONG_BULLISH": "bold green", "BULLISH": "green",
                        "STRONG_BEARISH": "bold red",   "BEARISH": "red",
                    }.get(onchain_snapshot.signal, "yellow")
                    console.print(
                        f"  [{oc_color}]⛓ On-Chain: {onchain_snapshot.signal} "
                        f"(Score: {onchain_snapshot.score:.0f}) "
                        f"[{onchain_snapshot.source}][/{oc_color}]"
                    )
            except Exception as e:
                log.debug("[%s] On-Chain-Analyse fehlgeschlagen: %s", ticker, e)

        _geo_ctx = _bench_geo_contexts.get(ticker)
        if _ctx is not None and _ctx.get("analysis") is not None:
            analysis = _ctx["analysis"]  # parallel vorberechnet
        else:
            console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
            if _geo_ctx:
                log.info("[%s] Geopolitischer Kontext wird an Claude übergeben: %s",
                         ticker, _geo_ctx.get("kategorie", ""))
            analysis = analyzer.analyze(
                ticker=ticker,
                news_items=news,
                price_data=price_data,
                historical_news=historical if historical else None,
                open_position=open_position_ctx,
                lessons_memo=lessons_memo,
                weekly_briefing=weekly_briefing,
                pattern_result=pattern_result,
                onchain_snapshot=onchain_snapshot,
                eu_market_snapshot=_eu_market_ctx if _is_eu_stock(ticker) else None,
                geo_context=_geo_ctx,
                macro_brief=_macro_brief,
                mechanical_brief=_mech_brief_fn(ticker, _mech_conv, regime),
                force_claude=ticker in _force_claude_tickers,
            )

        _print_analysis(analysis)
        # store(ticker, direction, sentiment_score, confidence, recommendation)
        # confidence = Konviktion (HIGH/MEDIUM/LOW), recommendation = Aktion (BUY/HOLD/SKIP).
        # Reihenfolge muss zur Signatur passen, sonst landen Aktion und Konviktion
        # vertauscht im Cache (Dashboard zeigt dann ~0 BUY, TV-Filter falsch).
        _analysis_cache.store(
            ticker, analysis.direction, analysis.sentiment_score,
            analysis.confidence, analysis.recommendation,
        )
        # Echte Nicht-Signale (Score ~0.5, HOLD/SKIP, keine Position) nicht ins Log –
        # verhindert Flut von sinnlosen 0.5-Einträgen.
        _is_noise = (
            0.43 <= analysis.sentiment_score <= 0.57
            and analysis.recommendation in ("HOLD", "SKIP")
            and not portfolio.get_position(ticker)
            and ticker not in _force_claude_tickers
        )
        _analysis_row_id = None
        if not _is_noise:
            # Persistenz-Fehler eines einzelnen Tickers darf den gesamten
            # Analyse-Zyklus nicht abreißen (vgl. sources_used-dict-Crash).
            try:
                # Zeilen-ID einfangen: verkettet die Entscheidung unten mit
                # ihrer Analyse samt Quellen-Breakdown (Roadmap 1.4b).
                _analysis_row_id = _analysis_log.store(
                    analysis, sources_breakdown=sources_breakdown)
            except Exception as _store_err:
                log.warning("Analysis-Log store(%s) fehlgeschlagen: %s", ticker, _store_err)
            _live.feed_emit(
                "analysis_done", ticker=ticker,
                detail=f"{analysis.recommendation} · Score "
                       f"{analysis.sentiment_score:.2f} · {analysis.confidence}",
            )

        # Headline-Signal-Ticker: Ergebnis als kompakte Zeile sammeln. Versand
        # erfolgt gebündelt am Zyklus-Ende (eine Digest-Nachricht), nicht pro Aktie.
        if ticker in _headline_meta:
            try:
                _cur = price_data.get("current_price") if price_data else None
                _price_str = f" @ ${_cur:.2f}" if _cur else ""
                _rec = analysis.recommendation
                _rec_icon = {"BUY": "🟢", "SKIP": "⏭️"}.get(_rec, "ℹ️")
                _headline_results.append(
                    f"{_rec_icon} <b>{ticker}</b> {_rec} "
                    f"({analysis.sentiment_score:.2f}){_price_str}"
                )
            except Exception as _fu_err:
                log.debug("Headline-Ergebnis sammeln fehlgeschlagen: %s", _fu_err)

        # Bei SKIP mit bullischem Potential: Conditional Entry speichern
        if (
            analysis.recommendation == "SKIP"
            and analysis.entry_trigger_price
            and analysis.sentiment_score >= 0.50
            and analysis.direction in ("BULLISH", "NEUTRAL")
        ):
            try:
                from analyzers.conditional_entry import ConditionalEntryWatcher
                _cur_price = price_data.get("current_price", 0) if price_data else 0
                if _cur_price and analysis.entry_trigger_price < _cur_price:
                    _ce = ConditionalEntryWatcher.build(
                        ticker, analysis.entry_trigger_price, _cur_price, analysis
                    )
                    ConditionalEntryWatcher().add(_ce)
                    console.print(
                        f"  [yellow]📌 Conditional Entry gesetzt: {ticker} – "
                        f"Kauf bei ${analysis.entry_trigger_price:.2f} "
                        f"({_ce.pct_to_trigger:.1f}% unter aktuellem Kurs, "
                        f"Ablauf {_ce.expires_at[:10]})[/yellow]"
                    )
            except Exception as _ce_err:
                log.debug("Conditional Entry konnte nicht gespeichert werden: %s", _ce_err)

        # Bei BUY: verwandte Aktien ins Netz aufnehmen
        if analysis.recommendation == "BUY" and analysis.related_tickers:
            try:
                from analyzers.stock_relations import StockRelations
                from analyzers.bench_list import BenchList
                StockRelations().add_relation(
                    ticker, analysis.related_tickers, analysis.entry_rationale
                )
                bench = BenchList()
                for rt in analysis.related_tickers:
                    bench.add(
                        rt,
                        score=round(analysis.sentiment_score * 0.85, 3),
                        reason=f"Verwandt mit {ticker}: {analysis.entry_rationale[:80]}",
                    )
                console.print(
                    f"  [cyan]🔗 Verwandte Aktien → BenchList: "
                    f"{', '.join(analysis.related_tickers)}[/cyan]"
                )
            except Exception as e:
                log.debug("Stock-Relations Fehler: %s", e)

        _raw_px = (price_data or {}).get("current_price")
        _cur_px = float(_raw_px) if _valid_price(_raw_px) else 0.0
        _result = None
        if _cur_px > 0:
            # Fehler bei EINEM Ticker darf den restlichen Zyklus nicht abreißen
            # und muss sichtbar sein (nicht still verschluckt – vgl. Execution-Bug).
            try:
                _rl = _rl_agent if getattr(config, "rl_veto_enabled", False) else None
                _result = strategy.evaluate(ticker, analysis, _cur_px, regime, rl_agent=_rl)
                action = executor.execute(_result, analysis=analysis, sources_breakdown=sources_breakdown)
            except Exception as _exec_err:
                log.error("Evaluate/Execute [%s] fehlgeschlagen: %s", ticker, _exec_err, exc_info=True)
                console.print(f"  [bold red]⚠ {ticker}: Entscheidung/Ausführung fehlgeschlagen – {_exec_err}[/bold red]")
                action = None
        else:
            action = f"[{ticker}] Kein Kurs verfügbar – übersprungen"
        # Entscheidungs-Transparenz: jede Strategie-Entscheidung samt Grund und
        # Kontext persistieren — das Dashboard (Tab "Entscheidungen") zeigt
        # daraus, WARUM gekauft/übersprungen wurde. Fail-open.
        try:
            from analyzers.decision_log import get_decision_log
            _dlog = get_decision_log()
            if _dlog is not None:
                _dl_mb = None
                try:
                    from analyzers.macro_context import get_macro_context
                    _dl_mb = round(float(get_macro_context().bias_score()), 4)
                except Exception:
                    pass
                _dl_su = getattr(analysis, "sources_used", 0)
                _dl_nsrc = (sum(int(v or 0) for v in _dl_su.values())
                            if isinstance(_dl_su, dict) else int(_dl_su or 0))
                _dlog.log({
                    "ticker": ticker,
                    "action": _result.action if _result is not None else "SKIP",
                    "reason": (_result.reason if _result is not None
                               else "Kein Kurs verfügbar"),
                    "executed": action,
                    "source": "cycle",
                    "recommendation": getattr(analysis, "recommendation", None),
                    "direction": getattr(analysis, "direction", None),
                    "sentiment_score": getattr(analysis, "sentiment_score", None),
                    "confidence": getattr(analysis, "confidence", None),
                    "sources_used": _dl_nsrc,
                    "regime": str(regime) if regime else None,
                    "macro_bias": _dl_mb,
                    "analysis_id": _analysis_row_id,
                })
        except Exception as _dl_err:
            log.debug("Decision-Log Fehler [%s]: %s", ticker, _dl_err)
        if action:
            if "GEKAUFT" in action:
                color = "bold green"
            elif "VERKAUFT" in action:
                color = "bold red"
            elif "übersprungen" in action or "Limit" in action or "Schwelle" in action or "übersprungen" in action:
                color = "yellow"
            else:
                color = "dim"
            console.print(f"  [{color}]{action}[/{color}]")
            # Nur echte Käufe/Verkäufe in die Tages-Zusammenfassung
            if "GEKAUFT" in action or "VERKAUFT" in action:
                cycle_actions.append(action)
                _live.feed_emit("trade", ticker=ticker, detail=action)
                # Prediction-Tracking: Kauf = neue Vorhersage, Verkauf = Outcome.
                # Speist Genauigkeits-Reports, Reflektion, Kalibrierung, adaptive Schwellen.
                _px = float((price_data or {}).get("current_price") or 0)
                if _px > 0:
                    try:
                        if "GEKAUFT" in action:
                            # Idempotent: pro Ticker nur eine offene Vorhersage
                            if tracker.open_prediction_id(ticker) is None:
                                tracker.record_prediction(
                                    ticker, analysis.direction, analysis.confidence,
                                    analysis.sentiment_score, _px, analysis.debate_winner,
                                )
                        else:  # VERKAUFT
                            _pid = tracker.open_prediction_id(ticker)
                            if _pid is not None:
                                tracker.record_outcome(_pid, _px, exit_reason=action)
                    except Exception as _pt_err:
                        log.debug("Prediction-Tracking Fehler [%s]: %s", ticker, _pt_err)
                    # Experience-Store (Selbstlern-Datensatz): spiegelt Entry/Exit
                    # als gelabelte Erfahrung mit label_source='live'. Fail-open.
                    try:
                        _es = _get_experience_store()
                        if _es is not None:
                            if "GEKAUFT" in action:
                                if _es.open_decision_id(ticker) is None:
                                    # Kontext-Features zum Entscheidungszeitpunkt:
                                    # Marktregime (Scope-Var) + Makro-Bias. Beide fail-open
                                    # (None), damit ein fehlender Makro-Snapshot nie den
                                    # Entry-Log reißt.
                                    _macro_bias = None
                                    try:
                                        from analyzers.macro_context import get_macro_context
                                        _macro_bias = round(float(get_macro_context().bias_score()), 4)
                                    except Exception:
                                        _macro_bias = None
                                    _es.record_live_entry({
                                        "decided_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                                        "ticker": ticker,
                                        "recommendation": analysis.recommendation,
                                        "direction": analysis.direction,
                                        "sentiment_score": analysis.sentiment_score,
                                        "confidence": analysis.confidence,
                                        "debate_winner": analysis.debate_winner,
                                        "target_price": getattr(analysis, "target_price", None),
                                        "suggested_hold": getattr(analysis, "suggested_hold_days", None),
                                        "sources_used": int(getattr(analysis, "sources_used", 0) or 0)
                                        if not isinstance(getattr(analysis, "sources_used", 0), dict)
                                        else sum((analysis.sources_used or {}).values()),
                                        "key_catalysts": list(getattr(analysis, "key_catalysts", []) or []),
                                        "risk_factors": list(getattr(analysis, "risk_factors", []) or []),
                                        "regime": str(regime) if regime else None,
                                        "macro_bias": _macro_bias,
                                    }, _px)
                            else:  # VERKAUFT
                                _es.record_live_exit(ticker, _px, exit_reason=action)
                    except Exception as _es_err:
                        log.debug("Experience-Store Fehler [%s]: %s", ticker, _es_err)
                # Lessons-Memo nach jedem Verkauf aktualisieren
                if reflection and "VERKAUFT" in action:
                    new_memo = reflection.generate_memo()
                    if new_memo:
                        console.print("  [dim]📚 Lessons-Memo aktualisiert[/dim]")
                        lessons_memo = new_memo
        elif analysis.recommendation == "BUY":
            console.print(
                f"  [dim][{ticker}] BUY-Signal vorhanden, aber kein Trade "
                f"(Konfidenz/Schwelle/Filter – Logs prüfen)[/dim]"
            )

        # Earnings Strategy: pre/post-earnings plays (independent of swing signal)
        if earnings_strategy and price_data:
            _ep = price_data.get("current_price") or 0
            if _ep > 0:
                try:
                    _pv = portfolio.total_value(broker.get_prices(list(portfolio.all_positions().keys()) + [ticker]))
                    earn_action = earnings_strategy.evaluate(ticker, _ep, _pv)
                    if earn_action:
                        _ec = "bold green" if "GEKAUFT" in earn_action else "dim"
                        console.print(f"  [{_ec}]{earn_action}[/{_ec}]")
                        if "GEKAUFT" in earn_action:
                            cycle_actions.append(earn_action)
                except Exception as _eae:
                    log.debug("EarningsStrategy.evaluate error [%s]: %s", ticker, _eae)

        # Korrektur-Follow-Up: Headline-BUY-Signal durch Strategy geblockt → User informieren
        if (
            ticker in _headline_meta
            and analysis.recommendation == "BUY"
            and (action is None or "GEKAUFT" not in action)
        ):
            try:
                _block_reason = action or f"[{ticker}] Kein Kauf (Filter-Details in Logs)"
                TelegramNotifier().send(
                    f"⚠️ <b>{ticker} – Kauf nicht ausgeführt</b>\n\n"
                    f"Claude: BUY (Score {analysis.sentiment_score:.2f}) – aber Trade blockiert:\n"
                    f"{_block_reason.replace(f'[{ticker}] ', '')}"
                )
            except Exception as _corr_err:
                log.debug("Korrektur-Follow-Up fehlgeschlagen: %s", _corr_err)

    # Headline-Analyse: alle Ergebnisse gebündelt in EINER Nachricht (statt einer
    # Einzelnachricht pro Aktie – das war die Telegram-Flut).
    if _headline_results:
        try:
            TelegramNotifier().send(
                f"⚡ <b>Headline-Analyse</b> ({len(_headline_results)} Titel)\n"
                f"━━━━━━━━━━━━━━\n"
                + "\n".join(_headline_results)
            )
        except Exception as _dg_err:
            log.debug("Headline-Digest fehlgeschlagen: %s", _dg_err)

    # Record portfolio snapshot
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total_value = portfolio.total_value(prices)
    phase = phase_ctrl.current_phase(total_value)
    # record_snapshot erwartet (total_value, cash, n_positions); daily_pnl wird
    # intern aus dem letzten Snapshot berechnet. Früher wurden hier fälschlich
    # positions_value (statt Anzahl) und phase (statt pnl) übergeben.
    tracker.record_snapshot(total_value, portfolio.cash, len(portfolio.all_positions()))

    # Clean up news older than 32 days
    archive.cleanup_old(keep_days=32)

    _print_portfolio_summary(portfolio, broker, phase_ctrl)

    # Send Telegram daily summary
    notifier = TelegramNotifier()

    # Live-Quellen-Health auswerten: plötzlich tote/fehlerhafte Quellen sofort
    # (gedrosselt) melden, statt erst im 7-Tage-Report.
    try:
        from analyzers.source_monitor import get_monitor
        get_monitor().finalize_cycle(notifier)
    except Exception as _sh_err:
        log.debug("Quellen-Health-Auswertung übersprungen: %s", _sh_err)

    # Tages-Zusammenfassung NICHT pro Zyklus senden – das war die Telegram-Flut
    # (1× je Markt-Slot/Trigger/Watchdog → mehrere "Tages-Zusammenfassungen" am
    # Tag). Aktionen werden gesammelt; die Abend-Summary (_daily_summary_job im
    # Scheduler) sendet sie einmal täglich gebündelt. Einzeltrades werden ohnehin
    # bereits sofort per notify_buy/notify_sell gemeldet.
    record_daily_actions(cycle_actions)

    # Live-Sichtbarkeit: Zyklus fertig → Status auf Idle, Abschluss-Event.
    _n_trades = sum(1 for a in cycle_actions if "GEKAUFT" in a or "VERKAUFT" in a)
    _live.feed_emit("cycle_end",
                    detail=f"{_wl_total} Titel analysiert · {_n_trades} Trade(s)")
    _live.set_idle(note="Zyklus beendet")
