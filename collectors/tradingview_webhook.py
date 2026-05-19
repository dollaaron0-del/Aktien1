"""
TradingView Webhook Receiver

Empfängt Alerts von TradingView Pine Script Strategien und speist sie
als Signale in die bot-eigene SignalQueue ein.

Setup:
  1. TRADINGVIEW_WEBHOOK_ENABLED=true in .env setzen
  2. TRADINGVIEW_WEBHOOK_SECRET=<geheimes_wort> setzen
  3. Bot starten – Webhook läuft auf Port TRADINGVIEW_WEBHOOK_PORT (Standard: 8080)
  4. Port per ngrok/Frp/Router-Portweiterleitung öffentlich erreichbar machen
  5. In TradingView Alert: Webhook URL = http://<deine-ip>:8080/webhook/tradingview

Erwartetes JSON von TradingView:
  {
    "secret":    "dein_geheimes_wort",
    "ticker":    "{{ticker}}",
    "action":    "BUY",            -- BUY | SELL | SKIP
    "price":     {{close}},
    "score":     0.75,             -- 0.0–1.0 (optional, default 0.70 bei BUY)
    "confidence":"HIGH",           -- HIGH | MEDIUM | LOW (optional)
    "rationale": "EMA crossover",  -- optional
    "hold_days": 14,               -- optional, default 14
    "strategy":  "EMA_Cross"       -- Name der Strategie (optional)
  }
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

_server_thread: Optional[threading.Thread] = None
_shutdown_event = threading.Event()


def start_webhook_server(signal_queue, port: int = 8080, secret: str = "") -> None:
    """Startet den Webhook-Server in einem Background-Thread."""
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        log.info("TradingView Webhook-Server läuft bereits auf Port %d", port)
        return

    _server_thread = threading.Thread(
        target=_run_server,
        args=(signal_queue, port, secret),
        daemon=True,
        name="tradingview-webhook",
    )
    _server_thread.start()
    log.info("TradingView Webhook-Server gestartet auf Port %d", port)


def _run_server(signal_queue, port: int, secret: str) -> None:
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        log.error("Flask nicht installiert – 'pip install flask' ausführen")
        return

    app = Flask("tradingview_webhook")
    log.getLogger("werkzeug").setLevel(logging.WARNING)  # Flask-Logs reduzieren

    @app.route("/webhook/tradingview", methods=["POST"])
    def receive_alert():
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400

        # Sicherheits-Check
        if secret and data.get("secret") != secret:
            log.warning("Webhook: ungültiges Secret von %s", request.remote_addr)
            return jsonify({"error": "Unauthorized"}), 401

        result = _process_signal(data, signal_queue)
        return jsonify(result), 200 if result.get("ok") else 400

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "tradingview-webhook"}), 200

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def _process_signal(data: dict, signal_queue) -> dict:
    """Verarbeitet ein eingehendes TradingView-Signal."""
    ticker = (data.get("ticker") or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "ticker fehlt"}

    action = (data.get("action") or "").upper()
    if action not in ("BUY", "SELL", "SKIP"):
        return {"ok": False, "error": f"Ungültige action: {action}"}

    if action != "BUY":
        log.info("TradingView [%s]: %s – kein Queue-Eintrag nötig", ticker, action)
        return {"ok": True, "queued": False, "action": action}

    # Standardwerte wenn TradingView keine optionalen Felder sendet
    score      = float(data.get("score", 0.70))
    confidence = (data.get("confidence") or "MEDIUM").upper()
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        confidence = "MEDIUM"
    price      = data.get("price")
    rationale  = data.get("rationale") or f"TradingView Signal: {data.get('strategy', 'Unbekannte Strategie')}"
    hold_days  = int(data.get("hold_days") or 14)
    strategy   = data.get("strategy") or "TradingView"

    sig_id = signal_queue.enqueue(
        ticker=ticker,
        sentiment_score=score,
        confidence=confidence,
        target_price=None,
        direction="BULLISH",
        entry_rationale=f"[{strategy}] {rationale}",
        key_catalysts=[f"TradingView-Alert: {strategy}"],
        risk_factors=[],
        sources_used=1,
        sources_breakdown={"tradingview": 1},
        suggested_hold_days=hold_days,
    )

    log.info(
        "TradingView [%s]: BUY-Signal in Queue eingereiht (ID #%d, Score: %.2f, %s)",
        ticker, sig_id, score, confidence,
    )
    return {"ok": True, "queued": True, "signal_id": sig_id, "ticker": ticker}
