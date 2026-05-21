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

import collections
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import List, Optional, Dict

_SELL_FILE  = os.path.join(os.path.dirname(__file__), "..", "data", "tv_sell_signals.json")
_MACRO_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "tv_macro_events.json")

from logger import get_logger

log = get_logger(__name__)

_server_thread: Optional[threading.Thread] = None
_shutdown_event = threading.Event()

# ── Multi-Timeframe-Puffer ────────────────────────────────────────────────────
_MTF_BUFFER: Dict[str, dict] = {}
_MTF_TTL = 900       # 15 Minuten
_MTF_REQUIRED = 2    # Anzahl verschiedener Timeframes die übereinstimmen müssen
_mtf_lock = threading.Lock()

# ── Replay-Schutz ────────────────────────────────────────────────────────────
# Signale mit Timestamp älter als 5 Minuten werden abgelehnt.
# Gesehene Nonces werden für 10 Minuten gespeichert.
_REPLAY_MAX_AGE  = 300   # Sekunden
_seen_nonces: collections.deque = collections.deque(maxlen=500)
_nonce_lock = threading.Lock()


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
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # Flask-Logs reduzieren

    def _auth(data: dict) -> bool:
        return not secret or data.get("secret") == secret

    def _replay_check(data: dict) -> tuple[bool, str]:
        """
        Schützt gegen Replay-Attacks:
        1. Timestamp 'ts' (Unix-Sekunden) darf max. _REPLAY_MAX_AGE Sek. alt sein.
        2. Optionale Nonce 'nonce' darf nicht doppelt vorkommen.
        Gibt (True, "") zurück wenn alles in Ordnung.
        """
        ts = data.get("ts")
        if ts is not None:
            try:
                age = abs(time.time() - float(ts))
                if age > _REPLAY_MAX_AGE:
                    return False, f"Signal zu alt ({age:.0f}s > {_REPLAY_MAX_AGE}s) – abgelehnt"
            except (TypeError, ValueError):
                pass

        nonce = data.get("nonce")
        if nonce:
            with _nonce_lock:
                if nonce in _seen_nonces:
                    return False, f"Nonce '{nonce}' bereits verarbeitet – Replay abgelehnt"
                _seen_nonces.append(nonce)

        return True, ""

    @app.route("/webhook/tradingview", methods=["POST"])
    def receive_alert():
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400
        if not _auth(data):
            log.warning("Webhook: ungültiges Secret von %s", request.remote_addr)
            return jsonify({"error": "Unauthorized"}), 401
        replay_ok, replay_reason = _replay_check(data)
        if not replay_ok:
            log.warning("Webhook: Replay-Schutz – %s (%s)", replay_reason, request.remote_addr)
            return jsonify({"error": replay_reason}), 429

        tickers_raw = data.get("tickers")
        if isinstance(tickers_raw, list):
            results = [_process_signal({**data, "ticker": t}, signal_queue) for t in tickers_raw]
            return jsonify({"ok": True, "results": results}), 200

        result = _process_signal(data, signal_queue)
        return jsonify(result), 200 if result.get("ok") else 400

    @app.route("/webhook/external", methods=["POST"])
    def receive_external():
        """Generischer Endpunkt für externe Dienste (Benzinga, Unusual Whales, etc.)"""
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400
        if not _auth(data):
            return jsonify({"error": "Unauthorized"}), 401
        replay_ok, replay_reason = _replay_check(data)
        if not replay_ok:
            return jsonify({"error": replay_reason}), 429

        # Normalisierung externer Formate auf internes Format
        normalized = _normalize_external(data)
        result = _process_signal(normalized, signal_queue)
        return jsonify(result), 200 if result.get("ok") else 400

    @app.route("/webhook/macro", methods=["POST"])
    def receive_macro():
        """
        Makroökonomische Events (FRED, eigene Skripte, etc.)
        Erwartet: {"secret":"...", "event":"CPI", "surprise":"ABOVE"|"BELOW"|"INLINE",
                   "impact":"HIGH"|"MEDIUM", "value": 3.2, "forecast": 3.0}
        """
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            return jsonify({"error": "Invalid JSON"}), 400
        if not _auth(data):
            return jsonify({"error": "Unauthorized"}), 401
        result = _process_macro_event(data, signal_queue)
        return jsonify(result), 200

    @app.route("/health", methods=["GET"])
    def health():
        with _mtf_lock:
            mtf_pending = len(_MTF_BUFFER)
        return jsonify({
            "status":       "ok",
            "service":      "tradingview-webhook",
            "mtf_pending":  mtf_pending,
            "endpoints":    ["/webhook/tradingview", "/webhook/external",
                             "/webhook/macro", "/health"],
        }), 200

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def _process_signal(data: dict, signal_queue) -> dict:
    """Verarbeitet ein eingehendes TradingView-Signal."""
    ticker = (data.get("ticker") or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "ticker fehlt"}

    action = (data.get("action") or "").upper()
    if action not in ("BUY", "SELL", "SKIP"):
        return {"ok": False, "error": f"Ungültige action: {action}"}

    if action == "SELL":
        _cancel_pending_buys(ticker, signal_queue)
        _write_sell_signal(ticker, data)
        log.info("TradingView [%s]: SELL-Signal gespeichert (offene BUYs storniert)", ticker)
        # Korrelations-Signale für SELL (z.B. SPY SELL → SH BUY)
        _queue_correlated(ticker, "SELL", data, signal_queue)
        return {"ok": True, "queued": False, "action": "SELL", "ticker": ticker}

    if action == "SKIP":
        log.info("TradingView [%s]: SKIP – kein Eintrag", ticker)
        return {"ok": True, "queued": False, "action": "SKIP"}

    # BUY nur einreihen wenn kein SELL für denselben Ticker aussteht
    if _has_pending_sell(ticker):
        log.info("TradingView [%s]: BUY ignoriert – SELL-Signal steht noch aus", ticker)
        return {"ok": True, "queued": False, "action": "BUY_BLOCKED", "reason": "pending SELL"}

    # ── Multi-Timeframe-Bestätigung ───────────────────────────────────────────
    timeframe = (data.get("timeframe") or "").strip()
    if timeframe:
        confirmed, combined = _mtf_check(ticker, timeframe, data)
        if not confirmed:
            return {"ok": True, "queued": False, "action": "MTF_PENDING",
                    "reason": f"Warte auf zweiten Timeframe (bisher: {timeframe})"}
        # Kombiniertes Signal: höchsten Score beider Timeframes nehmen
        data = combined

    # Standardwerte wenn TradingView keine optionalen Felder sendet
    score      = float(data.get("score", 0.70))
    confidence = (data.get("confidence") or "MEDIUM").upper()
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        confidence = "MEDIUM"
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
        "TradingView [%s]: BUY in Queue (ID #%d, Score: %.2f, %s%s)",
        ticker, sig_id, score, confidence,
        f", MTF bestätigt: {timeframe}" if timeframe else "",
    )

    # Korrelations-Signale (z.B. GLD BUY → GDX, NEM BUY)
    _queue_correlated(ticker, "BUY", data, signal_queue)

    return {"ok": True, "queued": True, "signal_id": sig_id, "ticker": ticker}


