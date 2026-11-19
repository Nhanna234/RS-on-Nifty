"""
Runs the backtest with one set of settings on several stock universes and several periods,
and writes everything into one Excel workbook (no Streamlit imports, so it can be tested on its own).

The indicators (RS ratio, EMA, closing range, returns) depend only on each stock's own prices and the
Nifty 50, so they are computed once for the union of all universes and then cut per universe. Entry
ranking happens inside each universe, as in a single backtest.
"""

from __future__ import annotations

import io

import pandas as pd

import bt_engine as E


def _slice(d: dict, cols: list[str]) -> dict:
    return {k: v[cols] if isinstance(v, pd.DataFrame) else v for k, v in d.items()}


def run_all(monthly: dict, ind: dict, universes: dict, p: dict, periods: list[tuple[str, pd.Period, pd.Period]],
            bench: pd.Series, bench_label: str, own_bench: dict, progress=None) -> dict:
    """universes: {index name: {"symbols": [...], "sectors": {sym: sector}, "count": n, "lenders": int}}
    periods  : [(label, start, end)]
    own_bench: {index name: (monthly series or None, label)}"""
    runs, monthly_rows, trade_rows, hold_rows, summary = [], [], [], [], []
    have = set(monthly["Close"].columns)
    total = len(universes) * len(periods)
    k = 0
    for name, u in universes.items():
        cols = [s for s in u["symbols"] if s in have]
        m_i, ind_i = _slice(monthly, cols), _slice(ind, cols)
        ob, ob_label = own_bench.get(name, (None, "unavailable"))
        for plabel, start, end in periods:
            k += 1
            if progress:
                progress(k, total, f"{name}, {plabel}")
            if start - 1 not in m_i["Close"].index:
                continue
            sim = E.simulate(m_i, ind_i, p, start, end, u["sectors"])
            rep = E.build_report(sim, bench, p, start, end)
            eq = pd.Series({e["period"]: e["value"] for e in sim["equity"]})
            own = None
            if ob is not None and len(ob):
                o = ob.reindex(eq.index).ffill()
                if o.notna().all():
                    own = o / o.iloc[0] * eq.iloc[0]
            own_stats = E.series_stats(own, p["cash_yield"]) if own is not None else {}
            ts = rep["tradeStats"]
            runs.append({
                "Index": name, "Period": plabel, "Start": start.strftime("%b %Y"), "End": end.strftime("%b %Y"),
                "Months": (end - start).n + 1, "Stocks in universe": u["count"], "With price data": len(cols),
                "Lenders left out": u.get("lenders", 0), "Nifty 50 benchmark": bench_label, "Own-index benchmark": ob_label,
            })
            s, b = rep["strategy"], rep["benchmark"] or {}
            summary.append({
                "Index": name, "Period": plabel,
                "CAGR": s["cagr"], "Total return": s["total"], "Max drawdown": s["maxDD"], "Volatility": s["vol"],
                "Sharpe": s["sharpe"], "Calmar": s["calmar"], "Best month": s["best"], "Worst month": s["worst"],
                "Nifty 50 CAGR": b.get("cagr"), "Nifty 50 max drawdown": b.get("maxDD"),
                "Own index CAGR": E._f(own_stats.get("cagr")), "Own index max drawdown": E._f(own_stats.get("maxDD")),
                "Months beating Nifty 50": rep["beatShare"],
                "Trades": ts["trades"], "Win rate": ts["winRate"], "Avg win": ts["avgWin"], "Avg loss": ts["avgLoss"],
                "Profit factor": ts["profitFactor"], "Avg months held": ts["avgMonths"],
                "Avg invested": ts["avgInvested"], "Months fully in cash": ts["monthsFullyInCash"],
            })
            for i, c in enumerate(rep["curve"]):
                monthly_rows.append({
                    "Index": name, "Period": plabel, "Month": c["m"], "Strategy value": c["s"],
                    "Nifty 50 value": c["b"], "Own index value": E._f(own.iloc[i], 2) if own is not None else None,
                    "Strategy drawdown": c["dd"], "Nifty 50 drawdown": c["bdd"], "Invested share": c["inv"],
                    "Stocks held": c["n"],
                })
            for t in rep["trades"]:
                trade_rows.append({"Index": name, "Period": plabel, "Symbol": t["symbol"], "Sector": t["sector"],
                                   "Bought": t["entry"], "Buy price": t["entryPx"], "Sold": t["exit"],
                                   "Sell price": t["exitPx"], "Return": t["ret"], "Months held": t["months"],
                                   "Profit": t["pnl"], "Exit reason": t["reason"]})
            for mo in rep["months"]:
                for h in mo["held"]:
                    hold_rows.append({"Index": name, "Period": plabel, "Month": mo["m"], "Symbol": h["symbol"],
                                      "Sector": h["sector"], "Weight": h["w"], "Return in month": h["r"],
                                      "Bought this month": bool(h["new"])})
    return {"runs": runs, "summary": summary, "monthly": monthly_rows, "trades": trade_rows, "holdings": hold_rows}


def to_excel(res: dict, settings_rows: list[tuple]) -> bytes:
    from openpyxl.styles import Alignment, Font, PatternFill

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        pd.DataFrame(res["summary"]).to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame(res["runs"]).to_excel(xw, sheet_name="Runs", index=False)
        pd.DataFrame(settings_rows, columns=["Setting", "Value"]).to_excel(xw, sheet_name="Settings", index=False)
        pd.DataFrame(res["monthly"]).to_excel(xw, sheet_name="Monthly", index=False)
        pd.DataFrame(res["trades"]).to_excel(xw, sheet_name="Trades", index=False)
        pd.DataFrame(res["holdings"]).to_excel(xw, sheet_name="Holdings", index=False)
        head = PatternFill(start_color="0F1C2E", fill_type="solid")
        for ws in xw.sheets.values():
            for cell in ws[1]:
                cell.fill, cell.font = head, Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(
                    11, min(34, max(len(str(c.value or "")) for c in col[:200]) + 2))
            ws.freeze_panes = "C2"
    return out.getvalue()
