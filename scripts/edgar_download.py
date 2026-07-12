#!/usr/bin/env python3
"""
EDGAR-8-K-Archiv-Download — Deep-Research Phase 1 / Vision V0.2.

Baut das lokale Roh-Archiv der 8-K-Filings auf, das später (GPU-Phase) vom
lokalen LLM annotiert und in der Event-Study (H1-Verdikt) ausgewertet wird.
Reines Sammel-Skript: KEIN Bot-Wiring, kein Live-Pfad.

Quelle ist die Submissions-API der SEC (data.sec.gov): die Hauptdatei je CIK
enthält die jüngsten ~1000 Filings, ältere liegen in paginierten
Archiv-Dateien (filings.files) — zusammen die komplette Filing-Historie.

Ablage (Roadmap 6.8):
  data/edgar/{cik}/{accession_ohne_striche}.txt   – Primärdokument (Rohtext/HTML)
  data/edgar/manifest.parquet                     – ticker, cik, form, filing_date,
                                                    items, url, path, size_bytes, …

SEC-Regeln: max. 10 req/s, User-Agent mit Kontakt-Mail (SEC_CONTACT_EMAIL in
.env, sonst Abbruch), IP-Block bei Verstoß → Default hier konservativ 5 req/s.
Idempotent: bereits geladene Accessions (Manifest + Datei vorhanden) werden
übersprungen; ein abgebrochener Lauf setzt einfach wieder auf.

Usage:
  python -m scripts.edgar_download                       # Watchlist, 8-K, ab 2005
  python -m scripts.edgar_download --tickers AAPL NVDA --since 2010-01-01
  python -m scripts.edgar_download --manifest-only       # nur Metadaten, keine Doks
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd  # noqa: E402

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://data.sec.gov/submissions/{name}"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
FULLTEXT_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession}.txt"

DEFAULT_DEST = Path(__file__).parent.parent / "data" / "edgar"
MANIFEST_NAME = "manifest.parquet"
MANIFEST_COLUMNS = ["ticker", "cik", "accession", "form", "filing_date", "items",
                    "primary_doc", "url", "path", "size_bytes", "downloaded_at"]

# Manifest alle N neuen Downloads zwischenspeichern (Absturz kostet max. N Doks)
_SAVE_EVERY = 25

FetchJson = Callable[[str], Optional[dict]]
FetchBytes = Callable[[str], Optional[bytes]]


@dataclass
class FilingMeta:
    """Eine 8-K-Zeile aus der Submissions-API (Punkt-in-Zeit: filing_date)."""
    ticker: str
    cik: int
    accession: str          # z. B. 0000320193-24-000001
    form: str
    filing_date: str        # YYYY-MM-DD
    items: str              # z. B. "2.02,9.01"
    primary_doc: str        # Dateiname des Primärdokuments (kann leer sein)
    url: str                # bevorzugte Download-URL

    @property
    def acc_nodash(self) -> str:
        return self.accession.replace("-", "")


class SecThrottle:
    """Einfache Ratenbremse: garantiert >= 1/max_per_sec Abstand je Request."""

    def __init__(self, max_per_sec: float = 5.0):
        self._min_gap = 1.0 / max(0.1, max_per_sec)
        self._last = 0.0

    def wait(self) -> None:
        gap = self._min_gap - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


# ── Netz-Schicht (im Test durch Fakes ersetzt) ───────────────────────────────

def make_default_fetchers(throttle: SecThrottle) -> Tuple[FetchJson, FetchBytes]:
    from system.http import http_get, sec_user_agent
    headers = {"User-Agent": sec_user_agent()}

    def fetch_json(url: str) -> Optional[dict]:
        throttle.wait()
        try:
            resp = http_get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    def fetch_bytes(url: str) -> Optional[bytes]:
        throttle.wait()
        try:
            resp = http_get(url, headers=headers, timeout=60)
            if resp.status_code != 200 or not resp.content:
                return None
            return resp.content
        except Exception:
            return None

    return fetch_json, fetch_bytes


# ── CIK-Auflösung ────────────────────────────────────────────────────────────

def resolve_cik(ticker: str, fetch_json: FetchJson,
                cik_map_cache: Dict[str, int]) -> Optional[int]:
    """Builtin-Map des Live-Collectors zuerst, dann company_tickers.json."""
    from collectors.sec_edgar_collector import _BUILTIN_CIKS, _TICKERS_URL
    t = ticker.upper()
    if t in _BUILTIN_CIKS:
        return _BUILTIN_CIKS[t]
    if not cik_map_cache:
        data = fetch_json(_TICKERS_URL)
        if data:
            cik_map_cache.update({
                e["ticker"].upper(): int(e["cik_str"])
                for e in data.values() if "ticker" in e and "cik_str" in e
            })
    return cik_map_cache.get(t)


# ── Submissions-API: Historie einsammeln ─────────────────────────────────────

def _page_columns(page: dict) -> dict:
    """Hauptdatei nistet die Spalten unter filings.recent, Archiv-Seiten sind flach."""
    if "filings" in page:
        return page.get("filings", {}).get("recent", {})
    return page


def _rows_from_page(page: dict, ticker: str, cik: int,
                    forms: set, since: str) -> List[FilingMeta]:
    cols = _page_columns(page)
    forms_col = cols.get("form", [])
    dates = cols.get("filingDate", [])
    accs = cols.get("accessionNumber", [])
    items = cols.get("items", [])
    docs = cols.get("primaryDocument", [])
    out: List[FilingMeta] = []
    for i, form in enumerate(forms_col):
        if form not in forms or i >= len(accs) or i >= len(dates):
            continue
        filing_date = str(dates[i])[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}$", filing_date) or filing_date < since:
            continue
        accession = str(accs[i])
        primary = str(docs[i]) if i < len(docs) and docs[i] else ""
        acc_nodash = accession.replace("-", "")
        url = (DOC_URL.format(cik=cik, acc_nodash=acc_nodash, doc=primary) if primary
               else FULLTEXT_URL.format(cik=cik, acc_nodash=acc_nodash, accession=accession))
        out.append(FilingMeta(
            ticker=ticker, cik=cik, accession=accession, form=form,
            filing_date=filing_date, items=str(items[i]) if i < len(items) else "",
            primary_doc=primary, url=url,
        ))
    return out


def collect_filings(ticker: str, cik: int, fetch_json: FetchJson,
                    forms: set, since: str) -> List[FilingMeta]:
    """Komplette Filing-Historie eines CIK: Hauptdatei + alle Archiv-Seiten."""
    main_page = fetch_json(SUBMISSIONS_URL.format(cik=cik))
    if not main_page:
        return []
    rows = _rows_from_page(main_page, ticker, cik, forms, since)
    for extra in main_page.get("filings", {}).get("files", []):
        name = extra.get("name", "")
        # Archiv-Seite überspringen, wenn ihr Zeitraum komplett vor `since` liegt
        if not name or str(extra.get("filingTo", "9999"))[:10] < since:
            continue
        page = fetch_json(ARCHIVE_URL.format(name=name))
        if page:
            rows.extend(_rows_from_page(page, ticker, cik, forms, since))
    # Dedupe (recent/Archiv können sich überlappen), älteste zuerst
    seen: Dict[str, FilingMeta] = {}
    for m in rows:
        seen.setdefault(m.accession, m)
    return sorted(seen.values(), key=lambda m: m.filing_date)


# ── Download + Manifest ──────────────────────────────────────────────────────

def download_filing(meta: FilingMeta, dest_root: Path,
                    fetch_bytes: FetchBytes) -> Optional[Tuple[Path, int, str]]:
    """Lädt das Primärdokument (Fallback: komplette Submission-TXT).
    Rückgabe (pfad, bytes, benutzte_url) oder None bei Fehlschlag."""
    target = dest_root / str(meta.cik) / f"{meta.acc_nodash}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    content, used_url = None, meta.url
    if meta.primary_doc:
        content = fetch_bytes(meta.url)
    if content is None:
        used_url = FULLTEXT_URL.format(cik=meta.cik, acc_nodash=meta.acc_nodash,
                                       accession=meta.accession)
        content = fetch_bytes(used_url)
    if content is None:
        return None
    tmp = target.with_suffix(".part")
    tmp.write_bytes(content)
    tmp.replace(target)                       # atomar: nie halbe Dateien im Archiv
    return target, len(content), used_url


def load_manifest(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=MANIFEST_COLUMNS)


def save_manifest(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def already_done(manifest: pd.DataFrame, dest_root: Path) -> set:
    """Accessions, die als geladen gelten (Manifest-Eintrag UND Datei existiert)."""
    done = set()
    if manifest.empty:
        return done
    for _, row in manifest.iterrows():
        p = row.get("path", "")
        if p and (dest_root.parent.parent / p).exists():
            done.add(row["accession"])
    return done


# ── CLI ──────────────────────────────────────────────────────────────────────

def _universe(args) -> List[str]:
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="EDGAR-8-K-Archiv aufbauen (Vision V0.2)")
    ap.add_argument("--tickers", nargs="*", default=None,
                    help="Default: config.watchlist")
    ap.add_argument("--since", default="2005-01-01", help="frühestes filing_date")
    ap.add_argument("--forms", nargs="*", default=["8-K"],
                    help="exakte Form-Typen (z. B. 8-K 8-K/A)")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--rps", type=float, default=5.0,
                    help="max. Requests/s (SEC-Limit 10; konservativ bleiben)")
    ap.add_argument("--limit-per-ticker", type=int, default=0,
                    help="nur die N ältesten neuen Filings je Ticker (0 = alle)")
    ap.add_argument("--manifest-only", action="store_true",
                    help="nur Metadaten ins Manifest, keine Dokumente laden")
    args = ap.parse_args()

    if not os.getenv("SEC_CONTACT_EMAIL", "").strip():
        print("ABBRUCH: SEC_CONTACT_EMAIL ist nicht gesetzt (.env). Die SEC "
              "verlangt eine Kontakt-Mail im User-Agent — ohne sie riskiert "
              "der Massen-Download einen IP-Block.")
        sys.exit(2)

    dest_root = Path(args.dest)
    manifest_path = dest_root / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    done = already_done(manifest, dest_root)

    throttle = SecThrottle(max_per_sec=args.rps)
    fetch_json, fetch_bytes = make_default_fetchers(throttle)
    cik_cache: Dict[str, int] = {}
    universe = _universe(args)
    forms = set(args.forms)

    print(f"EDGAR-Download: {len(universe)} Ticker | Forms {sorted(forms)} | "
          f"seit {args.since} | {args.rps} req/s | Ziel {dest_root}")
    print(f"Manifest: {len(manifest)} Einträge, davon {len(done)} vollständig.\n")

    new_rows: List[dict] = []
    repo_root = dest_root.parent.parent
    n_downloaded = n_failed = 0
    try:
        for ticker in universe:
            cik = resolve_cik(ticker, fetch_json, cik_cache)
            if not cik:
                print(f"  {ticker}: kein CIK auflösbar — übersprungen")
                continue
            metas = collect_filings(ticker, cik, fetch_json, forms, args.since)
            todo = [m for m in metas if m.accession not in done]
            if args.limit_per_ticker > 0:
                todo = todo[:args.limit_per_ticker]
            print(f"  {ticker} (CIK {cik}): {len(metas)} Filings, {len(todo)} neu")
            for meta in todo:
                row = asdict(meta)
                row["path"], row["size_bytes"], row["downloaded_at"] = "", 0, ""
                if not args.manifest_only:
                    result = download_filing(meta, dest_root, fetch_bytes)
                    if result is None:
                        n_failed += 1
                        continue                # nicht ins Manifest → nächster Lauf
                    path, size, row["url"] = result
                    row["path"] = str(path.relative_to(repo_root))
                    row["size_bytes"] = size
                    row["downloaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    n_downloaded += 1
                new_rows.append(row)
                done.add(meta.accession)
                if len(new_rows) % _SAVE_EVERY == 0:
                    manifest = pd.concat([manifest, pd.DataFrame(new_rows)], ignore_index=True)
                    save_manifest(manifest, manifest_path)
                    new_rows = []
    except KeyboardInterrupt:
        print("\nUnterbrochen — Manifest wird gesichert, Lauf ist wiederaufsetzbar.")

    if new_rows:
        manifest = pd.concat([manifest, pd.DataFrame(new_rows)], ignore_index=True)
    save_manifest(manifest, manifest_path)
    print(f"\nFertig: {n_downloaded} Dokumente geladen, {n_failed} Fehlschläge "
          f"(werden beim nächsten Lauf erneut versucht). "
          f"Manifest: {len(manifest)} Einträge → {manifest_path}")


if __name__ == "__main__":
    main()