def _mtf_check(ticker: str, timeframe: str, data: dict) -> tuple[bool, dict]:
    """
    Puffert das Signal und gibt (True, combined_data) zurück wenn
    mindestens _MTF_REQUIRED verschiedene Timeframes übereinstimmen.
    Nach _MTF_TTL Sekunden verfällt der Puffer.
    """
    now = time.time()
    with _mtf_lock:
        # Abgelaufene Einträge bereinigen
        expired = [t for t, v in _MTF_BUFFER.items() if now - v["ts"] > _MTF_TTL]
        for t in expired:
            del _MTF_BUFFER[t]

        entry = _MTF_BUFFER.setdefault(ticker, {"timeframes": {}, "ts": now})
        entry["timeframes"][timeframe] = data
        entry["ts"] = now  # TTL zurücksetzen bei jedem neuen Signal

        if len(entry["timeframes"]) >= _MTF_REQUIRED:
            # Bestätigt: kombiniertes Signal erstellen
            all_signals = list(entry["timeframes"].values())
            combined = dict(all_signals[0])
            combined["score"]     = max(float(s.get("score", 0.70)) for s in all_signals)
            combined["rationale"] = (
                f"MTF-Bestätigung ({', '.join(entry['timeframes'].keys())}): "
                + combined.get("rationale", "")
            )
            del _MTF_BUFFER[ticker]
            return True, combined

    return False, {}


