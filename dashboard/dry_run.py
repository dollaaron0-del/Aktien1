"""
dashboard/dry_run.py — „Was würde der Bot jetzt tun?" (Ausbau-Roadmap H1.5).

Spielt für EINEN Ticker den echten Entscheidungspfad durch und zeigt,
welches Gate greift — ohne Order, ohne Log-Schreibung, ohne Kosten.

════════════════════════════════════════════════════════════════════════
SEITENEFFEKT-FREIHEIT (die Kernanforderung des Punkts)
════════════════════════════════════════════════════════════════════════
`SwingStrategy.evaluate()` ist eine reine Entscheidungsfunktion: sie gibt
ein `StrategyResult` zurück und schreibt selbst weder ins decision_log
noch ins analysis_log noch in den Experience Store — das tut erst der
Runner NACH ihr (`bot/cycle_analysis.py`). Damit bleiben genau drei
Berührungspunkte, die hier alle einzeln stillgelegt sind:

1. **Portfolio** — `evaluate()` liest Cash/Positionen/Slots. Wir lassen es
   NICHT auf `data/portfolio.db` zeigen, sondern auf eine KOPIE davon in
   einem Temp-Verzeichnis. Dadurch rechnen die Gates gegen die ECHTE Lage
   (gleiches Cash, gleiche Positionen), können sie aber nicht verändern.
2. **Signal-Queue** — `evaluate()` würde ein BUY-Signal bei vollen Slots
   in `data/signal_queue.db` VORMERKEN (swing_strategy.py Zeile ~343).
   Wir übergeben `signal_queue=None`; der Aufruf ist dort durch
   `if self.signal_queue:` geschützt, es wird also nichts geschrieben.
   (Dasselbe tut das bestehende `scripts/simulate_buy_path.py`.)
3. **SL-Cooldown** — `StopLossCooldown().is_blocked()` ist read-only
   (nur `_load()`), darf also unverändert echt laufen.

Kein Broker, kein `TradeExecutor` — die Frage „was würde er tun" wird von
`evaluate()` beantwortet; ein Executor würde nur zusätzliche
Schreibpfade öffnen, ohne die Antwort zu verbessern.

════════════════════════════════════════════════════════════════════════
WARUM KEIN FRISCHER LLM-LAUF (bewusst, gemessen)
════════════════════════════════════════════════════════════════════════
Die Roadmap sah vor: „Standard: nur Ollama/mechanisch, Claude nur mit
explizitem Haken". Auf DIESEM Server ist der Ollama-Teil davon nicht
haltbar — gemessen am 16.7.2026: **0,12 tok/s** (24 s für eine
Drei-Wort-Antwort, llama3.2:3b, CPU-Modus). Eine echte Analyse mit
hunderten Ausgabe-Tokens liefe damit viele Minuten in einem blockierten
Streamlit-Request; das Projekt hält dieselbe Grenze schon fest
(docs/ROADMAP.md: „bei 1,7 tok/s unmöglich"). Der Claude-Zweig wiederum
kostet pro Klick echtes Budget.

Beides ist auch unnötig, weil H1.2 den sauberen Weg dafür bereits hat:
Ticker in die `user_request_queue` einwerfen → der Bot analysiert ihn im
nächsten Zyklus mit seinem normalen Routing und Budget. Dieser
Trockenlauf beantwortet die andere, komplementäre Frage: „Was würde der
Bot mit dem, was er über X BEREITS WEISS, jetzt tun?" — und nutzt dafür
die letzte echte, gespeicherte Analyse aus dem analysis_log. Kostenlos
und ohne LLM.

Einziger Netz-Zugriff ist der Kurs-Abruf (`_current_price`, read-only,
gecacht) — der ist nötig, weil das analysis_log den Kurs zum
Analysezeitpunkt nicht speichert und ein falscher Kurs eine falsche
Positionsgröße und damit eine falsche Antwort ergäbe.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import types
from typing import Dict, List, Optional


def latest_analysis(ticker: str) -> Optional[Dict]:
    """Letzte gespeicherte Analyse eines Tickers (read-only). None, wenn
    der Bot den Ticker noch nie analysiert hat."""
    try:
        from analyzers.analysis_log import AnalysisLog
        rows = AnalysisLog().get_recent(limit=1, ticker=ticker.upper())
        return rows[0] if rows else None
    except Exception:
        return None


def _as_analysis_result(row: Dict):
    """Baut aus einer analysis_log-Zeile das Objekt, das `evaluate()`
    erwartet. Bewusst ein schlichter Namespace statt der echten
    AnalysisResult-Klasse: evaluate() greift nur per Attribut zu, und so
    hängt der Trockenlauf nicht am Konstruktor des Analyzers."""
    return types.SimpleNamespace(
        ticker=row.get("ticker"),
        sentiment_score=row.get("sentiment_score") or 0.0,
        confidence=row.get("confidence") or "LOW",
        direction=row.get("direction") or "NEUTRAL",
        recommendation=row.get("recommendation") or "SKIP",
        suggested_hold_days=row.get("suggested_hold") or 10,
        entry_rationale=row.get("entry_rationale") or "",
        key_catalysts=row.get("key_catalysts") or [],
        risk_factors=row.get("risk_factors") or [],
        target_price=row.get("target_price"),
        sources_used=row.get("sources_used") or 0,
    )


def _current_price(ticker: str) -> Optional[float]:
    """Aktueller Kurs — read-only über denselben Broker-Weg, den auch der
    echte Bot nutzt (`PaperBroker.get_price()`, intern gecacht).

    Bewusst NICHT `target_price` aus der Analyse: das ist Claudes KURSZIEL,
    nicht der heutige Kurs. Damit zu rechnen ergäbe eine falsche
    Positionsgröße und am Ende eine falsche Antwort — ein langsamerer,
    aber richtiger Trockenlauf ist besser als ein schneller, falscher.
    Das analysis_log speichert den Kurs zum Analysezeitpunkt nicht.
    """
    try:
        from broker.paper_broker import PaperBroker
        return PaperBroker().get_price(ticker)
    except Exception:
        return None


def _current_regime() -> str:
    try:
        import json
        from dashboard.factory.state import _REGIME_FILE
        with open(_REGIME_FILE, encoding="utf-8") as fh:
            return str(json.load(fh).get("regime") or "NEUTRAL").upper()
    except Exception:
        return "NEUTRAL"


def dry_run(ticker: str, price: Optional[float] = None) -> Dict:
    """Spielt den echten Strategie-Pfad für `ticker` durch.

    Rückgabe: `{"ok", "ticker", "action", "reason", "analysis",
    "regime", "price", "error"}`. `ok=False` + `error` wenn keine
    Grundlage da ist (kein Ticker, keine Analyse, kein Kurs).

    Fail-open in jeder Stufe — ein Trockenlauf darf nie etwas kaputt
    machen, er soll ja gerade das Gegenteil zeigen.
    """
    ticker = (ticker or "").strip().upper()
    out: Dict = {
        "ok": False, "ticker": ticker, "action": None, "reason": "",
        "analysis": None, "regime": None, "price": None, "error": None,
    }
    if not ticker:
        out["error"] = "Kein Ticker angegeben."
        return out

    row = latest_analysis(ticker)
    if row is None:
        out["error"] = (
            f"Für {ticker} liegt noch keine Analyse vor. Wirf ihn oben als "
            f"Werksauftrag ein — der Bot analysiert ihn im nächsten Zyklus."
        )
        return out
    out["analysis"] = row

    if price is None:
        price = _current_price(ticker)
    try:
        price = float(price) if price else None
    except (TypeError, ValueError):
        price = None
    if not price or price <= 0:
        out["error"] = (
            f"Kein aktueller Kurs für {ticker} abrufbar — ohne Kurs würde auch "
            f"der echte Bot den Ticker überspringen."
        )
        return out
    out["price"] = price

    regime = _current_regime()
    out["regime"] = regime

    import portfolio.portfolio as port_mod

    real_db = port_mod.PORTFOLIO_DB
    original = real_db
    tmpdir = tempfile.mkdtemp(prefix="dryrun_")
    try:
        # (1) Portfolio-KOPIE: echte Lage, aber unantastbar.
        tmp_db = os.path.join(tmpdir, "portfolio.db")
        try:
            if os.path.exists(real_db):
                shutil.copy2(real_db, tmp_db)
        except Exception:
            pass  # keine Kopie -> frisches Portfolio, Gates rechnen dann leer
        port_mod.PORTFOLIO_DB = tmp_db

        from strategy.swing_strategy import SwingStrategy
        from config import config

        pf = port_mod.Portfolio(initial_capital=config.initial_capital)

        # (2) Strategie ohne Signal-Queue -> kein Vormerken bei vollen Slots.
        strat = object.__new__(SwingStrategy)
        strat.portfolio = pf
        strat.signal_queue = None
        strat.earnings_filter = None
        strat.correlation_checker = None
        strat.kelly_sizer = None
        strat.goal_risk_assessor = None
        strat.focus_ctrl = types.SimpleNamespace(
            get_max_positions=lambda pv: getattr(config, "max_positions", 12)
        )
        strat._lock = threading.Lock()
        strat._daily_loss_usd = 0.0
        strat._daily_loss_date = ""

        result = strat.evaluate(ticker, _as_analysis_result(row), price, regime)
        out["ok"] = True
        out["action"] = getattr(result, "action", None)
        out["reason"] = getattr(result, "reason", "") or ""
    except Exception as exc:
        out["error"] = f"Trockenlauf fehlgeschlagen: {type(exc).__name__}"
    finally:
        port_mod.PORTFOLIO_DB = original
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out
