"""
Market-aware analysis scheduler.

Calculates analysis times as "30 minutes before market open" for each
configured exchange, with full DST/timezone awareness via zoneinfo.
Also skips national market holidays (NYSE + XETRA).

Supported exchanges:
  XETRA  – Frankfurt       09:00 CET/CEST  (Europe/Berlin)
  NYSE   – New York        09:30 ET        (America/New_York)
  NASDAQ – New York        09:30 ET        (same as NYSE)
  TSE    – Tokyo           09:00 JST       (Asia/Tokyo, no DST)
  HKEX   – Hong Kong       09:30 HKT       (Asia/Hong_Kong, no DST)
  SSE    – Shanghai        09:30 CST       (Asia/Shanghai, no DST)
  LSE    – London          08:00 GMT/BST   (Europe/London)
  ASX    – Sydney          10:00 AEST/AEDT (Australia/Sydney)
"""
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, time as dtime, date as date_type, timezone
from typing import List, Dict, Optional, Set


EXCHANGE_DEFS: Dict[str, Dict] = {
    "XETRA":  {"tz": "Europe/Berlin",      "open": dtime(9, 0),  "close": dtime(17, 30), "name": "Frankfurt / Tradegate", "lead": 90},
    "NYSE":   {"tz": "America/New_York",   "open": dtime(9, 30), "close": dtime(16, 0),  "name": "NYSE/NASDAQ New York"},
    "NASDAQ": {"tz": "America/New_York",   "open": dtime(9, 30), "close": dtime(16, 0),  "name": "NASDAQ New York"},
    "TSE":    {"tz": "Asia/Tokyo",         "open": dtime(9, 0),  "close": dtime(15, 0),  "name": "Tokyo TSE"},
    "HKEX":   {"tz": "Asia/Hong_Kong",     "open": dtime(9, 30), "close": dtime(16, 0),  "name": "Hong Kong HKEX"},
    "SSE":    {"tz": "Asia/Shanghai",      "open": dtime(9, 30), "close": dtime(15, 0),  "name": "Shanghai SSE"},
    "LSE":    {"tz": "Europe/London",      "open": dtime(8, 0),  "close": dtime(16, 30), "name": "London LSE"},
    "ASX":    {"tz": "Australia/Sydney",   "open": dtime(10, 0), "close": dtime(16, 0),  "name": "Sydney ASX"},
}

# ISO weekday: Mon=1 … Sun=7; exchanges trade Mon–Fri
_TRADING_DAYS = {1, 2, 3, 4, 5}

# ── Market Holidays ────────────────────────────────────────────────────────────
# NYSE holidays 2025 + 2026 (mm-dd format, year-independent where applicable)
_NYSE_HOLIDAYS: Set[str] = {
    # 2025
    "2025-01-01",  # New Year's Day
    "2025-01-20",  # MLK Day
    "2025-02-17",  # Presidents' Day
    "2025-04-18",  # Good Friday
    "2025-05-26",  # Memorial Day
    "2025-06-19",  # Juneteenth
    "2025-07-04",  # Independence Day
    "2025-09-01",  # Labor Day
    "2025-11-27",  # Thanksgiving
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed, July 4 is Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}

# XETRA / Frankfurt holidays 2025 + 2026
_XETRA_HOLIDAYS: Set[str] = {
    # 2025
    "2025-01-01",  # New Year's Day
    "2025-04-18",  # Good Friday
    "2025-04-21",  # Easter Monday
    "2025-05-01",  # Labour Day
    "2025-12-24",  # Christmas Eve (half day → skip)
    "2025-12-25",  # Christmas Day
    "2025-12-26",  # Boxing Day
    "2025-12-31",  # New Year's Eve (half day → skip)
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-04-03",  # Good Friday
    "2026-04-06",  # Easter Monday
    "2026-05-01",  # Labour Day
    "2026-12-24",  # Christmas Eve
    "2026-12-25",  # Christmas Day
    "2026-12-31",  # New Year's Eve
}

# Combined: holiday in any major market → skip trading day
_ALL_HOLIDAYS: Set[str] = _NYSE_HOLIDAYS | _XETRA_HOLIDAYS


