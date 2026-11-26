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

import auth
import bt_data
import bt_engine
import compare
import lender_filter
import rules_doc
import settings
import sweep
import us_universe

# After a git push, Streamlit Cloud reruns this file but can keep the previous copy of these
# modules in memory. Reload any whose file has changed since it was loaded.
for _mod in (settings, auth, bt_data, bt_engine, lender_filter, compare, sweep, us_universe, rules_doc):
    _mtime = os.path.getmtime(_mod.__file__)
    if getattr(_mod, "_loaded_mtime", None) != _mtime:
        importlib.reload(_mod)
        _mod._loaded_mtime = _mtime

from settings import (BT_DEFAULTS, COMPARE_ORDER, DEFAULT_INDEX, INDEX_FILES, LONG_START, LOOKBACK_MONTHS,  # noqa: E402
                      LOGIN_REQUIRED, MARKETS, RS_TRAIL_MONTHS, SCREENER_DEFAULTS, SCREENER_PRICE_PERIOD, US_INDICES,
                      WEEKLY_CR_REQ, WEEKLY_RET_REQ, market_of)

ALL_INDICES = list(INDEX_FILES) + list(US_INDICES)

# --- STANDARD INDEX UNIVERSES ---
# Official NSE Indices constituent files (columns: Company Name, Industry, Symbol, Series, ISIN Code)
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


def clean_us_symbol(s):
    """'NASDAQ:aapl', ' brk.b ' -> 'AAPL', 'BRK-B' (Yahoo form)"""
    return us_universe.yahoo_symbol(s)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_us_constituents(index_name):
    """Returns (table with Symbol / Company Name / Industry / Subsector / ISIN Code, note on where it came from)."""
    return us_universe.load_nasdaq100()


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
@st.cache_resource(max_entries=1)
def _basic_industry_cache(code_version):
    return lender_filter.BasicIndustryCache(os.path.join(tempfile.gettempdir(), "rs_basic_industry.json"))


def basic_industry_cache():
    """Rebuilt when lender_filter.py changes; the saved classifications on disk are reused."""
    return _basic_industry_cache(os.path.getmtime(lender_filter.__file__))


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
    if universe.get("market") == "US":
        symbols = sorted(set(universe["symbols"]))
        industries = {s: universe.get("industries", {}).get(s) or universe["sector_map"].get(s, {}).get("Sector", "")
                      for s in symbols}
        result = lender_filter.classify_us(symbols, universe["names"], industries,
                                           req.get("alwaysExclude"), req.get("neverExclude"))
        drop = {r["symbol"] for r in result["excluded"]}
        universe["symbols"] = [s for s in universe["symbols"] if s not in drop]
        universe["sector_map"] = {s: v for s, v in universe["sector_map"].items() if s not in drop}
        universe["lenders"] = {"on": True, **result}
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
    result["allFinancials"] = bool(req.get("excludeFinancials"))
    if result["allFinancials"]:
        # leave out every financial company, not only lenders (your never-exclude list still keeps a stock)
        moved = [r for r in result["kept"] if r["source"] != "your never-exclude list"]
        result["excluded"] = sorted(result["excluded"] + [{**r, "source": "all financial companies left out"}
                                                          for r in moved], key=lambda r: r["symbol"])
        result["kept"] = [r for r in result["kept"] if r["source"] == "your never-exclude list"]
    drop = {r["symbol"] for r in result["excluded"]}
    universe["symbols"] = [s for s in universe["symbols"] if s not in drop]
    universe["sector_map"] = {s: v for s, v in universe["sector_map"].items() if s not in drop}
    universe["lenders"] = {"on": True, **result}
    return universe


def read_uploaded_universe(files, clean=clean_symbol):
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
            s = clean(row[sym_col])
            if not s:
                continue
            sec = str(row[sec_col]).strip() if sec_col and pd.notna(row[sec_col]) else "N/A"
            isin_code = str(row[isin_col]).strip() if isin_col and pd.notna(row[isin_col]) else ""
            sector_map[s] = {"Sector": sec, "ISIN": isin_code}
            symbols.append(s)
    return sector_map, symbols


