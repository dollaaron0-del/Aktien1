from dataclasses import dataclass, field
from typing import List
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _parse_exchanges(raw: str) -> List[str]:
    known = {"XETRA", "NYSE", "NASDAQ", "TSE", "HKEX", "SSE", "LSE", "ASX"}
    return [e.strip().upper() for e in raw.split(",") if e.strip().upper() in known] or ["XETRA", "NYSE", "TSE"]


@dataclass
class Config:
    # API Keys
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    reddit_client_id: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", ""))
    reddit_client_secret: str = field(default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", ""))
    reddit_user_agent: str = field(default_factory=lambda: os.getenv("REDDIT_USER_AGENT", "StockSentimentBot/1.0"))
    newsapi_key: str = field(default_factory=lambda: os.getenv("NEWSAPI_KEY", ""))
    twitter_bearer_token: str = field(default_factory=lambda: os.getenv("TWITTER_BEARER_TOKEN", ""))
    quiver_api_key: str = field(default_factory=lambda: os.getenv("QUIVER_API_KEY", ""))

    # ── Intraday-Scan (drittes optionales Analysefenster) ────────────────────
    # Aktivieren: INTRADAY_SCAN_ENABLED=true in .env
    # Empfehlung: 17:30 UTC = 19:30 MESZ (während der US-Session)
    # Scannt die volle Watchlist + BenchList ein drittes Mal pro Tag.
    intraday_scan_enabled: bool = field(
        default_factory=lambda: os.getenv("INTRADAY_SCAN_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    intraday_scan_time: str = field(
        default_factory=lambda: os.getenv("INTRADAY_SCAN_TIME", "17:30")
    )
    # Interactive Brokers (TWS / IB Gateway)
    ibkr_host:      str = field(default_factory=lambda: os.getenv("IBKR_HOST",      "127.0.0.1"))
    ibkr_port:      int = field(default_factory=lambda: int(os.getenv("IBKR_PORT",  "7497")))
    ibkr_client_id: int = field(default_factory=lambda: int(os.getenv("IBKR_CLIENT_ID", "1")))
    ibkr_account:   str = field(default_factory=lambda: os.getenv("IBKR_ACCOUNT",   ""))

    # Broker mode: "paper", "ibkr"
    broker_mode: str = field(default_factory=lambda: os.getenv("BROKER_MODE", "paper"))
    initial_capital: float = field(default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "10000.0")))

    # Watchlist – aus .env lesen (WATCHLIST=AAPL,MSFT,NVDA,...) oder Standardliste
    watchlist: List[str] = field(default_factory=lambda: (
        [t.strip().upper() for t in os.getenv("WATCHLIST", "").split(",") if t.strip()]
        or ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
    ))

    # Risk management
    max_position_pct: float = 0.20
    stop_loss_pct: float = 0.07
    take_profit_pct: float = 0.20

    # Sentiment thresholds (0–1)
    buy_threshold: float = 0.65
    sell_threshold: float = 0.35
    min_sources: int = field(default_factory=lambda: int(os.getenv("MIN_SOURCES", "1")))

    # ── Marktbasierter Analyse-Zeitplan ─────────────────────────────────────
    # Welche Börsen beobachten? Analyse läuft je 30 Min vor Börseneröffnung.
    # Verfügbare Codes: XETRA NYSE NASDAQ TSE HKEX SSE LSE ASX
    market_exchanges: List[str] = field(
        default_factory=lambda: _parse_exchanges(os.getenv("MARKET_EXCHANGES", "XETRA,NYSE,TSE"))
    )
    # Vorlauf in Minuten vor Börseneröffnung
    market_lead_minutes: int = field(
        default_factory=lambda: int(os.getenv("MARKET_LEAD_MINUTES", "30"))
    )

    # Signale in der Warteschlange: Ablauf nach N Stunden
    signal_queue_max_age_hours: int = field(
        default_factory=lambda: int(os.getenv("SIGNAL_QUEUE_MAX_AGE_HOURS", "48"))
    )

    # Claude model
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    )

    # Telegram notifications (optional)
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Focus mode: WEALTH_BUILDING | INCOME | TARGET_GOAL
    focus_mode: str = field(default_factory=lambda: os.getenv("FOCUS_MODE", "WEALTH_BUILDING"))
    target_goal_amount: float = field(
        default_factory=lambda: float(os.getenv("TARGET_GOAL_AMOUNT", "0") or 0)
    )
    target_goal_date: str = field(default_factory=lambda: os.getenv("TARGET_GOAL_DATE", ""))

    # Portfolio phase settings
    growth_target_multiple: float = field(
        default_factory=lambda: float(os.getenv("GROWTH_TARGET_MULTIPLE", "3.0"))
    )
    monthly_distribution_eur: float = field(
        default_factory=lambda: float(os.getenv("MONTHLY_DISTRIBUTION_EUR", "500.0"))
    )
    distribution_buffer_months: int = field(
        default_factory=lambda: int(os.getenv("DISTRIBUTION_BUFFER_MONTHS", "6"))
    )

    # Risk filters
    block_earnings_days: int = field(
        default_factory=lambda: int(os.getenv("BLOCK_EARNINGS_DAYS", "5"))
    )
    max_sector_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_SECTOR_PCT", "0.40"))
    )

    # Kelly criterion sizing
    use_kelly_sizing: bool = field(
        default_factory=lambda: os.getenv("USE_KELLY_SIZING", "false").lower() in ("1", "true", "yes")
    )
    kelly_fraction: float = field(
        default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.25"))
    )

    # ── Margin / Hebel (progressives Tier-System) ─────────────────────────────
    # false = kein Hebel (Standard)
    # true  = Bot verdient sich Hebel durch Performance (Tier 1–4: 1.25×–2.00×)
    # Erfordert Alpaca Margin-Account (nicht Cash-Account).
    use_margin: bool = field(
        default_factory=lambda: os.getenv("USE_MARGIN", "false").lower() in ("1", "true", "yes")
    )
    # Nur bei dieser Konfidenz Margin nutzen (HIGH empfohlen)
    margin_min_confidence: str = field(
        default_factory=lambda: os.getenv("MARGIN_MIN_CONFIDENCE", "HIGH")
    )

    # Watchlist auto-scanner
    auto_scan_watchlist: bool = field(
        default_factory=lambda: os.getenv("AUTO_SCAN_WATCHLIST", "false").lower() in ("1", "true", "yes")
    )
    scan_max_picks: int = field(
        default_factory=lambda: int(os.getenv("SCAN_MAX_PICKS", "3"))
    )

    # ── Ollama (lokales KI-Modell, Mac mini M5) ──────────────────────────────
    # false = nur Claude API; true = Ollama filtert vor, Claude bestätigt
    ollama_enabled: bool = field(
        default_factory=lambda: os.getenv("OLLAMA_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434")
    )
    # Modell-Empfehlung: llama3.1:8b (16GB), qwen2.5:14b (24GB), llama3.3:70b (32GB)
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    )
    ollama_timeout: int = field(
        default_factory=lambda: int(os.getenv("OLLAMA_TIMEOUT", "30"))
    )

    # ── Einstiegs-Timing + Exit-Management ───────────────────────────────────
    # EMA21-Check: Kurs darf max. X% über EMA21 liegen (sonst: Conditional Entry)
    # 0.03 = 3% → bei >3% über EMA21 wird in Warteschlange gestellt, nicht sofort gekauft
    entry_ema_max_deviation: float = field(
        default_factory=lambda: float(os.getenv("ENTRY_EMA_MAX_DEVIATION", "0.03"))
    )
    # Partial Take-Profit: Bei X% Gewinn werden Y% der Position verkauft
    # Verbleibende Position läuft mit SL auf Breakeven weiter (Trailing)
    partial_tp_pct: float = field(
        default_factory=lambda: float(os.getenv("PARTIAL_TP_PCT", "0.10"))
    )
    partial_tp_sell_frac: float = field(
        default_factory=lambda: float(os.getenv("PARTIAL_TP_SELL_FRAC", "0.50"))
    )
    # Zweite Partial-TP-Stufe: bei X% Gewinn weitere 50% der verbleibenden Shares verkaufen
    partial_tp2_pct: float = field(
        default_factory=lambda: float(os.getenv("PARTIAL_TP2_PCT", "0.20"))
    )
    # Stop-Loss Re-Entry Sperre: N Tage nach SL-Auslösung kein Wiederkauf
    sl_cooldown_days: int = field(
        default_factory=lambda: int(os.getenv("SL_COOLDOWN_DAYS", "5"))
    )
    # Mindest-Trades bevor RL-Agent und adaptive Threshold-Anpassung aktiv werden
    min_trades_for_adaptive: int = field(
        default_factory=lambda: int(os.getenv("MIN_TRADES_FOR_ADAPTIVE", "50"))
    )
    # Volumen-Bestätigung: heutiges Volumen muss mind. X% des 20d-Durchschnitts betragen
    volume_confirm_ratio: float = field(
        default_factory=lambda: float(os.getenv("VOLUME_CONFIRM_RATIO", "0.80"))
    )
    # News-Staleness: Kurs bereits X% gestiegen seit Newsveröffentlichung → Signal veraltet
    news_stale_pct: float = field(
        default_factory=lambda: float(os.getenv("NEWS_STALE_PCT", "0.05"))
    )

    # ── Hedge / Rezessions-Absicherung ───────────────────────────────────────
    # Inverse ETFs kaufen wenn Marktregime BEAR oder CRISIS erreicht
    enable_hedging: bool = field(
        default_factory=lambda: os.getenv("ENABLE_HEDGING", "true").lower() in ("1", "true", "yes")
    )
    # Ab welchem Regime hedgen: NEUTRAL | BEAR | CRISIS
    hedge_from_regime: str = field(
        default_factory=lambda: os.getenv("HEDGE_FROM_REGIME", "BEAR")
    )
    # Maximaler Portfolio-Anteil für alle Krisen-Instrumente (Inverse ETFs + Safe Havens + Defensive)
    max_hedge_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_HEDGE_PCT", "0.35"))
    )
    # Wie oft Regime-Check läuft (in Stunden, zwischen den Vollanalysen)
    regime_check_interval_hours: int = field(
        default_factory=lambda: int(os.getenv("REGIME_CHECK_INTERVAL_HOURS", "6"))
    )


    # ── TradingView Webhook ───────────────────────────────────────────────────
    tradingview_webhook_enabled: bool = field(
        default_factory=lambda: os.getenv("TRADINGVIEW_WEBHOOK_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    tradingview_webhook_port: int = field(
        default_factory=lambda: int(os.getenv("TRADINGVIEW_WEBHOOK_PORT", "8080"))
    )
    tradingview_webhook_secret: str = field(
        default_factory=lambda: os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "")
    )

    # ── Multi-Timeframe-Bestätigung ───────────────────────────────────────────
    # true  = BUY erst wenn 2 verschiedene Timeframes bestätigen (z.B. 5m + 1h)
    # false = jedes Signal sofort ausführen (wie bisher)
    mtf_confirmation_enabled: bool = field(
        default_factory=lambda: os.getenv("MTF_CONFIRMATION_ENABLED", "false").lower() in ("1", "true", "yes")
    )

    # ── VIX Risk Management ───────────────────────────────────────────────────
    # VIX > vix_pause_threshold: Positionsgrößen reduziert
    # VIX > vix_block_threshold: keine neuen Käufe
    vix_risk_enabled: bool = field(
        default_factory=lambda: os.getenv("VIX_RISK_ENABLED", "true").lower() in ("1", "true", "yes")
    )

    # ── Earnings-Schutz ───────────────────────────────────────────────────────
    # Offene Positionen werden N Tage vor Earnings automatisch geschlossen
    earnings_protection_enabled: bool = field(
        default_factory=lambda: os.getenv("EARNINGS_PROTECTION_ENABLED", "true").lower() in ("1", "true", "yes")
    )
    earnings_protect_days: int = field(
        default_factory=lambda: int(os.getenv("EARNINGS_PROTECT_DAYS", "2"))
    )

    # ── Korrelations-Signale ──────────────────────────────────────────────────
    # Wenn ein führender ETF signalisiert, folgen korrelierte Aktien automatisch
    correlation_signals_enabled: bool = field(
        default_factory=lambda: os.getenv("CORRELATION_SIGNALS_ENABLED", "true").lower() in ("1", "true", "yes")
    )

    # ── Exploration Mode ──────────────────────────────────────────────────────
    # Lockere Parameter für die Datensammlungsphase (Paper-Trading).
    # Ein: EXPLORATION_MODE=true in .env  oder  python main.py --exploration on
    # Aus: EXPLORATION_MODE=false         oder  python main.py --exploration off
    # Wenn aktiv, überschreiben die expl_* Werte die normalen Schwellwerte.
    exploration_mode: bool = field(
        default_factory=lambda: os.getenv("EXPLORATION_MODE", "false").lower() in ("1", "true", "yes")
    )
    # Explorations-Parameter (nur aktiv wenn exploration_mode=True)
    expl_buy_threshold:    float = field(default_factory=lambda: float(os.getenv("EXPL_BUY_THRESHOLD",    "0.55")))
    expl_min_sources:      int   = field(default_factory=lambda: int(os.getenv("EXPL_MIN_SOURCES",       "1")))
    expl_max_position_pct: float = field(default_factory=lambda: float(os.getenv("EXPL_MAX_POSITION_PCT", "0.25")))
    expl_max_daily_loss:   float = field(default_factory=lambda: float(os.getenv("EXPL_MAX_DAILY_LOSS",   "0.08")))

    # ── Turbo Mode (nur Paper-Trading!) ──────────────────────────────────────
    # Maximaler Gewinnversuch ohne Sicherheitsgrenzen – nur zur Analyse ob
    # aggressive Strategien profitabel sind. NUR mit BROKER_MODE=paper nutzbar.
    # Ein: TURBO_MODE=true in .env
    turbo_mode: bool = field(
        default_factory=lambda: os.getenv("TURBO_MODE", "false").lower() in ("1", "true", "yes")
    )
    # Wie viel % des Portfolios pro Position (aggressiv: 40 %)
    turbo_max_position_pct: float = field(
        default_factory=lambda: float(os.getenv("TURBO_MAX_POSITION_PCT", "0.40"))
    )
    # Kaufschwelle Sentiment (aggressiv: 0.45 – auch schwache Signale kaufen)
    turbo_buy_threshold: float = field(
        default_factory=lambda: float(os.getenv("TURBO_BUY_THRESHOLD", "0.45"))
    )
    # Stop-Loss enger (aggressiv: 12 % um Gewinne laufen zu lassen)
    turbo_stop_loss_pct: float = field(
        default_factory=lambda: float(os.getenv("TURBO_STOP_LOSS_PCT", "0.12"))
    )
    # Take-Profit weiter (aggressiv: 40 %)
    turbo_take_profit_pct: float = field(
        default_factory=lambda: float(os.getenv("TURBO_TAKE_PROFIT_PCT", "0.40"))
    )
    # Max. gleichzeitige Positionen
    turbo_max_positions: int = field(
        default_factory=lambda: int(os.getenv("TURBO_MAX_POSITIONS", "15"))
    )

    # ── Krypto ───────────────────────────────────────────────────────────────
    # Krypto-Handel via Alpaca (gleiche API-Credentials, kein Extra-Account nötig).
    # CRYPTO_ENABLED=true  aktiviert Krypto im normalen Bot-Zyklus.
    # --crypto-scan läuft immer manuell (unabhängig von CRYPTO_ENABLED).
    crypto_enabled: bool = field(
        default_factory=lambda: os.getenv("CRYPTO_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    # Welche Coins handeln? Format: BTC,ETH,SOL (ohne /USD – wird automatisch ergänzt)
    crypto_watchlist: List[str] = field(
        default_factory=lambda: [c.strip().upper() for c in os.getenv("CRYPTO_WATCHLIST", "BTC,ETH,SOL").split(",") if c.strip()]
    )
    # Maximaler Portfolio-Anteil für alle Krypto-Positionen zusammen
    crypto_max_portfolio_pct: float = field(
        default_factory=lambda: float(os.getenv("CRYPTO_MAX_PORTFOLIO_PCT", "0.15"))
    )
    # Strengere Stop-Loss für Krypto (höhere Volatilität)
    crypto_stop_loss_pct: float = field(
        default_factory=lambda: float(os.getenv("CRYPTO_STOP_LOSS_PCT", "0.10"))
    )
    crypto_take_profit_pct: float = field(
        default_factory=lambda: float(os.getenv("CRYPTO_TAKE_PROFIT_PCT", "0.25"))
    )

    # ── Europäische Aktien ────────────────────────────────────────────────────
    # EU-Aktien im XETRA/LSE-Format (z.B. SAP.DE, ASML.AS, LVMH.PA).
    # EU_STOCKS_ENABLED=true fügt EU-Watchlist zum normalen Bot-Zyklus hinzu.
    # --eu-scan läuft immer manuell.
    eu_stocks_enabled: bool = field(
        default_factory=lambda: os.getenv("EU_STOCKS_ENABLED", "false").lower() in ("1", "true", "yes")
    )
    # Eigene EU-Watchlist (leer = Scanner-Empfehlungen nutzen)
    eu_watchlist: List[str] = field(
        default_factory=lambda: [t.strip().upper() for t in os.getenv("EU_WATCHLIST", "").split(",") if t.strip()]
    )


config = Config()


def validate_config() -> None:
    """
    Prüft kritische Konfigurationswerte beim Start.
    Gibt Warnungen aus und beendet das Programm bei fatalen Fehlern.
    """
    errors:   List[str] = []
    warnings: List[str] = []

    # ── Kritisch: ohne diese Keys läuft gar nichts ────────────────────────────
    if not config.anthropic_api_key:
        errors.append("ANTHROPIC_API_KEY fehlt – Claude-Analyse nicht möglich.")

    # ── Broker-spezifisch ─────────────────────────────────────────────────────
    if config.broker_mode == "ibkr":
        try:
            import ib_insync  # noqa: F401
        except ImportError:
            errors.append(
                "BROKER_MODE=ibkr, aber ib_insync fehlt. Bitte: pip install ib_insync"
            )

    # ── Warnungen für optionale aber empfohlene Keys ──────────────────────────
    if not config.telegram_bot_token or not config.telegram_chat_id:
        warnings.append("TELEGRAM_BOT_TOKEN / CHAT_ID fehlen – keine Push-Benachrichtigungen.")
    if not config.newsapi_key:
        warnings.append("NEWSAPI_KEY fehlt – NewsAPI-Quelle deaktiviert.")
    if not config.reddit_client_id:
        warnings.append("REDDIT_CLIENT_ID fehlt – Reddit-Quelle deaktiviert.")
    if not config.quiver_api_key:
        warnings.append(
            "QUIVER_API_KEY fehlt – Congressional-Trades mit Ausschuss-Kontext deaktiviert. "
            "Kostenlosen Key auf quiverquant.com registrieren."
        )

    # ── Wertebereich-Prüfungen ────────────────────────────────────────────────
    if not (0.0 < config.stop_loss_pct < 1.0):
        errors.append(f"STOP_LOSS_PCT={config.stop_loss_pct} ungültig (muss 0–1 sein).")
    if not (0.0 < config.take_profit_pct < 5.0):
        errors.append(f"TAKE_PROFIT_PCT={config.take_profit_pct} ungültig.")
    if not (0.0 < config.max_position_pct <= 1.0):
        errors.append(f"MAX_POSITION_PCT={config.max_position_pct} ungültig.")
    if not (0.0 < config.buy_threshold <= 1.0):
        errors.append(f"BUY_THRESHOLD={config.buy_threshold} ungültig (muss 0–1 sein).")
    if config.initial_capital <= 0:
        errors.append(f"INITIAL_CAPITAL={config.initial_capital} ungültig.")

    if config.focus_mode == "TARGET_GOAL":
        if not config.target_goal_amount or config.target_goal_amount <= 0:
            warnings.append("FOCUS_MODE=TARGET_GOAL aber TARGET_GOAL_AMOUNT nicht gesetzt.")
        if not config.target_goal_date:
            warnings.append("FOCUS_MODE=TARGET_GOAL aber TARGET_GOAL_DATE nicht gesetzt.")

    if config.kelly_fraction > 0.5:
        warnings.append(
            f"KELLY_FRACTION={config.kelly_fraction} ist sehr hoch (>0.5) – "
            f"erhöhtes Risiko. Empfehlung: 0.25."
        )

    # ── Ausgabe ───────────────────────────────────────────────────────────────
    for w in warnings:
        print(f"[CONFIG WARNUNG] {w}", file=sys.stderr)

    if errors:
        print("\n[CONFIG FEHLER] Programm kann nicht starten:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)
