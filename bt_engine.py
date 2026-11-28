"""
RS-high rotation backtest (no Streamlit imports, so it can be tested on its own).

Rules (decided at each month-end close, traded at the next month's first open):

RS ratio  : relative strength (stock / RS index: Nifty 50, or S&P 500 for US) as a fraction of its highest month-end value
            in the past 12 months (the current month included), the same measure as the screener.
ENTRY     : RS ratio = 100%, or RS ratio >= the buy threshold (default: the RS threshold) and higher than 2 months ago;
            and close >= 12-month EMA (if switched on), month closing range >= minimum,
            month return >= minimum.
RANKING   : when there are more candidates than empty slots: closing range (or 6- / 12-month
            return), then RS ratio, then month return, highest first.
EXIT      : RS ratio below the minimum threshold, or close below the 12-month EMA (if switched on).
BREADTH   : if switched on, at a month-end when market breadth (share of the universe's stocks closing at or above
            their 12-month EMA) is below the level and lower than the month before, sell everything and buy nothing.
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
SPIKE_FACTOR = 1.6          # a one-day move of this size that fully reverses next day is a bad price
ENTRY_RULES = {"rs": "RS rule", "breakout": "Setup A: first close near the 12-month high, with a second chance after a pause"}
HIGH_WINDOW = 12            # months in "12-month highest close" (Setup A)
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
    k = SPIKE_FACTOR
    spike = ((r > k) & (nxt < 1 / k)) | ((r < 1 / k) & (nxt > k))
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


def breadth(ind: dict) -> pd.Series:
    """Market breadth at each month-end: the share of the universe's stocks with a close that month whose close is at
    or above their 12-month EMA (RULES.md 4.13). Worked out from ind itself, so a sliced universe gets its own."""
    key = ("_breadth",)
    if key not in ind:
        C, ema = ind["C"], ind["ema"]
        have = C.notna()
        n = have.sum(axis=1)
        ind[key] = ((C >= ema) & have).sum(axis=1).div(n.where(n > 0))
    return ind[key]


def breadth_reason(p: dict) -> str:
    return f"Market breadth below {p['breadth_level'] * 100:g}% and falling"


def risk_off(ind: dict, p: dict, t: pd.Period) -> bool:
    """True when the breadth rule is on and, at the month-end t, breadth is below the level and lower than at the
    month-end before (RULES.md 4.13): everything is sold at the next open and nothing is bought."""
    if not p.get("breadth_rule"):
        return False
    b = breadth(ind)
    if t not in b.index or (t - 1) not in b.index:
        return False
    now, prev = b.at[t], b.at[t - 1]
    return bool(pd.notna(now) and pd.notna(prev) and now < p["breadth_level"] and now < prev)


def breadth_info(ind: dict, p: dict, t: pd.Period) -> dict:
    """Breadth at t and the month-end before, and what the rule decides, for the orders panel and the live view."""
    b = breadth(ind)
    now = b.get(t)
    prev = b.get(t - 1)
    return {"on": bool(p.get("breadth_rule")), "level": _f(p.get("breadth_level", 0.5)),
            "now": _f(now) if now is not None and pd.notna(now) else None,
            "prev": _f(prev) if prev is not None and pd.notna(prev) else None,
            "riskOff": risk_off(ind, p, t), "month": t.strftime("%b %Y")}


def eligible(ind: dict, p: dict, need_next_open: bool = True) -> pd.DataFrame:
    """True where a stock may be bought at that month-end. need_next_open=False is for the running
    month, whose next open has not happened yet. The RS part uses the buy threshold (RULES.md 4.2), which is never
    below the selling threshold; without one it is the selling threshold."""
    ratio, thr = ind["ratio"], max(p["threshold"], p.get("buy_threshold") or p["threshold"])
    rs_ok = (ratio >= AT_HIGH) | ((ratio >= thr) & (ratio > ind["ratio2"]))
    ok = rs_ok & (ind["cr"] * 100 >= p["cr_min"]) & (ind["ret"] * 100 >= p["mr_min"])
    if need_next_open:
        ok &= ind["exec_px"].notna() & (ind["exec_px"] > 0)
    if p["use_ema"]:
        ok &= ind["C"] >= ind["ema"]
    return ok.fillna(False)


def setup_signals(ind: dict, p: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Setup A and its second chance at each month-end (RULES.md 4.12). Neither depends on what the portfolio
    holds or whether slots were full.

    Setup A (fresh breakout): close >= near_high x the highest close of the last HIGH_WINDOW month-ends, for the
    first time in `quiet` month-ends; RS >= threshold; the usual EMA, closing-range and monthly-return filters.
    Second chance: within `window` months of its Setup A, a stock whose close has fallen below reset x its
    12-month highest close at some month-end since its last signal, and that now closes >= near_high x that high
    again with the same RS, EMA, closing-range and monthly-return filters."""
    key = ("_setups", p["near_high"], p["quiet"], p["reset"], p["window"], p["threshold"], p["cr_min"],
           p["mr_min"], p["use_ema"])
    if key in ind:
        return ind[key]
    C, ema, ratio, cr, ret = ind["C"], ind["ema"], ind["ratio"], ind["cr"], ind["ret"]
    high = C.rolling(HIGH_WINDOW, min_periods=HIGH_WINDOW).max()
    near = (C >= p["near_high"] * high).astype(float).where(high.notna() & C.notna())
    quiet = int(p["quiet"])
    before = near.shift(1).rolling(quiet, min_periods=quiet).max()        # NaN without full history
    strong = (ratio >= p["threshold"]) & (cr * 100 >= p["cr_min"]) & (ret * 100 >= p["mr_min"])
    if p["use_ema"]:
        strong &= C >= ema
    a = ((near == 1) & (before == 0) & strong).fillna(False)
    again = ((near == 1) & strong).fillna(False)
    below = (C < p["reset"] * high).fillna(False)                            # the pause that re-arms it

    av, gv, bv = a.values, again.values, below.values
    second = np.zeros(av.shape, dtype=bool)
    last_a = np.full(av.shape[1], -10 ** 6)
    armed = np.zeros(av.shape[1], dtype=bool)
    window = int(p["window"])
    for i in range(av.shape[0]):
        second[i] = (i - last_a <= window) & armed & gv[i] & ~av[i]
        last_a = np.where(av[i], i, last_a)
        armed = np.where(av[i] | second[i], False, armed) | bv[i]
    c = pd.DataFrame(second, index=C.index, columns=C.columns)
    ind[key] = (a, c)
    return a, c


