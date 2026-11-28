// The Market breadth and Sector rotation pages, moved from the Abdullah Dashboard. They keep their own look:
// they are drawn inside a shadow root with their own style sheet, so neither app's styles reach the other's.
import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import Breadth from "./Breadth.jsx";
import Sectors from "./Sectors.jsx";
import { day } from "./ui.jsx";
import "./fonts.css";
import dashCss from "./dash.css?inline";

function ShadowMount({ children }) {
  const hostRef = useRef(null);
  const rootRef = useRef(null);

  useEffect(() => {
    const shadow = hostRef.current.shadowRoot || hostRef.current.attachShadow({ mode: "open" });
    shadow.innerHTML = "";
    const style = document.createElement("style");
    style.textContent = dashCss;
    const box = document.createElement("div");
    box.className = "dash";
    shadow.append(style, box);
    rootRef.current = createRoot(box);
    return () => {
      const r = rootRef.current;
      rootRef.current = null;
      setTimeout(() => r && r.unmount(), 0); // unmounting a root while React is rendering is not allowed
    };
  }, []);

  // draw (again) whenever the page or its data changes
  useEffect(() => {
    if (rootRef.current) rootRef.current.render(children);
  });

  return <div ref={hostRef} className="dash-host" />;
}

export default function Market({ page, args, ask, pending }) {
  const asOf = page === "sectors" ? args.sectors?.asOf : args.breadth?.asOf;
  // "Refresh data" clears the saved market data and reloads this page with its current settings
  const refresh = () => ask("refresh", { page, settings: (page === "sectors" ? args.sectors : args.breadth)?.settings || null });
  return (
    <ShadowMount>
      <div className="dash-bar">
        <span className="dash-title">{page === "sectors" ? "Sector rotation" : "Market breadth"}</span>
        {asOf && <span>Closing prices of {day(asOf)}</span>}
        <button type="button" className="btn small" onClick={refresh} disabled={Boolean(pending[page])} title="Fetch fresh data from NSE and Yahoo instead of the saved copy">
          Refresh data
        </button>
      </div>
      {page === "sectors" ? <Sectors args={args} ask={ask} pending={pending} /> : <Breadth args={args} ask={ask} pending={pending} />}
    </ShadowMount>
  );
}
