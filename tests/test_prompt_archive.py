"""
Tests für analyzers/prompt_archive.py (Roadmap 1.4d: KI-Prompt-Archiv).
"""
from analyzers.prompt_archive import PromptArchive


def make_archive(tmp_path):
    return PromptArchive(db_path=str(tmp_path / "prompt_archive.db"))


def test_init_creates_table(tmp_path):
    a = make_archive(tmp_path)
    cols = {r[1] for r in a._conn.execute("PRAGMA table_info(prompts)")}
    assert cols == {"id", "analysis_id", "ticker", "created_at", "model",
                     "system_prompt", "user_prompt", "response_text"}


def test_store_and_get_by_analysis_id(tmp_path):
    a = make_archive(tmp_path)
    row_id = a.store(
        analysis_id=42, ticker="AAPL", model="claude-sonnet-5",
        system_prompt="SYS", user_prompt="USER", response_text='{"recommendation": "BUY"}',
    )
    assert row_id == 1
    entry = a.get_by_analysis_id(42)
    assert entry["ticker"] == "AAPL"
    assert entry["model"] == "claude-sonnet-5"
    assert entry["system_prompt"] == "SYS"
    assert entry["user_prompt"] == "USER"
    assert entry["response_text"] == '{"recommendation": "BUY"}'


def test_get_by_analysis_id_unknown_returns_none(tmp_path):
    a = make_archive(tmp_path)
    assert a.get_by_analysis_id(999) is None


def test_get_by_analysis_id_returns_latest_on_duplicate(tmp_path):
    """Falls je Analyse mehrfach gespeichert würde (sollte nicht vorkommen),
    liefert get_by_analysis_id konsequent den jüngsten Eintrag."""
    a = make_archive(tmp_path)
    a.store(analysis_id=7, ticker="MSFT", model="m1",
            system_prompt="s1", user_prompt="u1", response_text="r1")
    a.store(analysis_id=7, ticker="MSFT", model="m2",
            system_prompt="s2", user_prompt="u2", response_text="r2")
    assert a.get_by_analysis_id(7)["response_text"] == "r2"


def test_store_is_fail_open_on_closed_connection(tmp_path):
    """Ein Archiv-Fehler darf die Analyse nie zum Absturz bringen (fail-open)."""
    a = make_archive(tmp_path)
    a._conn.close()
    assert a.store(analysis_id=1, ticker="X", model="m",
                    system_prompt="s", user_prompt="u", response_text="r") is None
    assert a.get_by_analysis_id(1) is None
