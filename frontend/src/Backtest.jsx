import { useEffect, useMemo, useRef, useState } from "react";
import { num, downloadBlob, useWidth } from "./util";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* ---------- formatting ---------- */
const pct = (v, d = 1) => (v == null ? "–" : `${num(v * 100, d)}%`);
const spct = (v, d = 1) => (v == null ? "–" : `${v > 0 ? "+" : ""}${num(v * 100, d)}%`);
const rupees = (v) => (v == null ? "–" : `₹${Math.round(v).toLocaleString("en-IN")}`);
function rupeesShort(v) {
  if (v == null) return "–";
  if (Math.abs(v) >= 1e7) return `₹${num(v / 1e7, 2)} crore`;
  if (Math.abs(v) >= 1e5) return `₹${num(v / 1e5, 1)} lakh`;
  return rupees(v);
}
function axisRupees(v) {
  const short = (x) => (x >= 100 ? Math.round(x).toLocaleString("en-IN") : num(x, x >= 10 ? 0 : 1));
  if (v >= 1e7) return `₹${short(v / 1e7)} Cr`;
  if (v >= 1e5) return `₹${short(v / 1e5)} L`;
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}
const monthKeyLabel = (key) => {
  const [y, m] = String(key).split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
};

/* ---------- page ---------- */
export default function Backtest({ indices, defaults, result, running, busy, onRun }) {
  return (
    <>
      <BtControls indices={indices} defaults={defaults} running={running} busy={busy} onRun={onRun} />
      {running && (
        <div className="banner banner-run" role="status">
          <span className="pulse-dot" aria-hidden="true" />
          Running the backtest. The first run downloads about 15 years of daily prices for every stock and can take
          3 to 6 minutes; progress shows above this page. Later runs reuse the stored prices and take seconds.
        </div>
      )}
      {result && !result.ok && !running && (
        <div className="banner banner-error" role="alert">
          {result.error}
        </div>
      )}
      {result && result.ok ? (
        <BtResults result={result} dimmed={running} />
      ) : (
        !running && !result && <BtIntro defaults={defaults} />
      )}
    </>
  );
}

