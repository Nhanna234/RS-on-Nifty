import streamlit as st
import pandas as pd
import yfinance as yf
import io
import os
import tempfile
import base64
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from openpyxl.styles import PatternFill, Alignment, Font
import streamlit.components.v1 as components

import importlib

import bt_data
import bt_engine
import lender_filter

# After a git push, Streamlit Cloud reruns this file but can keep the previous copy of these
# modules in memory. Reload any whose file has changed since it was loaded.
for _mod in (bt_data, bt_engine, lender_filter):
    _mtime = os.path.getmtime(_mod.__file__)
    if getattr(_mod, "_loaded_mtime", None) != _mtime:
        importlib.reload(_mod)
        _mod._loaded_mtime = _mtime

# --- STANDARD INDEX UNIVERSES ---
# Official NSE Indices constituent files (columns: Company Name, Industry, Symbol, Series, ISIN Code)
INDEX_FILES = {
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv",
    "Nifty LargeMidcap 250": "ind_niftylargemidcap250list.csv",
    "Nifty Midcap 150": "ind_niftymidcap150list.csv",
    "Nifty Smallcap 100": "ind_niftysmallcap100list.csv",
    "Nifty Smallcap 250": "ind_niftysmallcap250list.csv",
    "Nifty Microcap 250": "ind_niftymicrocap250_list.csv",
    "Nifty 500": "ind_nifty500list.csv",
    "Nifty Total Market": "ind_niftytotalmarket_list.csv",
}
DEFAULT_INDEX = "Nifty Total Market"
CONSTITUENT_MIRRORS = [
    "https://niftyindices.com/IndexConstituent/{f}",
    "https://www.niftyindices.com/IndexConstituent/{f}",
    "https://nsearchives.nseindia.com/content/indices/{f}",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def clean_symbol(s):
    """'NSE:tcs', 'TCS.NS', ' tcs ' -> 'TCS'"""
    s = str(s).strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def _download_index_csv(fname):
    """Try each mirror in turn; return the raw constituent table."""
    errors = []
    for m in CONSTITUENT_MIRRORS:
        url = m.format(f=fname)
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=25)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [str(c).strip() for c in df.columns]
            if "Symbol" not in df.columns or len(df) == 0:
                raise ValueError("file has no 'Symbol' column or is empty")
            return df
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("\n".join(errors))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_index_constituents(index_name):
    """Returns (table with Symbol / Company Name / Industry / ISIN Code, note on where it came from)."""
    try:
        df = _download_index_csv(INDEX_FILES[index_name])
        note = "official NSE Indices list"
    except Exception as first_error:
        # Two indices can be rebuilt exactly from other lists (NSE's own definitions)
        if index_name == "Nifty Total Market":      # = Nifty 500 + Nifty Microcap 250
            df = pd.concat([_download_index_csv(INDEX_FILES["Nifty 500"]),
                            _download_index_csv(INDEX_FILES["Nifty Microcap 250"])])
            note = "built from Nifty 500 + Nifty Microcap 250 lists"
        elif index_name == "Nifty Midcap 150":      # = Nifty LargeMidcap 250 minus Nifty 100
            lm = _download_index_csv(INDEX_FILES["Nifty LargeMidcap 250"])
            n100 = _download_index_csv(INDEX_FILES["Nifty 100"])
            df = lm[~lm["Symbol"].map(clean_symbol).isin(set(n100["Symbol"].map(clean_symbol)))]
            note = "built from Nifty LargeMidcap 250 minus Nifty 100 lists"
        else:
            raise RuntimeError(f"Could not download the {index_name} list.\n{first_error}")
    if "Series" in df.columns:
        df = df[df["Series"].astype(str).str.strip().isin(["EQ", "BE", "BZ"])]
    for c in ("Company Name", "Industry", "ISIN Code"):
        if c not in df.columns:
            df[c] = ""
    df = df.copy()
    df["Symbol"] = df["Symbol"].map(clean_symbol)
    return df.drop_duplicates("Symbol")[["Symbol", "Company Name", "Industry", "ISIN Code"]].reset_index(drop=True), note


