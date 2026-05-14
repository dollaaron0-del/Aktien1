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
    alpaca_api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    alpaca_base_url: str = field(default_factory=lambda: os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"))

    # Broker mode: "paper", "alpaca", "ibkr"
    broker_mode: str = field(default_factory=lambda: os.getenv("BROKER_MODE", "paper"))
    initial_capital: float = field(default_factory=lambda: float(os.getenv("INITIAL_CAPITAL", "10000.0")))

    # Watchlist
    watchlist: List[str] = field(default_factory=lambda: [
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"
    ])

    # Risk management
    max_position_pct: float = 0.20
    stop_loss_pct: float = 0.07
    take_profit_pct: float = 0.20

    # Sentiment thresholds (0–1)
    buy_threshold: float = 0.65
    sell_threshold: float = 0.35
    min_sources: int = 2

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

    # Stündlicher Social-Scan aktivieren (Reddit + StockTwits, ohne Claude)
    enable_social_scan: bool = field(
        default_factory=lambda: os.getenv("ENABLE_SOCIAL_SCAN", "true").lower() in ("1", "true", "yes")
    )
    # Signale in der Warteschlange: Ablauf nach N Stunden
    signal_queue_max_age_hours: int = field(
        default_factory=lambda: int(os.getenv("SIGNAL_QUEUE_MAX_AGE_HOURS", "48"))
    )

    # Claude model
    claude_model: str = "claude-opus-4-7"

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

    # Watchlist auto-scanner
    auto_scan_watchlist: bool = field(
        default_factory=lambda: os.getenv("AUTO_SCAN_WATCHLIST", "false").lower() in ("1", "true", "yes")
    )
    scan_max_picks: int = field(
        default_factory=lambda: int(os.getenv("SCAN_MAX_PICKS", "3"))
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
    # Maximaler Portfolio-Anteil für Hedge-Positionen (0.20 = 20%)
    max_hedge_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_HEDGE_PCT", "0.20"))
    )
    # Wie oft Regime-Check läuft (in Stunden, zwischen den Vollanalysen)
    regime_check_interval_hours: int = field(
        default_factory=lambda: int(os.getenv("REGIME_CHECK_INTERVAL_HOURS", "6"))
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
    if config.broker_mode == "alpaca":
        if not config.alpaca_api_key or not config.alpaca_secret_key:
            errors.append(
                "BROKER_MODE=alpaca, aber ALPACA_API_KEY / ALPACA_SECRET_KEY fehlen."
            )

    # ── Warnungen für optionale aber empfohlene Keys ──────────────────────────
    if not config.telegram_bot_token or not config.telegram_chat_id:
        warnings.append("TELEGRAM_BOT_TOKEN / CHAT_ID fehlen – keine Push-Benachrichtigungen.")
    if not config.newsapi_key:
        warnings.append("NEWSAPI_KEY fehlt – NewsAPI-Quelle deaktiviert.")
    if not config.reddit_client_id:
        warnings.append("REDDIT_CLIENT_ID fehlt – Reddit-Quelle deaktiviert.")

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
