import { useEffect, useState } from "react";

const KEY = "rs-lender-overrides";

function loadOverrides() {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || "{}");
    return { alwaysExclude: v.alwaysExclude || "", neverExclude: v.neverExclude || "" };
  } catch {
    return { alwaysExclude: "", neverExclude: "" };
  }
}

function saveOverrides(v) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    /* storage unavailable: the lists still apply to this session */
  }
}

/* Switch plus the user's own always / never exclude lists. value = { excludeLenders, alwaysExclude, neverExclude } */
export function LenderControl({ value, onChange, idPrefix }) {
  const [open, setOpen] = useState(Boolean(value.alwaysExclude || value.neverExclude));
  const set = (k) => (e) => {
    const next = { ...value, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value };
    onChange(next);
    if (k !== "excludeLenders") saveOverrides({ alwaysExclude: next.alwaysExclude, neverExclude: next.neverExclude });
  };
  return (
    <div className="lender-control">
      <div className="lender-row">
        <label className="switch">
          <input type="checkbox" checked={Boolean(value.excludeLenders)} onChange={set("excludeLenders")} />
          <span className="switch-track" aria-hidden="true" />
          Exclude lending companies
        </label>
        <button type="button" className="link-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Hide my lists" : "Adjust the list"}
        </button>
      </div>
      {open && (
        <div className="lender-lists">
          <div className="field">
            <label htmlFor={`${idPrefix}-always`}>Always exclude (symbols)</label>
            <input id={`${idPrefix}-always`} type="text" placeholder="e.g. BAJAJFINSV, JIOFIN" value={value.alwaysExclude} onChange={set("alwaysExclude")} />
          </div>
          <div className="field">
            <label htmlFor={`${idPrefix}-never`}>Never exclude (symbols)</label>
            <input id={`${idPrefix}-never`} type="text" placeholder="e.g. a company you know does not lend" value={value.neverExclude} onChange={set("neverExclude")} />
          </div>
          <span className="hint">Separate symbols with commas. Your lists are remembered in this browser and apply to both tabs.</span>
        </div>
      )}
    </div>
  );
}

export function useLenderSettings(defaultOn = true) {
  const [v, setV] = useState(() => ({ excludeLenders: defaultOn, ...loadOverrides() }));
  // pick up list edits made in the other tab
  useEffect(() => {
    const onStorage = (e) => e.key === KEY && setV((cur) => ({ ...cur, ...loadOverrides() }));
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  return [v, setV];
}

/* What the filter removed, with the reason for each stock */
export function LenderNote({ lenders }) {
  if (!lenders) return null;
  if (!lenders.on) return <p className="provenance">Lending companies are included in this run.</p>;
  const ex = lenders.excluded || [];
  const kept = lenders.kept || [];
  const byRule = lenders.byRule || 0;
  const status =
    lenders.nseStatus === "ok"
      ? "using NSE's industry classification"
      : lenders.nseStatus === "partial"
      ? `NSE's classification for most; ${byRule} decided by name rules because NSE did not answer for them`
      : `NSE did not answer, so all ${byRule} were decided by name rules; check the list`;
  return (
    <details className="caveats lender-note">
      <summary>
        {ex.length} lending {ex.length === 1 ? "company" : "companies"} left out, {status}
      </summary>
      <p className="lender-def">
        Lending means NSE's basic industry is a bank (public, private, small finance, payment or foreign), an NBFC, a
        housing finance company or a financial institution. Insurers, fund houses, brokers, exchanges, depositories and
        holding companies stay in.
      </p>
      <div className="lender-cols">
        <div>
          <h4>Left out ({ex.length})</h4>
          <LenderTable rows={ex} />
        </div>
        <div>
          <h4>Other financial companies kept ({kept.length})</h4>
          <LenderTable rows={kept} />
        </div>
      </div>
    </details>
  );
}

function LenderTable({ rows }) {
  if (!rows.length) return <p className="quiet-note small">None.</p>;
  return (
    <div className="lender-scroll">
      <table className="hold-table">
        <thead>
          <tr>
            <th scope="col">Stock</th>
            <th scope="col">Classified as</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <th scope="row">
                <span className="stock-symbol">{r.symbol}</span>
                <span className="hold-sector">{r.name}</span>
              </th>
              <td>{r.label}</td>
              <td className={r.source.startsWith("rule") ? "src-rule" : "muted"}>{r.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
