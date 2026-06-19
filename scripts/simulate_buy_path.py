#!/usr/bin/env python3
"""
simulate_buy_path.py – End-to-End-Trockenlauf des BUY-Pfads.

Beantwortet die Frage: "Wenn dem Bot gesagt wird, Aktie X soll gekauft werden –
wird die Order rein theoretisch auch platziert?" Dazu wird je Szenario die ECHTE
Kette durchlaufen:

    Runner-Schranke (_valid_price + _ensure_current_price)
        → SwingStrategy.evaluate()           (Slots, Quellen, Sizing, Schwelle)
            → TradeExecutor.execute()        (Scale-in, Fill-Buchung)
                → broker.buy()               (Verbindung, Ganz-Aktien, Fill)

Es wird NICHTS real geordert: der Broker ist ein SimIBKRBroker, der die
IBKRBroker-Semantik (kein Fallback-Paper, Ganz-Aktien-Rundung, Fill/Timeout)
über die echte OrderResult-Klasse nachbildet und jeden buy()-Aufruf mitschreibt.

Aufruf:  venv/bin/python scripts/simulate_buy_path.py
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy
from strategy.executor import TradeExecutor, _NullNotifier
from broker.order_result import OrderResult
import bot.runner as runner  # echte Runner-Helfer _valid_price / _ensure_current_price


# ── Sim-Broker: bildet IBKRBroker.buy/sell/get_price ohne Live-Verbindung nach ──
class SimIBKRBroker:
    """Mimt die für den BUY-Pfad relevante IBKRBroker-Semantik. mode='ibkr' →
    KEIN stiller Paper-Fallback (wie produktiv). Jeder buy() wird protokolliert."""

    def __init__(self, *, connected=True, yahoo_price=120.0, broker_price=120.0,
                 fill="filled"):
        self.connected = connected
        self._broker_price = broker_price   # was get_price() (IBKR-Delayed) liefert
        self.yahoo_price = yahoo_price       # was die "Primärquelle" (Yahoo) liefert
        self.fill = fill                     # "filled" | "timeout" | "cancelled"
        self.buys: list = []
        self.sells: list = []

    # IBKR rundet Aktien auf ganze Stück (Error 10243 für Teilaktien)
    @staticmethod
    def _whole(shares: float) -> int:
        return int(shares)

    def get_price(self, ticker):
        # Delayed-Fallback im Runner; None = IBKR liefert auch nichts
        return self._broker_price

    def buy(self, ticker, shares, price, **kw):
        self.buys.append({"ticker": ticker, "shares": shares, "price": price})
        if not self.connected:
            return OrderResult.error(ticker=ticker, reason="IBKR nicht verbunden", mode="ibkr")
        whole = self._whole(shares)
        if whole <= 0:
            return OrderResult.error(
                ticker=ticker, mode="ibkr",
                reason=f"Positionsgröße {shares:.4f} < 1 Stück – Teilaktie nicht API-fähig")
        if self.fill == "filled":
            return OrderResult.filled(ticker, whole, price, order_id=1, mode="ibkr")
        if self.fill == "timeout":
            return OrderResult.cancelled(ticker=ticker, mode="ibkr",
                                         reason="Fill-Timeout – Order gecancelt")
        return OrderResult.cancelled(ticker=ticker, mode="ibkr", reason="Cancelled")

    def sell(self, ticker, shares, price, **kw):
        self.sells.append({"ticker": ticker, "shares": shares, "price": price})
        return OrderResult.filled(ticker, int(shares), price, mode="ibkr")


# ── Test-Gerüst (entspricht tests/test_evaluate_execute_chain.py) ──────────────
def make_portfolio(tmpdir, capital=100_000.0):
    db = os.path.join(tmpdir, "portfolio.db")
    port_mod.PORTFOLIO_DB = db
    return Portfolio(initial_capital=capital)


def make_strategy(portfolio, max_positions=12):
    s = object.__new__(SwingStrategy)
    s.portfolio = portfolio
    s.signal_queue = None
    s.earnings_filter = None
    s.correlation_checker = None
    s.kelly_sizer = None
    s.goal_risk_assessor = None
    s.focus_ctrl = types.SimpleNamespace(get_max_positions=lambda pv: max_positions)
    s._lock = threading.Lock()
    s._daily_loss_usd = 0.0
    s._daily_loss_date = ""
    return s


def neutralize_macro():
    import analyzers.macro_context as mc
    fake = types.SimpleNamespace(size_modifier=lambda t: 1.0, bias_score=lambda: 0.0)
    mc.get_macro_context = lambda: fake


def bullish_analysis(ticker="NVDA", sources=5):
    return types.SimpleNamespace(
        ticker=ticker, sentiment_score=0.95, confidence="HIGH",
        direction="BULLISH", recommendation="BUY", suggested_hold_days=10,
        entry_rationale="Starkes Momentum", key_catalysts=["Earnings-Beat"],
        risk_factors=["Bewertung"], target_price=160.0, sources_used=sources,
    )


# ── Eine Szenario-Ausführung ───────────────────────────────────────────────────
def run_scenario(name, *, broker, analysis, capital=100_000.0,
                 preopen=None, max_positions=12, ticker="NVDA"):
    """Durchläuft Runner-Schranke → evaluate → execute und meldet, ob eine Order
    platziert würde. Gibt ein Ergebnis-Dict für die Tabelle zurück."""
    with tempfile.TemporaryDirectory() as tmp:
        p = make_portfolio(tmp, capital)
        if preopen:
            for pos in preopen:
                p.open_position(pos)
        strat = make_strategy(p, max_positions=max_positions)
        ex = TradeExecutor(p, broker, journal=None, notifier=_NullNotifier())

        # 1) Runner-Schranke: Primärquelle (Yahoo) + IBKR-Delayed-Fallback
        price_data = {"ticker": ticker, "current_price": broker.yahoo_price}
        price_data = runner._ensure_current_price(ticker, price_data, broker)
        cur = price_data.get("current_price")

        gate_ok = runner._valid_price(cur)
        if not gate_ok:
            return {"name": name, "gate": "❌ kein Kurs", "action": "—",
                    "buy_called": False, "placed": False, "position": False,
                    "msg": f"[{ticker}] vor Claude übersprungen (Kurs={cur})"}

        # 2) Strategie-Entscheidung
        result = strat.evaluate(ticker, analysis, float(cur), "NEUTRAL")

        # 3) Ausführung
        out = ex.execute(result, analysis=analysis) or ""

        placed = any(b for b in broker.buys
                     if "GEKAUFT" in out)  # Order gefüllt & gebucht
        return {
            "name": name,
            "gate": f"✓ ${float(cur):.2f}",
            "action": result.action,
            "buy_called": bool(broker.buys),
            "placed": "GEKAUFT" in out,
            "position": p.get_position(ticker) is not None,
            "msg": out.strip() or result.reason,
        }


def main():
    neutralize_macro()
    nan = float("nan")
    scenarios = []

    # A) Sauberer Kauf: Yahoo-Kurs, freier Slot, IBKR verbunden, Fill
    scenarios.append(run_scenario(
        "A Sauberer BUY (alles ok)",
        broker=SimIBKRBroker(connected=True, yahoo_price=120.0, fill="filled"),
        analysis=bullish_analysis()))

    # B) Yahoo leer (NaN), aber IBKR-Delayed liefert Kurs → Fallback greift
    scenarios.append(run_scenario(
        "B Yahoo NaN → IBKR-Delayed-Fallback",
        broker=SimIBKRBroker(connected=True, yahoo_price=nan, broker_price=118.5,
                             fill="filled"),
        analysis=bullish_analysis()))

    # C) Weder Yahoo noch IBKR liefern Kurs → vor Claude übersprungen
    scenarios.append(run_scenario(
        "C Gar kein Kurs (Yahoo+IBKR leer)",
        broker=SimIBKRBroker(connected=True, yahoo_price=nan, broker_price=nan),
        analysis=bullish_analysis()))

    # D) Scale-in: Position existiert bereits → kein Add, keine Order
    scenarios.append(run_scenario(
        "D Position existiert (Scale-in)",
        broker=SimIBKRBroker(connected=True, yahoo_price=120.0, fill="filled"),
        analysis=bullish_analysis(),
        preopen=[Position(ticker="NVDA", shares=10, entry_price=100.0,
                          entry_date="2026-06-10T00:00:00", stop_loss=90.0,
                          take_profit=130.0, target_hold_days=10)]))

    # E) Zu wenige Quellen → evaluate SKIP, keine Order
    _few = bullish_analysis(); _few.sources_used = 0
    scenarios.append(run_scenario(
        "E Zu wenige Quellen (0)",
        broker=SimIBKRBroker(connected=True, yahoo_price=120.0, fill="filled"),
        analysis=_few))

    # F) Alle Slots belegt → evaluate blockt, keine Order
    scenarios.append(run_scenario(
        "F Slot voll (max=1, 1 belegt)",
        broker=SimIBKRBroker(connected=True, yahoo_price=120.0, fill="filled"),
        analysis=bullish_analysis(), max_positions=1,
        preopen=[Position(ticker="AAPL", shares=5, entry_price=200.0,
                          entry_date="2026-06-10T00:00:00", stop_loss=180.0,
                          take_profit=240.0, target_hold_days=10)]))

    # G) BUY ok, aber IBKR NICHT verbunden → Order scheitert, keine Buchung
    scenarios.append(run_scenario(
        "G IBKR nicht verbunden",
        broker=SimIBKRBroker(connected=False, yahoo_price=120.0),
        analysis=bullish_analysis()))

    # H) Teilaktie < 1 Stück (Mini-Kapital) → IBKR-Error, keine Buchung
    scenarios.append(run_scenario(
        "H Teilaktie < 1 Stück (Mini-Kapital)",
        broker=SimIBKRBroker(connected=True, yahoo_price=120.0, fill="filled"),
        analysis=bullish_analysis(), capital=50.0))

    # I) Fill-Timeout: Order eingereicht, aber nicht gefüllt → keine Buchung
    scenarios.append(run_scenario(
        "I Fill-Timeout (nicht gefüllt)",
        broker=SimIBKRBroker(connected=True, yahoo_price=120.0, fill="timeout"),
        analysis=bullish_analysis()))

    # ── Ausgabe ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("BUY-PFAD-SIMULATION – wird die Order rein theoretisch platziert?")
    print("=" * 100)
    hdr = f"{'Szenario':<36}{'Kurs-Gate':<14}{'evaluate':<10}{'buy()':<8}{'ORDER':<8}{'Pos':<6}"
    print(hdr)
    print("-" * 100)
    for r in scenarios:
        order = "✅ JA" if r["placed"] else "—"
        buyc = "ja" if r["buy_called"] else "—"
        pos = "ja" if r["position"] else "—"
        print(f"{r['name']:<36}{r['gate']:<14}{r['action']:<10}{buyc:<8}{order:<8}{pos:<6}")
    print("-" * 100)
    print("Details:")
    for r in scenarios:
        print(f"  • {r['name']}: {r['msg']}")
    print("=" * 100)

    placed = sum(1 for r in scenarios if r["placed"])
    print(f"\nErgebnis: {placed}/{len(scenarios)} Szenarien führen zu einer platzierten & gebuchten Order.")
    print("Erwartet: nur A und B (sauberer Kauf / Yahoo-leer-mit-IBKR-Fallback).")


if __name__ == "__main__":
    main()
