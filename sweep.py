"""
Start-month sweep: the same backtest run once for every start month up to a common end month, to see how much
the result depends on when you start (no Streamlit imports, so it can be tested on its own).

Each run is an ordinary backtest (bt_engine.simulate) from a clean start with all the money in cash. The output is
one Excel workbook per run (the same file the single backtest gives) plus a summary workbook comparing the runs,
all packed in one zip file.
"""

from __future__ import annotations

import io
import math
import zipfile

import pandas as pd

import bt_engine

SWEEP_MIN_MONTHS = 3       # shortest run in a sweep (same as the single backtest's minimum)
CAGR_MIN_MONTHS = 12       # runs shorter than this get no annual return: annualising a few months misleads
MAX_SWEEP_RUNS = 240       # 20 years of start months


def start_months(first: pd.Period, end: pd.Period) -> list[pd.Period]:
    """Every start month from `first` that leaves at least SWEEP_MIN_MONTHS months before `end`."""
    last = end - (SWEEP_MIN_MONTHS - 1)
    return list(pd.period_range(first, last, freq="M")) if last >= first else []


def drawdown_detail(curve: list[dict]) -> dict:
    """Deepest fall of a run: from which month-end high, to which low, and when (if ever) it was made good;
    plus the longest stretch spent below a previous high."""
    vals = pd.Series([c["s"] for c in curve], index=[c["m"] for c in curve], dtype=float)
    peak = vals.cummax()
    dd = vals / peak - 1
    trough_i = int(dd.values.argmin())
    worst = float(dd.iloc[trough_i])
    peak_i = int(vals.iloc[:trough_i + 1].values.argmax())
    recovered = None
    for j in range(trough_i + 1, len(vals)):
        if vals.iloc[j] >= vals.iloc[peak_i]:
            recovered = vals.index[j]
            break
    longest = run = 0
    for x in dd.values:
        run = run + 1 if x < 0 else 0
        longest = max(longest, run)
    return {"maxDD": worst, "peak": vals.index[peak_i] if worst < 0 else None,
            "trough": vals.index[trough_i] if worst < 0 else None,
            "recovered": recovered if worst < 0 else None,
            "monthsToTrough": trough_i - peak_i if worst < 0 else 0,
            "longestUnderwater": longest}


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def run_sweep(monthly: dict, ind: dict, p: dict, starts: list[pd.Period], end: pd.Period, sectors: dict,
              bench: pd.Series, settings_rows, progress=None) -> dict:
    """settings_rows(start) -> list of (setting, value) for that run's workbook.
    Returns {"rows": one dict per start, "years": {start: {year: return}}, "values": {start: [(month, value)]},
    "files": {name: xlsx bytes}}."""
    rows, years, values, drawdowns, files = [], {}, {}, {}, {}
    for i, s in enumerate(starts, 1):
        if progress:
            progress(i, len(starts), s)
        sim = bt_engine.simulate(monthly, ind, p, s, end, sectors)
        rep = bt_engine.build_report(sim, bench, p, s, end)
        files[f"runs/RS_backtest_start_{s}.xlsx"] = bt_engine.to_excel(rep, settings_rows(s))
        months = (end - s).n + 1
        st, bs, ts = rep["strategy"], rep["benchmark"] or {}, rep["tradeStats"]
        dd = drawdown_detail(rep["curve"])
        annual = months >= CAGR_MIN_MONTHS
        cagr = st.get("cagr") if annual else None
        bcagr = bs.get("cagr") if annual else None
        first = rep["months"][0] if rep["months"] else {}
        rows.append({
            "Start": s.strftime("%b-%y"), "startKey": str(s), "Months": months,
            "CAGR": cagr, "Benchmark CAGR": bcagr,
            "CAGR minus benchmark": (cagr - bcagr) if cagr is not None and bcagr is not None else None,
            "Total return": st.get("total"), "Benchmark total return": bs.get("total"),
            "Max drawdown": dd["maxDD"], "Benchmark max drawdown": bs.get("maxDD"),
            "Drawdown peak": dd["peak"], "Drawdown low": dd["trough"],
            "Recovered by": dd["recovered"] or ("Not yet" if dd["maxDD"] < 0 else None),
            "Months peak to low": dd["monthsToTrough"], "Longest time below a high (months)": dd["longestUnderwater"],
            "Volatility": st.get("vol"), "Sharpe": st.get("sharpe"),
            "Calmar": st.get("calmar") if annual else None,
            "Best month": st.get("best"), "Worst month": st.get("worst"),
            "Trades": ts.get("trades"), "Win rate": ts.get("winRate"), "Profit factor": ts.get("profitFactor"),
            "Avg invested": ts.get("avgInvested"), "Months fully in cash": ts.get("monthsFullyInCash"),
            "Stocks bought at start": len(first.get("entered", [])),
            "Candidates at start": first.get("candidates"),
            "Final value": rep["finalValue"],
        })
        years[str(s)] = {y["year"]: y["s"] for y in rep["yearly"] if y["months"] == 12}
        values[str(s)] = [(c["m"], c["s"]) for c in rep["curve"]]
        drawdowns[str(s)] = [(c["m"], c["dd"]) for c in rep["curve"]]
    return {"rows": rows, "years": years, "values": values, "drawdowns": drawdowns, "files": files}


