"""
Tests für den EDGAR-8-K-Archiv-Download (Vision V0.2, Deep-Research Phase 1).

Kern-Zusagen: (1) Pagination — Hauptdatei + Archiv-Seiten werden zusammengeführt,
Duplikate dedupliziert, nach filing_date sortiert. (2) Filter — nur gewünschte
Forms und filing_date >= since; Archiv-Seiten komplett vor `since` werden gar
nicht erst geladen. (3) Idempotenz — Accessions mit Manifest-Eintrag UND
existierender Datei gelten als erledigt. (4) Download — Primärdokument zuerst,
Fallback auf die Komplett-Submission, atomare Ablage unter {cik}/{acc}.txt.
Netzfrei: fetch_json/fetch_bytes werden durch Fakes ersetzt.
"""
from pathlib import Path

import pandas as pd

from scripts.edgar_download import (FilingMeta, MANIFEST_COLUMNS, SecThrottle,
                                    already_done, collect_filings,
                                    download_filing, load_manifest,
                                    save_manifest)

CIK = 320193


def _meta(acc="0000320193-24-000001", primary="doc.htm", **kw):
    defaults = dict(ticker="AAPL", cik=CIK, accession=acc, form="8-K",
                    filing_date="2024-02-01", items="2.02,9.01",
                    primary_doc=primary,
                    url=f"https://www.sec.gov/Archives/edgar/data/{CIK}/"
                        f"{acc.replace('-', '')}/{primary}")
    defaults.update(kw)
    return FilingMeta(**defaults)


def _main_page(files=()):
    return {
        "cik": CIK,
        "filings": {
            "recent": {
                "form": ["8-K", "10-K", "8-K"],
                "filingDate": ["2024-02-01", "2023-11-03", "2023-08-04"],
                "accessionNumber": ["0000320193-24-000001",
                                    "0000320193-23-000106",
                                    "0000320193-23-000077"],
                "items": ["2.02,9.01", "", "2.02"],
                "primaryDocument": ["a.htm", "b.htm", "c.htm"],
            },
            "files": list(files),
        },
    }


# Archiv-Seiten sind flache Spalten-Dicts (kein filings.recent-Nest)
_ARCHIVE_PAGE = {
    "form": ["8-K", "8-K"],
    "filingDate": ["2010-01-25", "2003-04-16"],
    "accessionNumber": ["0000320193-10-000001", "0000320193-03-000001"],
    "items": ["2.02", "9.01"],
    "primaryDocument": ["old.htm", ""],
}


def test_collect_filings_merges_pages_and_filters():
    urls = []

    def fetch_json(url):
        urls.append(url)
        if "submissions/CIK" in url:
            return _main_page(files=[
                {"name": "arch-001.json", "filingFrom": "2001-01-01",
                 "filingTo": "2010-12-31"},
            ])
        return _ARCHIVE_PAGE

    metas = collect_filings("AAPL", CIK, fetch_json, {"8-K"}, since="2005-01-01")
    # 10-K gefiltert, Archiv-Filing von 2003 unter `since` gefiltert
    assert [m.filing_date for m in metas] == ["2010-01-25", "2023-08-04", "2024-02-01"]
    assert all(m.form == "8-K" for m in metas)
    assert metas[0].primary_doc == "old.htm"
    assert len(urls) == 2  # Hauptdatei + 1 Archiv-Seite


def test_collect_filings_skips_archive_pages_before_since():
    fetched = []

    def fetch_json(url):
        fetched.append(url)
        return _main_page(files=[
            {"name": "arch-001.json", "filingFrom": "1998-01-01",
             "filingTo": "2003-12-31"},   # komplett vor since → nicht laden
        ])

    metas = collect_filings("AAPL", CIK, fetch_json, {"8-K"}, since="2005-01-01")
    assert len(fetched) == 1              # nur die Hauptdatei
    assert len(metas) == 2


def test_collect_filings_dedupes_overlap():
    overlap = {
        "form": ["8-K"],
        "filingDate": ["2024-02-01"],
        "accessionNumber": ["0000320193-24-000001"],   # auch in recent
        "items": ["2.02,9.01"],
        "primaryDocument": ["a.htm"],
    }

    def fetch_json(url):
        if "submissions/CIK" in url:
            return _main_page(files=[{"name": "arch.json", "filingTo": "2024-12-31"}])
        return overlap

    metas = collect_filings("AAPL", CIK, fetch_json, {"8-K"}, since="2005-01-01")
    assert len([m for m in metas if m.accession == "0000320193-24-000001"]) == 1


def test_download_primary_doc_and_atomic_layout(tmp_path):
    meta = _meta()

    def fetch_bytes(url):
        assert url.endswith("doc.htm")
        return b"<html>8-K</html>"

    result = download_filing(meta, tmp_path, fetch_bytes)
    assert result is not None
    path, size, used_url = result
    assert path == tmp_path / str(CIK) / f"{meta.acc_nodash}.txt"
    assert path.read_bytes() == b"<html>8-K</html>" and size == 16
    assert not path.with_suffix(".part").exists()      # keine halben Dateien


def test_download_falls_back_to_fulltext(tmp_path):
    meta = _meta()
    calls = []

    def fetch_bytes(url):
        calls.append(url)
        if url.endswith(".htm"):
            return None                                # Primärdoc weg (404)
        return b"FULL SUBMISSION"

    result = download_filing(meta, tmp_path, fetch_bytes)
    assert result is not None
    path, size, used_url = result
    assert used_url.endswith(f"{meta.accession}.txt")
    assert path.read_bytes() == b"FULL SUBMISSION"
    assert len(calls) == 2


def test_download_failure_returns_none(tmp_path):
    result = download_filing(_meta(), tmp_path, lambda url: None)
    assert result is None
    assert not any(tmp_path.rglob("*.txt"))            # nichts Halbes abgelegt


def test_manifest_roundtrip_and_already_done(tmp_path):
    dest = tmp_path / "data" / "edgar"
    mpath = dest / "manifest.parquet"
    assert load_manifest(mpath).empty

    # Eintrag MIT existierender Datei → done; ohne Datei → nicht done
    fpath = dest / str(CIK) / "acc1.txt"
    fpath.parent.mkdir(parents=True)
    fpath.write_text("x")
    df = pd.DataFrame([
        {**{c: "" for c in MANIFEST_COLUMNS}, "accession": "A-1",
         "path": str(fpath.relative_to(tmp_path))},
        {**{c: "" for c in MANIFEST_COLUMNS}, "accession": "A-2",
         "path": "data/edgar/999/missing.txt"},
        {**{c: "" for c in MANIFEST_COLUMNS}, "accession": "A-3", "path": ""},
    ])
    save_manifest(df, mpath)
    reloaded = load_manifest(mpath)
    assert list(reloaded["accession"]) == ["A-1", "A-2", "A-3"]
    assert already_done(reloaded, dest) == {"A-1"}


def test_throttle_enforces_gap():
    import time
    th = SecThrottle(max_per_sec=50)                   # 20 ms Abstand
    t0 = time.monotonic()
    for _ in range(3):
        th.wait()
    assert time.monotonic() - t0 >= 0.04               # mind. 2 volle Lücken
