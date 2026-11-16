// Invented data, used only when the page is opened on its own (npm run dev) without Streamlit.
const SECTORS = ["Capital Goods", "Financial Services", "Healthcare", "Automobile and Auto Components", "Chemicals", "Information Technology", "Metals & Mining"];
const NAMES = ["ALPHAENG", "BHARATFIN", "CURAMED", "DRIVELINE", "ELEMCHEM", "FLOWTECH", "GANGAMET", "HIMCAP", "INDUSPOW", "JALFIN", "KAVERIPH", "LOTUSAUTO", "MERUCHEM", "NILGIRIT", "OMSTEEL", "PRAGATI", "QUBEFIN", "RUDRAMED", "SAHYADRI", "TRIVENI", "UDAYCAP", "VINDHYA", "WESTFIN", "YAMUNAPH", "ZENAUTO", "ARAVALI", "BRAHMA", "CAUVERY"];

function rng(seed) {
  let s = seed;
  return () => ((s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296);
}

export const SAMPLE_ARGS = {
  indices: ["Nifty 50", "Nifty 100", "Nifty 200", "Nifty LargeMidcap 250", "Nifty Midcap 150", "Nifty Smallcap 100", "Nifty Smallcap 250", "Nifty Microcap 250", "Nifty 500", "Nifty Total Market"],
  defaults: { index: "Nifty Total Market", history: 6, threshold: 100, useEma: true, cr: 75, mr: 5, weekly: false },
  result: null,
};

export function sampleResult(nonce, settings) {
  const months = ["Jun-26", "Jul-26", "Aug-26", "Sep-26", "Oct-26", "Nov-26"].slice(-Math.min(6, settings.history));
  const labels = [];
  const start = new Date(2024, 11, 1);
  for (let i = 0; i < 24; i++) {
    const d = new Date(start.getFullYear(), start.getMonth() + i, 1);
    labels.push(d.toLocaleString("en-GB", { month: "short" }) + "-" + String(d.getFullYear()).slice(2));
  }
  const stocks = NAMES.map((symbol, k) => {
    const r = rng(k * 97 + 11);
    const drift = 0.012 + r() * 0.03;
    const rs = [100];
    for (let i = 1; i < 24; i++) rs.push(+(rs[i - 1] * (1 + drift + (r() - 0.5) * 0.11)).toFixed(1));
    const high = rs.map((_, i) => Math.max(...rs.slice(Math.max(0, i - 11), i + 1)));
    const cells = months.map((_, j) => {
      const i = 24 - months.length + j;
      const v = +((rs[i] / high[i]) * 100).toFixed(1);
      const q = v >= settings.threshold;
      return { v, q, high: v >= 100 };
    });
    let streak = 0;
    for (let i = cells.length - 1; i >= 0 && cells[i].q; i--) streak++;
    return {
      symbol, sector: SECTORS[k % SECTORS.length], isin: "INE" + String(100 + k) + "A01011", cells, streak,
      hits: cells.filter((c) => c.q).length, price: +(200 + r() * 3000).toFixed(2), monthReturn: +(1 + r() * 14).toFixed(1),
      closingRange: Math.round(55 + r() * 45), emaGap: +(4 + r() * 30).toFixed(1), trail: { labels, rs, high },
      weeklyCR: settings.weekly ? Math.round(76 + r() * 24) + "%" : null,
      dailyCR: settings.weekly ? Math.round(30 + r() * 70) + "%" : null,
      dailyChange: settings.weekly ? (r() * 4 - 1).toFixed(2) + "%" : null,
    };
  }).filter((s) => s.hits > 0);
  const sectorTotals = {};
  SECTORS.forEach((s, i) => (sectorTotals[s] = 60 + i * 17));
  return {
    ok: true, nonce, universe: { name: settings.index, source: "sample data", count: 750 }, asOf: "16 Nov 2026, 22:10 IST",
    months, monthInProgress: true, settings, sectorTotals, stocks, excel: null,
  };
}