_EXCHANGE_HOLIDAYS: Dict[str, Set[str]] = {
    "NYSE":   _NYSE_HOLIDAYS,
    "NASDAQ": _NYSE_HOLIDAYS,
    "XETRA":  _XETRA_HOLIDAYS,
    "LSE":    _XETRA_HOLIDAYS,
}


def is_exchange_open(exchange: str = "NYSE") -> bool:
    """Returns True if the given exchange is currently in its regular trading session."""
    exch = EXCHANGE_DEFS.get(exchange.upper())
    if not exch:
        return False
    tz = ZoneInfo(exch["tz"])
    now_local = datetime.now(tz)
    if now_local.weekday() >= 5:
        return False
    today_str = now_local.date().isoformat()
    holidays = _EXCHANGE_HOLIDAYS.get(exchange.upper(), _ALL_HOLIDAYS)
    if today_str in holidays:
        return False
    return exch["open"] <= now_local.time() <= exch["close"]


# ── Ticker → Börsenplatz (Order-Gate) ────────────────────────────────────────
# Spiegelt _SUFFIX_MAP aus broker/ibkr_broker.py: welcher Handelskalender gilt
# für einen Ticker. EU-Plätze (Euronext, SIX, Wien, Mailand, Madrid, Nordics)
# laufen bewusst über den XETRA-Kalender – gleiche Kernzeit 09:00–17:30 lokal,
# gleiche Hauptfeiertage. Das Gate soll Wochenenden/Feiertage abfangen, nicht
# jede Sonderauktion minutengenau abbilden.
_TICKER_SUFFIX_EXCHANGE: Dict[str, str] = {
    ".DE": "XETRA", ".F":  "XETRA", ".MU": "XETRA", ".BE": "XETRA",
    ".PA": "XETRA", ".AS": "XETRA", ".MI": "XETRA", ".MC": "XETRA",
    ".BR": "XETRA", ".VI": "XETRA", ".HE": "XETRA", ".SW": "XETRA",
    ".CO": "XETRA", ".ST": "XETRA", ".OL": "XETRA",
    ".L":  "LSE",
}


def exchange_for_ticker(ticker: str) -> str:
    """Handelsplatz eines Tickers. Ohne bekanntes Suffix → US (NYSE-Kalender)."""
    upper = (ticker or "").upper()
    for suffix in sorted(_TICKER_SUFFIX_EXCHANGE, key=len, reverse=True):
        if upper.endswith(suffix):
            return _TICKER_SUFFIX_EXCHANGE[suffix]
    return "NYSE"


def market_closed_reason(ticker: str) -> Optional[str]:
    """
    Grund, warum für `ticker` gerade KEINE Market-Order rausgehen darf – oder
    None, wenn die zuständige Börse offen ist.

    Hintergrund (25.7.2026): Der Bot reichte am Samstag dreimal eine SELL-
    Market-Order für SAP.DE ein. IBKR cancelte sie sofort, der Bot wertete das
    als Fill-Timeout und meldete lautstark "SELL fehlgeschlagen" – dabei war
    schlicht die Börse zu. Schlimmer: sell() räumt vorher den GTC-Schutz-Stop
    weg, die Position stand danach ungeschützt da.
    """
    exch = exchange_for_ticker(ticker)
    if is_exchange_open(exch):
        return None
    defs = EXCHANGE_DEFS.get(exch, {})
    tz = ZoneInfo(defs.get("tz", "UTC"))
    now_local = datetime.now(tz)
    name = defs.get("name", exch)
    if now_local.weekday() >= 5:
        return f"{name} geschlossen (Wochenende)"
    if is_market_holiday(now_local.date(), exch):
        return f"{name} geschlossen (Feiertag)"
    return (f"{name} außerhalb der Handelszeit "
            f"({defs.get('open')}–{defs.get('close')} lokal, jetzt {now_local:%H:%M})")


def is_market_holiday(d: Optional[date_type] = None, exchange: str = "") -> bool:
    """Returns True if the given date is a holiday for the specified exchange."""
    if d is None:
        d = datetime.now(timezone.utc).replace(tzinfo=None).date()
    holidays = _EXCHANGE_HOLIDAYS.get(exchange.upper(), _ALL_HOLIDAYS)
    return d.isoformat() in holidays