/* ---------- settings ---------- */
function BtControls({ indices, defaults, running, busy, onRun }) {
  const [s, setS] = useState(defaults);
  const [files, setFiles] = useState([]);
  const fileInput = useRef(null);
  const set = (key) => (e) =>
    setS((prev) => ({ ...prev, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  const pickFiles = async (e) => {
    const picked = await Promise.all([...e.target.files].map(async (f) => ({ name: f.name, text: await f.text() })));
    setFiles(picked);
    e.target.value = "";
  };

  const submit = (e) => {
    e.preventDefault();
    const n = (v, lo, hi, fb) => {
      const x = Number(v);
      return Number.isFinite(x) ? Math.min(hi, Math.max(lo, x)) : fb;
    };
    onRun(
      {
        index: s.index,
        start: s.start,
        end: s.end,
        slots: Math.round(n(s.slots, 1, 50, 10)),
        threshold: n(s.threshold, 50, 100, 98),
        useEma: Boolean(s.useEma),
        cr: n(s.cr, 0, 100, 75),
        mr: n(s.mr, 0, 100, 5),
        ranking: s.ranking,
        cost: n(s.cost, 0, 5, 0.2),
        cashYield: n(s.cashYield, 0, 20, 6),
        capital: n(s.capital, 1000, 1e12, 1e6),
      },
      files
    );
  };

  return (
    <form className="controls bt-controls" onSubmit={submit}>
      <div className="field field-wide">
        <label htmlFor="b-index">Stock universe</label>
        {files.length ? (
          <div className="own-list">
            <span className="own-list-name">{files.map((f) => f.name).join(", ")}</span>
            <button type="button" className="link-btn" onClick={() => setFiles([])}>
              Remove
            </button>
          </div>
        ) : (
          <select id="b-index" value={s.index} onChange={set("index")}>
            {indices.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        )}
        <span className="hint">
          {files.length ? (
            "Your list is used instead of the index."
          ) : (
            <>
              or{" "}
              <button type="button" className="link-btn" onClick={() => fileInput.current.click()}>
                use your own CSV
              </button>
            </>
          )}
        </span>
        <input ref={fileInput} type="file" accept=".csv" multiple hidden onChange={pickFiles} />
      </div>
      <div className="field">
        <label htmlFor="b-start">Start month</label>
        <input id="b-start" type="month" min={defaults.earliest} max={defaults.end} value={s.start} onChange={set("start")} required />
      </div>
      <div className="field">
        <label htmlFor="b-end">End month</label>
        <input id="b-end" type="month" min={defaults.earliest} max={defaults.end} value={s.end} onChange={set("end")} required />
      </div>
      <div className="field">
        <label htmlFor="b-slots">Stocks held</label>
        <input id="b-slots" type="number" min="1" max="50" value={s.slots} onChange={set("slots")} />
      </div>
      <div className="field">
        <label htmlFor="b-thr">Min RS threshold %</label>
        <input id="b-thr" type="number" min="50" max="100" step="0.5" value={s.threshold} onChange={set("threshold")} />
      </div>

      <div className="field">
        <label htmlFor="b-cr">Min closing range %</label>
        <input id="b-cr" type="number" min="0" max="100" value={s.cr} onChange={set("cr")} />
      </div>
      <div className="field">
        <label htmlFor="b-mr">Min monthly return %</label>
        <input id="b-mr" type="number" min="0" max="100" value={s.mr} onChange={set("mr")} />
      </div>
      <div className="field">
        <label htmlFor="b-rank">Rank candidates by</label>
        <select id="b-rank" value={s.ranking} onChange={set("ranking")}>
          {Object.entries(defaults.rankings).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="b-cost">Cost per buy / sell %</label>
        <input id="b-cost" type="number" min="0" max="5" step="0.05" value={s.cost} onChange={set("cost")} />
      </div>
      <div className="field">
        <label htmlFor="b-cash">Cash yield % a year</label>
        <input id="b-cash" type="number" min="0" max="20" step="0.5" value={s.cashYield} onChange={set("cashYield")} />
      </div>
      <div className="field">
        <label htmlFor="b-cap">Starting capital ₹</label>
        <input id="b-cap" type="number" min="1000" step="1000" value={s.capital} onChange={set("capital")} />
      </div>
      <div className="field field-switches">
        <label className="switch">
          <input type="checkbox" checked={Boolean(s.useEma)} onChange={set("useEma")} />
          <span className="switch-track" aria-hidden="true" />
          Price above 12-month EMA (entry and exit)
        </label>
      </div>
      <button className="run-btn" type="submit" disabled={busy}>
        {running ? "Running…" : "Run backtest"}
      </button>
    </form>
  );
}

function BtIntro({ defaults }) {
  return (
    <section className="hero">
      <div className="hero-text">
        <h2 className="hero-title">Hold the stocks at or near their 12-month RS high, month by month</h2>
        <p className="hero-sub">
          At each month-end the backtest buys stocks whose RS is at a 12-month high, or above your minimum threshold and
          higher than two months ago, that also pass the EMA, closing-range and monthly-return filters. They are bought
          at the next month's first open, in equal slots. A stock is sold when its RS drops below the threshold or it
          closes below its 12-month EMA. Empty slots stay in cash.
        </p>
      </div>
      <div className="hero-side">
        <dl className="facts facts-stack">
          <div>
            <dt>Default period</dt>
            <dd>
              {monthKeyLabel(defaults.start)} to {monthKeyLabel(defaults.end)}
            </dd>
          </div>
          <div>
            <dt>Compared with</dt>
            <dd>Nifty 50 Total Return Index</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

/* ---------- results ---------- */
function BtResults({ result, dimmed }) {
  const { report: r, period, universe, settings } = result;
  const curve = r.curve;
  const [selRaw, setSel] = useState(curve.length - 1);
  useEffect(() => setSel(curve.length - 1), [result.nonce, curve.length]);
  const sel = Math.min(Math.max(0, selRaw), curve.length - 1); // a new, shorter result can arrive before the reset

  const s = r.strategy;
  const b = r.benchmark;
  const benchShort = result.benchmarkSource.startsWith("Nifty 50 Total") ? "Nifty 50 TRI" : "Nifty 50";
  const beat = b && s.cagr != null && b.cagr != null ? s.cagr - b.cagr : null;

  const downloadExcel = () => {
    const bytes = Uint8Array.from(atob(result.excel), (c) => c.charCodeAt(0));
    downloadBlob("RS_Backtest.xlsx", new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
  };

  return (
    <div className={dimmed ? "results dimmed" : "results"}>
      <section className="hero bt-hero">
        <div className="hero-text">
          <h2 className="hero-title">
            {pct(s.cagr)} a year{b ? `, against ${pct(b.cagr)} for the ${benchShort}` : ""}
          </h2>
          <p className="hero-sub">
            {rupeesShort(r.capital)} invested at the start of {period.start} grew to {rupeesShort(r.finalValue)} by the end
            of {period.end}
            {b ? `; the same money in the ${benchShort} grew to ${rupeesShort(r.benchFinal)}` : ""}. The deepest fall from a
            peak was {pct(Math.abs(s.maxDD))}
            {b ? `, against ${pct(Math.abs(b.maxDD))} for the index` : ""}.
          </p>
          <dl className="facts">
            <div>
              <dt>Return gap per year</dt>
              <dd>{beat == null ? "–" : `${beat >= 0 ? "+" : ""}${num(beat * 100)} points`}</dd>
            </div>
            <div>
              <dt>Months ahead of the index</dt>
              <dd>{r.beatShare == null ? "–" : pct(r.beatShare, 0)}</dd>
            </div>
            <div>
              <dt>Average money invested</dt>
              <dd>{pct(r.tradeStats.avgInvested, 0)}</dd>
            </div>
            <div>
              <dt>Trades</dt>
              <dd>
                {r.tradeStats.trades}, {pct(r.tradeStats.winRate, 0)} won
              </dd>
            </div>
          </dl>
        </div>
        <div className="hero-side">
          <MetricsTable s={s} b={b} benchShort={benchShort} />
        </div>
      </section>

      <p className="provenance">
        {universe.name} ({universe.source}): {universe.withData} of {universe.count} stocks had price data.{" "}
        {period.start} to {period.end}, {period.months} months. Holding up to {settings.slots} stocks, RS threshold{" "}
        {num(settings.threshold, 1)}%, ranked by {settings.rankingLabel.toLowerCase()}, {num(settings.cost, 2)}% cost per buy and
        per sell. Benchmark: {result.benchmarkSource}. Run at {result.asOf}.
      </p>
      <Caveats result={result} />

      {result.live && (
        <section className="panel live-panel">
          <LiveMonth live={result.live} slots={settings.slots} benchShort={benchShort} rankingLabel={settings.rankingLabel} />
        </section>
      )}

      <section className="panel bt-panel">
        <EquityChart curve={curve} sel={sel} onSel={setSel} benchShort={benchShort} />
      </section>

      <div className="bt-grid">
        <section className="panel">
          <Heatmap grid={r.grid} yearly={r.yearly} curve={curve} sel={sel} onSel={setSel} benchShort={benchShort} />
        </section>
        <section className="panel">
          <MonthHoldings months={r.months} curve={curve} sel={sel} onSel={setSel} slots={settings.slots} />
        </section>
      </div>

      <section className="panel">
        <TradeStats t={r.tradeStats} />
      </section>

      <section className="panel">
        <TradeLog trades={r.trades} onExcel={downloadExcel} />
      </section>
    </div>
  );
}

function MetricsTable({ s, b, benchShort }) {
  const rows = [
    ["Annual return (CAGR)", "cagr", pct],
    ["Total return", "total", (v) => pct(v, 0)],
    ["Deepest fall from a peak", "maxDD", pct],
    ["Volatility, annualised", "vol", pct],
    ["Sharpe ratio", "sharpe", (v) => num(v, 2)],
    ["Return per unit of drawdown", "calmar", (v) => num(v, 2)],
    ["Best month", "best", spct],
    ["Worst month", "worst", spct],
  ];
  return (
    <table className="metrics">
      <thead>
        <tr>
          <th scope="col" />
          <th scope="col">
            <span className="swatch swatch-s" />
            Strategy
          </th>
          {b && (
            <th scope="col">
              <span className="swatch swatch-b" />
              {benchShort}
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, key, f]) => (
          <tr key={key}>
            <th scope="row">{label}</th>
            <td>{f(s[key])}</td>
            {b && <td>{f(b[key])}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Caveats({ result }) {
  const u = result.universe;
  return (
    <details className="caveats">
      <summary>What these numbers do not account for</summary>
      <ul>
        <li>
          <strong>Survivorship bias.</strong> Today's index members are used for every past month. Companies that collapsed
          or left the index since then are missing, so the results are better than a real investor would have got, more so
          for small and micro caps.
        </li>
        <li>
          Prices are Yahoo Finance daily data, adjusted for splits and dividends, with {result.data.spikesRemoved} one-day
          bad prices removed. Other data errors in small and micro caps are not caught.
        </li>
        {u.missingCount > 0 && (
          <li>
            {u.missingCount} stocks had no price data and were left out: {u.missing.join(", ")}
            {u.missingCount > u.missing.length ? " and others" : ""}.
          </li>
        )}
        <li>Taxes, slippage beyond the cost you set, and impact costs in thinly traded stocks are not modelled.</li>
        {result.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </details>
  );
}

/* ---------- equity, invested share and drawdown, on one time axis ---------- */
function EquityChart({ curve, sel, onSel, benchShort }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const [log, setLog] = useState(true);
  const W = Math.max(360, width);
  const pad = { l: 72, r: 104, t: 16 };
  const H1 = 290;
  const STRIP = 16;
  const GAP = 26;
  const H2 = 120;
  const AX = 26;
  const H = pad.t + H1 + GAP + STRIP + GAP + H2 + AX;
  const n = curve.length;
  const hasB = curve.every((c) => c.b != null);

  const x = (i) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
  const vals = curve.flatMap((c) => (hasB ? [c.s, c.b] : [c.s])).filter((v) => v > 0);
  const vmin = Math.min(...vals);
  const vmax = Math.max(...vals);
  const tf = (v) => (log ? Math.log(v) : v);
  const lo = tf(vmin) - (tf(vmax) - tf(vmin)) * 0.04;
  const hi = tf(vmax) + (tf(vmax) - tf(vmin)) * 0.06;
  const y1 = (v) => pad.t + (1 - (tf(v) - lo) / (hi - lo || 1)) * H1;

  const ddMin = Math.min(-0.05, ...curve.map((c) => c.dd ?? 0), ...(hasB ? curve.map((c) => c.bdd ?? 0) : []));
  const top2 = pad.t + H1 + GAP + STRIP + GAP;
  const y2 = (v) => top2 + (v / ddMin) * H2;
  const stripTop = pad.t + H1 + GAP;

  const path = (get, y) => curve.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(get(c)).toFixed(1)}`).join(" ");
  const sPath = path((c) => c.s, y1);
  const bPath = hasB ? path((c) => c.b, y1) : null;
  const ddArea = `${path((c) => c.dd, y2)} L${x(n - 1).toFixed(1)},${top2} L${x(0).toFixed(1)},${top2} Z`;
  const bddPath = hasB ? path((c) => c.bdd, y2) : null;

  // value gridlines: "nice" steps on linear, powers of 2 of the start on log
  const ticks = useMemo(() => {
    if (log) {
      const d0 = Math.floor(Math.log10(vmin));
      const d1 = Math.ceil(Math.log10(vmax));
      const span = d1 - d0;
      const mult = span <= 1 ? [1, 1.5, 2, 3, 5, 7] : span <= 2 ? [1, 2, 5] : span <= 4 ? [1, 3] : [1];
      const out = [];
      for (let d = d0; d <= d1; d++) for (const k of mult) {
        const v = k * 10 ** d;
        if (v >= vmin * 0.97 && v <= vmax * 1.03) out.push(v);
      }
      return out.length >= 2 ? out : [vmin, vmax];
    }
    const step = 10 ** Math.floor(Math.log10((vmax - vmin) / 4 || 1));
    const nice = [1, 2, 2.5, 5, 10].map((k) => k * step).find((st) => (vmax - vmin) / st <= 6) || step * 10;
    const out = [];
    for (let v = Math.ceil(vmin / nice) * nice; v <= vmax; v += nice) out.push(v);
    return out;
  }, [log, curve, vmin, vmax]);
  const ddStep = ddMin > -0.2 ? 0.05 : ddMin > -0.5 ? 0.1 : 0.2;
  const ddTicks = [];
  for (let v = 0; v >= ddMin - 1e-9; v -= ddStep) ddTicks.push(Math.round(v * 100) / 100);

  const years = curve.map((c, i) => ({ i, m: c.m })).filter((c) => c.m.startsWith("Jan"));
  const yearEvery = years.length > 12 ? 2 : 1;

  const onMove = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const rel = (e.clientX - box.left - pad.l) / (W - pad.l - pad.r);
    setHover(Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1)))));
  };
  const focus = hover ?? sel;
  const c = curve[focus];
  const last = curve[n - 1];

  const onKey = (e) => {
    if (e.key === "ArrowLeft") onSel(Math.max(0, sel - 1));
    if (e.key === "ArrowRight") onSel(Math.min(n - 1, sel + 1));
  };

  return (
    <figure className="equity" ref={ref}>
      <div className="panel-head">
        <h3>Growth of the portfolio</h3>
        <div className="legend">
          <span>
            <span className="key-line key-s" /> Strategy
          </span>
          {hasB && (
            <span>
              <span className="key-line key-b" /> {benchShort}
            </span>
          )}
          <label className="check">
            <input type="checkbox" checked={log} onChange={(e) => setLog(e.target.checked)} />
            Log scale
          </label>
        </div>
      </div>
      <p className="chart-hint">Hover to read a month; click to see what was held. Arrow keys step through months.</p>
      <div className="equity-plot">
        {width > 0 && (
          <svg
            width={W}
            height={H}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            onClick={() => hover != null && onSel(hover)}
            tabIndex={0}
            onKeyDown={onKey}
            role="img"
            aria-label="Portfolio value against the benchmark, money invested, and drawdown, by month"
          >
            {ticks.map((t) => (
              <g key={`t${t}`}>
                <line className="grid" x1={pad.l} x2={W - pad.r} y1={y1(t)} y2={y1(t)} />
                <text className="axis" x={pad.l - 8} y={y1(t) + 4} textAnchor="end">
                  {axisRupees(t)}
                </text>
              </g>
            ))}
            {hasB && <path className="eq-b" d={bPath} />}
            <path className="eq-s" d={sPath} />
            <text className="end-label end-s" x={x(n - 1) + 8} y={y1(last.s) + 4}>
              Strategy
            </text>
            {hasB && (
              <text className="end-label end-b" x={x(n - 1) + 8} y={y1(last.b) + 4 + (Math.abs(y1(last.b) - y1(last.s)) < 14 ? (last.b < last.s ? 12 : -12) : 0)}>
                {benchShort}
              </text>
            )}

            <text className="axis axis-left" x={pad.l - 8} y={stripTop + STRIP - 4} textAnchor="end">
              Invested
            </text>
            {curve.map((cc, i) =>
              i === 0 ? null : (
                <rect
                  key={`v${i}`}
                  x={x(i - 1)}
                  y={stripTop}
                  width={Math.max(0.5, x(i) - x(i - 1))}
                  height={STRIP}
                  className="inv-cell"
                  style={{ opacity: 0.12 + 0.88 * (cc.inv ?? 0) }}
                />
              )
            )}

            {ddTicks.map((t) => (
              <g key={`d${t}`}>
                <line className="grid" x1={pad.l} x2={W - pad.r} y1={y2(t)} y2={y2(t)} />
                <text className="axis" x={pad.l - 8} y={y2(t) + 4} textAnchor="end">
                  {t === 0 ? "0%" : `${Math.round(t * 100)}%`}
                </text>
              </g>
            ))}
            <text className="axis axis-title" x={pad.l} y={top2 - 8}>
              Fall from previous peak
            </text>
            <path className="dd-s" d={ddArea} />
            {hasB && <path className="dd-b" d={bddPath} />}

            {years.map((yy, k) =>
              k % yearEvery === 0 ? (
                <text key={yy.m} className="axis" x={x(yy.i)} y={H - 6} textAnchor="middle">
                  {"20" + yy.m.slice(-2)}
                </text>
              ) : null
            )}

            <line className="sel-line" x1={x(sel)} x2={x(sel)} y1={pad.t} y2={top2 + H2} />
            {hover != null && <line className="cross" x1={x(hover)} x2={x(hover)} y1={pad.t} y2={top2 + H2} />}
            <circle className="dot-s" cx={x(focus)} cy={y1(c.s)} r="4" />
            {hasB && <circle className="dot-b" cx={x(focus)} cy={y1(c.b)} r="4" />}
          </svg>
        )}
        {width > 0 && hover != null && (
          <div className="tip eq-tip" style={{ left: Math.min(Math.max(x(focus), 120), W - 120) }}>
            <strong>{c.m}</strong>
            <span>
              Strategy {rupeesShort(c.s)}
              {c.dd < 0 ? `, ${pct(c.dd)} from peak` : ", at a peak"}
            </span>
            {hasB && (
              <span>
                {benchShort} {rupeesShort(c.b)}
                {c.bdd < 0 ? `, ${pct(c.bdd)} from peak` : ", at a peak"}
              </span>
            )}
            <span>
              {c.n} stocks, {pct(c.inv, 0)} invested
            </span>
          </div>
        )}
      </div>
    </figure>
  );
}

/* ---------- monthly returns ---------- */
function heat(v) {
  if (v == null) return {};
  const k = Math.min(1, Math.abs(v) / 0.1);
  const bg = v >= 0 ? `rgba(0, 147, 138, ${0.08 + 0.82 * k})` : `rgba(184, 50, 90, ${0.08 + 0.82 * k})`;
  return { background: bg, color: k > 0.55 ? "#fff" : "var(--ink)" };
}

function Heatmap({ grid, yearly, curve, sel, onSel, benchShort }) {
  const index = useMemo(() => {
    const m = {};
    curve.forEach((c, i) => (m[c.m] = i));
    return m;
  }, [curve]);
  const selLabel = curve[sel]?.m;
  const yb = Object.fromEntries(yearly.map((y) => [y.year, y]));
  return (
    <div>
      <div className="panel-head">
        <h3>Monthly returns</h3>
      </div>
      <p className="chart-hint">Click a month to see its holdings. Years marked * are part-years.</p>
      <div className="heat-scroll">
        <table className="heat">
          <thead>
            <tr>
              <th scope="col">Year</th>
              {MONTHS.map((m) => (
                <th scope="col" key={m}>
                  {m.slice(0, 1)}
                  <span className="sr-only">{m.slice(1)}</span>
                </th>
              ))}
              <th scope="col" className="heat-total">
                Year
              </th>
              <th scope="col" className="heat-total">
                {benchShort}
              </th>
            </tr>
          </thead>
          <tbody>
            {grid.map((row) => {
              const y = yb[row.year];
              return (
                <tr key={row.year}>
                  <th scope="row">
                    {row.year}
                    {y && y.months < 12 ? "*" : ""}
                  </th>
                  {row.months.map((v, mi) => {
                    const lab = `${MONTHS[mi]}-${String(row.year).slice(2)}`;
                    const i = index[lab];
                    return (
                      <td key={mi}>
                        {v == null ? null : (
                          <button
                            type="button"
                            className={`heat-cell${lab === selLabel ? " is-sel" : ""}`}
                            style={heat(v)}
                            onClick={() => i != null && onSel(i)}
                            title={`${MONTHS[mi]} ${row.year}: ${spct(v)}`}
                          >
                            {num(v * 100, 1)}
                          </button>
                        )}
                      </td>
                    );
                  })}
                  <td className="heat-total">
                    <span className="heat-year" style={heat(y?.s != null ? y.s / 3 : null)}>
                      {spct(y?.s)}
                    </span>
                  </td>
                  <td className="heat-total muted">{spct(y?.b)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------- what was held in a month ---------- */
function MonthHoldings({ months, curve, sel, onSel, slots }) {
  // curve[0] is the starting point (all cash); months[i - 1] belongs to curve[i]
  const i = Math.max(1, sel);
  const mo = months[i - 1];
  if (!mo) return null;
  const maxW = Math.max(0.0001, ...mo.held.map((h) => h.w));
  return (
    <div className="holdings">
      <div className="panel-head">
        <h3>Holdings in {mo.m}</h3>
        <div className="stepper">
          <button type="button" className="ghost-btn" onClick={() => onSel(Math.max(1, i - 1))} disabled={i <= 1} aria-label="Previous month">
            ‹
          </button>
          <button type="button" className="ghost-btn" onClick={() => onSel(Math.min(curve.length - 1, i + 1))} disabled={i >= curve.length - 1} aria-label="Next month">
            ›
          </button>
        </div>
      </div>
      <p className="chart-hint">
        Chosen at the {curve[i - 1].m} close and traded at the first open of {mo.m}. {mo.held.length} of {slots} slots filled,{" "}
        {pct(mo.cash, 0)} in cash. {mo.candidates} stocks qualified{mo.entered.length ? `; ${mo.entered.length} bought` : "; none bought"}.
      </p>
      {mo.held.length ? (
        <table className="hold-table">
          <thead>
            <tr>
              <th scope="col">Stock</th>
              <th scope="col">Weight at month-end</th>
              <th scope="col" className="num">
                Return in month
              </th>
            </tr>
          </thead>
          <tbody>
            {mo.held.map((h) => (
              <tr key={h.symbol}>
                <th scope="row">
                  <span className="stock-symbol">{h.symbol}</span>
                  {h.new && <span className="tag-new">Bought</span>}
                  <span className="hold-sector">{h.sector}</span>
                </th>
                <td>
                  <span className="wbar">
                    <span style={{ width: `${(h.w / maxW) * 100}%` }} />
                  </span>
                  {pct(h.w, 1)}
                </td>
                <td className={`num${h.r < 0 ? " neg" : ""}`}>{spct(h.r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="quiet-note">All in cash this month: no stock passed the entry rules.</p>
      )}
      {mo.exited.length > 0 && (
        <div className="sold">
          <h4>Sold at the start of {mo.m}</h4>
          <ul>
            {mo.exited.map((x) => (
              <li key={x.symbol}>
                <span className="stock-symbol">{x.symbol}</span>: {x.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {mo.skipped.length > 0 && (
        <p className="chart-hint">Not bought for lack of cash: {mo.skipped.join(", ")}.</p>
      )}
    </div>
  );
}

/* ---------- the running month ---------- */
function LiveMonth({ live, slots, benchShort, rankingLabel }) {
  const n = live.next;
  const ahead = live.benchMtd != null && live.mtd != null ? live.mtd - live.benchMtd : null;
  return (
    <div className="live">
      <div className="live-head">
        <div>
          <h3>{live.month} so far</h3>
          <p className="chart-hint">
            These {live.held.length} stocks were chosen at the {live.decidedAt} close and bought at the first open of {live.month}.
            The list is final; the returns run to the latest close{live.dataTo ? ` (${live.dataTo})` : ""} and will change until the month ends.
          </p>
        </div>
        <dl className="live-figs">
          <div>
            <dt>Portfolio this month</dt>
            <dd className={live.mtd < 0 ? "neg" : ""}>{spct(live.mtd)}</dd>
          </div>
          {live.benchMtd != null && (
            <div>
              <dt>{benchShort} this month</dt>
              <dd className={live.benchMtd < 0 ? "neg" : ""}>{spct(live.benchMtd)}</dd>
            </div>
          )}
          {ahead != null && (
            <div>
              <dt>Difference</dt>
              <dd>{`${ahead >= 0 ? "+" : ""}${num(ahead * 100)} points`}</dd>
            </div>
          )}
        </dl>
      </div>

      <div className="live-grid">
        <div>
          <h4>
            Held in {live.month}: {live.held.length} of {slots} slots, {pct(live.cash, 0)} in cash
          </h4>
          {live.held.length ? (
            <table className="hold-table">
              <thead>
                <tr>
                  <th scope="col">Stock</th>
                  <th scope="col">Bought</th>
                  <th scope="col" className="num">This month</th>
                  <th scope="col" className="num">Since bought</th>
                  <th scope="col" className="num">Weight</th>
                </tr>
              </thead>
              <tbody>
                {live.held.map((h) => (
                  <tr key={h.symbol}>
                    <th scope="row">
                      <span className="stock-symbol">{h.symbol}</span>
                      {h.new && <span className="tag-new">New</span>}
                      <span className="hold-sector">{h.sector}</span>
                    </th>
                    <td>
                      {h.bought} <span className="muted">at {num(h.entryPx, 2)}</span>
                    </td>
                    <td className={`num${h.mtd < 0 ? " neg" : ""}`}>{spct(h.mtd)}</td>
                    <td className={`num${h.since < 0 ? " neg" : ""}`}>{spct(h.since)}</td>
                    <td className="num">{pct(h.w, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="quiet-note">All in cash: no stock passed the entry rules at the {live.decidedAt} close.</p>
          )}
          {live.sold.length > 0 && (
            <p className="chart-hint">
              Sold at the first open of {live.month}: {live.sold.map((x) => `${x.symbol} (${x.reason})`).join(", ")}.
            </p>
          )}
          {live.skipped.length > 0 && <p className="chart-hint">Not bought for lack of cash: {live.skipped.join(", ")}.</p>}
        </div>

        <div className="provisional">
          <h4>
            If {live.month} closed at the latest prices <span className="live-tag inline">Provisional</span>
          </h4>
          <p className="chart-hint">
            What the rules would do at the next month's first open. This changes every day until the month-end close; act
            only on the list after the month has closed.
          </p>
          <h5>Would sell</h5>
          {n.exits.length ? (
            <ul className="plain">
              {n.exits.map((x) => (
                <li key={x.symbol}>
                  <span className="stock-symbol">{x.symbol}</span>: {x.reason}
                  {x.ratio != null ? `, RS at ${pct(x.ratio)} of its high` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p className="quiet-note small">Nothing; every holding still passes.</p>
          )}
          <h5>
            Would buy ({n.buys.length} of {n.free} free {n.free === 1 ? "slot" : "slots"}; {n.qualified} stocks qualify)
          </h5>
          {n.buys.length ? (
            <CandTable rows={n.buys} rankingLabel={rankingLabel} />
          ) : (
            <p className="quiet-note small">{n.free ? "No stock qualifies at the latest prices." : "No free slots."}</p>
          )}
          {n.reserve.length > 0 && (
            <>
              <h5>Next in line</h5>
              <CandTable rows={n.reserve} rankingLabel={rankingLabel} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CandTable({ rows, rankingLabel }) {
  const cr = rankingLabel === "Closing range";
  return (
    <table className="hold-table cand-table">
      <thead>
        <tr>
          <th scope="col">Stock</th>
          <th scope="col" className="num">RS of high</th>
          <th scope="col" className="num">Closing range</th>
          <th scope="col" className="num">Month so far</th>
          {!cr && <th scope="col" className="num">{rankingLabel}</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((x) => (
          <tr key={x.symbol}>
            <th scope="row">
              <span className="stock-symbol">{x.symbol}</span>
              <span className="hold-sector">{x.sector}</span>
            </th>
            <td className="num">{pct(x.ratio)}</td>
            <td className="num">{pct(x.cr, 0)}</td>
            <td className="num">{spct(x.ret)}</td>
            {!cr && <td className="num">{spct(x.rank)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ---------- trade statistics ---------- */
function TradeStats({ t }) {
  const reasons = Object.entries(t.exitReasons || {}).sort((a, b) => b[1] - a[1]);
  const total = reasons.reduce((a, [, v]) => a + v, 0) || 1;
  const items = [
    ["Trades", num(t.trades, 0)],
    ["Won", pct(t.winRate, 0)],
    ["Average winner", spct(t.avgWin)],
    ["Average loser", spct(t.avgLoss)],
    ["Median trade", spct(t.medianRet)],
    ["Profit factor", num(t.profitFactor, 2)],
    ["Average months held", num(t.avgMonths, 1)],
    ["New buys per year", num(t.entriesPerYear, 1)],
    ["Months fully in cash", num(t.monthsFullyInCash, 0)],
  ];
  return (
    <div className="tstats">
      <div className="panel-head">
        <h3>How the trades went</h3>
      </div>
      <dl className="tstats-grid">
        {items.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {reasons.length > 0 && (
        <div className="reasons">
          <h4>Why stocks were sold ({total} sales)</h4>
          {reasons.map(([k, v]) => (
            <div className="reason-row" key={k}>
              <span className="reason-name">{k}</span>
              <span className="reason-bar">
                <span style={{ width: `${(v / total) * 100}%` }} />
              </span>
              <span className="reason-val">
                {v} ({pct(v / total, 0)})
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- trade log ---------- */
function TradeLog({ trades, onExcel }) {
  const [q, setQ] = useState("");
  const [show, setShow] = useState("all");
  const [sort, setSort] = useState({ key: "entryIdx", dir: 1 });

  const rows = useMemo(() => {
    const parse = (lab) => {
      const [m, y] = lab.split("-");
      return Number(y) * 12 + MONTHS.indexOf(m);
    };
    const query = q.trim().toUpperCase();
    let out = trades
      .map((t) => ({ ...t, entryIdx: parse(t.entry), exitIdx: parse(t.exit) }))
      .filter((t) => (!query || t.symbol.includes(query)) &&
        (show === "all" || (show === "win" && !t.open && t.ret > 0) || (show === "loss" && !t.open && t.ret <= 0) || (show === "open" && t.open)));
    out.sort((a, b) => {
      const va = a[sort.key];
      const vb = b[sort.key];
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * sort.dir;
    });
    return out;
  }, [trades, q, show, sort]);

  const head = (key, label, cls = "") => (
    <th scope="col" className={cls} aria-sort={sort.key === key ? (sort.dir > 0 ? "ascending" : "descending") : "none"}>
      <button type="button" className="sort-btn" onClick={() => setSort((s) => ({ key, dir: s.key === key ? -s.dir : key === "ret" || key === "pnl" ? -1 : 1 }))}>
        {label}
        {sort.key === key ? (sort.dir > 0 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );

  const csv = () => {
    const lines = [["Symbol", "Sector", "Bought", "Buy price", "Sold", "Sell price", "Months", "Return", "Profit", "Exit reason"].join(",")];
    rows.forEach((t) =>
      lines.push([t.symbol, `"${t.sector}"`, t.entry, t.entryPx, t.open ? "" : t.exit, t.open ? "" : t.exitPx, t.months, t.ret, t.pnl, t.reason].join(","))
    );
    downloadBlob("RS_Backtest_trades.csv", new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
  };

  return (
    <div>
      <div className="panel-head">
        <h3>Trade log</h3>
        <div className="toolbar-actions">
          <button type="button" className="ghost-btn" onClick={csv}>
            Download trades (CSV)
          </button>
          <button type="button" className="ghost-btn" onClick={onExcel}>
            Download full report (Excel)
          </button>
        </div>
      </div>
      <div className="toolbar toolbar-flat">
        <input className="search" type="search" placeholder="Find a symbol" aria-label="Find a symbol" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg" role="group" aria-label="Show trades">
          {[["all", "All"], ["win", "Winners"], ["loss", "Losers"], ["open", "Still held"]].map(([k, l]) => (
            <button type="button" key={k} className={show === k ? "on" : ""} onClick={() => setShow(k)}>
              {l}
            </button>
          ))}
        </div>
        <span className="muted">{rows.length} trades</span>
      </div>
      <div className="log-scroll">
        <table className="log">
          <thead>
            <tr>
              {head("symbol", "Stock")}
              {head("sector", "Sector")}
              {head("entryIdx", "Bought")}
              {head("exitIdx", "Sold")}
              {head("months", "Months", "num")}
              {head("ret", "Return", "num")}
              {head("pnl", "Profit ₹", "num")}
              {head("reason", "Why sold")}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={`${t.symbol}-${t.entry}`}>
                <th scope="row" className="stock-symbol">
                  {t.symbol}
                </th>
                <td className="log-sector">{t.sector}</td>
                <td>
                  {t.entry} <span className="muted">at {num(t.entryPx, 2)}</span>
                </td>
                <td>{t.open ? <span className="tag-open">Still held</span> : <>{t.exit} <span className="muted">at {num(t.exitPx, 2)}</span></>}</td>
                <td className="num">{t.months}</td>
                <td className={`num${t.ret < 0 ? " neg" : ""}`}>
                  {spct(t.ret)}
                </td>
                <td className={`num${t.pnl < 0 ? " neg" : ""}`}>{t.pnl == null ? "–" : Math.round(t.pnl).toLocaleString("en-IN")}</td>
                <td>{t.open ? "–" : t.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
