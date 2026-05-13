from dataclasses import dataclass, field
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


def _parse_times(raw: str) -> List[str]:
    """Parses comma-separated HH:MM strings, falls back to ['08:30']."""
    times = [t.strip() for t in raw.split(",") if t.strip()]
    valid = []
    for t in times:
        parts = t.split(":")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            valid.append(f"{int(parts[0]):02d}:{int(parts[1]):02d}")
    return valid or ["08:30"]


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

    # ── Analyse-Zeitplan ──────────────────────────────────────────────────────
    # Vollanalyse (Claude + alle Quellen) mehrmals täglich, z.B. "08:30,12:00,16:00"
    analysis_times: List[str] = field(
        default_factory=lambda: _parse_times(os.getenv("ANALYSIS_TIMES", "08:30,12:00,16:00"))
    )
    # Legacy single-time fallback (wird ignoriert wenn ANALYSIS_TIMES gesetzt)
    analysis_hour: int = 8
    analysis_minute: int = 30

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


config = Config()