# --- RS LOGIC ENGINE ---
class RSHeatmapScreener:
    def __init__(self, rs_threshold, lookback_months, output_history, use_ema, cr_threshold, mr_threshold,
                 market="IN"):
        self.rs_threshold = rs_threshold / 100.0
        self.lookback_months = lookback_months
        self.output_history = output_history
        self.market = market
        self.index_ticker = MARKETS[market]["rs_index"]     # Nifty 50, or the S&P 500 for US universes
        self.suffix = MARKETS[market]["yahoo_suffix"]       # ".NS" for NSE stocks on Yahoo, "" for US stocks
        self.exchange = MARKETS[market]["exchange"]
        self.use_ema = use_ema
        # kept in percent: the filters below compare (value * 100) against these
        self.cr_threshold = cr_threshold
        self.mr_threshold = mr_threshold

    def fetch_data(self, tickers, interval="1mo", period=SCREENER_PRICE_PERIOD):
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
            clean_name = ticker[:-len(self.suffix)] if self.suffix and ticker.endswith(self.suffix) else ticker
            
            # MONTHLY FILTERS
            curr_price = stock_prices[ticker].iloc[-1]
            prev_price = stock_prices[ticker].iloc[-2]
            curr_high = high_df[ticker].iloc[-1]
            curr_low = low_df[ticker].iloc[-1]
            
            if self.use_ema:
                ema_12 = stock_prices[ticker].ewm(span=bt_engine.EMA_SPAN, adjust=False).mean().iloc[-1]
                if curr_price < ema_12: continue
            
            cr_val = (curr_price - curr_low) / (curr_high - curr_low) if curr_high != curr_low else 0
            if (cr_val * 100) < self.cr_threshold: continue
            
            mr_val = (curr_price / prev_price) - 1
            if (mr_val * 100) < self.mr_threshold: continue

            # Extract mapping details safely
            sector = sector_map.get(clean_name, {}).get("Sector", "N/A")
            isin_val = sector_map.get(clean_name, {}).get("ISIN", "")

            row = {"Symbols": f"{self.exchange}:{clean_name}", "Sector": sector, "ISIN_Hidden": isin_val}
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
    shortlisted_tickers = [s.split(":")[1] + screener.suffix for s in matrix_df['Symbols'].tolist()]
    weekly_data = screener.fetch_data(shortlisted_tickers, interval="1wk", period="6mo")
    daily_data = screener.fetch_data(shortlisted_tickers, interval="1d", period="1mo")

    final_rows = []
    for _, m_row in matrix_df.iterrows():
        t_name = m_row['Symbols'].split(":")[1] + screener.suffix
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


def describe_stocks(screener, monthly_data, matrix_df, month_headers, tv_prefix="NSE"):
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
        ticker = name + screener.suffix
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
            "tv": f"{tv_prefix}:{name}" if tv_prefix else name,     # symbol as TradingView knows it
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


def market_info(market, tv_prefix):
    """What the dashboard needs to show a universe's market: exchange, currency and RS index."""
    m = MARKETS[market]
    return {"code": market, "exchange": m["exchange"], "tvPrefix": tv_prefix, "currency": m["currency"],
            "rsIndex": m["rs_index_name"]}


def resolve_universe(req):
    """Stocks to analyse: the uploaded CSV(s) if any, otherwise the selected index. The market (India or
    US) follows the selected index, also for an uploaded list."""
    index_name = req.get("index") if req.get("index") in ALL_INDICES else DEFAULT_INDEX
    market = market_of(index_name)
    csv_files = req.get("csvFiles") or []
    if csv_files:
        sector_map, all_symbols = read_uploaded_universe([io.StringIO(f.get("text", "")) for f in csv_files],
                                                         clean_us_symbol if market == "US" else clean_symbol)
        if not all_symbols:
            return {"error": "The uploaded file has no 'Symbols' (or 'Symbol') column, or it is empty. "
                             "Fix the file, or remove it to run on the selected index."}
        return apply_lender_filter({"sector_map": sector_map, "symbols": all_symbols, "names": {},
                                    "source": "your uploaded list" + (", read as US stocks" if market == "US" else ""),
                                    "name": ", ".join(f.get("name", "CSV") for f in csv_files),
                                    "market": market, "tv_prefix": "" if market == "US" else "NSE"}, req)
    if market == "US":
        try:
            const_df, source = load_us_constituents(index_name)
        except Exception as e:
            return {"error": f"Could not load the {index_name} stock list ({e}). Run it again, or upload your own CSV."}
        sector_map = {r["Symbol"]: {"Sector": r["Industry"] or "N/A", "ISIN": r["ISIN Code"]}
                      for _, r in const_df.iterrows()}
        industries = {r["Symbol"]: " / ".join(x for x in (r["Industry"], r["Subsector"]) if x and x != "N/A")
                      for _, r in const_df.iterrows()}
        return apply_lender_filter({"sector_map": sector_map, "symbols": list(const_df["Symbol"]),
                                    "names": dict(zip(const_df["Symbol"], const_df["Company Name"])),
                                    "industries": industries, "source": source, "name": index_name,
                                    "market": "US", "tv_prefix": MARKETS["US"]["exchange"]}, req)
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
                                "source": source, "name": index_name, "market": "IN", "tv_prefix": "NSE"}, req)


