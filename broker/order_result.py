"""
broker/order_result.py – typisiertes, dict-kompatibles Order-Ergebnis.

Bisher gaben Broker lose dicts zurück ({"status": ..., "fill_price": ...}). Ein
Tippfehler im Schlüssel beim Erzeugen fiel nicht auf – und die Konsumenten
(executor._fill_price/_fill_shares) fallen bei fehlendem Key still auf einen
Ersatzwert zurück, d.h. ein falsch geschriebenes "fill_pirce" hätte unbemerkt
den falschen Preis gebucht.

OrderResult ist ein dict (bleibt 100% abwärtskompatibel: ["status"], .get(),
isinstance(x, dict) funktionieren weiter), aber sein Konstruktor hat benannte,
geprüfte Kernfelder – ein Tippfehler dort wirft sofort TypeError. Broker-
spezifische Zusatzdaten (Slippage, Kommission, …) laufen bewusst durch das
separate `extra`-Dict, damit sie erhalten bleiben ohne den Tippfehler-Schutz
der Kernfelder zu unterlaufen.
"""
from __future__ import annotations

from typing import Optional


class OrderResult(dict):
    def __init__(
        self,
        status: str,
        *,
        ticker: str = "",
        shares: float = 0.0,
        fill_price: float = 0.0,
        order_id: Optional[int] = None,
        mode: str = "",
        reason: str = "",
        partial: bool = False,
        usd_amount: Optional[float] = None,
        extra: Optional[dict] = None,
    ):
        super().__init__()
        self["status"]     = status
        self["ticker"]     = ticker
        self["shares"]     = shares
        self["fill_price"] = fill_price
        self["order_id"]   = order_id
        self["mode"]       = mode
        if reason:
            self["reason"] = reason
        if partial:
            self["partial"] = True
        if usd_amount is not None:
            self["usd_amount"] = usd_amount
        if extra:
            self.update(extra)

    @property
    def is_filled(self) -> bool:
        return self.get("status") == "filled"

    # ── Convenience-Factories ────────────────────────────────────────────────
    @classmethod
    def filled(cls, ticker: str, shares: float, fill_price: float,
               *, order_id: Optional[int] = None, mode: str = "",
               partial: bool = False, usd_amount: Optional[float] = None,
               extra: Optional[dict] = None) -> "OrderResult":
        return cls("filled", ticker=ticker, shares=shares, fill_price=fill_price,
                   order_id=order_id, mode=mode, partial=partial,
                   usd_amount=usd_amount, extra=extra)

    @classmethod
    def error(cls, ticker: str = "", reason: str = "", mode: str = "") -> "OrderResult":
        return cls("error", ticker=ticker, reason=reason, mode=mode)

    @classmethod
    def cancelled(cls, ticker: str = "", reason: str = "", mode: str = "") -> "OrderResult":
        return cls("cancelled", ticker=ticker, reason=reason, mode=mode)