_MAX_CORR_PER_GROUP = int(os.getenv("MAX_CORR_SIGNALS_PER_GROUP", "2"))


def _queue_correlated(ticker: str, action: str, source_data: dict, signal_queue) -> None:
    """
    Reiht Korrelations-Signale in die Queue ein.
    Sektor-Cap: maximal MAX_CORR_SIGNALS_PER_GROUP Signale pro Trigger-Gruppe
    um Sektor-Konzentration zu vermeiden (Standard: 2).
    """
    try:
        from collectors.correlation_engine import get_correlated_signals
        corr_list = get_correlated_signals(ticker, action)
        if not corr_list:
            return

        queued_count = 0
        for corr in corr_list:
            if queued_count >= _MAX_CORR_PER_GROUP:
                log.info(
                    "CorrelationCap: %d/%d Signale für %s %s eingereiht – Rest ignoriert",
                    queued_count, len(corr_list), ticker, action,
                )
                break
            if corr["action"] != "BUY":
                continue
            if _has_pending_sell(corr["ticker"]):
                continue
            signal_queue.enqueue(
                ticker=corr["ticker"],
                sentiment_score=corr["score"],
                confidence=corr["confidence"],
                target_price=None,
                direction="BULLISH",
                entry_rationale=f"[{corr['strategy']}] {corr['rationale']}",
                key_catalysts=[f"Korrelation: {ticker} {action}"],
                risk_factors=[],
                sources_used=1,
                sources_breakdown={"correlation": 1},
                suggested_hold_days=corr["hold_days"],
            )
            queued_count += 1
    except Exception as e:
        log.warning("Korrelations-Signale fehlgeschlagen: %s", e)


def _process_macro_event(data: dict, signal_queue) -> dict:
    """
    Verarbeitet ein Makro-Event (CPI, NFP, FOMC etc.).
    Speichert es in tv_macro_events.json für den nächsten Analyse-Zyklus.
    """
    event    = (data.get("event") or "").upper()
    surprise = (data.get("surprise") or "INLINE").upper()
    impact   = (data.get("impact")   or "MEDIUM").upper()
    value    = data.get("value")
    forecast = data.get("forecast")

    if not event:
        return {"ok": False, "error": "event fehlt"}

    entry = {
        "event":     event,
        "surprise":  surprise,
        "impact":    impact,
        "value":     value,
        "forecast":  forecast,
        "timestamp": datetime.utcnow().isoformat(),
    }
    _append_macro_event(entry)
    log.info("Makro-Event: %s, Surprise=%s, Impact=%s", event, surprise, impact)

    # Bei sehr negativen Überraschungen: Defensive auslösen
    if surprise == "ABOVE" and event in ("CPI", "PPI") and impact == "HIGH":
        log.warning("Makro-Alert: Inflation höher als erwartet (%s=%s) – defensiver Modus", event, value)
        return {"ok": True, "event": event, "action": "DEFENSIVE_MODE_TRIGGERED"}

    if surprise == "BELOW" and event in ("NFP", "GDP") and impact == "HIGH":
        log.warning("Makro-Alert: Wirtschaft schwächer als erwartet (%s=%s) – defensiver Modus", event, value)
        return {"ok": True, "event": event, "action": "DEFENSIVE_MODE_TRIGGERED"}

    return {"ok": True, "event": event, "recorded": True}


def _has_pending_sell(ticker: str) -> bool:
    """Prüft ob ein SELL-Signal für diesen Ticker noch aussteht."""
    try:
        with open(_SELL_FILE) as f:
            signals = json.load(f)
        return any(s.get("ticker") == ticker for s in signals)
    except Exception:
        return False