def run_screen(req):
    """Runs the whole screen for one request from the dashboard and returns a JSON-safe result."""
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
    market = universe["market"]

    # 2. the RS engine (unchanged; for US stocks the RS index is the S&P 500)
    screener = RSHeatmapScreener(cutoff, LOOKBACK_MONTHS, history, use_ema, cr_limit, mr_limit, market)
    symbols = sorted(set(all_symbols))
    unique_tickers = [f"{s}{screener.suffix}" for s in symbols]
    monthly_data = screener.fetch_data(unique_tickers, interval="1mo")
    if monthly_data is None or monthly_data.empty or screener.index_ticker not in monthly_data['Close'].columns:
        return {"ok": False, "error": "Yahoo Finance returned no price data. It may be throttling requests; "
                                      "wait a minute and run the screen again."}
    matrix_df, month_headers = screener.generate_matrix(monthly_data, sector_map)
    if not matrix_df.empty and enable_weekly:
        matrix_df = apply_weekly_filter(screener, matrix_df)

    now = datetime.now(IST)
    market_today = bt_data.market_now(market).date()
    last_month = monthly_data['Close'].dropna(how='all').index[-1]
    result = {
        "ok": True,
        "market": market_info(market, universe["tv_prefix"]),
        "universe": {"name": universe_name, "source": universe_source, "count": len(symbols)},
        "lenders": universe.get("lenders"),
        "asOf": now.strftime("%d %b %Y, %H:%M IST"),
        "months": month_headers,
        "monthInProgress": (last_month.year, last_month.month) == (market_today.year, market_today.month),
        "settings": {"history": history, "threshold": cutoff, "useEma": use_ema, "cr": cr_limit,
                     "mr": mr_limit, "weekly": enable_weekly},
        "sectorTotals": dict(Counter(sector_map[s]["Sector"] for s in symbols)),
        "stocks": [],
        "excel": None,
    }
    if matrix_df.empty:
        return result

    matrix_df = matrix_df.sort_values(by=["Sector", "Symbols"]).reset_index(drop=True)
    result["stocks"] = describe_stocks(screener, monthly_data, matrix_df, month_headers, universe["tv_prefix"])
    result["excel"] = base64.b64encode(build_excel(matrix_df, month_headers)).decode("ascii")
    return result


# --- BACKTEST ---


PRICE_STORE_DIRS = {"IN": "rs_bt_price_store", "US": "rs_bt_price_store_us"}


@st.cache_resource(max_entries=2)
def _price_store(code_version, market):
    return bt_data.PriceStore(os.path.join(tempfile.gettempdir(), PRICE_STORE_DIRS[market]),
                              suffix=MARKETS[market]["yahoo_suffix"])


def price_store(market="IN"):
    """One shared daily-price store per market for all users of this app (kept on the server's disk). It is
    rebuilt when bt_data.py changes, so it never runs old code after an update; the prices on disk are reused.
    Indian and US prices are kept apart, because a symbol can mean different companies on the two markets."""
    return _price_store(os.path.getmtime(bt_data.__file__), market)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_rs_index(cutoff_key, market="IN"):
    return bt_data.rs_index_monthly(pd.Timestamp(bt_data.DATA_START).date(), datetime.now(IST).date(),
                                    MARKETS[market]["rs_index"])


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_benchmark(cutoff_key, market="IN"):
    return bt_data.benchmark_monthly(pd.Timestamp(bt_data.DATA_START).date(), datetime.now(IST).date(), market)


def entry_settings(req):
    """The entry rule and its Setup A + B settings (RULES.md 4.12), checked and in engine units."""
    d = BT_DEFAULTS

    def num(key, lo, hi):
        try:
            return min(hi, max(lo, float(req.get(key, d[key]))))
        except (TypeError, ValueError):
            return float(d[key])

    return {
        "entry": "breakout" if req.get("entry") == "breakout" else "rs",
        "near_high": num("nearHigh", 50, 100) / 100.0,
        "quiet": int(num("quiet", 1, 36)),
        "a_slots": int(num("aSlots", 0, 50)),
        "use_b": bool(req.get("useB", d["useB"])),
        "band": 1 + num("band", 0, 50) / 100.0,
        "list_life": int(num("listLife", 1, 36)),
    }


def entry_rows(p):
    """Settings-sheet rows describing the entry rule."""
    if p.get("entry") != "breakout":
        return [("Entry rule", bt_engine.ENTRY_RULES["rs"])]
    b_slots = max(0, p["slots"] - min(p["a_slots"], p["slots"]))
    return [
        ("Entry rule", bt_engine.ENTRY_RULES["breakout"]),
        ("Setup A: close at least % of the 12-month highest close", p["near_high"] * 100),
        ("Setup A: first time in (months)", p["quiet"]),
        ("Setup B (pullback)", "On" if p["use_b"] else "Off"),
        ("Slots for Setup A (rest for Setup B)", f"{min(p['a_slots'], p['slots'])} (Setup B: {b_slots})"
         if p["use_b"] else f"All {p['slots']}"),
        ("Setup B: month low below EMA x", round(p["band"], 4)),
        ("Signals list: months a stock stays on it", p["list_life"]),
    ]


