import { useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";

/* The Rules tab shows RULES.md from the repository, with its Parameters section regenerated from the code. */
const slug = (t) =>
  t
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

export default function Rules({ markdown }) {
  const bodyRef = useRef(null);
  const [active, setActive] = useState(null);

  const { html, toc, updated } = useMemo(() => {
    const src = markdown || "# Rules\n\nThe rules document has not loaded yet.";
    const tokens = marked.lexer(src);
    const toc = tokens.filter((t) => t.type === "heading" && t.depth === 2).map((t) => ({ text: t.text, id: slug(t.text) }));
    const log = src.split(/^## .*Change log.*$/m)[1] || "";
    const m = log.match(/^\*\*([^*]+)\*\*/m);
    return { html: marked.parser(tokens), toc, updated: m ? m[1] : null };
  }, [markdown]);

  // give headings stable ids for the contents list, and open external links in a new tab
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    root.querySelectorAll("h2, h3").forEach((h) => (h.id = slug(h.textContent)));
    root.querySelectorAll("a[href^='http']").forEach((a) => {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    });
  }, [html]);

  const go = (id) => {
    const el = bodyRef.current?.querySelector(`#${CSS.escape(id)}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActive(id);
    }
  };

  return (
    <div className="rules-page">
      <aside className="rules-toc" aria-label="Contents">
        <p className="rules-toc-title">Contents</p>
        <ol>
          {toc.map((t) => (
            <li key={t.id}>
              <button type="button" className={active === t.id ? "on" : ""} onClick={() => go(t.id)}>
                {t.text}
              </button>
            </li>
          ))}
        </ol>
        {updated && <p className="rules-updated">Last change: {updated}</p>}
        <p className="rules-src">
          Source: <a href="https://github.com/Nhanna234/RS-on-Nifty/blob/main/RULES.md" target="_blank" rel="noopener noreferrer">RULES.md</a> in the repository.
        </p>
      </aside>
      <article className="rules-body panel" ref={bodyRef} dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
