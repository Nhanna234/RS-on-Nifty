import { useEffect, useMemo, useRef, useState } from "react";
import { connect, embedded, sendValue, setFrameHeight } from "./streamlit";
import { SAMPLE_ARGS, sampleResult } from "./sample";

/* ---------- small helpers ---------- */
const num = (v, d = 1) =>
  v == null ? "–" : v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const signedPct = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${num(v)}%`);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function downloadBlob(name, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/* ---------- app shell ---------- */
export default function App() {
  const [args, setArgs] = useState(embedded ? null : SAMPLE_ARGS);
  const [running, setRunning] = useState(null);
  const rootRef = useRef(null);

  useEffect(() => (embedded ? connect(setArgs) : undefined), []);

  // keep the Streamlit iframe exactly as tall as the page
  useEffect(() => {
    if (!embedded || !rootRef.current) return undefined;
    const report = () => setFrameHeight(Math.ceil(rootRef.current.getBoundingClientRect().height) + 4);
    const ro = new ResizeObserver(report);
    ro.observe(rootRef.current);
    report();
    return () => ro.disconnect();
  }, []);

  const result = args?.result || null;
  useEffect(() => {
    if (running && result && result.nonce === running) setRunning(null);
  }, [running, result]);

  const run = (settings, csvFiles) => {
    const nonce = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    setRunning(nonce);
    if (embedded) sendValue({ nonce, ...settings, csvFiles });
    else setTimeout(() => setArgs((a) => ({ ...a, result: sampleResult(nonce, settings) })), 900);
  };

  return (
    <div className="app" ref={rootRef}>
      <header className="masthead">
        <h1 className="wordmark">Abdullah RS Leaderboard</h1>
        <p className="masthead-note">Relative strength against the Nifty 50, from month-end closes. Prices from Yahoo Finance.</p>
      </header>

      {!args ? (
        <p className="quiet-note">Loading the screen…</p>
      ) : (
        <>
          <Controls indices={args.indices} defaults={args.defaults} running={Boolean(running)} onRun={run} />
          {running && (
            <div className="banner banner-run" role="status">
              <span className="pulse-dot" aria-hidden="true" />
              Downloading prices and running the screen. A large universe takes about a minute.
            </div>
          )}
          {result && !result.ok && !running && (
            <div className="banner banner-error" role="alert">
              {result.error}
            </div>
          )}
          {result && result.ok ? <Results result={result} dimmed={Boolean(running)} /> : !running && !result && <Welcome />}
        </>
      )}
    </div>
  );
}

/* ---------- screen settings ---------- */
function Controls({ indices, defaults, running, onRun }) {
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
    const clamp = (v, lo, hi, fallback) => {
      const n = Number(v);
      return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
    };
    onRun(
      {
        index: s.index,
        history: Math.round(clamp(s.history, 1, 24, 6)),
        threshold: clamp(s.threshold, 50, 100, 100),
        useEma: Boolean(s.useEma),
        cr: clamp(s.cr, 0, 100, 75),
        mr: clamp(s.mr, 0, 100, 5),
        weekly: Boolean(s.weekly),
      },
      files
    );
  };

  return (
    <form className="controls" onSubmit={submit}>
      <div className="field field-wide">
        <label htmlFor="f-index">Stock universe</label>
        {files.length ? (
          <div className="own-list">
            <span className="own-list-name" title={files.map((f) => f.name).join(", ")}>
              {files.map((f) => f.name).join(", ")}
            </span>
            <button type="button" className="link-btn" onClick={() => setFiles([])}>
              Remove
            </button>
          </div>
        ) : (
          <select id="f-index" value={s.index} onChange={set("index")}>
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
        <label htmlFor="f-history">Months shown</label>
        <input id="f-history" type="number" min="1" max="24" value={s.history} onChange={set("history")} />
      </div>
      <div className="field">
        <label htmlFor="f-threshold">RS threshold %</label>
        <input id="f-threshold" type="number" min="50" max="100" value={s.threshold} onChange={set("threshold")} />
      </div>
      <div className="field">
        <label htmlFor="f-cr">Min closing range %</label>
        <input id="f-cr" type="number" min="0" max="100" value={s.cr} onChange={set("cr")} />
      </div>
      <div className="field">
        <label htmlFor="f-mr">Min monthly return %</label>
        <input id="f-mr" type="number" min="0" max="100" value={s.mr} onChange={set("mr")} />
      </div>

      <div className="field field-switches">
        <label className="switch">
          <input type="checkbox" checked={Boolean(s.useEma)} onChange={set("useEma")} />
          <span className="switch-track" aria-hidden="true" />
          Price above 12-month EMA
        </label>
        <label className="switch">
          <input type="checkbox" checked={Boolean(s.weekly)} onChange={set("weekly")} />
          <span className="switch-track" aria-hidden="true" />
          Strong latest week only
        </label>
      </div>

      <button className="run-btn" type="submit" disabled={running}>
        {running ? "Running…" : "Run screen"}
      </button>
    </form>
  );
}

/* ---------- before the first run ---------- */
function Welcome() {
  return (
    <section className="hero">
      <div className="hero-text">
        <h2 className="hero-title">Which stocks are at a 12-month high against the Nifty 50?</h2>
        <p className="hero-sub">
          Relative strength is a stock's price divided by the Nifty 50. Each month, this screen compares it with its
          highest month-end value of the past 12 months. Pick a universe above and run the screen.
        </p>
      </div>
      <div className="hero-side">
        <CellKey onDark />
      </div>
    </section>
  );
}

function CellKey({ onDark, threshold = 100 }) {
  return (
    <ul className={`cell-key${onDark ? " on-dark" : ""}`}>
      <li>
        <span className="cell hi">100</span> at a new 12-month RS high
      </li>
      {threshold < 100 && (
        <li>
          <span className="cell near">{Math.round(threshold)}</span> within the threshold of the high
        </li>
      )}
      <li>
        <span className="cell miss">88</span> below it, shown as % of the high
      </li>
    </ul>
  );
}

/* ---------- results ---------- */
function Results({ result, dimmed }) {
  const { stocks, months, universe, settings, sectorTotals } = result;
  const last = months.length - 1;
  const [sector, setSector] = useState(null);
  const [query, setQuery] = useState("");
  const [onlyNow, setOnlyNow] = useState(false);
  const [sort, setSort] = useState("sector");
  const [openSymbol, setOpenSymbol] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setSector(null);
    setOpenSymbol(null);
  }, [result.nonce]);

  const leadersNow = useMemo(() => stocks.filter((s) => s.cells[last]?.q), [stocks, last]);
  const pulse = useMemo(() => months.map((_, i) => stocks.filter((s) => s.cells[i]?.q).length), [stocks, months]);

  const sectors = useMemo(() => {
    const counts = {};
    stocks.forEach((s) => (counts[s.sector] = (counts[s.sector] || 0) + 1));
    return Object.entries(counts)
      .map(([name, count]) => ({ name, count, total: sectorTotals[name] || count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [stocks, sectorTotals]);

  const visible = useMemo(() => {
    const q = query.trim().toUpperCase();
    const rows = stocks.filter(
      (s) => (!sector || s.sector === sector) && (!q || s.symbol.includes(q)) && (!onlyNow || s.cells[last]?.q)
    );
    const bySymbol = (a, b) => a.symbol.localeCompare(b.symbol);
    if (sort === "streak") rows.sort((a, b) => b.streak - a.streak || b.hits - a.hits || bySymbol(a, b));
    else if (sort === "return") rows.sort((a, b) => (b.monthReturn ?? -1e9) - (a.monthReturn ?? -1e9));
    else if (sort === "symbol") rows.sort(bySymbol);
    else rows.sort((a, b) => a.sector.localeCompare(b.sector) || bySymbol(a, b));
    return rows;
  }, [stocks, sector, query, onlyNow, sort, last]);

  if (!stocks.length) {
    return (
      <section className="hero">
        <div className="hero-text">
          <h2 className="hero-title">No stock cleared the screen</h2>
          <p className="hero-sub">
            None of the {universe.count} stocks in {universe.name} passed with these settings. Lower the RS threshold
            or relax the filters, then run it again.
          </p>
        </div>
      </section>
    );
  }

  const thresholdText =
    settings.threshold >= 100
      ? "at a new 12-month relative-strength high"
      : `within ${num(100 - settings.threshold, 0)}% of a 12-month relative-strength high`;
  const longest = [...stocks].sort((a, b) => b.streak - a.streak || b.hits - a.hits)[0];
  const busiest = sectors[0];
  const densest = [...sectors].filter((x) => x.total >= 5).sort((a, b) => b.count / b.total - a.count / a.total)[0];
  const showWeekly = stocks.some((s) => s.weeklyCR);

  const copySymbols = async () => {
    const ok = await copyText(visible.map((s) => `NSE:${s.symbol}`).join(", "));
    setCopied(ok ? "done" : "failed");
    setTimeout(() => setCopied(false), 2200);
  };
  const downloadIsins = () =>
    downloadBlob(
      "Shortlisted_ISINs.csv",
      new Blob([visible.map((s) => s.isin).filter(Boolean).join("\n") + "\n"], { type: "text/csv" })
    );
  const downloadExcel = () => {
    const bytes = Uint8Array.from(atob(result.excel), (c) => c.charCodeAt(0));
    downloadBlob(
      "Alpha_RS_Report.xlsx",
      new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" })
    );
  };

  return (
    <div className={dimmed ? "results dimmed" : "results"}>
      <section className="hero">
        <div className="hero-text">
          <h2 className="hero-title">
            {stocks.length} of {universe.count} stocks cleared the screen
          </h2>
          <p className="hero-sub">
            {leadersNow.length} of them are {thresholdText} in {months[last]}
            {result.monthInProgress ? ", a month that is still in progress" : ""}. The rest were there earlier in the
            last {plural(months.length, "month", "months")}.
          </p>
          <dl className="facts">
            {longest && longest.streak > 0 && (
              <div>
                <dt>Longest current run</dt>
                <dd>
                  {longest.symbol}, {plural(longest.streak, "month", "months")}
                </dd>
              </div>
            )}
            {busiest && (
              <div>
                <dt>Most names</dt>
                <dd>
                  {busiest.name}, {busiest.count}
                </dd>
              </div>
            )}
            {densest && (
              <div>
                <dt>Highest share of its sector</dt>
                <dd>
                  {densest.name}, {Math.round((densest.count / densest.total) * 100)}% of {densest.total}
                </dd>
              </div>
            )}
          </dl>
        </div>
        <div className="hero-side">
          <Pulse months={months} counts={pulse} total={stocks.length} inProgress={result.monthInProgress} />
        </div>
      </section>

      <p className="provenance">
        {universe.name} ({universe.source}), {universe.count} stocks. Run at {result.asOf}.
      </p>

      <div className="board">
        <SectorPanel sectors={sectors} active={sector} onPick={setSector} total={stocks.length} />

        <section className="matrix-panel">
          <div className="toolbar">
            <input
              className="search"
              type="search"
              placeholder="Find a symbol"
              aria-label="Find a symbol"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <label className="check">
              <input type="checkbox" checked={onlyNow} onChange={(e) => setOnlyNow(e.target.checked)} />
              Marked in {months[last]} only
            </label>
            <label className="sorter">
              Sort by
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="sector">Sector</option>
                <option value="streak">Longest run</option>
                <option value="return">Month return</option>
                <option value="symbol">Symbol</option>
              </select>
            </label>
            <div className="toolbar-actions">
              <button type="button" className="ghost-btn" onClick={copySymbols} disabled={!visible.length}>
                {copied === "done"
                  ? `Copied ${visible.length} symbols`
                  : copied === "failed"
                  ? "Copy was blocked by the browser"
                  : `Copy ${visible.length} symbols for TradingView`}
              </button>
              <button type="button" className="ghost-btn" onClick={downloadIsins} disabled={!visible.length}>
                Download ISINs
              </button>
              {result.excel && (
                <button type="button" className="ghost-btn" onClick={downloadExcel}>
                  Download Excel report
                </button>
              )}
            </div>
          </div>

          <div className="matrix-meta">
            <span>
              Showing {visible.length} of {stocks.length}
              {sector ? ` in ${sector}` : ""}
            </span>
            <CellKey threshold={settings.threshold} />
          </div>

          <Matrix
            rows={visible}
            months={months}
            grouped={sort === "sector"}
            inProgress={result.monthInProgress}
            showWeekly={showWeekly}
            openSymbol={openSymbol}
            onToggle={(sym) => setOpenSymbol((cur) => (cur === sym ? null : sym))}
          />
          {!visible.length && <p className="quiet-note">No stock matches these filters.</p>}
        </section>
      </div>
    </div>
  );
}

/* ---------- monthly pulse: how many shortlisted stocks were marked in each month ---------- */
function Pulse({ months, counts, total, inProgress }) {
  const [hover, setHover] = useState(null);
  const active = hover ?? months.length - 1;
  const max = Math.max(1, ...counts);
  const H = 132;
  const showValues = months.length <= 9;
  return (
    <figure className="pulse">
      <figcaption>Stocks on this list marked in each month</figcaption>
      <div className="pulse-bars" style={{ height: H + 22 }} onMouseLeave={() => setHover(null)}>
        {months.map((m, i) => {
          const live = inProgress && i === months.length - 1;
          return (
            <button
              type="button"
              key={m}
              className={`pulse-col${i === active ? " is-active" : ""}`}
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              aria-label={`${m}: ${counts[i]} of ${total} stocks`}
            >
              {showValues && <span className="pulse-val">{counts[i]}</span>}
              <span
                className={`pulse-bar${live ? " is-live" : ""}`}
                style={{ height: Math.max(3, Math.round((counts[i] / max) * H)) }}
              />
              <span className="pulse-month">{months.length > 12 ? m.slice(0, 1) : m.slice(0, 3)}</span>
            </button>
          );
        })}
      </div>
      <p className="pulse-read">
        {months[active]}: {counts[active]} of {total}
        {inProgress && active === months.length - 1 ? " so far, the month is not over" : ""}
      </p>
    </figure>
  );
}

/* ---------- sector leadership ---------- */
function SectorPanel({ sectors, active, onPick, total }) {
  const [rank, setRank] = useState("count");
  const list = useMemo(() => {
    const share = (x) => x.count / x.total;
    return rank === "share" ? [...sectors].sort((a, b) => share(b) - share(a) || b.count - a.count) : sectors;
  }, [sectors, rank]);
  const max = Math.max(...list.map((x) => (rank === "share" ? x.count / x.total : x.count)));
  return (
    <aside className="sector-panel">
      <div className="panel-head">
        <h3>Sectors</h3>
        <div className="seg" role="group" aria-label="Rank sectors by">
          <button type="button" className={rank === "count" ? "on" : ""} onClick={() => setRank("count")}>
            Names
          </button>
          <button type="button" className={rank === "share" ? "on" : ""} onClick={() => setRank("share")}>
            Share of sector
          </button>
        </div>
      </div>
      <ul className="sector-list">
        <li>
          <button type="button" className={`sector-row${active === null ? " on" : ""}`} onClick={() => onPick(null)}>
            <span className="sector-name">All sectors</span>
            <span className="sector-val">{total}</span>
          </button>
        </li>
        {list.map((x) => {
          const value = rank === "share" ? x.count / x.total : x.count;
          return (
            <li key={x.name}>
              <button
                type="button"
                className={`sector-row${active === x.name ? " on" : ""}`}
                onClick={() => onPick(active === x.name ? null : x.name)}
                title={`${x.count} of ${x.total} ${x.name} stocks in the universe`}
              >
                <span className="sector-bar" style={{ width: `${Math.max(2, (value / max) * 100)}%` }} />
                <span className="sector-name">{x.name}</span>
                <span className="sector-val">
                  {rank === "share" ? `${Math.round(value * 100)}%` : x.count}
                  <small>{rank === "share" ? ` ${x.count}/${x.total}` : ` of ${x.total}`}</small>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

/* ---------- the RS matrix ---------- */
function Matrix({ rows, months, grouped, inProgress, showWeekly, openSymbol, onToggle }) {
  const span = 1 + months.length + 4 + (showWeekly ? 3 : 0);
  const body = [];
  let lastSector = null;
  rows.forEach((s) => {
    if (grouped && s.sector !== lastSector) {
      lastSector = s.sector;
      body.push(
        <tr className="group-row" key={`g-${s.sector}`}>
          <th colSpan={span} scope="colgroup">
            {s.sector}
            <span className="group-count">{rows.filter((r) => r.sector === s.sector).length}</span>
          </th>
        </tr>
      );
    }
    const open = openSymbol === s.symbol;
    body.push(
      <tr key={s.symbol} className={open ? "stock-row is-open" : "stock-row"} onClick={() => onToggle(s.symbol)}>
        <th scope="row" className="col-stock">
          <button
            type="button"
            className="stock-btn"
            aria-expanded={open}
            onClick={(e) => {
              e.stopPropagation();
              onToggle(s.symbol);
            }}
          >
            <span className="chev" aria-hidden="true" />
            <span className="stock-symbol">{s.symbol}</span>
            {!grouped && <span className="stock-sector">{s.sector}</span>}
          </button>
        </th>
        {s.cells.map((c, i) => {
          const joinL = c.q && s.cells[i - 1]?.q;
          const joinR = c.q && s.cells[i + 1]?.q;
          const kind = c.q ? (c.high ? "hi" : "near") : "miss";
          const shown = c.v == null ? "" : c.high ? 100 : Math.floor(c.v);
          return (
            <td key={months[i]} className="col-month">
              <span
                className={`cell ${kind}${joinL ? " join-l" : ""}${joinR ? " join-r" : ""}`}
                title={c.v == null ? `${months[i]}: not enough history` : `${months[i]}: RS at ${num(c.v)}% of its 12-month high`}
              >
                {shown}
              </span>
            </td>
          );
        })}
        <td className="col-run">{s.streak ? <strong>{s.streak}</strong> : <span className="muted">–</span>}</td>
        <td className="col-spark">
          <Spark values={s.trail.rs} />
        </td>
        <td className={`col-num${s.monthReturn < 0 ? " neg" : ""}`}>{signedPct(s.monthReturn)}</td>
        <td className="col-range">
          <span className="range" title="Where the latest price sits between this month's low and high">
            <span className="range-fill" style={{ width: `${Math.max(0, Math.min(100, s.closingRange ?? 0))}%` }} />
          </span>
          <span className="range-num">{s.closingRange == null ? "–" : `${num(s.closingRange, 0)}%`}</span>
        </td>
        {showWeekly && (
          <>
            <td className="col-num">{s.weeklyCR || "–"}</td>
            <td className="col-num">{s.dailyCR || "–"}</td>
            <td className="col-num">{s.dailyChange || "–"}</td>
          </>
        )}
      </tr>
    );
    if (open) {
      body.push(
        <tr className="detail-row" key={`d-${s.symbol}`}>
          <td colSpan={span}>
            <Detail stock={s} months={months} />
          </td>
        </tr>
      );
    }
  });

  return (
    <div className="matrix-scroll">
      <table className="matrix">
        <thead>
          <tr>
            <th scope="col" className="col-stock">
              Stock
            </th>
            {months.map((m, i) => (
              <th scope="col" key={m} className="col-month">
                {m}
                {inProgress && i === months.length - 1 && <span className="live-tag">to date</span>}
              </th>
            ))}
            <th scope="col" className="col-run" title="Months in a row marked, counting back from the latest month">
              Run
            </th>
            <th scope="col" className="col-spark">
              RS, 24 months
            </th>
            <th scope="col" className="col-num">
              Month
            </th>
            <th scope="col" className="col-range">
              Close in range
            </th>
            {showWeekly && (
              <>
                <th scope="col" className="col-num">
                  Week range
                </th>
                <th scope="col" className="col-num">
                  Day range
                </th>
                <th scope="col" className="col-num">
                  Day
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </div>
  );
}

function Spark({ values }) {
  const pts = (values || []).filter((v) => v != null);
  if (pts.length < 2) return null;
  const W = 112;
  const H = 28;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const x = (i) => 2 + (i / (pts.length - 1)) * (W - 8);
  const y = (v) => H - 4 - ((v - min) / (max - min || 1)) * (H - 8);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <svg className="spark" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Relative strength over 24 months">
      <path d={d} />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="2.6" />
    </svg>
  );
}

/* ---------- one stock, opened ---------- */
function Detail({ stock, months }) {
  return (
    <div className="detail">
      <RSChart trail={stock.trail} />
      <dl className="detail-facts">
        <div>
          <dt>Last price</dt>
          <dd>₹{num(stock.price, 2)}</dd>
        </div>
        <div>
          <dt>Above its 12-month EMA</dt>
          <dd>{signedPct(stock.emaGap)}</dd>
        </div>
        <div>
          <dt>Months marked</dt>
          <dd>
            {stock.hits} of {months.length}
          </dd>
        </div>
        <div>
          <dt>Sector</dt>
          <dd>{stock.sector}</dd>
        </div>
        {stock.isin && (
          <div>
            <dt>ISIN</dt>
            <dd>{stock.isin}</dd>
          </div>
        )}
        <a
          className="tv-link"
          href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`NSE:${stock.symbol}`)}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open the chart on TradingView
        </a>
      </dl>
    </div>
  );
}

function RSChart({ trail }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const H = 220;
  const pad = { l: 44, r: 16, t: 14, b: 26 };
  const n = trail.rs.length;
  const W = Math.max(320, width);
  const all = [...trail.rs, ...trail.high].filter((v) => v != null);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const yMin = lo - span * 0.08;
  const yMax = hi + span * 0.08;
  const x = (i) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin)) * (H - pad.t - pad.b);

  const rsPath = trail.rs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  // the 12-month high only changes at month-ends, so it is drawn as steps
  const highPath = trail.high
    .map((v, i) => (i ? `H${x(i).toFixed(1)} V${y(v).toFixed(1)}` : `M${x(0).toFixed(1)},${y(v).toFixed(1)}`))
    .join(" ");
  const atHigh = trail.rs.map((v, i) => v != null && trail.high[i] != null && v >= trail.high[i] - 0.05);
  const ticks = [0, 1, 2, 3].map((k) => yMin + ((k + 0.5) / 4) * (yMax - yMin));
  const every = n > 18 ? 4 : n > 9 ? 2 : 1;

  const onMove = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const rel = (e.clientX - box.left - pad.l) / (W - pad.l - pad.r);
    setHover(Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1)))));
  };

  return (
    <figure className="rs-chart" ref={ref}>
      <figcaption>
        <span className="key-line" /> Relative strength, start = 100
        <span className="key-step" /> Highest of the past 12 months
        <span className="key-dot" /> New 12-month high
      </figcaption>
      <div className="rs-chart-plot">
        {width > 0 && (
          <svg width={W} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label="Relative strength line with its 12-month high">
            {ticks.map((t) => (
              <g key={t}>
                <line className="grid" x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} />
                <text className="axis" x={pad.l - 8} y={y(t) + 4} textAnchor="end">
                  {Math.round(t)}
                </text>
              </g>
            ))}
            {trail.labels.map((label, i) =>
              (n - 1 - i) % every === 0 ? (
                <text className="axis" key={label} x={x(i)} y={H - 6} textAnchor={i === n - 1 ? "end" : "middle"}>
                  {label}
                </text>
              ) : null
            )}
            <path className="step" d={highPath} />
            <path className="line" d={rsPath} />
            {trail.rs.map((v, i) => (atHigh[i] ? <circle className="dot" key={i} cx={x(i)} cy={y(v)} r="4" /> : null))}
            {hover != null && <line className="cross" x1={x(hover)} x2={x(hover)} y1={pad.t} y2={H - pad.b} />}
          </svg>
        )}
        {hover != null && (
          <div className="tip" style={{ left: Math.min(Math.max(x(hover), 90), W - 90), top: pad.t }}>
            <strong>{trail.labels[hover]}</strong>
            <span>RS {num(trail.rs[hover])}</span>
            <span>12-month high {num(trail.high[hover])}</span>
            <span>{num((trail.rs[hover] / trail.high[hover]) * 100)}% of the high</span>
          </div>
        )}
      </div>
    </figure>
  );
}
