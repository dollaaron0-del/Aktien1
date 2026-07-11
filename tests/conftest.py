"""
Shared pytest fixtures for the Aktien test suite.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

# Ensure the project root is on sys.path so imports like `from logger import ...` work
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Decision-Log (prozessweites Singleton) in Tests IMMER in ein Temp-File
# umlenken — Seams wie process_signal_queue loggen sonst in die echte data/.
os.environ.setdefault(
    "DECISION_LOG_PATH",
    os.path.join(tempfile.gettempdir(), f"decision_log_test_{os.getpid()}.db"),
)


@pytest.fixture(autouse=True)
def _isolate_sl_cooldown(tmp_path, monkeypatch):
    """SL-Cooldown-Datei IMMER in ein Temp-File umlenken: der Executor-Hook
    (Clean-SL → record()) feuert sonst aus beliebigen Exit-Tests heraus in die
    echte data/sl_cooldown.json und sperrt dort Ticker für Folge-Tests."""
    import analyzers.sl_cooldown as slc_mod
    monkeypatch.setattr(slc_mod, "_FILE", str(tmp_path / "sl_cooldown_test.json"))


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Creates a temporary directory and points all module-level DATA_FILE /
    _CACHE_FILE / _WEIGHTS_FILE path constants to it.
    Returns the tmp_path object.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Patch the env var used by circuit_breaker limits (keep defaults)
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "0.05")
    monkeypatch.setenv("MAX_DRAWDOWN_PCT", "0.15")

    return tmp_path


@pytest.fixture()
def fresh_portfolio(tmp_data_dir, monkeypatch):
    """
    Returns a Portfolio instance backed by a temp SQLite DB (10 000 cash).
    Patches portfolio.portfolio.PORTFOLIO_DB so no real data/portfolio.db is touched.
    """
    import portfolio.portfolio as port_mod

    (tmp_data_dir / "data").mkdir(exist_ok=True)
    db_file = str(tmp_data_dir / "data" / "portfolio.db")
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)

    from portfolio.portfolio import Portfolio
    return Portfolio(initial_capital=10_000.0)


@pytest.fixture()
def sample_position():
    """A realistic Position fixture for AAPL."""
    from portfolio.portfolio import Position
    return Position(
        ticker="AAPL",
        shares=10,
        entry_price=150.0,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=138.0,
        take_profit=180.0,
        target_hold_days=14,
    )