def prepare_backtest(req, progress=None):
    """Everything a backtest needs before simulating: checked period, parameters, universe and monthly prices.
    Returns {"error": ...} or a dict of the pieces."""
    market = market_of(req.get("index"))
    last_done = bt_data.last_completed_month(market=market)
    earliest = pd.Period(bt_data.EARLIEST_START, "M")
    try:
        start = pd.Period(str(req.get("start") or BT_DEFAULTS["start"]), "M")
        end = pd.Period(str(req.get("end") or last_done), "M")
    except Exception:
        return {"error": "Start and end must be months, for example 2015-01."}
    notes = []
    if end > last_done:
        notes.append(f"The end was moved to {last_done.strftime('%b %Y')}, the last completed month.")
        end = last_done
    if start < earliest:
        notes.append(f"The start was moved to {earliest.strftime('%b %Y')}: earlier months lack the 12 months "
                     f"of history that RS needs.")
        start = earliest
    if (end - start).n < 2:
        return {"error": "Choose an end month at least three months after the start month."}

    p = {
        "slots": int(min(50, max(1, int(req.get("slots", 10))))),
        "threshold": min(100.0, max(50.0, float(req.get("threshold", 98)))) / 100.0,
        "use_ema": bool(req.get("useEma", True)),
        "cr_min": float(req.get("cr", 75)), "mr_min": float(req.get("mr", 5)),
        "cost": max(0.0, float(req.get("cost", 0.2))) / 100.0,
        "cash_yield": float(req.get("cashYield", 6)) / 100.0,
        "capital": max(1000.0, float(req.get("capital", MARKETS[market]["capital"]))),
        "ranking": req.get("ranking") if req.get("ranking") in bt_engine.RANKINGS else "cr",
        **entry_settings(req),
    }

    universe = resolve_universe(req)
    if "error" in universe:
        return {"error": universe["error"]}
    symbols = sorted(set(universe["symbols"]))
    sectors = {s: universe["sector_map"][s]["Sector"] for s in symbols}

    store = price_store(market)
    cutoff = bt_data.last_cutoff(market=market)
    session = bt_data.last_session_date(market=market)
    stats = store.update(symbols, cutoff, progress, need_date=session)
    # monthly candles up to the running month (on the market's own calendar); the backtest itself uses
    # completed months only
    cur = bt_data.running_month(market=market)
    try:
        monthly_all, spikes = bt_engine.monthly_in_batches(store.panel, symbols, cur)
    except RuntimeError:
        return {"error": "Yahoo Finance returned no price data for this universe. "
                         "It may be throttling requests; wait a minute and run it again."}
    try:
        rs_index = load_rs_index(cutoff.isoformat(), market)
    except Exception as e:
        return {"error": f"Could not download the {MARKETS[market]['rs_index_name']} index from "
                         f"Yahoo Finance ({e}). Run it again."}
    try:
        bench, bench_source = load_benchmark(cutoff.isoformat(), market)
    except Exception:
        bench, bench_source = pd.Series(dtype=float), "unavailable"

    monthly = {f: df[df.index <= end] for f, df in monthly_all.items()}
    if start - 1 not in monthly["Close"].index:
        return {"error": "There is no price data for the month before the start. Pick a later start."}
    return {"market": market, "last_done": last_done, "start": start, "end": end, "notes": notes, "p": p,
            "universe": universe, "symbols": symbols, "sectors": sectors, "store": store, "session": session,
            "stats": stats, "cur": cur, "monthly_all": monthly_all, "spikes": spikes, "rs_index": rs_index,
            "bench": bench, "bench_source": bench_source, "monthly": monthly}


def bt_settings_rows(universe, start, end, p, market, bench_source):
    """The settings block of a backtest workbook."""
    lenders = universe.get("lenders") or {}
    return [
        ("Universe", universe["name"]), ("Period", f"{start.strftime('%b %Y')} to {end.strftime('%b %Y')}"),
        ("Stocks held", p["slots"]), ("Minimum RS threshold %", p["threshold"] * 100),
        ("Price above 12-month EMA", "Yes" if p["use_ema"] else "No"),
        ("Min closing range %", p["cr_min"]), ("Min monthly return %", p["mr_min"]),
        *entry_rows(p),
        ("Ranking", bt_engine.RANKINGS[p["ranking"]]), ("Cost per buy and per sell %", p["cost"] * 100),
        ("Cash yield % a year", p["cash_yield"] * 100), ("Starting capital", p["capital"]),
        ("Lending companies", "Excluded" if lenders.get("on") else "Included"),
        ("Other financial companies", "Excluded" if lenders.get("allFinancials") else "Included"),
        ("Always excluded (your list)", ", ".join(sorted(r["symbol"] for r in lenders.get("excluded", [])
                                                          if r.get("source") == "your always-exclude list")) or "None"),
        ("Currency", MARKETS[market]["currency"]),
        ("Relative strength measured against", MARKETS[market]["rs_index_name"]),
        ("Benchmark", bench_source),
    ]