def entry_signals(ind: dict, p: dict, need_next_open: bool = True) -> dict:
    """{"RS": eligible} for the RS rule, {"A": ..., "C": ...} for Setup A with its second chance."""
    if p.get("entry", "rs") != "breakout":
        return {"RS": eligible(ind, p, need_next_open)}
    a, c = setup_signals(ind, p)
    if need_next_open:
        ok = ind["exec_px"].notna() & (ind["exec_px"] > 0)
        a, c = a & ok, c & ok
    return {"A": a, "C": c if p.get("second", True) else c & False}


def entry_picks(sig: dict, ind: dict, p: dict, t: pd.Period, held: dict, free: int,
                exclude=()) -> tuple[list, list]:
    """Stocks to buy at the next open, in order, as (symbol, setup) pairs, plus every qualifying stock in
    order (for reserves and counts). held: {symbol: setup} after this month's exits; exclude: other symbols
    that may not be bought (the ones being sold). Setup A and second-chance stocks share one ranking."""
    free = max(0, free)
    skip = set(held) | set(exclude)
    kind = {}
    for k in ("RS", "A", "C"):
        if k in sig:
            for s in sig[k].columns[sig[k].loc[t].values]:
                if s not in skip:
                    kind.setdefault(s, k)
    ranked = rank(ind, p, t, list(kind))
    qualified = [(s, kind[s]) for s in ranked]
    return qualified[:free], qualified


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
    sig = entry_signals(ind, p)
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
        off = risk_off(ind, p, t)
        if off:                                    # breadth rule: everything goes, nothing is bought
            named = dict(exits)
            exits = [(s, named.get(s) or breadth_reason(p)) for s in pos]
        free = 0 if off else slots - (len(pos) - len(exits))
        out = {s for s, _ in exits}
        picks, qualified = entry_picks(sig, ind, p, t, {s: ps["setup"] for s, ps in pos.items() if s not in out},
                                       free, exclude=out)
        cands = [s for s, _ in qualified]

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
                "setup": ps["setup"],
            })
            exited.append({"symbol": s, "reason": why})

        value_open = cash + sum(ps["units"] * px_open(nxt, s) for s, ps in pos.items())
        target = value_open / slots
        entered, skipped, setups = [], [], {}
        for s, setup in picks:
            amt = min(target, cash)
            if amt < MIN_ALLOC_FRAC * target:
                skipped.append(s)
                continue
            px = O.at[nxt, s]
            pos[s] = {"units": amt * (1 - cost) / px, "entry": nxt, "entryPx": px, "amount": amt,
                      "rsRatio": float(ind["ratio"].at[t, s]), "cr": float(ind["cr"].at[t, s]), "setup": setup}
            cash -= amt
            entered.append(s)
            setups[s] = setup

        cash *= 1 + cash_m

        # ---- value at the close of month t+1
        held_rows = []
        stock_value = 0.0
        for s, ps in pos.items():
            v = ps["units"] * px_close(nxt, s)
            stock_value += v
            start_px = ps["entryPx"] if ps["entry"] == nxt else px_close(t, s)
            held_rows.append({"symbol": s, "sector": sectors.get(s, "N/A"), "value": v,
                              "ret": px_close(nxt, s) / start_px - 1, "new": ps["entry"] == nxt,
                              "setup": ps["setup"]})
        total = cash + stock_value
        for h in held_rows:
            h["weight"] = h["value"] / total if total else 0.0
        equity.append({"period": nxt, "value": total, "invested": stock_value / total if total else 0.0,
                       "n": len(pos)})
        months.append({
            "period": nxt, "held": sorted(held_rows, key=lambda h: -h["weight"]),
            "entered": entered, "exited": exited, "skipped": skipped, "setups": setups,
            "candidates": len(cands), "cash": cash / total if total else 1.0,
            "riskOff": off, "breadth": float(breadth(ind).get(t, np.nan)),
        })

    for s, ps in pos.items():                       # positions still open at the end
        px = px_close(end, s)
        trades.append({
            "symbol": s, "sector": sectors.get(s, "N/A"),
            "entry": ps["entry"], "entryPx": ps["entryPx"], "exit": end, "exitPx": px,
            "invested": ps["amount"], "proceeds": ps["units"] * px,
            "ret": ps["units"] * px / ps["amount"] - 1, "months": (end - ps["entry"]).n + 1,
            "reason": "Still held", "open": True, "setup": ps["setup"],
        })
    return {"equity": equity, "months": months, "trades": trades}


