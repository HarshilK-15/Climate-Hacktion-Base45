// Shared helpers + tab switching. Owner: Mech B.
const COLORS = { lagoon: "#0f4c5c", sun: "#f4b400", reef: "#2e9e83", diesel: "#6b4f3a", coral: "#c8463d", line: "#c9d8da" };

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

const fmt = (n, d = 0) => (n === null || n === undefined || Number.isNaN(n)) ? "–"
  : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
const money = (n) => "$" + fmt(n);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const tabHandlers = {};   // other files register: tabHandlers.map = () => {...}
document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b === btn));
    document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + btn.dataset.tab));
    if (tabHandlers[btn.dataset.tab]) tabHandlers[btn.dataset.tab]();
  });
});