# --- LENDING COMPANIES FILTER (rules in lender_filter.py) ---
@st.cache_resource
def basic_industry_cache():
    return lender_filter.BasicIndustryCache(os.path.join(tempfile.gettempdir(), "rs_basic_industry.json"))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_nbfc_index():
    """NSE's Nifty NBFC index: all NBFC stocks in the Nifty Total Market. Empty if it cannot be downloaded."""
    try:
        return set(_download_index_csv("ind_niftyNBFC_list.csv")["Symbol"].map(clean_symbol))
    except Exception:
        return set()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def total_market_names():
    """Company name and broad sector for every Nifty Total Market stock (used for uploaded lists)."""
    try:
        df, _ = load_index_constituents("Nifty Total Market")
        return dict(zip(df["Symbol"], df["Company Name"])), dict(zip(df["Symbol"], df["Industry"]))
    except Exception:
        return {}, {}


def apply_lender_filter(universe, req):
    """Removes lending companies from the universe unless the request switches the filter off."""
    if not req.get("excludeLenders", True):
        universe["lenders"] = {"on": False}
        return universe
    tm_names, tm_sectors = total_market_names()
    symbols = sorted(set(universe["symbols"]))
    names = {s: universe["names"].get(s) or tm_names.get(s, "") for s in symbols}
    sectors = {}
    for s in symbols:
        own = universe["sector_map"].get(s, {}).get("Sector", "")
        sectors[s] = own if own and own != "N/A" else tm_sectors.get(s, "")
        if not lender_filter.is_financial_sector(sectors[s]) and lender_filter.is_financial_sector(tm_sectors.get(s)):
            sectors[s] = tm_sectors[s]
    result = lender_filter.classify(symbols, names, sectors, basic_industry_cache(), load_nbfc_index(),
                                    req.get("alwaysExclude"), req.get("neverExclude"))
    drop = {r["symbol"] for r in result["excluded"]}
    universe["symbols"] = [s for s in universe["symbols"] if s not in drop]
    universe["sector_map"] = {s: v for s, v in universe["sector_map"].items() if s not in drop}
    universe["lenders"] = {"on": True, **result}
    return universe


def read_uploaded_universe(files):
    """Accepts the original format (Symbols, Sector, ISIN) and the NSE format (Symbol, Industry, ISIN Code)."""
    sector_map, symbols = {}, []
    for f in files:
        df_temp = pd.read_csv(f)
        df_temp.columns = [str(c).strip() for c in df_temp.columns]
        sym_col = next((c for c in ("Symbols", "Symbol") if c in df_temp.columns), None)
        if sym_col is None:
            continue
        sec_col = next((c for c in ("Sector", "Industry") if c in df_temp.columns), None)
        isin_col = next((c for c in ("ISIN", "ISIN Code") if c in df_temp.columns), None)
        for _, row in df_temp.iterrows():
            if pd.isna(row[sym_col]):
                continue
            s = clean_symbol(row[sym_col])
            if not s:
                continue
            sec = str(row[sec_col]).strip() if sec_col and pd.notna(row[sec_col]) else "N/A"
            isin_code = str(row[isin_col]).strip() if isin_col and pd.notna(row[isin_col]) else ""
            sector_map[s] = {"Sector": sec, "ISIN": isin_code}
            symbols.append(s)
    return sector_map, symbols


