"""
portfolio/integrity.py — Konsistenzprüfung der Portfolio-Buchhaltung.

Erkennt stille Drift zwischen Trade-Log (`trades`), Positions-Tabelle
(`positions`) und Cash-Stand. Solche Drift entsteht z.B. durch einen
Portfolio-Reset, der Positionen löscht, die `trades`-Tabelle aber stehen lässt
– dann existieren BUYs ohne zugehörige Position bzw. ohne Gegen-SELL
("Phantom-Trades"), und jede Auswertung aus dem Trade-Log wird verfälscht.

Geprüft wird:
  1. Orphan-Trades     – Ticker mit Netto-Long im Trade-Log, aber ohne Position
  2. Geister-Positionen – Position vorhanden, aber kein Netto-Long im Trade-Log
  3. Cash-Identität     – cash ≈ Epochen-Start − Σ(BUY) + Σ(SELL)  (informativ;
                          wird durch einen Reset bewusst gebrochen → reset_at-
                          Marker erklärt die Differenz)

Der Check ist rein lesend. Reparatur erfolgt separat über `reconcile()`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from logger import get_logger

log = get_logger(__name__)

# Schwellen
_SHARE_EPS = 1e-4        # Netto-Shares darunter gelten als "flach"
_CASH_TOL = 0.01         # 1 % Cash-Drift toleriert (FX/Rundung)

# Buchungs-Zeilen (kein echter Cash-Fluss): vom Cash-Identitätscheck ausgenommen,
# aber im Struktur-Check (Netto-Shares) bewusst mitgezählt – sie nullen Orphans.
_BOOKKEEPING_REASONS = ("Portfolio-Reset", "Reset-Bereinigung")


@dataclass
class IntegrityReport:
    ok: bool
    orphan_trades: Dict[str, float] = field(default_factory=dict)   # ticker -> netto-shares im Log ohne Position
    ghost_positions: List[str] = field(default_factory=list)        # Position ohne Netto-Long im Log
    cash_expected: float = 0.0
    cash_actual: float = 0.0
    cash_drift_pct: float = 0.0
    reset_at: Optional[str] = None
    issues: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "Portfolio-Integrität: OK (Trade-Log, Positionen und Cash konsistent)."
        lines = ["Portfolio-Integrität: ⚠️ Probleme gefunden:"]
        lines.extend(f"  • {i}" for i in self.issues)
        return "\n".join(lines)


def _connect(db: Union[str, sqlite3.Connection]) -> tuple[sqlite3.Connection, bool]:
    """Gibt (conn, owns) zurück. owns=True → Aufrufer darf schließen."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn, True


def _meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM portfolio_meta WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else None


def _net_shares_by_ticker(conn: sqlite3.Connection) -> Dict[str, float]:
    """Netto-Shares pro Ticker aus dem GESAMTEN Trade-Log (BUY − SELL).

    Bewusst über das volle Log: Reset-/Reconcile-SELLs nullen damit die
    zugehörigen Alt-BUYs, sodass das Log strukturell flach bleibt.
    """
    rows = conn.execute(
        """
        SELECT ticker,
               SUM(CASE WHEN action='BUY'  THEN shares ELSE 0 END) -
               SUM(CASE WHEN action='SELL' THEN shares ELSE 0 END) AS net
        FROM trades
        GROUP BY ticker
        """,
    ).fetchall()
    return {r[0]: float(r[1] or 0.0) for r in rows}