def overview(rows: list[dict]) -> list[tuple]:
    """How the results spread across start months (annual figures only from runs of 12 months or more)."""
    annual = [r for r in rows if r["CAGR"] is not None]
    out = [("Start months tested", len(rows)), ("Runs of 12 months or more", len(annual))]
    if annual:
        c = pd.Series([r["CAGR"] for r in annual])
        ex = pd.Series([r["CAGR minus benchmark"] for r in annual if r["CAGR minus benchmark"] is not None])
        dd = pd.Series([r["Max drawdown"] for r in annual])
        best = max(annual, key=lambda r: r["CAGR"])
        worst = min(annual, key=lambda r: r["CAGR"])
        out += [
            ("Annual return: median", float(c.median())), ("Annual return: lowest", float(c.min())),
            ("Start with the lowest annual return", worst["Start"]),
            ("Annual return: highest", float(c.max())), ("Start with the highest annual return", best["Start"]),
            ("Annual return: middle half of starts, from", float(c.quantile(0.25))),
            ("Annual return: middle half of starts, to", float(c.quantile(0.75))),
            ("Starts that beat the benchmark", float((ex > 0).mean()) if len(ex) else None),
            ("Deepest fall: median", float(dd.median())), ("Deepest fall: worst", float(dd.min())),
            ("Deepest fall: mildest", float(dd.max())),
        ]
    return out


def summary_workbook(res: dict, rows_settings: list[tuple]) -> bytes:
    from openpyxl.styles import Alignment, Font, PatternFill

    head_fill = PatternFill(start_color="0F1C2E", fill_type="solid")
    rows = res["rows"]
    by_start = pd.DataFrame([{k: v for k, v in r.items() if k != "startKey"} for r in rows])
    yrs = sorted({y for d in res["years"].values() for y in d})
    cal = pd.DataFrame([{"Start": r["Start"], **{str(y): res["years"][r["startKey"]].get(y) for y in yrs}}
                        for r in rows])
    month_order = list(dict.fromkeys(m for v in res["values"].values() for m, _ in v))
    vals = pd.DataFrame({r["Start"]: dict(res["values"][r["startKey"]]) for r in rows}).reindex(month_order)
    vals.index.name = "Month"
    dds = pd.DataFrame({r["Start"]: dict(res["drawdowns"][r["startKey"]]) for r in rows}).reindex(month_order)
    dds.index.name = "Month"

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        pd.DataFrame(overview(rows), columns=["Measure", "Value"]).to_excel(xw, sheet_name="Overview", index=False)
        pd.DataFrame(rows_settings, columns=["Setting", "Value"]).to_excel(
            xw, sheet_name="Overview", index=False, startrow=len(overview(rows)) + 3)
        by_start.to_excel(xw, sheet_name="By start month", index=False)
        cal.to_excel(xw, sheet_name="Calendar years", index=False)
        vals.to_excel(xw, sheet_name="Values")
        dds.to_excel(xw, sheet_name="Drawdowns")

        pct_cols = {"CAGR", "Benchmark CAGR", "CAGR minus benchmark", "Total return", "Benchmark total return",
                    "Max drawdown", "Benchmark max drawdown", "Volatility", "Best month", "Worst month",
                    "Win rate", "Avg invested"}
        for name, ws in xw.sheets.items():
            for cell in ws[1]:
                cell.fill, cell.font = head_fill, Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
            for col in ws.columns:
                header = str(col[0].value or "")
                ws.column_dimensions[col[0].column_letter].width = max(
                    10, min(34, max(len(str(c.value or "")) for c in col[:60]) + 2))
                if name in ("Calendar years", "Drawdowns") or header in pct_cols:
                    for cell in col[1:]:
                        if isinstance(cell.value, float):
                            cell.number_format = "0.0%"
            if name == "Overview":
                for row in ws.iter_rows(min_row=2, max_col=2):
                    measure, val = row
                    if isinstance(val.value, float) and any(w in str(measure.value) for w in
                                                            ("return", "fall", "benchmark")):
                        val.number_format = "0.0%"
            ws.freeze_panes = "B2"
    return out.getvalue()


def to_zip(res: dict, rows_settings: list[tuple]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("RS_start_sweep_summary.xlsx", summary_workbook(res, rows_settings))
        for name, data in res["files"].items():
            z.writestr(name, data)
    return out.getvalue()


def json_rows(rows: list[dict]) -> list[dict]:
    """The summary rows with numbers made JSON-safe for the dashboard."""
    return [{k: (_num(v) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