def run_backtest(req, progress=None):
    """Runs one backtest request from the dashboard and returns a JSON-safe result."""
    ctx = prepare_backtest(req, progress)
    if "error" in ctx:
        return {"ok": False, "error": ctx["error"]}
    market, last_done, start, end, notes, p = (ctx[k] for k in ("market", "last_done", "start", "end", "notes", "p"))
    universe, symbols, sectors, store, session = (ctx[k] for k in ("universe", "symbols", "sectors", "store", "session"))
    stats, cur, monthly_all, spikes, rs_index = (ctx[k] for k in ("stats", "cur", "monthly_all", "spikes", "rs_index"))
    bench, bench_source, monthly = ctx["bench"], ctx["bench_source"], ctx["monthly"]
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
    settings_rows = bt_settings_rows(universe, start, end, p, market, bench_source)
    return {
        "ok": True,
        "market": market_info(market, universe["tv_prefix"]),
        "asOf": bt_data.now_ist().strftime("%d %b %Y, %H:%M IST"),
        "lenders": universe.get("lenders"),
        "universe": {"name": universe["name"], "source": universe["source"], "count": len(symbols),
                     "withData": len(with_data), "missing": missing[:40], "missingCount": len(missing)},
        "period": {"start": start.strftime("%b %Y"), "end": end.strftime("%b %Y"),
                   "startKey": str(start), "endKey": str(end), "months": (end - start).n + 1},
        "settings": {"slots": p["slots"], "threshold": p["threshold"] * 100, "useEma": p["use_ema"],
                     "cr": p["cr_min"], "mr": p["mr_min"], "cost": p["cost"] * 100,
                     "cashYield": p["cash_yield"] * 100, "capital": p["capital"],
                     "ranking": p["ranking"], "rankingLabel": bt_engine.RANKINGS[p["ranking"]],
                     "entry": p["entry"], "entryLabel": bt_engine.ENTRY_RULES[p["entry"]]},
        "benchmarkSource": bench_source,
        "notes": notes,
        "data": {"spikesRemoved": spikes, "downloaded": stats["full"] + stats["re-downloaded"],
                 "refreshed": stats["refreshed"]},
        "report": report,
        "live": live,
        "orders": orders,
        "excel": base64.b64encode(bt_engine.to_excel(report, settings_rows)).decode("ascii"),
    }


# --- START-MONTH SWEEP ---


def run_sweep(req, progress=None, step=None):
    """The backtest once for every start month from the chosen start to three months before the end."""
    ctx = prepare_backtest(req, progress)
    if "error" in ctx:
        return {"ok": False, "error": ctx["error"]}
    market, start, end, p = ctx["market"], ctx["start"], ctx["end"], ctx["p"]
    starts = sweep.start_months(start, end)
    if len(starts) > sweep.MAX_SWEEP_RUNS:
        return {"ok": False, "error": f"That is {len(starts)} start months; the most in one sweep is "
                                      f"{sweep.MAX_SWEEP_RUNS}. Pick a later start month."}
    if step:
        step("Preparing monthly prices")
    ind = bt_engine.indicators(ctx["monthly"], ctx["rs_index"])
    res = sweep.run_sweep(
        ctx["monthly"], ind, p, starts, end, ctx["sectors"], ctx["bench"],
        lambda s: bt_settings_rows(ctx["universe"], s, end, p, market, ctx["bench_source"]),
        progress=(lambda k, n, s: step(f"Backtesting from {s.strftime('%b %Y')} ({k} of {n})")) if step else None)
    rows_settings = bt_settings_rows(ctx["universe"], start, end, p, market, ctx["bench_source"])
    rows_settings[1] = ("Start months", f"Every month from {starts[0].strftime('%b %Y')} to "
                                        f"{starts[-1].strftime('%b %Y')}, each run to {end.strftime('%b %Y')}")
    if step:
        step("Writing the Excel files")
    universe = ctx["universe"]
    return {
        "ok": True,
        "market": market_info(market, universe["tv_prefix"]),
        "asOf": bt_data.now_ist().strftime("%d %b %Y, %H:%M IST"),
        "universe": {"name": universe["name"], "count": len(ctx["symbols"]),
                     "withData": len(ctx["monthly"]["Close"].columns)},
        "lenders": universe.get("lenders"),
        "end": end.strftime("%b %Y"), "endKey": str(end),
        "settings": {"threshold": p["threshold"] * 100, "cost": p["cost"] * 100, "slots": p["slots"]},
        "benchmarkSource": ctx["bench_source"],
        "notes": ctx["notes"],
        "rows": sweep.json_rows(res["rows"]),
        "overview": [(k, v) for k, v in sweep.overview(res["rows"])],
        "zip": base64.b64encode(sweep.to_zip(res, rows_settings)).decode("ascii"),
    }


