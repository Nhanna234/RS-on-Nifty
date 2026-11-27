"""
Saved price file ("snapshot") so the app does not download every stock's history after Streamlit restarts it.

No Streamlit imports, so it can be tested and run on its own.

How it works
------------
* Every weekday evening a GitHub job (.github/workflows/price-snapshot.yml) runs
  `python price_snapshot.py build --market IN` (and US). It loads the previous snapshot, brings every
  stock of the universe up to date with the same PriceStore code the app uses, packs the store into one
  compressed file and publishes it on the repository's Releases page under the tag SNAPSHOT_TAG.
* When the app starts on a fresh server (empty price folder), restore() downloads that file and unpacks it.
  The app then only fetches what is newer. If the download fails, the app downloads prices itself, as before.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from datetime import datetime
from urllib.parse import unquote

import pandas as pd
import requests

import bt_data
import settings
import us_universe

SNAPSHOT_REPO = "Nhanna234/RS-on-Nifty"          # GitHub repository whose Releases hold the files
SNAPSHOT_TAG = "price-store"                     # the release (tag) the files are attached to
SNAPSHOT_FILES = {"IN": "prices_in.tar.gz", "US": "prices_us.tar.gz"}
MARKER = ".snapshot.json"                        # written into the price folder after a restore
MIN_SHARE_WITH_DATA = 0.90                       # a build with fewer stocks priced than this is not published
DOWNLOAD_TIMEOUT = 90                            # seconds
UA = "Mozilla/5.0 (RS Leaderboard price snapshot)"


def snapshot_url(market: str) -> str:
    return f"https://github.com/{SNAPSHOT_REPO}/releases/download/{SNAPSHOT_TAG}/{SNAPSHOT_FILES[market]}"


# ─────────────────────────────────────────────────────────── packing and unpacking
def pack(store_dir: str, market: str, extra: dict | None = None) -> bytes:
    """All the price records of a store folder in one .tar.gz, with a small manifest."""
    files = sorted(f for f in os.listdir(store_dir) if f.endswith(".pkl"))
    manifest = {"market": market, "created": bt_data.now_ist().isoformat(), "stocks": len(files),
                "pandas": pd.__version__, **(extra or {})}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz", compresslevel=6) as tar:
        data = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for f in files:
            tar.add(os.path.join(store_dir, f), arcname=f)
    return out.getvalue()


SAFE_NAME = re.compile(r"^[A-Za-z0-9%._-]+\.pkl$")


def unpack(blob: bytes, store_dir: str) -> dict:
    """Writes the records into store_dir (only plain *.pkl names, nothing outside the folder). Returns the
    manifest. Records already in the folder are kept if they are newer."""
    os.makedirs(store_dir, exist_ok=True)
    manifest = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            if m.name == "manifest.json":
                manifest = json.loads(tar.extractfile(m).read().decode())
                continue
            if not SAFE_NAME.match(m.name):
                continue
            dest = os.path.join(store_dir, m.name)
            if os.path.exists(dest) and os.path.getmtime(dest) > m.mtime:
                continue
            data = tar.extractfile(m).read()
            tmp = dest + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
    return manifest


def restore(store_dir: str, market: str, url: str | None = None, getter=None) -> dict:
    """Fills an empty price folder from the published snapshot, once per folder.
    Returns {"restored": bool, "stocks": n, "created": ..., "error": ...}."""
    marker = os.path.join(store_dir, MARKER)
    if os.path.exists(marker):
        return {"restored": False, "reason": "already restored"}
    os.makedirs(store_dir, exist_ok=True)
    getter = getter or (lambda u: requests.get(u, headers={"User-Agent": UA}, timeout=DOWNLOAD_TIMEOUT))
    try:
        r = getter(url or snapshot_url(market))
        r.raise_for_status()
        manifest = unpack(r.content, store_dir)
        if manifest.get("market") not in (None, market):
            raise ValueError(f"snapshot is for market {manifest.get('market')}, not {market}")
        info = {"restored": True, "stocks": manifest.get("stocks"), "created": manifest.get("created")}
    except Exception as e:  # noqa: BLE001
        info = {"restored": False, "error": f"{type(e).__name__}: {e}"}
    with open(marker, "w") as fh:            # do not try again on every run of this server
        json.dump({**info, "at": bt_data.now_ist().isoformat()}, fh)
    return info


# ─────────────────────────────────────────────────────────── building (GitHub job)
def _index_symbols(fname: str) -> list[str]:
    errors = []
    for m in settings.CONSTITUENT_MIRRORS:
        url = m.format(f=fname)
        try:
            r = requests.get(url, headers={"User-Agent": bt_data.UA, "Accept": "text/csv,*/*"}, timeout=25)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [str(c).strip() for c in df.columns]
            if "Series" in df.columns:
                df = df[df["Series"].astype(str).str.strip().isin(["EQ", "BE", "BZ"])]
            syms = [str(s).strip().upper() for s in df["Symbol"].dropna()]
            if syms:
                return syms
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    raise RuntimeError("; ".join(errors))


def universe_symbols(market: str, store_dir: str) -> tuple[list[str], str]:
    """Every stock the app can ask for in this market, plus any already in the previous snapshot."""
    have = sorted(unquote(f[:-4]) for f in os.listdir(store_dir) if f.endswith(".pkl")) \
        if os.path.isdir(store_dir) else []
    if market == "US":
        df, note = us_universe.load_nasdaq100()
        return sorted(set(df["Symbol"]) | set(have)), note
    try:
        syms = _index_symbols(settings.INDEX_FILES["Nifty Total Market"])
        note = "Nifty Total Market list"
    except Exception:  # noqa: BLE001
        try:   # the total market is the Nifty 500 plus the Microcap 250
            syms = _index_symbols(settings.INDEX_FILES["Nifty 500"]) + \
                _index_symbols(settings.INDEX_FILES["Nifty Microcap 250"])
            note = "Nifty 500 + Microcap 250 lists"
        except Exception:  # noqa: BLE001
            if not have:
                raise
            syms, note = [], "stocks already in the previous file (index lists not reachable)"
    return sorted(set(syms) | set(have)), note


def build(market: str, out_path: str, work_dir: str | None = None, downloader=None, getter=None) -> dict:
    """Previous snapshot + today's prices -> out_path. Raises if too few stocks have prices."""
    work_dir = work_dir or tempfile.mkdtemp(prefix=f"prices_{market}_")
    prev = restore(work_dir, market, getter=getter)
    symbols, source = universe_symbols(market, work_dir)
    store = bt_data.PriceStore(work_dir, downloader=downloader, suffix=settings.MARKETS[market]["yahoo_suffix"])
    cutoff = bt_data.last_cutoff(market=market)
    stats = store.update(symbols, cutoff, need_date=bt_data.last_session_date(market=market))
    priced = [s for s in symbols if (rec := store._get(s)) is not None and not rec["df"].empty]
    share = len(priced) / max(1, len(symbols))
    summary = {"symbols": len(symbols), "priced": len(priced), "list": source,
               "previous": prev.get("stocks"), "latest": str(store.last_date(priced).date()) if priced else None,
               "downloaded_full": stats["full"], "refreshed": stats["refreshed"], "failed": len(stats["failed"])}
    if share < MIN_SHARE_WITH_DATA:
        raise RuntimeError(f"only {len(priced)} of {len(symbols)} stocks have prices; not publishing")
    with open(out_path, "wb") as fh:
        fh.write(pack(work_dir, market, {"latest": summary["latest"], "list": source}))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="update the snapshot for one market and write it to --out")
    b.add_argument("--market", choices=list(SNAPSHOT_FILES), required=True)
    b.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        summary = build(args.market, args.out)
        print(json.dumps(summary, indent=1))
        print(f"wrote {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB) at {datetime.now():%Y-%m-%d %H:%M}")


if __name__ == "__main__":
    sys.exit(main())