# ═══════════════════════════════════════════════════════════ running month
def live_view(m: dict, ind: dict, p: dict, start: pd.Period, end: pd.Period, cur: pd.Period,
              sectors: dict, bench: pd.Series) -> dict | None:
    """The month after `end`, which is still running.

    Holdings: final. They were decided at the close of `end` and bought at the first open of `cur`.
    Returns: provisional, from that open (or the `end` close for stocks carried over) to the latest close.
    Next month's changes: provisional, the exits and buys the rules would make if `cur` closed at the
    latest prices. m and ind must contain `cur` as a partial month.
    """
    if cur not in m["Close"].index or m["Close"].loc[cur].notna().sum() == 0:
        return None
    sim = simulate(m, ind, p, start, cur, sectors)
    mo = sim["months"][-1]
    v_prev, v_now = sim["equity"][-2]["value"], sim["equity"][-1]["value"]
    open_trades = {t["symbol"]: t for t in sim["trades"] if t["open"]}

    held = []
    for h in mo["held"]:
        t = open_trades.get(h["symbol"], {})
        held.append({"symbol": h["symbol"], "sector": h["sector"], "setup": h["setup"],
                     "w": _f(h["weight"]), "mtd": _f(h["ret"]),
                     "since": _f(t.get("ret")), "bought": label(t["entry"]) if t else None,
                     "entryPx": _f(t.get("entryPx"), 2), "lastPx": _f(t.get("exitPx"), 2), "new": h["new"]})

    # what the rules would do at the next open if this month closed at the latest prices
    keep_syms = [h["symbol"] for h in mo["held"]]
    off = risk_off(ind, p, cur)
    exits = []
    for s in keep_syms:
        why = exit_reason(ind, p, cur, s) or (breadth_reason(p) if off else None)
        if why:
            exits.append({"symbol": s, "reason": why, "ratio": _f(ind["ratio"].at[cur, s])})
    free = 0 if off else int(p["slots"]) - (len(keep_syms) - len(exits))
    out = {e["symbol"] for e in exits}
    picks, qualified = entry_picks(entry_signals(ind, p, need_next_open=False), ind, p, cur,
                                   {h["symbol"]: h["setup"] for h in mo["held"] if h["symbol"] not in out}, free,
                                   exclude=out)
    chosen = {s for s, _ in picks}
    rest = [x for x in qualified if x[0] not in chosen]

    def info(x):
        s, setup = x
        return {"symbol": s, "sector": sectors.get(s, "N/A"), "setup": setup, "ratio": _f(ind["ratio"].at[cur, s]),
                "cr": _f(ind["cr"].at[cur, s]), "ret": _f(ind["ret"].at[cur, s]),
                "rank": _f(ind[p.get("ranking", "cr")].at[cur, s])}

    b_now, b_prev = bench.get(cur), bench.get(end)
    return {
        "month": cur.strftime("%b %Y"), "monthKey": label(cur), "decidedAt": end.strftime("%b %Y"),
        "held": held, "sold": mo["exited"], "skipped": mo["skipped"], "cash": _f(mo["cash"]),
        "mtd": _f(v_now / v_prev - 1), "valueNow": _f(v_now, 0),
        "benchMtd": _f(b_now / b_prev - 1) if b_now is not None and b_prev is not None and pd.notna(b_now) and pd.notna(b_prev) else None,
        "next": {"exits": exits, "buys": [info(x) for x in picks],
                 "reserve": [info(x) for x in rest[:5]],
                 "free": max(0, free), "qualified": len(qualified)},
        "breadth": breadth_info(ind, p, cur), "heldRiskOff": bool(mo.get("riskOff")),
    }


