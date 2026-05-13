from dataclasses import dataclass, field
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


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

    # Watchlist (US-Ticker-Symbole für yfinance/Alpaca; für XETRA ".DE" anhängen z.B. "SAP.DE")
    watchlist: List[str] = field(default_factory=lambda: [
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"
    ])

    # Risk management
    max_position_pct: float = 0.20   # Max 20 % des Portfolios pro Position
    stop_loss_pct: float = 0.07      # 7 % Stop-Loss
    take_profit_pct: float = 0.20    # 20 % Take-Profit

    # Sentiment thresholds (0–1)
    buy_threshold: float = 0.65
    sell_threshold: float = 0.35
    min_sources: int = 2             # Mindestanzahl Quellen vor Entscheidung

    # Schedule
    analysis_hour: int = 8           # Uhrzeit der täglichen Analyse (Lokalzeit)
    analysis_minute: int = 30

    # Claude model
    claude_model: str = "claude-opus-4-7"

    # Telegram notifications (optional)
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Portfolio phase settings
    growth_target_multiple: float = field(
        default_factory=lambda: float(os.getenv("GROWTH_TARGET_MULTIPLE", "3.0"))
    )  # Switch to distribution when portfolio = initial_capital × this
    monthly_distribution_eur: float = field(
        default_factory=lambda: float(os.getenv("MONTHLY_DISTRIBUTION_EUR", "500.0"))
    )  # Monthly withdrawal goal once in DISTRIBUTION phase
    distribution_buffer_months: int = field(
        default_factory=lambda: int(os.getenv("DISTRIBUTION_BUFFER_MONTHS", "6"))
    )  # Months of distributions kept as safety reserve


config = Config()
