"""
The Nasdaq-100 stock list (no Streamlit imports, so it can be tested on its own).

Where the list comes from, in order:
1. Nasdaq's own website (api.nasdaq.com, the index operator): the current members.
   Their industry comes from Wikipedia's Nasdaq-100 page (ICB industry and subsector),
   or from the snapshot below if Wikipedia does not answer.
2. If Nasdaq does not answer: Wikipedia's Nasdaq-100 components table.
3. If neither answers: data/nasdaq100.csv, a snapshot kept in this repository
   (Wikipedia's table as of SNAPSHOT_DATE). Refresh it after an index change.

Symbols are kept in Yahoo Finance form: a class letter after a dot becomes a dash (BRK.B -> BRK-B).
"""

from __future__ import annotations

import io
import os
import re

import pandas as pd
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NASDAQ_API = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nasdaq100.csv")
SNAPSHOT_DATE = "20 Nov 2026"
MIN_MEMBERS = 90                      # a list shorter than this is treated as a failed download
COLUMNS = ["Symbol", "Company Name", "Industry", "Subsector", "ISIN Code"]


def yahoo_symbol(s) -> str:
    """'brk.b ' -> 'BRK-B'; also strips an exchange prefix such as 'NASDAQ:'."""
    s = str(s).strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    return re.sub(r"[./]", "-", s)


def _get(url: str, accept: str) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"},
                     timeout=25)
    r.raise_for_status()
    return r


def fetch_nasdaq_api() -> pd.DataFrame:
    """Members from Nasdaq: Symbol and Company Name."""
    rows = _get(NASDAQ_API, "application/json, text/plain, */*").json()["data"]["data"]["rows"]
    df = pd.DataFrame(rows)
    df = pd.DataFrame({"Symbol": df["symbol"].map(yahoo_symbol),
                       "Company Name": df["companyName"].astype(str).str.replace(r"\s+Common Stock.*$", "", regex=True)
                       .str.replace(r"\s+(Class [A-C]|Ordinary Shares|American Depositary Shares).*$", "", regex=True)
                       .str.strip()})
    if len(df) < MIN_MEMBERS:
        raise ValueError(f"Nasdaq returned only {len(df)} members")
    return df


def parse_wikipedia(html: str) -> pd.DataFrame:
    """The components table: Ticker, Company and industry columns, whatever the classification is called."""
    for t in pd.read_html(io.StringIO(html)):
        cols = {str(c).strip().lower(): c for c in t.columns}
        tick = next((cols[c] for c in cols if c in ("ticker", "symbol")), None)
        comp = next((cols[c] for c in cols if c in ("company", "security", "name")), None)
        if tick is None or comp is None or len(t) < MIN_MEMBERS:
            continue
        sub = next((cols[c] for c in cols if "sub" in c), None)
        ind = next((cols[c] for c in cols if ("industry" in c or "sector" in c) and "sub" not in c), None)
        return pd.DataFrame({"Symbol": t[tick].map(yahoo_symbol), "Company Name": t[comp].astype(str).str.strip(),
                             "Industry": t[ind].astype(str).str.strip() if ind is not None else "",
                             "Subsector": t[sub].astype(str).str.strip() if sub is not None else ""})
    raise ValueError("no components table found on the page")


def fetch_wikipedia() -> pd.DataFrame:
    return parse_wikipedia(_get(WIKI_URL, "text/html").text)


def load_snapshot() -> pd.DataFrame:
    df = pd.read_csv(SNAPSHOT, dtype=str).fillna("")
    df["Symbol"] = df["Symbol"].map(yahoo_symbol)
    return df


def _finish(df: pd.DataFrame, details: pd.DataFrame) -> pd.DataFrame:
    """Fill industry, subsector and ISIN from `details` where df lacks them."""
    df = df.copy()
    info = details.drop_duplicates("Symbol").set_index("Symbol")
    for c in COLUMNS[1:]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)
        if c in info.columns:
            fill = df["Symbol"].map(info[c]).fillna("")
            df[c] = df[c].where(df[c].str.strip() != "", fill)
    df["Industry"] = df["Industry"].replace("", "N/A")
    return df.drop_duplicates("Symbol")[COLUMNS].reset_index(drop=True)


def load_nasdaq100(fetch_api=fetch_nasdaq_api, fetch_wiki=fetch_wikipedia, snapshot=load_snapshot):
    """Returns (table with COLUMNS, note on where it came from)."""
    snap = snapshot()
    try:
        members = fetch_api()
    except Exception:  # noqa: BLE001
        members = None
    try:
        wiki = fetch_wiki()
    except Exception:  # noqa: BLE001
        wiki = None
    if members is not None:
        details = pd.concat([wiki, snap]) if wiki is not None else snap
        return _finish(members, details), ("Nasdaq's current list; industries from Wikipedia" if wiki is not None
                                           else "Nasdaq's current list; industries from the saved snapshot")
    if wiki is not None:
        return _finish(wiki, snap), "Wikipedia's Nasdaq-100 table (Nasdaq did not answer)"
    return _finish(snap, snap), f"saved snapshot of {SNAPSHOT_DATE} (Nasdaq and Wikipedia did not answer)"
