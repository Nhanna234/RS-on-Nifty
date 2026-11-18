"""
Data layer for the backtest (no Streamlit imports, so it can be tested on its own).
Adapted from the EMA-Reclaim app's data.py.

* Stock prices : Yahoo Finance daily OHLC, split + dividend adjusted, kept in a per-symbol
                 store on disk. Every index shares the same downloads and a refresh only
                 fetches the last few days. If Yahoo re-adjusts history (new split or
                 dividend) the symbol is downloaded again.
* RS index     : Nifty 50 price index (^NSEI) from Yahoo, the same series the screener uses.
* Benchmark    : Nifty 50 Total Return Index from niftyindices.com; the Nifty 50 price
                 index (niftyindices, then Yahoo) if the TRI cannot be fetched.
"""

from __future__ import annotations

import json
import os
import pickle
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NI_BASE = "https://www.niftyindices.com"
DATA_START = "2010-01-01"           # earliest daily data kept; backtests can start from 2012
EARLIEST_START = "2012-01"
FIELDS = ("Open", "High", "Low", "Close")
SESSION_CUTOFF_IST = (17, 0)        # after 5 pm IST the day's prices count as final


# ════════════════════════════════════════════════════════════════════ time
def now_ist() -> datetime:
    """The one clock the backtest uses (kept in one place so it can be tested at chosen times)."""
    return datetime.now(IST)


def last_cutoff(now: datetime | None = None) -> datetime:
    """Most recent weekday 5 pm IST that has passed; prices fetched before it are stale."""
    now = (now or now_ist()).astimezone(IST)
    d = now.date()
    if (now.hour, now.minute) < SESSION_CUTOFF_IST:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, *SESSION_CUTOFF_IST, tzinfo=IST)


def last_completed_month(now: datetime | None = None) -> pd.Period:
    """The current month once 5 pm IST has passed on its last weekday (no trading day is left in it);
    otherwise the previous month. A month that ends on a market holiday completes a day late here,
    which is safe."""
    now = (now or now_ist()).astimezone(IST)
    p = pd.Period(now.date(), "M")
    last_weekday = p.end_time.normalize()
    while last_weekday.weekday() >= 5:
        last_weekday -= pd.Timedelta(days=1)
    today = pd.Timestamp(now.date())
    if today > last_weekday or (today == last_weekday and (now.hour, now.minute) >= SESSION_CUTOFF_IST):
        return p
    return p - 1


def last_session_date(now: datetime | None = None):
    """The most recent weekday whose closing prices should be available (market holidays aside)."""
    return last_cutoff(now).date()


# ═══════════════════════════════════════════════════════════════ prices
def _clean(s) -> str:
    return str(s).strip().upper().replace(".NS", "")


