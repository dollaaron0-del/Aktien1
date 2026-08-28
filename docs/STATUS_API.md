# Status API (`monitoring/status_api.py`)

Read-only HTTP snapshot of the bot for the Overlay dashboard (and anything
else that wants one JSON call instead of scraping Streamlit).

- **stdlib only** (`http.server`) — no new dependency in the trading venv.
- **Own process** — a crash or slow read here cannot touch trading.
- **No broker connection, no network** — pure reads of files / SQLite the
  bot already writes (`data/bot_status.json`, `data/portfolio.db`,
  `data/*.db` snapshots, `data/bot_score.json`).
- Every section is isolated: a failure lands in `errors[]`, the rest of the
  payload still returns. `ok` is `false` only if *nothing* was readable.

## Run

```bash
python -m monitoring.status_api                 # 127.0.0.1:8607
STATUS_API_PORT=9000 python -m monitoring.status_api
```

Env: `STATUS_API_PORT` (default `8607`), `STATUS_API_HOST` (default `127.0.0.1`).

As a service: see `deploy/aktien-status.service` (not installed by default).

## Routes

| Route | Response |
| --- | --- |
| `GET /health` | `{"status": "ok"}` |
| `GET /status` | full snapshot (below) |
| anything else | `404 {"error": "not_found"}` |

## `/status` schema

```jsonc
{
  "ok": true,                        // false only if nothing was readable
  "service": "aktien-bot",
  "generated_at": "2026-08-28T06:24:46+00:00",

  "runtime": {                       // data/bot_status.json (system/live_status.py)
    "state": "idle",                 // "idle" | "cycle"
    "phase": null,
    "cycle_started_at": null,
    "progress": { "idx": null, "total": null, "ticker": null },
    "detail": null,
    "next_run": "2026-08-28T08:26:06",
    "updated_at": "2026-08-28T06:24:06",
    "pid": 320317,
    "age_seconds": 40.1,
    "stale": false                   // true if age_seconds > 30 min (crash leftover)
  },

  "portfolio": {                     // latest portfolio_snapshots row + open positions
    "total_value": 491693.69,
    "cash": 60711.89,
    "cash_live": 60711.89,           // from portfolio.db directly
    "n_positions": 17,
    "daily_pnl": -14828.04,
    "as_of": "2026-08-28",
    "positions": [
      { "ticker": "BLK", "shares": 38.35, "entry_price": 1049.2,
        "entry_date": "...", "stop_loss": 1112.15, "take_profit": 1280.02,
        "currency": "USD" }
    ]
  },

  "phase": {                         // portfolio/phase_controller.py get_info()
    "phase": "GROWTH",               // "GROWTH" | "DISTRIBUTION"
    "portfolio_value": 491693.69,
    "initial_capital": 10000.0,
    "growth_target": 3000000.0,
    "progress_pct": 0.0,
    "remaining_to_goal": 2508306.31
  },

  "score": {                         // data/bot_score.json
    "current": 60.61, "peak": 66.72,
    "trades_scored": 6, "total_earned": 18.62, "total_lost": 8.01,
    "milestones": [10, 25, 40, 60]
  },

  "risk": {                          // performance_tracker.get_risk_metrics(90d), {} if < 5 snapshots
    "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
    "max_drawdown": 0.0, "avg_daily_return": 0.0, "period_days": 90
  },

  "recent_trades": [                 // performance_tracker.get_recent_trades(10); [] if none closed
    { "ticker": "TSM", "direction": "long", "entry_price": 1.0, "exit_price": 1.05,
      "exit_reason": "TP", "pnl_pct": 4.8, "hold_days": 6, "outcome": "win",
      "closed_at": "2026-06-01T14:19" }
  ],

  "llm": {                           // analyzers/llm_client.py
    "provider": "gemini",            // "gemini" | "anthropic"
    "model": "gemini-3.1-flash-lite",
    "available": true
  },

  "errors": []                       // e.g. ["risk: no such table: ...", ...]
}
```

## Notes / follow-ups

- `recent_trades` reads the `predictions` table (`outcome IS NOT NULL`). If
  that table has been pruned it returns `[]` — not an error.
- If you later want live-marked position values, add an opt-in broker read;
  keep it off the default path so `/status` stays connection-free.
- FastAPI could replace `http.server` if OpenAPI docs are wanted — not worth
  a new venv dependency for two routes.
