"""Read-only HTTP status endpoint for the trading bot.

Purpose: give the Overlay (and any other dashboard) a single JSON snapshot of
what the bot is doing, without opening a broker connection or touching the
live trading process. Everything here is a pure read of files / SQLite the
bot already writes:

  - system/live_status.py    -> data/bot_status.json     (runtime state, cycle)
  - portfolio/performance_tracker.py -> data/*.db        (value snapshots, trades)
  - portfolio/portfolio.py   -> data/portfolio.db        (open positions, cash)
  - data/bot_score.json                                  (bot score)
  - analyzers/llm_client.py  -> active LLM provider

Deliberately stdlib-only (`http.server`): no new dependency in the trading
bot's venv. Runs as its own process, so a crash or slow read here can never
affect trading.

Run:  python -m monitoring.status_api            # 127.0.0.1:8607
      STATUS_API_PORT=9000 python -m monitoring.status_api

Routes:
  GET /health  -> {"status": "ok"}
  GET /status  -> full snapshot (see docs/STATUS_API.md for the schema)

Every section is isolated: a failing one lands in `errors[]` and the rest of
the payload is still returned. `ok` is false only if nothing could be read.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

DEFAULT_PORT = int(os.getenv("STATUS_API_PORT", "8607"))
DEFAULT_HOST = os.getenv("STATUS_API_HOST", "127.0.0.1")

# A runtime status not refreshed for this long is treated as a crash leftover
# (mirrors the dashboard's own staleness view).
STALE_AFTER_SECONDS = 30 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _runtime_section(errors: List[str]) -> Dict[str, Any]:
    """Live cycle state from data/bot_status.json."""
    try:
        from system import live_status

        raw = live_status.read_status() or {}
        age = live_status.status_age_seconds(raw)
        return {
            "state": raw.get("state"),
            "phase": raw.get("phase"),
            "cycle_started_at": raw.get("cycle_started_at"),
            "progress": {
                "idx": raw.get("idx"),
                "total": raw.get("total"),
                "ticker": raw.get("ticker"),
            },
            "detail": raw.get("detail"),
            "next_run": raw.get("next_run"),
            "updated_at": raw.get("updated_at"),
            "pid": raw.get("pid"),
            "age_seconds": round(age, 1) if age is not None else None,
            "stale": (age is not None and age > STALE_AFTER_SECONDS),
        }
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f"runtime: {exc}")
        return {}


def _portfolio_section(errors: List[str]) -> Dict[str, Any]:
    """Latest value snapshot + open positions. No broker connection."""
    out: Dict[str, Any] = {}
    try:
        from portfolio.performance_tracker import PerformanceTracker

        history = PerformanceTracker().get_value_history(days=3)
        if history:
            last = history[-1]
            out.update(
                {
                    "total_value": last.get("total_value"),
                    "cash": last.get("cash"),
                    "n_positions": last.get("n_positions"),
                    "daily_pnl": last.get("daily_pnl"),
                    "as_of": last.get("snapshot_date"),
                }
            )
    except Exception as exc:
        errors.append(f"portfolio.snapshot: {exc}")

    try:
        from portfolio.portfolio import Portfolio
        from config import config

        pf = Portfolio(config.initial_capital)
        positions = pf.all_positions()
        try:
            out["cash_live"] = round(pf.cash, 2)
        except Exception:
            pass
        out["positions"] = [
            {
                "ticker": p.ticker,
                "shares": p.shares,
                "entry_price": p.entry_price,
                "entry_date": p.entry_date,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "currency": getattr(p, "currency", "USD"),
            }
            for p in positions.values()
        ]
        out.setdefault("n_positions", len(positions))
    except Exception as exc:
        errors.append(f"portfolio.positions: {exc}")

    return out


def _phase_section(total_value: Any, errors: List[str]) -> Dict[str, Any]:
    try:
        from portfolio.phase_controller import PhaseController
        from config import config

        value = float(total_value) if total_value is not None else float(config.initial_capital)
        return PhaseController(config.initial_capital).get_info(value)
    except Exception as exc:
        errors.append(f"phase: {exc}")
        return {}


def _score_section(errors: List[str]) -> Dict[str, Any]:
    try:
        path = os.path.join(_PROJECT_DIR, "data", "bot_score.json")
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return {
            "current": raw.get("current"),
            "peak": raw.get("peak"),
            "trades_scored": raw.get("trades_scored"),
            "total_earned": raw.get("total_earned"),
            "total_lost": raw.get("total_lost"),
            "milestones": raw.get("milestones"),
        }
    except FileNotFoundError:
        return {}
    except Exception as exc:
        errors.append(f"score: {exc}")
        return {}


def _risk_section(errors: List[str]) -> Dict[str, Any]:
    try:
        from portfolio.performance_tracker import PerformanceTracker

        return PerformanceTracker().get_risk_metrics(days=90)
    except Exception as exc:
        errors.append(f"risk: {exc}")
        return {}


def _recent_trades_section(errors: List[str]) -> List[Dict[str, Any]]:
    try:
        from portfolio.performance_tracker import PerformanceTracker

        rows = PerformanceTracker().get_recent_trades(n=10)
        return [
            {
                "ticker": r.get("ticker"),
                "direction": r.get("direction"),
                "entry_price": r.get("entry_price"),
                "exit_price": r.get("exit_price"),
                "exit_reason": r.get("exit_reason"),
                "pnl_pct": r.get("pnl_pct"),
                "hold_days": r.get("hold_days"),
                "outcome": r.get("outcome"),
                "closed_at": r.get("predicted_at"),
            }
            for r in rows
        ]
    except Exception as exc:
        errors.append(f"recent_trades: {exc}")
        return []


def _llm_section(errors: List[str]) -> Dict[str, Any]:
    try:
        from analyzers import llm_client
        from config import config

        provider = llm_client._provider()
        claude_model = getattr(config, "claude_model", None)
        if provider == "gemini":
            model = llm_client._map_model(claude_model)
        else:
            model = claude_model
        return {"provider": provider, "model": model, "available": llm_client.available()}
    except Exception as exc:
        errors.append(f"llm: {exc}")
        return {}


def build_status() -> Dict[str, Any]:
    """Assemble the full snapshot. Never raises."""
    errors: List[str] = []
    runtime = _runtime_section(errors)
    portfolio = _portfolio_section(errors)
    phase = _phase_section(portfolio.get("total_value"), errors)
    payload = {
        "ok": True,
        "service": "aktien-bot",
        "generated_at": _now_iso(),
        "runtime": runtime,
        "portfolio": portfolio,
        "phase": phase,
        "score": _score_section(errors),
        "risk": _risk_section(errors),
        "recent_trades": _recent_trades_section(errors),
        "llm": _llm_section(errors),
        "errors": errors,
    }
    # Only a hard failure (nothing readable at all) flips ok to false.
    payload["ok"] = bool(runtime or portfolio or payload["score"])
    return payload


class _Handler(BaseHTTPRequestHandler):
    server_version = "AktienStatusAPI/1.0"

    def _send(self, code: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._send(200, {"status": "ok"})
        elif path == "/status":
            try:
                self._send(200, build_status())
            except Exception as exc:  # pragma: no cover - build_status shouldn't raise
                self._send(500, {"ok": False, "error": str(exc)})
        else:
            self._send(404, {"error": "not_found", "path": path})

    def log_message(self, *args: Any) -> None:  # keep the bot's stdout clean
        return


def main() -> None:
    host, port = DEFAULT_HOST, DEFAULT_PORT
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"aktien status API on http://{host}:{port}  (GET /status, /health)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