# ═══════════════════════════════════════════════════════ orders for next open
def next_orders(m: dict, ind: dict, p: dict, sim: dict, end: pd.Period, sectors: dict) -> dict:
    """What to do at the first open after the `end` close, which is complete.

    fresh    : a new portfolio starting now; the top `slots` stocks that qualify, 1/slots of the capital each.
    continue : the backtest's own portfolio; holdings to sell, holdings to keep, and buys for the free slots.
    """
    slots = int(p["slots"])
    sig = entry_signals(ind, p, need_next_open=False)
    C = ind["C"]
    setup_of = {}
    off = risk_off(ind, p, end)                    # breadth rule: sell everything, buy nothing

    def info(s):
        close = C.at[end, s]
        return {"symbol": s, "sector": sectors.get(s, "N/A"), "setup": setup_of.get(s), "close": _f(close, 2),
                "ratio": _f(ind["ratio"].at[end, s]), "cr": _f(ind["cr"].at[end, s]), "ret": _f(ind["ret"].at[end, s]),
                "rank": _f(ind[p.get("ranking", "cr")].at[end, s])}

    fresh_picks, qualified = entry_picks(sig, ind, p, end, {}, 0 if off else slots)
    setup_of.update({s: k for s, k in qualified})
    ranked_all = [s for s, _ in qualified]
    fresh_syms = [s for s, _ in fresh_picks]

    # ---- fresh start
    alloc = p["capital"] / slots
    fresh = []
    for s in fresh_syms:
        row = info(s)
        qty = int(alloc // row["close"]) if row["close"] else 0
        row.update({"weight": _f(1 / slots), "amount": _f(alloc, 0), "qty": qty, "cost": _f(qty * (row["close"] or 0), 0)})
        fresh.append(row)

    # ---- continue the backtest's portfolio
    last = sim["months"][-1]
    held = {h["symbol"]: h for h in last["held"]}
    setup_of.update({s: h["setup"] for s, h in held.items()})
    sells, keeps = [], []
    for s, h in held.items():
        why = exit_reason(ind, p, end, s) or (breadth_reason(p) if off else None)
        row = {**info(s), "weight": _f(h["weight"])}
        if why:
            sells.append({**row, "reason": why})
        else:
            keeps.append(row)
    free = 0 if off else slots - len(keeps)
    cash_share = last["cash"] + sum(x["weight"] or 0 for x in sells) * (1 - p["cost"])
    cont_picks, cont_q = entry_picks(sig, ind, p, end, {r["symbol"]: held[r["symbol"]]["setup"] for r in keeps}, free,
                                     exclude={r["symbol"] for r in sells})
    for s, k in cont_q:
        setup_of.setdefault(s, k)
    buys = []
    for s, k in cont_picks:
        setup_of[s] = k
        if len(buys) >= free:
            break
        w = min(1 / slots, cash_share)
        if w < MIN_ALLOC_FRAC / slots:
            break
        cash_share -= w
        buys.append({**info(s), "weight": _f(w)})

    return {
        "decidedAt": end.strftime("%b %Y"), "executeIn": (end + 1).strftime("%b %Y"),
        "qualified": len(ranked_all), "breadth": breadth_info(ind, p, end),
        "fresh": {"buys": fresh, "reserve": [info(s) for s in ranked_all if s not in fresh_syms][:5],
                  "capital": _f(p["capital"], 0), "perStock": _f(alloc, 0), "slots": slots},
        "continue": {"sells": sells, "keeps": sorted(keeps, key=lambda r: -(r["weight"] or 0)), "buys": buys,
                     "free": free, "cashAfter": _f(max(0.0, cash_share))},
    }


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
                  "new": h["new"], "setup": h.get("setup", "RS")} for h in x["held"]],
        "entered": x["entered"], "exited": x["exited"], "skipped": x["skipped"],
        "candidates": x["candidates"], "cash": _f(x["cash"]),
    } for x in sim["months"]]

    trades = [{
        "symbol": t["symbol"], "sector": t["sector"], "entry": label(t["entry"]), "entryPx": _f(t["entryPx"], 2),
        "exit": label(t["exit"]), "exitPx": _f(t["exitPx"], 2), "ret": _f(t["ret"]), "months": t["months"],
        "reason": t["reason"], "open": t["open"], "pnl": _f(t["proceeds"] - t["invested"], 0),
        "setup": t.get("setup", "RS"),
    } for t in sorted(sim["trades"], key=lambda t: (t["entry"], t["symbol"]))]

    by_setup = {}
    if p.get("entry", "rs") == "breakout":
        for k in ("A", "C"):
            tk = [t for t in sim["trades"] if t.get("setup") == k]
            closed = [t for t in tk if not t["open"]]
            rets = [t["ret"] for t in closed]
            by_setup[k] = {"trades": len(tk), "closed": len(closed),
                           "winRate": _f(sum(r > 0 for r in rets) / len(rets)) if rets else None,
                           "avgRet": _f(sum(rets) / len(rets)) if rets else None,
                           "avgMonths": _f(sum(t["months"] for t in closed) / len(closed), 1) if closed else None,
                           "profit": _f(sum(t["proceeds"] - t["invested"] for t in tk), 0)}

    # market breadth at each decision month-end (the month before each row's period) and what the rule decided
    bseries = breadth_history(sim, p)
    sstats = series_stats(eq, p["cash_yield"])
    return {
        "breadth": bseries,
        "curve": curve,
        "strategy": {k: _f(v) for k, v in sstats.items()},
        "benchmark": {k: _f(v) for k, v in series_stats(bvals, p["cash_yield"]).items()} if has_bench else None,
        "beatShare": beat,
        "tradeStats": {k: (_f(v) if not isinstance(v, dict) else v) for k, v in
                       trade_stats(sim["trades"], sim["months"], sim["equity"]).items()},
        "grid": grid, "yearly": yearly, "months": months, "trades": trades, "bySetup": by_setup,
        "finalValue": _f(eq.iloc[-1], 0), "capital": _f(eq.iloc[0], 0),
        "benchFinal": _f(bvals.iloc[-1], 0) if has_bench else None,
    }


