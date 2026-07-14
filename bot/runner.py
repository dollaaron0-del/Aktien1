"""
bot/runner.py – Analysis cycle, news collection, and related helpers.
"""

import math
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Optional

from rich.console import Console

from config import config
from logger import get_logger
from collectors import (
    YahooCollector, NewsAPICollector,
    InsiderCollector, USASpendingCollector,
    SECEdgarCollector, StockTwitsCollector, WireCollector,
    OptionsFlowCollector, EuropeanNewsCollector, TwitterCollector,
    SEC8KCollector, ShortInterestCollector, InstitutionalCollector,
    AnalystCollector,
    JobListingsCollector, CEOInterviewCollector, EURegulationCollector,
    ChineseMediaCollector, WebTrafficCollector, GermanMediaCollector,
    InternationalMediaCollector, QuiverCollector,
    EconomicCalendarCollector, AdhocCollector,
    FDACalendarCollector, EstimateRevisionsCollector, ShortVolumeCollector,
    GoogleTrendsCollector, WikipediaViewsCollector, OpenFDACollector,
    NHTSARecallsCollector, SECActivistCollector,
)
from collectors.news_archive import NewsArchive
from collectors.crypto_news_collector import CryptoNewsCollector
from analyzers import ClaudeAnalyzer, AnalysisResult
from analyzers.chart_patterns import ChartPatternAnalyzer
from analyzers.eu_market_context import EUMarketContext
from analyzers.reflection_engine import ReflectionEngine
from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from analyzers.news_velocity import NewsVelocityAnalyzer
from analyzers.multi_timeframe_sentiment import MultiTimeframeSentiment
from analyzers.reentry_tracker import ReEntryTracker
from analyzers.analysis_cache import AnalysisCache
from analyzers.analysis_log import AnalysisLog
from analyzers.prompt_archive import PromptArchive
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
from bot import cycle_analysis
from bot import cycle_checks
from bot import cycle_close
from bot import cycle_exits
from bot import cycle_prefetch
from bot.cycle_close import print_portfolio_summary as _print_portfolio_summary
from bot.cycle_close import progress_bar as _progress_bar

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
_prompt_archive     = PromptArchive()  # Roadmap 1.4d: KI-Prompt-Archiv

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
        "yahoo", "newsapi", "wire", "stocktwits",
        "twitter", "crypto_news", "econ_calendar",
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
    _cycle_ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    console.rule(f"[bold blue]Analyse-Zyklus – {_cycle_ts}")

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

    # Führt die StrategyResult-Entscheidungen der (reinen) SwingStrategy aus.
    from strategy.executor import TradeExecutor
    executor = TradeExecutor(portfolio, broker, getattr(strategy, "journal", None), strategy=strategy)

    # Pre-Analyse-Marktkontext (Roadmap 4.4a, ausgelagert nach bot/cycle_checks.py):
    # Regime-Check+Hedge-Eval → Makro-Events-Webhook → Earnings-Pre-Exit. Liefert
    # das Markt-Regime für den Rest des Zyklus (check_exits/evaluate brauchen es).
    regime = cycle_checks.run_pre_analysis_checks(hedge_strategy, earnings_strategy, cycle_actions)

    # Exit-Prüfung (Roadmap 4.4a, ausgelagert nach bot/cycle_exits.py): SL/TP-
    # Check (kein Claude nötig) → TradingView-SELL-Signale.
    cycle_exits.run_exit_checks(portfolio, broker, strategy, executor, regime, cycle_actions, _live)

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

    # Analyse-Vorladung (Roadmap 4.4a, ausgelagert nach bot/cycle_prefetch.py):
    # News+Preis für die ganze Watchlist parallel vorladen, danach optional
    # (mehrere Ticker, PARALLEL_ANALYSIS) die teure Analyse selbst vorab
    # parallel berechnen. Die serielle Schleife unten greift zuerst auf die
    # zurückgegebenen Dicts zu (Cache-Hit) und fällt sonst auf ihren eigenen
    # seriellen Pfad zurück (z.B. wenn PARALLEL_ANALYSIS aus ist).
    _prefetch = cycle_prefetch.run_prefetch(
        active_watchlist,
        broker=broker, strategy=strategy, analyzer=analyzer, archive=archive,
        collectors=collectors, lessons_memo=lessons_memo, weekly_briefing=weekly_briefing,
        macro_brief=_macro_brief, eu_market_ctx=_eu_market_ctx,
        bench_geo_contexts=_bench_geo_contexts, regime=regime,
        force_claude_tickers=_force_claude_tickers, multi_agent_enabled=_multi_agent_enabled,
        live=_live, normalize_ticker=_normalize_ticker, is_crypto=_is_crypto,
        is_eu_stock=_is_eu_stock, collect_news=collect_news,
        news_velocity_cls=NewsVelocityAnalyzer,
        multi_timeframe_sentiment_cls=MultiTimeframeSentiment,
        reentry_tracker_cls=ReEntryTracker,
        chart_pattern_analyzer_cls=ChartPatternAnalyzer,
    )
    _vel_analyzer, _mtf_sentiment   = _prefetch.vel_analyzer, _prefetch.mtf_sentiment
    _reentry_tracker, _chart_analyzer = _prefetch.reentry_tracker, _prefetch.chart_analyzer
    _mech_conv, _mech_brief_fn      = _prefetch.mech_conv, _prefetch.mech_brief_fn
    _prefetch_news, _prefetch_price = _prefetch.news, _prefetch.price
    _prefetch_analysis              = _prefetch.analysis

    _frugal_cache_hours = 8  # Frugal-Modus: Ticker < 8h alt überspringen

    _wl_total = len(active_watchlist)
    _hb_every = max(0, int(os.getenv("HEARTBEAT_EVERY", "20")))  # 0 = Heartbeat aus

    # Serielle Analyse-Schleife (Roadmap 4.4a, ausgelagert nach
    # bot/cycle_analysis.py): der eigentliche Kern des Zyklus – pro Ticker
    # News/Preis auflösen, Daten-Gate, FinBERT/Insider/Signal-Expander,
    # Claude-Analyse, Cache/Log-Persistenz, Conditional-Entry, Stock-
    # Relations, RL-Veto-gesteuertes evaluate()+Executor, Decision-Log,
    # Prediction-Tracker, Experience-Store, Earnings-Strategy. Mutiert
    # cycle_actions/_headline_results in place.
    cycle_analysis.run_ticker_loop(
        active_watchlist,
        portfolio=portfolio, broker=broker, strategy=strategy, executor=executor,
        tracker=tracker, reflection=reflection, earnings_strategy=earnings_strategy,
        archive=archive, collectors=collectors, analyzer=analyzer,
        cycle_actions=cycle_actions, regime=regime, lessons_memo=lessons_memo,
        weekly_briefing=weekly_briefing, macro_brief=_macro_brief,
        eu_market_ctx=_eu_market_ctx, bench_geo_contexts=_bench_geo_contexts,
        force_claude_tickers=_force_claude_tickers, headline_meta=_headline_meta,
        headline_results=_headline_results, mech_conv=_mech_conv,
        mech_brief_fn=_mech_brief_fn, prefetch_analysis=_prefetch_analysis,
        prefetch_news=_prefetch_news, prefetch_price=_prefetch_price,
        frugal_cache_hours=_frugal_cache_hours, announce_start=announce_start,
        wl_total=_wl_total, hb_every=_hb_every, live=_live,
        vel_analyzer=_vel_analyzer, mtf_sentiment=_mtf_sentiment,
        reentry_tracker=_reentry_tracker, chart_analyzer=_chart_analyzer,
        normalize_ticker=_normalize_ticker, is_crypto=_is_crypto,
        is_eu_stock=_is_eu_stock, collect_news=collect_news,
        ensure_current_price=_ensure_current_price, valid_price=_valid_price,
        print_analysis=_print_analysis,
        news_velocity_cls=NewsVelocityAnalyzer,
        multi_timeframe_sentiment_cls=MultiTimeframeSentiment,
        reentry_tracker_cls=ReEntryTracker,
        chart_pattern_analyzer_cls=ChartPatternAnalyzer,
        telegram_notifier_cls=TelegramNotifier,
        analysis_cache=_analysis_cache, analysis_log=_analysis_log,
        prompt_archive=_prompt_archive,
        earnings_predictor=_earnings_predictor, signal_expander=_signal_expander,
        rl_agent=_rl_agent, get_experience_store=_get_experience_store,
    )

    # Zyklus-Abschluss (Roadmap 4.4a, ausgelagert nach bot/cycle_close.py):
    # Headline-Digest, Snapshot, Archiv-Cleanup, Konsolen-Summary, Telegram-
    # Tagessummary/Quellen-Health, Tages-Aktionen, Live-Idle.
    cycle_close.finalize_cycle(
        portfolio, broker, tracker, phase_ctrl, archive,
        cycle_actions, _headline_results, _wl_total, _live,
        record_daily_actions,
    )