# --- RS LOGIC ENGINE ---
class RSHeatmapScreener:
    def __init__(self, rs_threshold, lookback_months, output_history, use_ema, cr_threshold, mr_threshold):
        self.rs_threshold = rs_threshold / 100.0
        self.lookback_months = lookback_months
        self.output_history = output_history
        self.index_ticker = "^NSEI"
        self.use_ema = use_ema
        # kept in percent: the filters below compare (value * 100) against these
        self.cr_threshold = cr_threshold
        self.mr_threshold = mr_threshold

    def fetch_data(self, tickers, interval="1mo", period="3y"):
        all_tickers = tickers + [self.index_ticker]
        data = yf.download(all_tickers, period=period, interval=interval, auto_adjust=True)
        if interval == "1mo":
            data = self.one_row_per_month(data)
        return data

    @staticmethod
    def one_row_per_month(data):
        """Yahoo sometimes returns two rows for the same month (the month's bar plus a
        stray row dated today, filled for only some tickers). Merge them so every month
        appears exactly once: High = highest, Low = lowest, Close = latest available."""
        if data is None or data.empty:
            return data
        idx = pd.DatetimeIndex(data.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        month = idx.to_period("M")
        how = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        fields = [f for f in how if f in data.columns.get_level_values(0)]
        merged = pd.concat({f: data[f].groupby(month).agg(how[f]) for f in fields}, axis=1)
        merged.index = merged.index.to_timestamp()
        return merged

    def generate_matrix(self, full_data, sector_map):
        price_df = full_data['Close'].dropna(how='all')
        index_prices = price_df[self.index_ticker]
        stock_prices = price_df.drop(columns=[self.index_ticker])
        high_df = full_data['High']
        low_df = full_data['Low']
        
        target_months = sorted(stock_prices.index[-self.output_history:])
        month_headers = [d.strftime('%b-%y') for d in target_months]
        
        matrix_data = []
        for ticker in stock_prices.columns:
            clean_name = ticker.replace(".NS", "")
            
            # MONTHLY FILTERS
            curr_price = stock_prices[ticker].iloc[-1]
            prev_price = stock_prices[ticker].iloc[-2]
            curr_high = high_df[ticker].iloc[-1]
            curr_low = low_df[ticker].iloc[-1]
            
            if self.use_ema:
                ema_12 = stock_prices[ticker].ewm(span=12, adjust=False).mean().iloc[-1]
                if curr_price < ema_12: continue
            
            cr_val = (curr_price - curr_low) / (curr_high - curr_low) if curr_high != curr_low else 0
            if (cr_val * 100) < self.cr_threshold: continue
            
            mr_val = (curr_price / prev_price) - 1
            if (mr_val * 100) < self.mr_threshold: continue

            # Extract mapping details safely
            sector = sector_map.get(clean_name, {}).get("Sector", "N/A")
            isin_val = sector_map.get(clean_name, {}).get("ISIN", "")

            row = {"Symbols": f"NSE:{clean_name}", "Sector": sector, "ISIN_Hidden": isin_val}
            has_any_dot = False
            full_rs_series = (stock_prices[ticker] / index_prices).dropna()

            for i, date in enumerate(target_months):
                header = month_headers[i]
                if date in full_rs_series.index:
                    idx = full_rs_series.index.get_loc(date)
                    if idx >= self.lookback_months:
                        window = full_rs_series.iloc[idx - self.lookback_months + 1 : idx + 1]
                        max_rs = window.max()
                        current_rs = full_rs_series.loc[date]
                        retainment = (current_rs / max_rs)
                        
                        if retainment >= 1.0:
                            row[header] = f"CYAN_{100}"
                            has_any_dot = True
                        elif retainment >= self.rs_threshold:
                            row[header] = f"GREEN_{int(retainment * 100)}"
                            has_any_dot = True
            
            if has_any_dot:
                matrix_data.append(row)
        
        return pd.DataFrame(matrix_data), month_headers

# --- RESULT BUILDER (prepares everything the React dashboard shows) ---
LOOKBACK_MONTHS = 12        # RS is compared with its highest month-end value of the past 12 months
RS_TRAIL_MONTHS = 24        # length of the RS line drawn for each stock
WEEKLY_CR_REQ = 75
WEEKLY_RET_REQ = 4
IST = timezone(timedelta(hours=5, minutes=30))


def _num(x, nd=2):
    """Plain rounded number, or None for missing values (keeps the data JSON-safe)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def apply_weekly_filter(screener, matrix_df):
    """Phase 2 of the original app: keep stocks whose latest week is strong; add daily figures."""
    shortlisted_tickers = [s.split(":")[1] + ".NS" for s in matrix_df['Symbols'].tolist()]
    weekly_data = screener.fetch_data(shortlisted_tickers, interval="1wk", period="6mo")
    daily_data = screener.fetch_data(shortlisted_tickers, interval="1d", period="1mo")

    final_rows = []
    for _, m_row in matrix_df.iterrows():
        t_name = m_row['Symbols'].split(":")[1] + ".NS"
        if t_name in weekly_data['Close'].columns:
            w_close, w_high, w_low = weekly_data['Close'][t_name].dropna(), weekly_data['High'][t_name].dropna(), weekly_data['Low'][t_name].dropna()
            if len(w_close) >= 2:
                w_return = (w_close.iloc[-1] / w_close.iloc[-2] - 1) * 100
                w_cr = ((w_close.iloc[-1] - w_low.iloc[-1]) / (w_high.iloc[-1] - w_low.iloc[-1]) * 100) if w_high.iloc[-1] != w_low.iloc[-1] else 0

                if w_return > WEEKLY_RET_REQ and w_cr > WEEKLY_CR_REQ:
                    m_row['Weekly CR%'] = f"{int(w_cr)}%"
                    m_row['Daily CR%'] = ""
                    m_row['Daily % Change'] = ""
                    if t_name in daily_data['Close'].columns:
                        d_close = daily_data['Close'][t_name].dropna()
                        d_high = daily_data['High'][t_name].dropna()
                        d_low = daily_data['Low'][t_name].dropna()
                        if len(d_close) >= 2:
                            d_cr = ((d_close.iloc[-1] - d_low.iloc[-1]) / (d_high.iloc[-1] - d_low.iloc[-1]) * 100) if d_high.iloc[-1] != d_low.iloc[-1] else 0
                            d_change = (d_close.iloc[-1] / d_close.iloc[-2] - 1) * 100
                            m_row['Daily CR%'] = f"{int(d_cr)}%"
                            m_row['Daily % Change'] = f"{d_change:.2f}%"
                    final_rows.append(m_row)
    return pd.DataFrame(final_rows)


def build_excel(matrix_df, month_headers):
    """The styled Excel report of the original app, returned as bytes."""
    available_months = [m for m in month_headers if m in matrix_df.columns]
    additional_cols = [c for c in ("Weekly CR%", "Daily CR%", "Daily % Change") if c in matrix_df.columns]
    export_df = matrix_df[["Symbols", "Sector"] + available_months + additional_cols].copy()
    export_df.insert(0, 'Sl. No', range(1, len(export_df) + 1))

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        export_df.to_excel(writer, index=False, sheet_name='Alpha_RS')
        ws = writer.sheets['Alpha_RS']

        blue_fill = PatternFill(start_color="0EA5E9", fill_type="solid")
        green_fill = PatternFill(start_color="10B981", fill_type="solid")
        header_fill = PatternFill(start_color="0F172A", fill_type="solid")

        for cell in ws[1]:
            cell.fill, cell.font, cell.alignment = header_fill, Font(color="FFFFFF", bold=True), Alignment(horizontal='center')

        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(horizontal='center')
                val = str(cell.value) if cell.value else ""
                if "_" in val:
                    tag, num = val.split("_")
                    cell.value = int(num) / 100.0
                    cell.number_format = '0%'
                    cell.font = Font(color="FFFFFF", bold=True)
                    cell.fill = blue_fill if tag == "CYAN" else green_fill
    return output.getvalue()


def describe_stocks(screener, monthly_data, matrix_df, month_headers):
    """Display detail for each shortlisted stock. The shortlist itself comes from the RS engine;
    nothing here changes which stocks qualify."""
    price_df = monthly_data['Close'].dropna(how='all')
    index_prices = price_df[screener.index_ticker]
    high_df, low_df = monthly_data['High'], monthly_data['Low']
    target_months = sorted(price_df.index[-screener.output_history:])
    n = screener.lookback_months

    stocks = []
    for _, r in matrix_df.iterrows():
        name = r['Symbols'].split(":")[1]
        ticker = name + ".NS"
        px = price_df[ticker]
        rs = (px / index_prices).dropna()

        # one cell per displayed month: RS as % of its 12-month high, and whether the engine marked it
        cells = []
        for d, header in zip(target_months, month_headers):
            val = None
            if d in rs.index:
                i = rs.index.get_loc(d)
                if i >= n:
                    val = _num(rs.loc[d] / rs.iloc[i - n + 1: i + 1].max() * 100, 1)
            tag = r.get(header)
            marked = isinstance(tag, str) and "_" in tag
            cells.append({"v": val, "q": marked, "high": marked and tag.startswith("CYAN")})

        streak = 0
        for c in reversed(cells):
            if not c["q"]:
                break
            streak += 1

        trail = rs.iloc[-RS_TRAIL_MONTHS:]
        trail_high = rs.rolling(n, min_periods=1).max().iloc[-RS_TRAIL_MONTHS:]
        base = trail.iloc[0] if len(trail) else float("nan")

        curr, prev = px.iloc[-1], px.iloc[-2]
        hi, lo = high_df[ticker].iloc[-1], low_df[ticker].iloc[-1]
        ema_12 = px.ewm(span=12, adjust=False).mean().iloc[-1]

        stocks.append({
            "symbol": name,
            "sector": r['Sector'],
            "isin": r['ISIN_Hidden'] if isinstance(r['ISIN_Hidden'], str) else "",
            "cells": cells,
            "streak": streak,
            "hits": sum(1 for c in cells if c["q"]),
            "price": _num(curr),
            "monthReturn": _num((curr / prev - 1) * 100, 1),
            "closingRange": _num((curr - lo) / (hi - lo) * 100, 0) if hi != lo else 0,
            "emaGap": _num((curr / ema_12 - 1) * 100, 1),
            "trail": {
                "labels": [d.strftime('%b-%y') for d in trail.index],
                "rs": [_num(v / base * 100, 1) for v in trail],
                "high": [_num(v / base * 100, 1) for v in trail_high],
            },
            "weeklyCR": r.get("Weekly CR%") if isinstance(r.get("Weekly CR%"), str) else None,
            "dailyCR": r.get("Daily CR%") if isinstance(r.get("Daily CR%"), str) else None,
            "dailyChange": r.get("Daily % Change") if isinstance(r.get("Daily % Change"), str) else None,
        })
    return stocks


def resolve_universe(req):
    """Stocks to analyse: the uploaded CSV(s) if any, otherwise the selected index."""
    index_name = req.get("index") if req.get("index") in INDEX_FILES else DEFAULT_INDEX
    csv_files = req.get("csvFiles") or []
    if csv_files:
        sector_map, all_symbols = read_uploaded_universe([io.StringIO(f.get("text", "")) for f in csv_files])
        if not all_symbols:
            return {"error": "The uploaded file has no 'Symbols' (or 'Symbol') column, or it is empty. "
                             "Fix the file, or remove it to run on the selected index."}
        return apply_lender_filter({"sector_map": sector_map, "symbols": all_symbols, "names": {},
                                    "source": "your uploaded list",
                                    "name": ", ".join(f.get("name", "CSV") for f in csv_files)}, req)
    try:
        const_df, source = load_index_constituents(index_name)
    except Exception as e:
        return {"error": f"Could not load the {index_name} stock list from NSE. "
                         f"Run it again, or upload your own CSV. ({e})"}
    sector_map, all_symbols = {}, []
    for _, row in const_df.iterrows():
        s = row["Symbol"]
        sec = str(row["Industry"]).strip() if pd.notna(row["Industry"]) and str(row["Industry"]).strip() else "N/A"
        isin_code = str(row["ISIN Code"]).strip() if pd.notna(row["ISIN Code"]) else ""
        sector_map[s] = {"Sector": sec, "ISIN": isin_code}
        all_symbols.append(s)
    names = dict(zip(const_df["Symbol"], const_df["Company Name"].astype(str)))
    return apply_lender_filter({"sector_map": sector_map, "symbols": all_symbols, "names": names,
                                "source": source, "name": index_name}, req)


def run_screen(req):
    """Runs the whole screen for one request from the dashboard and returns a JSON-safe result."""
    index_name = req.get("index") if req.get("index") in INDEX_FILES else DEFAULT_INDEX
    history = int(req.get("history", 6))
    cutoff = float(req.get("threshold", 100))
    use_ema = bool(req.get("useEma", True))
    cr_limit = float(req.get("cr", 75))
    mr_limit = float(req.get("mr", 5))
    enable_weekly = bool(req.get("weekly", False))

    # 1. universe: an uploaded list replaces the index
    universe = resolve_universe(req)
    if "error" in universe:
        return {"ok": False, "error": universe["error"]}
    sector_map, all_symbols = universe["sector_map"], universe["symbols"]
    universe_name, universe_source = universe["name"], universe["source"]

    symbols = sorted(set(all_symbols))
    unique_tickers = [f"{s}.NS" for s in symbols]

    # 2. the RS engine (unchanged)
    screener = RSHeatmapScreener(cutoff, LOOKBACK_MONTHS, history, use_ema, cr_limit, mr_limit)
    monthly_data = screener.fetch_data(unique_tickers, interval="1mo")
    if monthly_data is None or monthly_data.empty or screener.index_ticker not in monthly_data['Close'].columns:
        return {"ok": False, "error": "Yahoo Finance returned no price data. It may be throttling requests; "
                                      "wait a minute and run the screen again."}
    matrix_df, month_headers = screener.generate_matrix(monthly_data, sector_map)
    if not matrix_df.empty and enable_weekly:
        matrix_df = apply_weekly_filter(screener, matrix_df)

    now = datetime.now(IST)
    last_month = monthly_data['Close'].dropna(how='all').index[-1]
    result = {
        "ok": True,
        "universe": {"name": universe_name, "source": universe_source, "count": len(symbols)},
        "lenders": universe.get("lenders"),
        "asOf": now.strftime("%d %b %Y, %H:%M IST"),
        "months": month_headers,
        "monthInProgress": (last_month.year, last_month.month) == (now.year, now.month),
        "settings": {"history": history, "threshold": cutoff, "useEma": use_ema, "cr": cr_limit,
                     "mr": mr_limit, "weekly": enable_weekly},
        "sectorTotals": dict(Counter(sector_map[s]["Sector"] for s in symbols)),
        "stocks": [],
        "excel": None,
    }
    if matrix_df.empty:
        return result

    matrix_df = matrix_df.sort_values(by=["Sector", "Symbols"]).reset_index(drop=True)
    result["stocks"] = describe_stocks(screener, monthly_data, matrix_df, month_headers)
    result["excel"] = base64.b64encode(build_excel(matrix_df, month_headers)).decode("ascii")
    return result


# --- BACKTEST ---
BT_DEFAULTS = {"index": DEFAULT_INDEX, "start": "2015-01", "slots": 10, "threshold": 98, "useEma": True,
               "cr": 75, "mr": 5, "cost": 0.2, "cashYield": 6, "capital": 1000000, "ranking": "cr",
               "excludeLenders": True}


@st.cache_resource
def price_store():
    """One shared daily-price store for all users of this app (kept on the server's disk)."""
    return bt_data.PriceStore(os.path.join(tempfile.gettempdir(), "rs_bt_price_store"))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_rs_index(cutoff_key):
    return bt_data.rs_index_monthly(pd.Timestamp(bt_data.DATA_START).date(), datetime.now(IST).date())


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_benchmark(cutoff_key):
    return bt_data.benchmark_monthly(pd.Timestamp(bt_data.DATA_START).date(), datetime.now(IST).date())


def run_backtest(req, progress=None):
    """Runs one backtest request from the dashboard and returns a JSON-safe result."""
    last_done = bt_data.last_completed_month()
    earliest = pd.Period(bt_data.EARLIEST_START, "M")
    try:
        start = pd.Period(str(req.get("start") or BT_DEFAULTS["start"]), "M")
        end = pd.Period(str(req.get("end") or last_done), "M")
    except Exception:
        return {"ok": False, "error": "Start and end must be months, for example 2015-01."}
    notes = []
    if end > last_done:
        notes.append(f"The end was moved to {last_done.strftime('%b %Y')}, the last completed month.")
        end = last_done
    if start < earliest:
        notes.append(f"The start was moved to {earliest.strftime('%b %Y')}: earlier months lack the 12 months "
                     f"of history that RS needs.")
        start = earliest
    if (end - start).n < 2:
        return {"ok": False, "error": "Choose an end month at least three months after the start month."}

    p = {
        "slots": int(min(50, max(1, int(req.get("slots", 10))))),
        "threshold": min(100.0, max(50.0, float(req.get("threshold", 98)))) / 100.0,
        "use_ema": bool(req.get("useEma", True)),
        "cr_min": float(req.get("cr", 75)), "mr_min": float(req.get("mr", 5)),
        "cost": max(0.0, float(req.get("cost", 0.2))) / 100.0,
        "cash_yield": float(req.get("cashYield", 6)) / 100.0,
        "capital": max(1000.0, float(req.get("capital", 1_000_000))),
        "ranking": req.get("ranking") if req.get("ranking") in bt_engine.RANKINGS else "cr",
    }

    universe = resolve_universe(req)
    if "error" in universe:
        return {"ok": False, "error": universe["error"]}
    symbols = sorted(set(universe["symbols"]))
    sectors = {s: universe["sector_map"][s]["Sector"] for s in symbols}

    store = price_store()
    cutoff = bt_data.last_cutoff()
    session = bt_data.last_session_date()
    stats = store.update(symbols, cutoff, progress, need_date=session)
    # monthly candles up to the running month; the backtest itself uses completed months only
    cur = pd.Period(bt_data.now_ist().date(), "M")
    try:
        monthly_all, spikes = bt_engine.monthly_in_batches(store.panel, symbols, cur)
    except RuntimeError:
        return {"ok": False, "error": "Yahoo Finance returned no price data for this universe. "
                                      "It may be throttling requests; wait a minute and run it again."}
    try:
        rs_index = load_rs_index(cutoff.isoformat())
    except Exception as e:
        return {"ok": False, "error": f"Could not download the Nifty 50 index from Yahoo Finance ({e}). Run it again."}
    try:
        bench, bench_source = load_benchmark(cutoff.isoformat())
    except Exception:
        bench, bench_source = pd.Series(dtype=float), "unavailable"

    monthly = {f: df[df.index <= end] for f, df in monthly_all.items()}
    if start - 1 not in monthly["Close"].index:
        return {"ok": False, "error": "There is no price data for the month before the start. Pick a later start."}
    ind = bt_engine.indicators(monthly, rs_index)
    sim = bt_engine.simulate(monthly, ind, p, start, end, sectors)
    report = bt_engine.build_report(sim, bench, p, start, end)

    data_to = store.last_date(symbols)
    data_to_label = data_to.strftime("%d %b %Y") if data_to is not None else None

    # orders for the first open after the latest completed month-end
    orders = None
    if end == last_done:
        orders = bt_engine.next_orders(monthly, ind, p, sim, end, sectors)
        nxt = end + 1
        started = nxt in monthly_all["Close"].index and monthly_all["Close"].loc[nxt].notna().any()
        orders["started"] = bool(started)
        orders["dataTo"] = data_to_label
        orders["pricesBehind"] = bool(data_to is not None and data_to.date() < session and not started)
        orders["expected"] = pd.Timestamp(session).strftime("%d %b %Y")

    # the running month: final holdings, provisional returns and next-month changes
    live = None
    if end == last_done and cur == end + 1:
        live = bt_engine.live_view(monthly_all, bt_engine.indicators(monthly_all, rs_index), p, start, end, cur,
                                   sectors, bench)
        if live:
            live["dataTo"] = data_to_label

    with_data = list(monthly["Close"].columns)
    missing = sorted(set(symbols) - set(with_data))
    settings_rows = [
        ("Universe", universe["name"]), ("Period", f"{start.strftime('%b %Y')} to {end.strftime('%b %Y')}"),
        ("Stocks held", p["slots"]), ("Minimum RS threshold %", p["threshold"] * 100),
        ("Price above 12-month EMA", "Yes" if p["use_ema"] else "No"),
        ("Min closing range %", p["cr_min"]), ("Min monthly return %", p["mr_min"]),
        ("Ranking", bt_engine.RANKINGS[p["ranking"]]), ("Cost per buy and per sell %", p["cost"] * 100),
        ("Cash yield % a year", p["cash_yield"] * 100), ("Starting capital", p["capital"]),
        ("Lending companies", "Excluded" if (universe.get("lenders") or {}).get("on") else "Included"),
        ("Benchmark", bench_source),
    ]
    return {
        "ok": True,
        "asOf": bt_data.now_ist().strftime("%d %b %Y, %H:%M IST"),
        "lenders": universe.get("lenders"),
        "universe": {"name": universe["name"], "source": universe["source"], "count": len(symbols),
                     "withData": len(with_data), "missing": missing[:40], "missingCount": len(missing)},
        "period": {"start": start.strftime("%b %Y"), "end": end.strftime("%b %Y"),
                   "startKey": str(start), "endKey": str(end), "months": (end - start).n + 1},
        "settings": {"slots": p["slots"], "threshold": p["threshold"] * 100, "useEma": p["use_ema"],
                     "cr": p["cr_min"], "mr": p["mr_min"], "cost": p["cost"] * 100,
                     "cashYield": p["cash_yield"] * 100, "capital": p["capital"],
                     "ranking": p["ranking"], "rankingLabel": bt_engine.RANKINGS[p["ranking"]]},
        "benchmarkSource": bench_source,
        "notes": notes,
        "data": {"spikesRemoved": spikes, "downloaded": stats["full"] + stats["re-downloaded"],
                 "refreshed": stats["refreshed"]},
        "report": report,
        "live": live,
        "orders": orders,
        "excel": base64.b64encode(bt_engine.to_excel(report, settings_rows)).decode("ascii"),
    }


# --- PAGE (the interface is the React app in frontend/, built into frontend/dist) ---
st.set_page_config(page_title="Abdullah RS Pro", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
  header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
  [data-testid="stToolbar"], footer { display: none !important; }
  .stApp { background: #E8EEF1; }
  .block-container, [data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: 100% !important; }
  iframe { display: block; }
</style>
""", unsafe_allow_html=True)

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
if not (FRONTEND_DIR / "index.html").exists():
    st.error("The dashboard files are missing (frontend/dist). Build them with: cd frontend && npm install && npm run build")
    st.stop()

rs_dashboard = components.declare_component("rs_dashboard", path=str(FRONTEND_DIR))

progress_slot = st.empty()

request = rs_dashboard(
    indices=list(INDEX_FILES),
    defaults={"index": DEFAULT_INDEX, "history": 6, "threshold": 100, "useEma": True, "cr": 75, "mr": 5, "weekly": False,
              "excludeLenders": True},
    result=st.session_state.get("result"),
    btDefaults={**BT_DEFAULTS, "end": str(bt_data.last_completed_month()), "earliest": bt_data.EARLIEST_START,
                "rankings": bt_engine.RANKINGS},
    btResult=st.session_state.get("bt_result"),
    key="rs_dashboard",
    default=None,
)

# A new request carries a new id; results are kept in the session so they survive reruns
if request and request.get("nonce") and request["nonce"] != st.session_state.get("last_nonce"):
    st.session_state["last_nonce"] = request["nonce"]
    is_backtest = request.get("kind") == "backtest"

    def show_progress(done, total):
        progress_slot.progress(min(1.0, done / max(1, total)),
                               text=f"Downloading daily prices: {done} of {total} stocks")

    try:
        outcome = run_backtest(request, show_progress) if is_backtest else run_screen(request)
    except Exception as e:
        what = "backtest" if is_backtest else "screen"
        outcome = {"ok": False, "error": f"The {what} stopped with an error: {type(e).__name__}: {e}"}
    progress_slot.empty()
    outcome["nonce"] = request["nonce"]
    st.session_state["bt_result" if is_backtest else "result"] = outcome
    st.rerun()