def breadth_history(sim: dict, p: dict) -> dict:
    """Breadth at each month-end the backtest decided at, with the decision; and how often the rule sold out."""
    rows, prev_off, switches = [], False, 0
    for x in sim["months"]:
        dec = x["period"] - 1
        off = bool(x.get("riskOff"))
        switches += off != prev_off
        prev_off = off
        rows.append({"m": label(dec), "b": _f(x.get("breadth")), "off": off})
    for i in range(1, len(rows)):
        b0, b1 = rows[i - 1]["b"], rows[i]["b"]
        rows[i]["chg"] = _f(b1 - b0) if b0 is not None and b1 is not None else None
    if rows:
        rows[0]["chg"] = None
    n_off = sum(r["off"] for r in rows)
    return {"on": bool(p.get("breadth_rule")), "level": _f(p.get("breadth_level", 0.5)), "rows": rows,
            "monthsOff": n_off, "share": _f(n_off / len(rows)) if rows else None, "switches": switches}


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
                               columns=["Measure", "Strategy", "Benchmark"])
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
                       "Exit reason": t["reason"], "Entry setup": t.get("setup", "RS")} for t in report["trades"]]
                     ).to_excel(xw, sheet_name="Trades", index=False)
        pd.DataFrame([{"Month": mo["m"], "Symbol": h["symbol"], "Sector": h["sector"], "Weight": h["w"],
                       "Return in month": h["r"], "Bought this month": "Yes" if h["new"] else "",
                       "Entry setup": h.get("setup", "RS")}
                      for mo in report["months"] for h in mo["held"]]
                     ).to_excel(xw, sheet_name="Holdings", index=False)
        br = report.get("breadth") or {}
        if br.get("rows"):
            on = br.get("on")
            pd.DataFrame([{"Decision month-end": r["m"], "Market breadth": r["b"], "Change vs month before": r.get("chg"),
                           "Breadth rule": ("Sell everything, buy nothing" if r["off"] else "Normal rules") if on else "Switched off"}
                          for r in br["rows"]]).to_excel(xw, sheet_name="Market breadth", index=False)

        pct_cols = {"Monthly": "DEF", "Trades": "G", "Holdings": "DE", "Market breadth": "BC"}
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