class MarketSchedule:
    """
    Computes analysis times (N minutes before open) for a set of exchanges,
    returned as UTC datetimes or HH:MM strings in the local server timezone.
    """

    def __init__(
        self,
        exchanges: List[str],
        lead_minutes: int = 30,
    ):
        self.exchanges = [e.upper() for e in exchanges if e.upper() in EXCHANGE_DEFS]
        self.lead_minutes = lead_minutes

    def get_analysis_times_utc(self, date: Optional["datetime.date"] = None) -> List[Dict]:
        """
        Returns list of dicts for each exchange for the given date:
          {exchange, name, open_local, open_utc, analysis_utc, is_trading_day}

        If `date` is None, uses today in UTC.
        """
        if date is None:
            date = datetime.now().date()

        results = []
        for code in self.exchanges:
            ex = EXCHANGE_DEFS[code]
            tz = ZoneInfo(ex["tz"])
            # Build the open datetime in the exchange's local timezone
            open_local_naive = datetime.combine(date, ex["open"])
            open_local = open_local_naive.replace(tzinfo=tz)
            open_utc = open_local.astimezone(ZoneInfo("UTC"))
            lead = ex.get("lead", self.lead_minutes)
            analysis_utc = open_utc - timedelta(minutes=lead)
            is_trading = (date.isoweekday() in _TRADING_DAYS) and not is_market_holiday(date, code)
            results.append({
                "exchange": code,
                "name": ex["name"],
                "open_local": open_local.strftime("%H:%M %Z"),
                "open_utc": open_utc.strftime("%H:%M UTC"),
                "analysis_utc": analysis_utc,
                "analysis_utc_str": analysis_utc.strftime("%H:%M UTC"),
                "is_trading_day": is_trading,
            })
        # Sort chronologically
        results.sort(key=lambda r: r["analysis_utc"])
        return results

    def get_schedule_strings(self, date: Optional["datetime.date"] = None) -> List[Dict]:
        """
        Returns the schedule for today as HH:MM strings in the SERVER'S local time,
        suitable for passing to the `schedule` library.
        Only includes trading days.
        """
        entries = self.get_analysis_times_utc(date)
        seen: set = set()
        result = []
        for e in entries:
            if not e["is_trading_day"]:
                continue
            # Convert UTC to local server time
            local_dt = e["analysis_utc"].astimezone()
            hhmm = local_dt.strftime("%H:%M")
            if hhmm not in seen:
                seen.add(hhmm)
                result.append({
                    "hhmm": hhmm,
                    "exchange": e["exchange"],
                    "name": e["name"],
                    "open_local": e["open_local"],
                    "analysis_utc_str": e["analysis_utc_str"],
                })
        return result

    def next_window(self) -> Optional[Dict]:
        """Returns the next upcoming analysis window from now."""
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None).replace(tzinfo=ZoneInfo("UTC"))
        now_local = datetime.now()
        # Check today and tomorrow (use local date to avoid UTC-date mismatch at midnight)
        for delta in (0, 1):
            date = (now_local + timedelta(days=delta)).date()
            for entry in self.get_analysis_times_utc(date):
                if entry["is_trading_day"] and entry["analysis_utc"] > now_utc:
                    local_dt = entry["analysis_utc"].astimezone()
                    return {
                        **entry,
                        "analysis_local": local_dt.strftime("%d.%m. %H:%M"),
                    }
        return None

    def describe(self, date: Optional["datetime.date"] = None) -> str:
        """Human-readable schedule description for the given date."""
        entries = self.get_schedule_strings(date)
        if not entries:
            day = (date or datetime.now().date())
            return f"Kein Handel am {day.strftime('%A, %d.%m.%Y')}."
        lines = []
        for e in entries:
            lines.append(
                f"  {e['hhmm']} Lokalzeit  ←  {e['name']} öffnet um {e['open_local']}  "
                f"(= {e['analysis_utc_str']})"
            )
        return "\n".join(lines)