# --- ALL-INDEX COMPARISON ---


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_index_tri(index_name, cutoff_key):
    """Month-end values of the index itself (TRI if possible), to compare each run with its own universe."""
    start, end = pd.Timestamp("2014-01-01").date(), bt_data.now_ist().date()
    for kind, label in (("TRI", f"{index_name} Total Return Index"),
                        ("PRICE", f"{index_name} price index (TRI unavailable)")):
        try:
            return bt_data.monthly_last(bt_data.fetch_index_niftyindices(index_name.upper(), start, end, kind)), label
        except Exception:
            continue
    return None, "unavailable"


def run_compare(req, progress=None, step=None):
    """The current backtest settings on every index, for the chosen period and since Jan 2015."""
    last_done = bt_data.last_completed_month()
    try:
        start = pd.Period(str(req.get("start") or BT_DEFAULTS["start"]), "M")
        end = min(pd.Period(str(req.get("end") or last_done), "M"), last_done)
    except Exception:
        return {"ok": False, "error": "Start and end must be months, for example 2025-01."}
    start = max(start, pd.Period(bt_data.EARLIEST_START, "M"))
    if (end - start).n < 2:
        return {"ok": False, "error": "Choose an end month at least three months after the start month."}
    periods = [(f"{start.strftime('%b %Y')} to {end.strftime('%b %Y')}", start, end)]
    long_start = pd.Period(LONG_START, "M")
    if start > long_start:
        periods.append((f"{long_start.strftime('%b %Y')} to {end.strftime('%b %Y')}", long_start, end))

    p = {
        "slots": int(min(50, max(1, int(req.get("slots", 10))))),
        "threshold": min(100.0, max(50.0, float(req.get("threshold", 98)))) / 100.0,
        "use_ema": bool(req.get("useEma", True)),
        "cr_min": float(req.get("cr", 75)), "mr_min": float(req.get("mr", 5)),
        "cost": max(0.0, float(req.get("cost", 0.2))) / 100.0,
        "cash_yield": float(req.get("cashYield", 6)) / 100.0,
        "capital": max(1000.0, float(req.get("capital", 1_000_000))),
        "ranking": req.get("ranking") if req.get("ranking") in bt_engine.RANKINGS else "cr",
        **entry_settings(req),
    }

    if step:
        step("Loading index lists")
    universes = {}
    for name in COMPARE_ORDER:
        u = resolve_universe({**req, "index": name, "csvFiles": []})
        if "error" in u:
            continue
        syms = sorted(set(u["symbols"]))
        universes[name] = {"symbols": syms, "sectors": {s: u["sector_map"][s]["Sector"] for s in syms},
                           "count": len(syms), "lenders": len((u.get("lenders") or {}).get("excluded", []))}
    if not universes:
        return {"ok": False, "error": "Could not load any index list from NSE. Run it again in a few minutes."}
    all_syms = sorted(set().union(*[set(u["symbols"]) for u in universes.values()]))

    store = price_store()
    cutoff = bt_data.last_cutoff()
    store.update(all_syms, cutoff, progress, need_date=bt_data.last_session_date())
    if step:
        step("Preparing monthly prices")
    try:
        monthly, spikes = bt_engine.monthly_in_batches(store.panel, all_syms, end)
    except RuntimeError:
        return {"ok": False, "error": "Yahoo Finance returned no price data. Wait a minute and run it again."}
    try:
        rs_index = load_rs_index(cutoff.isoformat())
    except Exception as e:
        return {"ok": False, "error": f"Could not download the Nifty 50 index from Yahoo Finance ({e}). Run it again."}
    try:
        bench, bench_source = load_benchmark(cutoff.isoformat())
    except Exception:
        bench, bench_source = pd.Series(dtype=float), "unavailable"
    if step:
        step("Fetching each index's own values")
    own = {name: load_index_tri(name, cutoff.isoformat()) for name in universes}

    ind = bt_engine.indicators(monthly, rs_index)
    res = compare.run_all(monthly, ind, universes, p, periods, bench, bench_source, own,
                          progress=(lambda k, n, what: step(f"Backtesting {what} ({k} of {n})")) if step else None)
    settings_rows = [
        ("Stocks held", p["slots"]), ("Minimum RS threshold %", p["threshold"] * 100),
        ("Price above 12-month EMA", "Yes" if p["use_ema"] else "No"), ("Min closing range %", p["cr_min"]),
        ("Min monthly return %", p["mr_min"]), *entry_rows(p), ("Ranking", bt_engine.RANKINGS[p["ranking"]]),
        ("Cost per buy and per sell %", p["cost"] * 100), ("Cash yield % a year", p["cash_yield"] * 100),
        ("Starting capital", p["capital"]),
        ("Lending companies", "Excluded" if req.get("excludeLenders", True) else "Included"),
        ("Always exclude", req.get("alwaysExclude") or ""), ("Never exclude", req.get("neverExclude") or ""),
        ("Nifty 50 benchmark", bench_source), ("One-day bad prices removed", spikes),
        ("Run at", bt_data.now_ist().strftime("%d %b %Y, %H:%M IST")),
    ]
    return {
        "ok": True, "kind": "compare",
        "asOf": bt_data.now_ist().strftime("%d %b %Y, %H:%M IST"),
        "periods": [x[0] for x in periods],
        "summary": [{k: v for k, v in r.items()} for r in res["summary"]],
        "excel": base64.b64encode(compare.to_excel(res, settings_rows)).decode("ascii"),
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

# --- SIGN-IN (accounts live in the app's Secrets; see auth.py) ---
LOGIN_CSS = """
<style>
  [data-testid="stMainBlockContainer"], .block-container { padding: 7vh 1rem 2rem !important; }
  [data-testid="stForm"] { background: #fff; border: 1px solid #d5dee3; border-radius: 14px; padding: 1.6rem 1.6rem 1.2rem; }
  div.login-title { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em; color: #0f1c2e; margin: 0 0 .25rem; }
  div.login-sub { color: #52606d; margin: 0 0 1.2rem; }
</style>
"""


@st.cache_resource
def attempt_log():
    """Wrong-password counts, shared by everyone using the app (cleared when the app restarts)."""
    return auth.AttemptLog()


def setup_page():
    """Shown while no accounts exist: the website stays locked; this page only makes the first Secrets entry."""
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        st.markdown('<div class="login-title">Set up sign-in</div><div class="login-sub">No accounts exist yet, so the '
                    'website is locked. Choose your administrator username and password below. This page only '
                    'makes the text to paste; nothing is saved here.</div>', unsafe_allow_html=True)
        with st.form("setup"):
            name = st.text_input("Your username", help=auth.USERNAME_HELP)
            pw = st.text_input("Password", type="password")
            pw2 = st.text_input("Password again", type="password")
            go = st.form_submit_button("Make my Secrets text", type="primary", width="stretch")
        if go:
            name = auth.clean_username(name)
            problem = (None if auth.USERNAME_RULE.match(name) else f"Username: {auth.USERNAME_HELP}.") \
                or auth.password_problem(pw, pw2)
            if problem:
                st.error(problem)
            else:
                st.success("Copy all of this. In Streamlit Cloud open this app's Settings, then Secrets, paste it, "
                           "and press Save. The app restarts and asks you to sign in.")
                st.code(auth.secrets_block(name, auth.hash_password(pw)), language="toml")


def login_page(cfg):
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown('<div class="login-title">Abdullah RS Leaderboard</div>'
                    '<div class="login-sub">Sign in to continue.</div>', unsafe_allow_html=True)
        with st.form("login"):
            name = st.text_input("Username", autocomplete="username")
            pw = st.text_input("Password", type="password", autocomplete="current-password")
            remember = st.checkbox(f"Keep me signed in on this device for {auth.REMEMBER_DAYS} days", value=True,
                                   help="Leave this off on a shared computer.")
            go = st.form_submit_button("Sign in", type="primary", width="stretch")
        if go:
            ok, message = auth.check_login(cfg, attempt_log(), name, pw)
            if ok:
                user = auth.clean_username(name)
                st.session_state["user"] = user
                st.session_state["signed_out"] = False
                st.session_state["new_token"] = auth.make_token(cfg, user) if remember else ""
                st.rerun()
            st.error(message)
        st.caption("No account? Ask the site's owner.")


def signed_in_user():
    """The signed-in username; otherwise draws the sign-in (or setup) page and stops the script."""
    cfg = auth.load_config(st.secrets)
    if not cfg["users"]:
        setup_page()
        st.stop()
    user = st.session_state.get("user")
    if user and user not in cfg["users"]:              # account removed from Secrets meanwhile
        user = None
        st.session_state.pop("user", None)
    if not user and not st.session_state.get("signed_out"):
        try:
            cookie = st.context.cookies.get(auth.COOKIE_NAME)
        except Exception:  # noqa: BLE001
            cookie = None
        user = auth.read_token(cfg, cookie)
        if user:
            st.session_state["user"] = user
    if not user:
        login_page(cfg)
        st.stop()
    return user, cfg


if LOGIN_REQUIRED:
    current_user, login_cfg = signed_in_user()
else:                                   # sign-in switched off in settings.py: everyone goes straight in
    current_user, login_cfg = None, {"users": {}, "admins": set(), "secret": ""}

rs_dashboard = components.declare_component("rs_dashboard", path=str(FRONTEND_DIR))

progress_slot = st.empty()

request = rs_dashboard(
    indices=ALL_INDICES,
    defaults={**SCREENER_DEFAULTS, "indexMarkets": {name: market_of(name) for name in ALL_INDICES}},
    result=st.session_state.get("result"),
    btDefaults={**BT_DEFAULTS, "end": str(bt_data.last_completed_month()), "earliest": bt_data.EARLIEST_START,
                "rankings": bt_engine.RANKINGS,
                "indexMarkets": {name: market_of(name) for name in ALL_INDICES},
                "markets": {k: {"currency": m["currency"], "capital": m["capital"], "rsIndex": m["rs_index_name"],
                                "end": str(bt_data.last_completed_month(market=k))}
                            for k, m in MARKETS.items()}},
    btResult=st.session_state.get("bt_result"),
    rulesMd=rules_doc.rules_for_app(),
    cmpResult=st.session_state.get("cmp_result"),
    sweepResult=st.session_state.get("sweep_result"),
    user=({"name": current_user, "admin": current_user in login_cfg["admins"],
           # a new "keep me signed in" cookie for the dashboard to store; "" removes it
           "token": st.session_state.get("new_token"), "cookie": auth.COOKIE_NAME,
           "days": auth.REMEMBER_DAYS} if current_user else None),
    key="rs_dashboard",
    default=None,
)

# A new request carries a new id; results are kept in the session so they survive reruns
if request and request.get("nonce") and request["nonce"] != st.session_state.get("last_nonce"):
    st.session_state["last_nonce"] = request["nonce"]
    kind = request.get("kind", "screen")
    if kind == "tokenSaved":                            # the dashboard has stored (or removed) the cookie
        st.session_state.pop("new_token", None)
        st.rerun()
    if kind == "logout":
        for k in ("user", "new_token", "result", "bt_result", "cmp_result", "sweep_result"):
            st.session_state.pop(k, None)
        st.session_state["signed_out"] = True            # do not sign straight back in from the old cookie
        st.rerun()

    def show_progress(done, total):
        progress_slot.progress(min(1.0, done / max(1, total)),
                               text=f"Downloading daily prices: {done} of {total} stocks")

    def show_step(text):
        progress_slot.info(text)

    try:
        if kind == "backtest":
            outcome = run_backtest(request, show_progress)
        elif kind == "compare":
            outcome = run_compare(request, show_progress, show_step)
        elif kind == "sweep":
            outcome = run_sweep(request, show_progress, show_step)
        else:
            outcome = run_screen(request)
    except Exception as e:
        what = {"backtest": "backtest", "compare": "comparison", "sweep": "start-month test"}.get(kind, "screen")
        outcome = {"ok": False, "error": f"The {what} stopped with an error: {type(e).__name__}: {e}"}
    progress_slot.empty()
    outcome["nonce"] = request["nonce"]
    st.session_state[{"backtest": "bt_result", "compare": "cmp_result", "sweep": "sweep_result"}.get(kind, "result")] = outcome
    st.rerun()


# --- ACCOUNT TOOLS (administrators only) ---
if current_user in login_cfg["admins"]:
    st.markdown("<style>[data-testid='stExpander'] { margin: 8px 24px 24px; }</style>", unsafe_allow_html=True)
    with st.expander("Account tools (only administrators see this)"):
        st.markdown("To **add a person** or **change a password**: make the line here, then in Streamlit Cloud open "
                    "this app's Settings, then Secrets, and put the line under `[login.users]` (replace the "
                    "person's old line when changing a password). To **remove a person**, delete their line. "
                    "Press Save; the app restarts. Make someone an administrator by adding their username to "
                    "`admins`.")
        with st.form("make_user"):
            c1, c2, c3 = st.columns(3)
            new_name = c1.text_input("Username", help=auth.USERNAME_HELP)
            new_pw = c2.text_input("Password", type="password")
            new_pw2 = c3.text_input("Password again", type="password")
            made = st.form_submit_button("Make the line")
        if made:
            new_name = auth.clean_username(new_name)
            problem = (None if auth.USERNAME_RULE.match(new_name) else f"Username: {auth.USERNAME_HELP}.") \
                or auth.password_problem(new_pw, new_pw2)
            if problem:
                st.error(problem)
            else:
                st.code(auth.user_line(new_name, auth.hash_password(new_pw)), language="toml")
                st.caption("Send the person their username and password yourself (not by the same message, ideally). "
                           "The password is not stored anywhere; only this scrambled line is.")
        st.caption(f"Accounts now: {', '.join(sorted(login_cfg['users']))}. Administrators: "
                   f"{', '.join(sorted(login_cfg['admins']))}.")