def _yf_download(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    """One yfinance call for a batch. Returns {SYMBOL: OHLC frame} for tickers with data."""
    import yfinance as yf
    raw = yf.download(tickers, start=start, auto_adjust=True, actions=False,
                      progress=False, threads=True, group_by="column")
    out = {}
    if raw is None or raw.empty:
        return out
    if not isinstance(raw.columns, pd.MultiIndex):        # single ticker, flat columns
        raw.columns = pd.MultiIndex.from_product([raw.columns, tickers[:1]])
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    lvl = 1 if set(FIELDS) & set(raw.columns.get_level_values(0)) else 0
    for t in tickers:
        try:
            df = raw.xs(t, level=lvl, axis=1)
        except KeyError:
            continue
        df = df.reindex(columns=list(FIELDS)).dropna(how="all", subset=["Close"])
        if len(df):
            out[_clean(t)] = df.astype("float64")
    return out


class PriceStore:
    """Per-symbol daily OHLC cache (memory + disk), shared by every index and every user."""

    def __init__(self, cache_dir: str, downloader=None, batch: int = 50, pause: float = 1.0):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.mem: dict[str, dict] = {}
        self.downloader = downloader or (lambda tickers, start: _yf_download(tickers, start))
        self.batch = batch
        self.pause = pause

    def _path(self, sym: str) -> str:
        return os.path.join(self.dir, quote(sym, safe="") + ".pkl")

    def _get(self, sym: str) -> dict | None:
        if sym in self.mem:
            return self.mem[sym]
        p = self._path(sym)
        if os.path.exists(p):
            try:
                with open(p, "rb") as fh:
                    rec = pickle.load(fh)
                self.mem[sym] = rec
                return rec
            except Exception:  # noqa: BLE001
                return None
        return None

    def _put(self, sym: str, df: pd.DataFrame, fetched: datetime):
        df = df[~df.index.duplicated(keep="last")].sort_index()
        rec = {"df": df.astype("float32"), "fetched": fetched}
        self.mem[sym] = rec
        try:
            with open(self._path(sym), "wb") as fh:
                pickle.dump(rec, fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:  # noqa: BLE001
            pass

    def plan(self, symbols, fresh_after: datetime, need_date=None) -> tuple[list, list]:
        """need_date: the latest session whose close should be present. A stock still trading recently
        but missing that close (Yahoo had not published it at the last fetch) is fetched again,
        at most every 20 minutes."""
        full, stale = [], []
        now = now_ist()
        need = pd.Timestamp(need_date) if need_date is not None else None
        for s in symbols:
            rec = self._get(s)
            if rec is None or rec["df"].empty:
                if rec is None or rec["fetched"] < fresh_after:
                    full.append(s)
            elif rec["fetched"] < fresh_after:
                stale.append(s)
            elif need is not None:
                last = rec["df"].index.max()
                if need - pd.Timedelta(days=10) <= last < need and rec["fetched"] < now - timedelta(minutes=20):
                    stale.append(s)
        return full, stale

    def _fetch(self, syms: list[str], start: str, progress=None, done0=0, total=1) -> dict:
        got = {}
        for i in range(0, len(syms), self.batch):
            chunk = syms[i:i + self.batch]
            for attempt in range(3):
                try:
                    got.update(self.downloader([f"{s}.NS" for s in chunk], start))
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(self.pause * (attempt + 2))
            if progress:
                progress(done0 + min(i + self.batch, len(syms)), total)
            time.sleep(self.pause)
        missing = [s for s in syms if s not in got]
        if missing and len(missing) < len(syms):            # one slower retry for gaps
            time.sleep(self.pause * 3)
            for i in range(0, len(missing), 20):
                try:
                    got.update(self.downloader([f"{s}.NS" for s in missing[i:i + 20]], start))
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(self.pause)
        return got

    def update(self, symbols, fresh_after: datetime, progress=None, need_date=None) -> dict:
        """Bring `symbols` up to date. Returns counts for display."""
        with self.lock:
            full, stale = self.plan(symbols, fresh_after, need_date)
            now = now_ist()
            stats = {"full": len(full), "refreshed": len(stale), "re-downloaded": 0, "failed": []}
            total = len(full) + len(stale)
            if total == 0:
                return stats
            redo = []
            cutoff_old = pd.Timestamp(now.date()) - pd.Timedelta(days=40)
            old = [s for s in stale if self._get(s)["df"].index.max() < cutoff_old]
            stale = [s for s in stale if s not in old]
            full = full + old
            if stale:
                last = min(self._get(s)["df"].index.max() for s in stale)
                start = (last - timedelta(days=12)).strftime("%Y-%m-%d")
                got = self._fetch(stale, start, progress, 0, total)
                for s in stale:
                    prev = self._get(s)["df"]
                    new = got.get(s)
                    if new is None or new.empty:
                        self._put(s, prev, now)              # nothing new (holiday): keep
                        continue
                    common = prev.index.intersection(new.index)
                    if len(common):
                        ratio = (new.loc[common, "Close"] / prev.loc[common, "Close"].astype("float64")).dropna()
                        if len(ratio) and (ratio - 1).abs().max() > 0.002:
                            redo.append(s)                    # Yahoo re-adjusted history
                            continue
                    self._put(s, pd.concat([prev.astype("float64"), new]), now)
            full_all = full + redo
            stats["re-downloaded"] = len(redo)
            if full_all:
                got = self._fetch(full_all, DATA_START, progress, len(stale), total + len(redo))
                for s in full_all:
                    df = got.get(s)
                    if df is None or df.empty:
                        stats["failed"].append(s)
                        if self._get(s) is None:
                            self._put(s, pd.DataFrame(columns=list(FIELDS), dtype="float64"), now)
                    else:
                        self._put(s, df, now)
            return stats

    def last_date(self, symbols):
        """Latest date with a price across `symbols`."""
        dates = [rec["df"].index.max() for s in symbols
                 if (rec := self._get(s)) is not None and not rec["df"].empty]
        return max(dates) if dates else None

    def panel(self, symbols) -> dict[str, pd.DataFrame]:
        """{field: DataFrame(date x symbol)} for the symbols that have data."""
        frames = {}
        for s in symbols:
            rec = self._get(s)
            if rec is not None and not rec["df"].empty:
                frames[s] = rec["df"]
        if not frames:
            raise RuntimeError("No price data is available for this universe.")
        return {f: pd.concat({s: df[f] for s, df in frames.items()}, axis=1).sort_index().astype("float64")
                for f in FIELDS}


# ═══════════════════════════════════════════════════════════ index series
def _ni_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get(f"{NI_BASE}/reports/historical-data", timeout=15)
    except requests.RequestException:
        pass
    return s


def _ni_rows(s: requests.Session, endpoints: list[str], api_name: str, start: date, end: date) -> list[dict]:
    cinfo = (f"{{'name':'{api_name}','startDate':'{start:%d-%b-%Y}',"
             f"'endDate':'{end:%d-%b-%Y}','indexName':'{api_name}'}}")
    last_err = None
    for ep in endpoints:
        try:
            r = s.post(ep, data=json.dumps({"cinfo": cinfo}), timeout=30,
                       headers={"Content-Type": "application/json; charset=utf-8",
                                "Accept": "application/json, text/javascript, */*; q=0.01",
                                "X-Requested-With": "XMLHttpRequest", "Origin": NI_BASE,
                                "Referer": f"{NI_BASE}/reports/historical-data"})
            r.raise_for_status()
            txt = r.text.strip()
            if not txt.startswith(("[", "{")):
                raise ValueError("non-JSON response (blocked or changed)")
            data = json.loads(txt)
            if isinstance(data, dict):
                data = data.get("d", [])
                data = json.loads(data) if isinstance(data, str) else data
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"niftyindices failed: {last_err}")


def _rows_to_series(rows: list[dict], kind: str) -> pd.Series:
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("no rows")
    dcol = next(c for c in df.columns if "date" in c.lower())
    if kind == "TRI":
        vcol = next(c for c in df.columns if "totalreturn" in c.lower().replace(" ", "")
                    or c.upper() in ("TRI", "TOTAL RETURNS INDEX"))
    else:
        vcol = next(c for c in df.columns if c.upper() == "CLOSE")
    dates = pd.to_datetime(df[dcol], format="mixed", dayfirst=True, errors="coerce")
    ser = pd.to_numeric(df[vcol].astype(str).str.replace(",", ""), errors="coerce")
    ser.index = dates
    ser = ser[ser.index.notna()].sort_index()
    return ser[~ser.index.duplicated(keep="last")].dropna()


def fetch_index_niftyindices(api_name: str, start: date, end: date, kind: str) -> pd.Series:
    eps = ([f"{NI_BASE}/BackPage/getTotalReturnIndexString", f"{NI_BASE}/Backpage.aspx/getTotalReturnIndexString"]
           if kind == "TRI" else
           [f"{NI_BASE}/BackPage/getHistoricaldatatabletoString", f"{NI_BASE}/Backpage.aspx/getHistoricaldatatabletoString"])
    s, rows, cur = _ni_session(), [], start
    while cur <= end:
        nxt = min(cur + timedelta(days=360), end)
        rows += _ni_rows(s, eps, api_name, cur, nxt)
        cur = nxt + timedelta(days=1)
        time.sleep(0.3)
    return _rows_to_series(rows, kind)


def fetch_index_yahoo(ticker: str, start: date, end: date) -> pd.Series:
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end + timedelta(days=1), auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise ValueError(f"Yahoo returned nothing for {ticker}")
    c = df["Close"]
    c = c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c.dropna()


def monthly_last(ser: pd.Series) -> pd.Series:
    return ser.groupby(ser.index.to_period("M")).last()


def rs_index_monthly(start: date, end: date) -> pd.Series:
    """Nifty 50 price index month-end closes: the denominator of relative strength."""
    return monthly_last(fetch_index_yahoo("^NSEI", start, end))


def benchmark_monthly(start: date, end: date) -> tuple[pd.Series, str]:
    """Nifty 50 TRI month-end values, with the price index as fallback."""
    errs = []
    for kind, label in (("TRI", "Nifty 50 Total Return Index (niftyindices.com)"),
                        ("PRICE", "Nifty 50 price index (niftyindices.com); TRI unavailable")):
        try:
            return monthly_last(fetch_index_niftyindices("NIFTY 50", start, end, kind)), label
        except Exception as e:  # noqa: BLE001
            errs.append(f"{label}: {e}")
    return monthly_last(fetch_index_yahoo("^NSEI", start, end)), \
        "Nifty 50 price index (Yahoo); TRI unavailable, so dividends are not included"
