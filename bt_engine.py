"""
RS-high rotation backtest (no Streamlit imports, so it can be tested on its own).

Rules (decided at each month-end close, traded at the next month's first open):

RS ratio  : relative strength (stock / Nifty 50) as a fraction of its highest month-end value
            in the past 12 months (the current month included), the same measure as the screener.
ENTRY     : RS ratio = 100%, or RS ratio >= the minimum threshold and higher than 2 months ago;
            and close >= 12-month EMA (if switched on), month closing range >= minimum,
            month return >= minimum.
RANKING   : when there are more candidates than empty slots: closing range (or 6- / 12-month
            return), then RS ratio, then month return, highest first.
EXIT      : RS ratio below the minimum threshold, or close below the 12-month EMA (if switched on).
            A held stock is never swapped for a better candidate.
PORTFOLIO : `slots` slots. Each new stock gets 1/slots of the portfolio value at that open,
            paid from cash; holdings are not rebalanced afterwards. Empty slots stay in cash,
            earning the cash yield.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd

LOOKBACK = 12
EMA_SPAN = 12
AT_HIGH = 1 - 1e-9
MIN_ALLOC_FRAC = 0.20       # skip a buy if less than 20% of a slot's money is left in cash
RANKINGS = {"cr": "Closing range", "ret6": "6-month return", "ret12": "12-month return"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ════════════════════════════════════════════════════════════ monthly data
def clean_daily(daily: dict) -> tuple[dict, int]:
    """Remove one-day spikes that fully revert (bad ticks) and fix the OHLC order."""
    O, H, L, C = (daily[f].copy() for f in ("Open", "High", "Low", "Close"))
    for X in (O, H, L, C):
        X[X <= 0] = np.nan
    r = C / C.ffill().shift(1)
    nxt = C.shift(-1) / C
    spike = ((r > 1.6) & (nxt < 1 / 1.6)) | ((r < 1 / 1.6) & (nxt > 1.6))
    n = int(spike.sum().sum())
    for X in (O, H, L, C):
        X[spike] = np.nan
    H = pd.DataFrame(np.fmax(np.fmax(H.values, O.values), C.values), H.index, H.columns)
    L = pd.DataFrame(np.fmin(np.fmin(L.values, O.values), C.values), L.index, L.columns)
    return {"Open": O, "High": H, "Low": L, "Close": C}, n


def to_monthly(daily: dict, last_month: pd.Period) -> dict:
    """Monthly candles up to and including `last_month` (a completed month)."""
    O, H, L, C = (daily[f] for f in ("Open", "High", "Low", "Close"))
    g = C.index.to_period("M")
    m = {"Open": O.groupby(g).first(), "High": H.groupby(g).max(),
         "Low": L.groupby(g).min(), "Close": C.groupby(g).last()}
    has = C.notna().groupby(g).any()                 # a month without a close is missing
    for k in m:
        m[k] = m[k].where(has)
        m[k] = m[k][m[k].index <= last_month]
    return m


def monthly_in_batches(get_panel, symbols: list[str], last_month: pd.Period, batch: int = 50) -> tuple[dict, int]:
    """Clean daily prices and turn them into monthly candles a batch of stocks at a time, so the
    daily prices of the whole universe are never in memory together."""
    parts = {f: [] for f in ("Open", "High", "Low", "Close")}
    spikes = 0
    for i in range(0, len(symbols), batch):
        try:
            daily = get_panel(symbols[i:i + batch])
        except RuntimeError:                         # no stock in this batch has data
            continue
        daily, n = clean_daily(daily)
        spikes += n
        m = to_monthly(daily, last_month)
        for f in parts:
            parts[f].append(m[f])
        del daily, m
    if not parts["Close"]:
        raise RuntimeError("No price data is available for this universe.")
    out = {f: pd.concat(parts[f], axis=1).sort_index() for f in parts}
    return out, spikes


# ═══════════════════════════════════════════════════════════════ signals
def indicators(m: dict, rs_index: pd.Series) -> dict:
    C, H, L = m["Close"], m["High"], m["Low"]
    idx = rs_index.reindex(C.index)
    rs = C.div(idx, axis=0)
    ratio = rs / rs.rolling(LOOKBACK, min_periods=LOOKBACK).max()
    ema = C.ewm(span=EMA_SPAN, adjust=False).mean().where(C.notna())
    return {
        "C": C,
        "ratio": ratio,
        "ratio2": ratio.shift(2),
        "ema": ema,
        "ret": C / C.shift(1) - 1,
        "cr": ((C - L) / (H - L)).where(H > L, 0.0).where(C.notna()),
        "ret6": C / C.shift(6) - 1,
        "ret12": C / C.shift(12) - 1,
        "exec_px": m["Open"].shift(-1),                # next month's first open
        "last_close": C.ffill(),
    }


def eligible(ind: dict, p: dict) -> pd.DataFrame:
    """True where a stock may be bought at that month-end."""
    ratio, thr = ind["ratio"], p["threshold"]
    rs_ok = (ratio >= AT_HIGH) | ((ratio >= thr) & (ratio > ind["ratio2"]))
    ok = rs_ok & (ind["cr"] * 100 >= p["cr_min"]) & (ind["ret"] * 100 >= p["mr_min"])
    ok &= ind["exec_px"].notna() & (ind["exec_px"] > 0)
    if p["use_ema"]:
        ok &= ind["C"] >= ind["ema"]
    return ok.fillna(False)


def exit_reason(ind: dict, p: dict, t: pd.Period, s: str) -> str | None:
    c = ind["C"].at[t, s]
    if pd.isna(c):
        return "Price data stopped"
    r = ind["ratio"].at[t, s]
    if pd.isna(r) or r < p["threshold"]:
        return "RS below threshold"
    if p["use_ema"] and c < ind["ema"].at[t, s]:
        return "Close below 12-month EMA"
    return None


def rank(ind: dict, p: dict, t: pd.Period, cands: list[str]) -> list[str]:
    if not cands:
        return []
    key = p.get("ranking", "cr")
    df = pd.DataFrame({
        "k1": ind[key].loc[t, cands], "k2": ind["ratio"].loc[t, cands], "k3": ind["ret"].loc[t, cands],
        "sym": cands,
    }).fillna({"k1": -np.inf, "k2": -np.inf, "k3": -np.inf})
    df = df.sort_values(["k1", "k2", "k3", "sym"], ascending=[False, False, False, True])
    return df["sym"].tolist()


# ════════════════════════════════════════════════════════════ simulation
def simulate(m: dict, ind: dict, p: dict, start: pd.Period, end: pd.Period, sectors: dict) -> dict:
    """Month-by-month portfolio. Returns equity path, monthly holdings and the trade log."""
    O, C, last_close = m["Open"], ind["C"], ind["last_close"]
    elig = eligible(ind, p)
    slots, cost = int(p["slots"]), p["cost"]
    cash_m = (1 + p["cash_yield"]) ** (1 / 12) - 1

    def px_open(t, s):
        v = O.at[t, s] if t in O.index else np.nan
        return v if pd.notna(v) and v > 0 else last_close.at[t - 1, s]

    def px_close(t, s):
        v = C.at[t, s] if t in C.index else np.nan
        return v if pd.notna(v) else last_close.at[t, s]

    cash = float(p["capital"])
    pos: dict[str, dict] = {}
    equity = [{"period": start - 1, "value": cash, "invested": 0.0, "n": 0}]
    months, trades = [], []

    for t in pd.period_range(start - 1, end - 1, freq="M"):
        nxt = t + 1
        # ---- decisions at the close of month t
        exits = [(s, why) for s in pos if (why := exit_reason(ind, p, t, s))]
        free = slots - (len(pos) - len(exits))
        cands = [s for s in elig.columns[elig.loc[t].values] if s not in pos]
        ranked = rank(ind, p, t, cands)
        picks = ranked[:max(0, free)]

        # ---- trades at the first open of month t+1
        exited = []
        for s, why in exits:
            ps = pos.pop(s)
            px = px_open(nxt, s)
            proceeds = ps["units"] * px * (1 - cost)
            cash += proceeds
            held = (nxt - ps["entry"]).n
            trades.append({
                "symbol": s, "sector": sectors.get(s, "N/A"),
                "entry": ps["entry"], "entryPx": ps["entryPx"], "exit": nxt, "exitPx": px,
                "invested": ps["amount"], "proceeds": proceeds,
                "ret": proceeds / ps["amount"] - 1, "months": held, "reason": why, "open": False,
            })
            exited.append({"symbol": s, "reason": why})

        value_open = cash + sum(ps["units"] * px_open(nxt, s) for s, ps in pos.items())
        target = value_open / slots
        entered, skipped = [], []
        for s in picks:
            amt = min(target, cash)
            if amt < MIN_ALLOC_FRAC * target:
                skipped.append(s)
                continue
            px = O.at[nxt, s]
            pos[s] = {"units": amt * (1 - cost) / px, "entry": nxt, "entryPx": px, "amount": amt,
                      "rsRatio": float(ind["ratio"].at[t, s]), "cr": float(ind["cr"].at[t, s])}
            cash -= amt
            entered.append(s)

        cash *= 1 + cash_m

        # ---- value at the close of month t+1
        held_rows = []
        stock_value = 0.0
        for s, ps in pos.items():
            v = ps["units"] * px_close(nxt, s)
            stock_value += v
            start_px = ps["entryPx"] if ps["entry"] == nxt else px_close(t, s)
            held_rows.append({"symbol": s, "sector": sectors.get(s, "N/A"), "value": v,
                              "ret": px_close(nxt, s) / start_px - 1, "new": ps["entry"] == nxt})
        total = cash + stock_value
        for h in held_rows:
            h["weight"] = h["value"] / total if total else 0.0
        equity.append({"period": nxt, "value": total, "invested": stock_value / total if total else 0.0,
                       "n": len(pos)})
        months.append({
            "period": nxt, "held": sorted(held_rows, key=lambda h: -h["weight"]),
            "entered": entered, "exited": exited, "skipped": skipped,
            "candidates": len(cands), "cash": cash / total if total else 1.0,
        })

    for s, ps in pos.items():                       # positions still open at the end
        px = px_close(end, s)
        trades.append({
            "symbol": s, "sector": sectors.get(s, "N/A"),
            "entry": ps["entry"], "entryPx": ps["entryPx"], "exit": end, "exitPx": px,
            "invested": ps["amount"], "proceeds": ps["units"] * px,
            "ret": ps["units"] * px / ps["amount"] - 1, "months": (end - ps["entry"]).n + 1,
            "reason": "Still held", "open": True,
        })
    return {"equity": equity, "months": months, "trades": trades}


# ═══════════════════════════════════════════════════════════════ metrics
def max_drawdown(values: pd.Series) -> tuple[float, pd.Series]:
    dd = values / values.cummax() - 1
    return float(dd.min()), dd


def series_stats(values: pd.Series, rf: float) -> dict:
    r = values.pct_change().dropna()
    n = len(r)
    years = n / 12
    total = values.iloc[-1] / values.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else float("nan")
    vol = r.std(ddof=1) * math.sqrt(12) if n > 1 else float("nan")
    rf_m = (1 + rf) ** (1 / 12) - 1
    sharpe = (r.mean() - rf_m) / r.std(ddof=1) * math.sqrt(12) if n > 1 and r.std(ddof=1) > 0 else float("nan")
    mdd, _ = max_drawdown(values)
    return {"cagr": cagr, "total": total, "vol": vol, "sharpe": sharpe, "maxDD": mdd,
            "calmar": cagr / abs(mdd) if mdd < 0 else float("nan"),
            "best": float(r.max()) if n else float("nan"), "worst": float(r.min()) if n else float("nan")}


def trade_stats(trades: list[dict], months: list[dict], equity: list[dict]) -> dict:
    closed = [t for t in trades if not t["open"]]
    wins = [t["ret"] for t in closed if t["ret"] > 0]
    losses = [t["ret"] for t in closed if t["ret"] <= 0]
    gross_win = sum(t["proceeds"] - t["invested"] for t in closed if t["ret"] > 0)
    gross_loss = -sum(t["proceeds"] - t["invested"] for t in closed if t["ret"] <= 0)
    inv = [e["invested"] for e in equity[1:]]
    reasons = {}
    for t in closed:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "trades": len(trades), "closed": len(closed), "open": len(trades) - len(closed),
        "winRate": len(wins) / len(closed) if closed else float("nan"),
        "avgWin": float(np.mean(wins)) if wins else float("nan"),
        "avgLoss": float(np.mean(losses)) if losses else float("nan"),
        "profitFactor": gross_win / gross_loss if gross_loss > 0 else float("nan"),
        "avgMonths": float(np.mean([t["months"] for t in closed])) if closed else float("nan"),
        "medianRet": float(np.median([t["ret"] for t in closed])) if closed else float("nan"),
        "avgInvested": float(np.mean(inv)) if inv else float("nan"),
        "monthsFullyInCash": sum(1 for x in inv if x < 1e-9),
        "entriesPerYear": len(trades) / (len(months) / 12) if months else float("nan"),
        "exitReasons": reasons,
    }


# ══════════════════════════════════════════════════════════════ reporting
def _f(x, nd=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def label(p: pd.Period) -> str:
    return p.strftime("%b-%y")


def build_report(sim: dict, bench: pd.Series, p: dict, start: pd.Period, end: pd.Period) -> dict:
    eq = pd.Series({e["period"]: e["value"] for e in sim["equity"]})
    b = bench.reindex(eq.index).ffill()
    has_bench = b.notna().all()
    bvals = b / b.iloc[0] * eq.iloc[0] if has_bench else None
    _, dd = max_drawdown(eq)
    bdd = max_drawdown(bvals)[1] if has_bench else None

    curve = []
    for i, e in enumerate(sim["equity"]):
        per = e["period"]
        curve.append({"m": label(per), "s": _f(e["value"], 2), "b": _f(bvals.iloc[i], 2) if has_bench else None,
                      "dd": _f(dd.iloc[i]), "bdd": _f(bdd.iloc[i]) if has_bench else None,
                      "inv": _f(e["invested"]), "n": e["n"]})

    sret = eq.pct_change()
    bret = bvals.pct_change() if has_bench else None
    years = sorted({per.year for per in eq.index[1:]})
    grid, yearly = [], []
    for y in years:
        row = []
        for mth in range(1, 13):
            per = pd.Period(year=y, month=mth, freq="M")
            row.append(_f(sret.get(per)) if per in eq.index[1:] else None)
        grid.append({"year": y, "months": row})
        in_y = [per for per in eq.index[1:] if per.year == y]
        prev = in_y[0] - 1
        ys = eq[in_y[-1]] / eq[prev] - 1
        yb = bvals[in_y[-1]] / bvals[prev] - 1 if has_bench else None
        yearly.append({"year": y, "s": _f(ys), "b": _f(yb), "months": len(in_y)})

    beat = None
    if has_bench:
        diff = (sret - bret).dropna()
        beat = _f((diff > 0).mean())

    months = [{
        "m": label(x["period"]),
        "held": [{"symbol": h["symbol"], "sector": h["sector"], "w": _f(h["weight"]), "r": _f(h["ret"]),
                  "new": h["new"]} for h in x["held"]],
        "entered": x["entered"], "exited": x["exited"], "skipped": x["skipped"],
        "candidates": x["candidates"], "cash": _f(x["cash"]),
    } for x in sim["months"]]

    trades = [{
        "symbol": t["symbol"], "sector": t["sector"], "entry": label(t["entry"]), "entryPx": _f(t["entryPx"], 2),
        "exit": label(t["exit"]), "exitPx": _f(t["exitPx"], 2), "ret": _f(t["ret"]), "months": t["months"],
        "reason": t["reason"], "open": t["open"], "pnl": _f(t["proceeds"] - t["invested"], 0),
    } for t in sorted(sim["trades"], key=lambda t: (t["entry"], t["symbol"]))]

    sstats = series_stats(eq, p["cash_yield"])
    return {
        "curve": curve,
        "strategy": {k: _f(v) for k, v in sstats.items()},
        "benchmark": {k: _f(v) for k, v in series_stats(bvals, p["cash_yield"]).items()} if has_bench else None,
        "beatShare": beat,
        "tradeStats": {k: (_f(v) if not isinstance(v, dict) else v) for k, v in
                       trade_stats(sim["trades"], sim["months"], sim["equity"]).items()},
        "grid": grid, "yearly": yearly, "months": months, "trades": trades,
        "finalValue": _f(eq.iloc[-1], 0), "capital": _f(eq.iloc[0], 0),
        "benchFinal": _f(bvals.iloc[-1], 0) if has_bench else None,
    }


def to_excel(report: dict, settings_rows: list[tuple]) -> bytes:
    """Summary, monthly values, trades and holdings as an Excel workbook."""
    from openpyxl.styles import Alignment, Font, PatternFill

    out = io.BytesIO()
    head_fill = PatternFill(start_color="0F1C2E", fill_type="solid")
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        s, b = report["strategy"], report["benchmark"] or {}
        names = [("CAGR", "cagr"), ("Total return", "total"), ("Max drawdown", "maxDD"),
                 ("Volatility (annual)", "vol"), ("Sharpe ratio", "sharpe"), ("Best month", "best"),
                 ("Worst month", "worst")]
        summary = pd.DataFrame([(n, s.get(k), b.get(k)) for n, k in names],
                               columns=["Measure", "Strategy", "Nifty 50 benchmark"])
        summary.to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame(settings_rows, columns=["Setting", "Value"]).to_excel(
            xw, sheet_name="Summary", index=False, startrow=len(summary) + 3)
        ts = report["tradeStats"]
        tsr = [(k, v) for k, v in ts.items() if not isinstance(v, dict)]
        pd.DataFrame(tsr, columns=["Trade statistic", "Value"]).to_excel(
            xw, sheet_name="Summary", index=False, startrow=len(summary) + len(settings_rows) + 6)

        pd.DataFrame([{"Month": c["m"], "Strategy value": c["s"], "Benchmark value": c["b"],
                       "Strategy drawdown": c["dd"], "Benchmark drawdown": c["bdd"],
                       "Invested share": c["inv"], "Stocks held": c["n"]} for c in report["curve"]]
                     ).to_excel(xw, sheet_name="Monthly", index=False)
        pd.DataFrame([{"Symbol": t["symbol"], "Sector": t["sector"], "Bought (month)": t["entry"],
                       "Buy price": t["entryPx"], "Sold (month)": t["exit"], "Sell price": t["exitPx"],
                       "Return after costs": t["ret"], "Months held": t["months"], "Profit": t["pnl"],
                       "Exit reason": t["reason"]} for t in report["trades"]]
                     ).to_excel(xw, sheet_name="Trades", index=False)
        pd.DataFrame([{"Month": mo["m"], "Symbol": h["symbol"], "Sector": h["sector"], "Weight": h["w"],
                       "Return in month": h["r"], "Bought this month": "Yes" if h["new"] else ""}
                      for mo in report["months"] for h in mo["held"]]
                     ).to_excel(xw, sheet_name="Holdings", index=False)

        pct_cols = {"Monthly": "DEF", "Trades": "G", "Holdings": "DE"}
        ws = xw.sheets["Summary"]
        for r_i, (_, key) in enumerate(names, start=2):           # strategy vs benchmark block
            if key != "sharpe":
                for letter in "BC":
                    ws[f"{letter}{r_i}"].number_format = "0.0%"
        for name, ws in xw.sheets.items():
            for cell in ws[1]:
                cell.fill, cell.font = head_fill, Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(horizontal="center")
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(
                    12, min(40, max(len(str(c.value or "")) for c in col) + 2))
            for letter in pct_cols.get(name, ""):
                for cell in ws[letter][1:]:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.0%"
    return out.getvalue()