def _cancel_pending_buys(ticker: str, signal_queue) -> None:
    """Storniert alle ausstehenden BUY-Signale in der Queue für diesen Ticker."""
    try:
        for sig in signal_queue.get_pending():
            if sig.get("ticker") == ticker:
                signal_queue.mark_expired(sig["id"])
                log.info("TradingView [%s]: ausstehender BUY #%d storniert (SELL kam rein)", ticker, sig["id"])
    except Exception as e:
        log.warning("_cancel_pending_buys fehlgeschlagen: %s", e)


def _normalize_external(data: dict) -> dict:
    """
    Normalisiert externe Signal-Formate auf das interne Format.
    Unterstützt: Benzinga, Unusual Whales (Options Flow), generisches Format.
    """
    # Benzinga-Format: {"symbol": "AAPL", "headline": "...", "urgency": 3}
    if "symbol" in data and "headline" in data:
        return {
            "ticker":    data["symbol"],
            "action":    "BUY" if data.get("urgency", 0) >= 3 else "SKIP",
            "score":     min(0.5 + data.get("urgency", 1) * 0.1, 0.85),
            "confidence":"HIGH" if data.get("urgency", 0) >= 4 else "MEDIUM",
            "rationale": data.get("headline", "")[:200],
            "hold_days": 5,
            "strategy":  "Benzinga",
            "secret":    data.get("secret", ""),
        }

    # Unusual Whales / Options Flow: {"ticker": "NVDA", "type": "call", "premium": 500000}
    if "type" in data and data.get("type") in ("call", "put"):
        action = "BUY" if data["type"] == "call" else "SELL"
        premium = data.get("premium", 0)
        score = min(0.65 + premium / 5_000_000, 0.90)
        return {
            "ticker":    data.get("ticker", ""),
            "action":    action,
            "score":     round(score, 2),
            "confidence":"HIGH" if premium > 1_000_000 else "MEDIUM",
            "rationale": f"Unusual Options: {data['type'].upper()} ${premium:,.0f} Premium",
            "hold_days": 3,
            "strategy":  "UnusualWhales",
            "secret":    data.get("secret", ""),
        }

    # Generisches Format – direkt durchreichen
    return data


def _write_sell_signal(ticker: str, data: dict) -> None:
    """Speichert ein SELL-Signal atomar in eine JSON-Datei."""
    import tempfile
    os.makedirs(os.path.dirname(_SELL_FILE), exist_ok=True)
    try:
        with open(_SELL_FILE) as f:
            signals = json.load(f)
    except Exception:
        signals = []

    signals.append({
        "ticker":    ticker,
        "price":     data.get("price"),
        "strategy":  data.get("strategy", "TradingView"),
        "timestamp": datetime.utcnow().isoformat(),
    })

    with tempfile.NamedTemporaryFile(
        mode="w", dir=os.path.dirname(_SELL_FILE), suffix=".tmp", delete=False
    ) as tmp:
        json.dump(signals, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, _SELL_FILE)


def _append_macro_event(entry: dict) -> None:
    """Hängt ein Makro-Event atomar an die JSON-Datei an."""
    import tempfile
    os.makedirs(os.path.dirname(_MACRO_FILE), exist_ok=True)
    try:
        with open(_MACRO_FILE) as f:
            events = json.load(f)
    except Exception:
        events = []
    events.append(entry)
    # Nur die letzten 50 Events behalten
    events = events[-50:]
    with tempfile.NamedTemporaryFile(
        mode="w", dir=os.path.dirname(_MACRO_FILE), suffix=".tmp", delete=False
    ) as tmp:
        json.dump(events, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, _MACRO_FILE)


def get_pending_macro_events(since_hours: int = 24) -> List[dict]:
    """Gibt Makro-Events der letzten N Stunden zurück."""
    try:
        from datetime import timedelta
        with open(_MACRO_FILE) as f:
            events = json.load(f)
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        return [
            e for e in events
            if datetime.fromisoformat(e["timestamp"]) >= cutoff
        ]
    except Exception:
        return []


def get_pending_sells() -> List[dict]:
    """Gibt ausstehende SELL-Signale zurück und leert die Liste."""
    try:
        with open(_SELL_FILE) as f:
            signals = json.load(f)
        # Sofort leeren damit Signale nicht doppelt verarbeitet werden
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(_SELL_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump([], tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _SELL_FILE)
        return signals
    except Exception:
        return []