def check_integrity(
    db: Union[str, sqlite3.Connection],
    initial_capital: float,
) -> IntegrityReport:
    """Prüft die Portfolio-DB auf Buchhaltungs-Drift. Rein lesend."""
    conn, owns = _connect(db)
    try:
        reset_at = _meta(conn, "reset_at")
        epoch_cash = _meta(conn, "epoch_cash")
        start_cash = float(epoch_cash) if epoch_cash is not None else float(initial_capital)

        net = _net_shares_by_ticker(conn)
        positions = {
            r[0] for r in conn.execute("SELECT ticker FROM positions").fetchall()
        }

        # 1. Orphan-Trades: Netto-Long im Log, aber keine Position
        orphan = {
            t: round(n, 4)
            for t, n in net.items()
            if n > _SHARE_EPS and t not in positions
        }
        # 2. Geister-Positionen: Position, aber kein Netto-Long im Log
        ghosts = [
            t for t in positions
            if net.get(t, 0.0) <= _SHARE_EPS
        ]

        # 3. Cash-Identität (informativ): Epochen-Start − Σ(BUY-Kosten) + Σ(SELL-Erlös).
        # Nur echte Trades ab dem letzten Reset; Buchungs-Zeilen (Reset/Reconcile)
        # haben keinen Cash-Fluss und werden ausgeschlossen.
        clauses = []
        params: list = []
        if reset_at:
            clauses.append("timestamp >= ?")
            params.append(reset_at)
        for r in _BOOKKEEPING_REASONS:
            clauses.append("reason NOT LIKE ?")
            params.append(r + "%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        crow = conn.execute(
            f"""
            SELECT
              SUM(CASE WHEN action='BUY'  THEN shares*price ELSE 0 END),
              SUM(CASE WHEN action='SELL' THEN shares*price ELSE 0 END)
            FROM trades {where}
            """,
            tuple(params),
        ).fetchone()
        buy_cost = float(crow[0] or 0.0)
        sell_proceeds = float(crow[1] or 0.0)
        cash_expected = start_cash - buy_cost + sell_proceeds

        crow2 = conn.execute(
            "SELECT value FROM portfolio_meta WHERE key='cash'"
        ).fetchone()
        cash_actual = float(crow2[0]) if crow2 else 0.0
        denom = abs(cash_expected) if abs(cash_expected) > 1e-9 else 1.0
        cash_drift_pct = abs(cash_actual - cash_expected) / denom * 100.0

        issues: List[str] = []
        if orphan:
            issues.append(
                f"{len(orphan)} Phantom-Trade(s) ohne Position: "
                + ", ".join(f"{t} ({n:g})" for t, n in orphan.items())
            )
        if ghosts:
            issues.append(
                f"{len(ghosts)} Geister-Position(en) ohne Trade-Log-Deckung: "
                + ", ".join(ghosts)
            )
        # Cash-Drift nur dann als Problem werten, wenn KEIN Reset-Marker sie erklärt
        if cash_drift_pct > _CASH_TOL * 100 and reset_at is None:
            issues.append(
                f"Cash-Drift {cash_drift_pct:.1f}% "
                f"(erwartet ${cash_expected:,.2f}, ist ${cash_actual:,.2f}) – "
                f"deutet auf nicht protokollierten Reset/Buchungsfehler."
            )

        return IntegrityReport(
            ok=not issues,
            orphan_trades=orphan,
            ghost_positions=ghosts,
            cash_expected=round(cash_expected, 2),
            cash_actual=round(cash_actual, 2),
            cash_drift_pct=round(cash_drift_pct, 2),
            reset_at=reset_at,
            issues=issues,
        )
    finally:
        if owns:
            conn.close()


def reconcile(
    db: Union[str, sqlite3.Connection],
    reason: str = "Reset-Bereinigung",
    reset_marker: Optional[str] = None,
) -> Dict[str, float]:
    """
    Bereinigt Phantom-Trades: bucht für jeden Orphan einen Gegen-SELL zum
    Einstiegspreis (pnl=0), sodass das Trade-Log netto flach ist. Cash bleibt
    unverändert (ein Reset hat den Cash-Stand bereits bewusst gesetzt).

    Schreibt optional einen `reset_at`-Marker (+ `epoch_cash`), damit die
    Cash-Identität ab diesem Zeitpunkt wieder aufgeht.

    Gibt {ticker: bereinigte_shares} zurück.
    """
    from datetime import datetime, timezone

    conn, owns = _connect(db)
    try:
        # Aktuellen Report holen (ohne initial_capital-Abhängigkeit für Orphans)
        rep = check_integrity(conn, initial_capital=0.0)
        fixed: Dict[str, float] = {}
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with conn:
            for ticker, shares in rep.orphan_trades.items():
                # Letzten BUY-Preis dieses Tickers als Schließungspreis verwenden
                prow = conn.execute(
                    "SELECT price, currency, fx_rate FROM trades "
                    "WHERE ticker=? AND action='BUY' ORDER BY id DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
                price = float(prow[0]) if prow else 0.0
                currency = (prow[1] if prow and len(prow) > 1 else "USD") or "USD"
                fx_rate = float(prow[2]) if prow and len(prow) > 2 and prow[2] else 1.0
                conn.execute(
                    """
                    INSERT INTO trades
                        (ticker, action, shares, price, timestamp, pnl, reason, currency, fx_rate)
                    VALUES (?, 'SELL', ?, ?, ?, 0.0, ?, ?, ?)
                    """,
                    (ticker, shares, price, now, reason, currency, fx_rate),
                )
                fixed[ticker] = shares
            if reset_marker:
                cash = _meta(conn, "cash") or "0"
                conn.execute(
                    "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('reset_at', ?)",
                    (reset_marker,),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('epoch_cash', ?)",
                    (cash,),
                )
        if fixed:
            log.info("Portfolio-Reconcile: %d Phantom-Trade(s) bereinigt: %s",
                     len(fixed), ", ".join(fixed))
        return fixed
    finally:
        if owns:
            conn.close()


# Ausbuch-Bremse: höchstens so viele Voll-Phantome pro Lauf, bzw. dieser
# Anteil des Buchs – wird beides überschritten, gilt der Broker-Schnappschuss
# als unvollständig und es wird gar nichts ausgebucht.
_MAX_PHANTOM_BOOKOUT  = 2
_MAX_PHANTOM_FRACTION = 0.34


@dataclass
class BrokerReconcileReport:
    reconciled: Dict[str, float] = field(default_factory=dict)        # Buch-Position ohne IBKR-Deckung → ausgebucht
    partial_mismatch: Dict[str, tuple] = field(default_factory=dict)  # ticker -> (ibkr_held, buch_shares)
    untracked: Dict[str, float] = field(default_factory=dict)         # bei IBKR gehalten, aber nicht im Buch
    snapshot_rejected: bool = False                                   # Schnappschuss unplausibel → nichts ausgebucht

    @property
    def ok(self) -> bool:
        return not (self.reconciled or self.partial_mismatch or self.untracked
                    or self.snapshot_rejected)

    def summary(self) -> str:
        if self.ok:
            return "Broker-Abgleich: OK (Buch ↔ IBKR deckungsgleich)."
        parts = ["Broker-Abgleich: ⚠️ Buch ↔ IBKR weicht ab:"]
        if self.snapshot_rejected:
            parts.append("  • ⚠️ Broker-Schnappschuss unplausibel (zu viele "
                         "Positionen fehlten) – KEINE Ausbuchung vorgenommen")
        if self.reconciled:
            parts.append("  • Phantome ausgebucht (nicht bei IBKR): "
                         + ", ".join(f"{t} ({s:g})" for t, s in self.reconciled.items()))
        if self.partial_mismatch:
            parts.append("  • Teil-Abweichung (nur gemeldet, NICHT geändert): "
                         + ", ".join(f"{t} (IBKR {h:g} / Buch {b:g})"
                                     for t, (h, b) in self.partial_mismatch.items()))
        if self.untracked:
            parts.append("  • Bei IBKR gehalten, nicht im Buch: "
                         + ", ".join(f"{t} ({s:g})" for t, s in self.untracked.items()))
        return "\n".join(parts)


def _norm(sym: str) -> str:
    """Ticker auf IBKR-Basissymbol normalisieren (z.B. 'ASML.AS' → 'ASML')."""
    return (sym or "").split(".")[0].upper()


def reconcile_against_broker(
    db: Union[str, sqlite3.Connection],
    broker_positions: Dict[str, float],
    reason: str = "Reset-Bereinigung: nicht in IBKR (Broker-Abgleich)",
) -> BrokerReconcileReport:
    """Gleicht das Positions-Buch gegen die TATSÄCHLICHEN Broker-Positionen ab.

    `broker_positions` = {symbol: shares} vom Broker. WICHTIG: der Aufrufer darf
    diese Funktion nur mit einem real ermittelten Stand aufrufen – niemals mit
    {} wenn der Broker nur offline war (sonst würden alle Buch-Positionen als
    Phantome ausgebucht). `broker.positions()` liefert dafür None statt {}.

    Voll-Phantome (Broker hält ~0 des Symbols) werden cash-neutral ausgebucht:
    Position gelöscht + Gegen-SELL, sodass das Trade-Log netto flach bleibt und
    der Cash-Stand (kommt separat über den Broker-Cash-Sync) unangetastet
    bleibt. Der Gegen-SELL wird zum vermuteten SL-/TP-Preis gebucht (nicht mehr
    pauschal zum Einstiegspreis mit pnl=0 – das verschleierte reale Verluste,
    s. META-Vorfall 27.7.2026), nur ohne Referenzpreis fällt er auf
    Einstiegspreis/pnl=0 zurück. Teil-Abweichungen werden nur GEMELDET,
    nicht automatisch verändert (zu selten/riskant für Auto-Repair).
    """
    from datetime import datetime, timezone

    rep = BrokerReconcileReport()
    held_by_base: Dict[str, float] = {}
    for sym, qty in (broker_positions or {}).items():
        held_by_base[_norm(sym)] = held_by_base.get(_norm(sym), 0.0) + float(qty)

    conn, owns = _connect(db)
    try:
        rows = conn.execute(
            "SELECT ticker, shares, entry_price, currency, fx_rate_at_entry, "
            "stop_loss, partial_tp_taken, take_profit FROM positions"
        ).fetchall()
        book_bases = {_norm(r[0]) for r in rows}
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Plausibilitäts-Bremse gegen unvollständige Broker-Schnappschüsse:
        # verschwindet auf einen Schlag ein großer Teil des Buchs aus dem
        # IBKR-Stand, ist fast immer der Schnappschuss unvollständig (Cache
        # noch nicht gefüllt), nicht das Depot leer. In dem Fall NICHTS
        # ausbuchen – nur laut melden. (broker.positions() sollte seit dem
        # reqPositions()-Fix nie mehr partiell liefern; das hier ist die
        # zweite Verteidigungslinie, die die Buch-Korruption 2026 verhindert
        # hätte.)
        _phantoms = [r for r in rows
                     if held_by_base.get(_norm(r[0]), 0.0) + _SHARE_EPS < float(r[1])
                     and held_by_base.get(_norm(r[0]), 0.0) <= _SHARE_EPS]
        _limit = max(_MAX_PHANTOM_BOOKOUT, len(rows) * _MAX_PHANTOM_FRACTION)
        if len(rows) >= 3 and len(_phantoms) > _limit:
            log.error(
                "Broker-Abgleich ABGEBROCHEN: %d/%d Buch-Positionen fehlen im "
                "IBKR-Stand (%s) – Schnappschuss unplausibel, es wird NICHTS "
                "ausgebucht. Broker-Verbindung / reqPositions prüfen.",
                len(_phantoms), len(rows),
                ", ".join(r[0] for r in _phantoms[:10]),
            )
            rep.snapshot_rejected = True
            for base, qty in held_by_base.items():
                if abs(qty) > _SHARE_EPS and base not in book_bases:
                    rep.untracked[base] = round(qty, 4)
            return rep

        with conn:
            for r in rows:
                ticker = r[0]
                shares = float(r[1])
                held = held_by_base.get(_norm(ticker), 0.0)
                if held + _SHARE_EPS >= shares:
                    continue  # voll gedeckt – ok
                if held <= _SHARE_EPS:
                    # Voll-Phantom → ausbuchen (cash-neutral: die reale
                    # Kassenwirkung kommt über den nächsten Broker-Cash-Sync,
                    # hier würde eine zweite Verbuchung doppelt zählen).
                    #
                    # Verschwindet eine Position komplett aus IBKR, ohne dass der
                    # Bot sie selbst verkauft hat, ist der broker-seitige
                    # GTC-Schutz-Stop (analyzers/sl_cooldown.py-Docstring) die
                    # einzige plausible Erklärung – der Bot bekommt dessen Fill
                    # sonst nie mit. Bug bis 27.7.2026 (META-Doppelkauf-Vorfall):
                    # der Gegen-SELL wurde trotzdem IMMER zum Einstiegspreis mit
                    # pnl=0 gebucht, obwohl genau in diesem Zweig ein realer
                    # Stop/TP-Exit angenommen wird – der reale Verlust/Gewinn
                    # verschwand spurlos aus Trade-Log, Win-Rate und Lern-Daten,
                    # und die freigewordene Position sah für neue Signale wie
                    # unberührtes Kapital aus. Jetzt: Exit-Preis konservativ vom
                    # vermuteten Auslöser ableiten (TP falls Partial-TP schon
                    # lief, sonst SL; fail-open auf Einstiegspreis/pnl=0 nur wenn
                    # gar kein Referenzpreis vorliegt) und den Grund im Trade-Log
                    # klar als Schätzung kennzeichnen.
                    entry_price = float(r[2] or 0.0)
                    stop_loss = float(r[5] or 0.0)
                    take_profit = float(r[7] or 0.0) if len(r) > 7 else 0.0
                    partial_tp_taken = int(r[6] or 0)
                    assumed_trigger = "TP" if partial_tp_taken else "SL"
                    exit_price = (take_profit if partial_tp_taken else stop_loss) or entry_price
                    pnl = round((exit_price - entry_price) * shares, 4)
                    booked_reason = (
                        f"{reason} – vermuteter {assumed_trigger}-Exit "
                        f"(Fill unbestätigt, Preis geschätzt)"
                        if exit_price != entry_price else reason
                    )
                    conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
                    conn.execute(
                        """
                        INSERT INTO trades
                            (ticker, action, shares, price, timestamp, pnl, reason, currency, fx_rate)
                        VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (ticker, shares, exit_price, now, pnl, booked_reason,
                         (r[3] or "USD"), float(r[4] or 1.0)),
                    )
                    rep.reconciled[ticker] = round(shares, 4)
                    if not partial_tp_taken:
                        try:
                            from analyzers.sl_cooldown import StopLossCooldown
                            StopLossCooldown().record(ticker, stop_loss)
                        except Exception as e:
                            log.debug("[%s] SL-Cooldown record (Broker-Abgleich) fehlgeschlagen: %s",
                                      ticker, e)
                else:
                    # Teil-Deckung → nur melden, Buch unangetastet
                    rep.partial_mismatch[ticker] = (round(held, 4), round(shares, 4))
            # Bei IBKR gehalten, aber nicht im Buch → melden (kein Auto-Adopt)
            for base, qty in held_by_base.items():
                if abs(qty) > _SHARE_EPS and base not in book_bases:
                    rep.untracked[base] = round(qty, 4)
        if rep.reconciled:
            log.warning("Broker-Abgleich: %d Phantom-Position(en) ausgebucht: %s",
                        len(rep.reconciled), ", ".join(rep.reconciled))
        return rep
    finally:
        if owns:
            conn.close()
