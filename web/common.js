// Shared helpers + tab switching. Owner: Mech B.
// Same values as the CSS variables in style.css (lagoon = --sea, reef = --leaf).
const COLORS = { lagoon: "#1d5f80", sun: "#f2b53a", reef: "#3f8f55", diesel: "#7a5b45", coral: "#c8463d", line: "#d4ded9", ink: "#122023" };
if (window.Chart) {
  Chart.defaults.font.family = '"Atkinson Hyperlegible", system-ui, sans-serif';
  Chart.defaults.color = "#536a6d";
  Chart.defaults.borderColor = "rgba(18,32,35,.08)";
}

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

// Demo-only sensor locations for the Live and Alerts tabs: a handful of districts per
// country so the app feels like a fleet, not one sensor. tz = hours offset from UTC
// (used to work out each district's real local hour, so the diurnal curve differs by
// location even at the same instant). Not read by the engine or /api/plan.
const DEMO_LOCATIONS = [
  { id: "funafuti", district: "Funafuti", country: "Tuvalu", tz: 12, genKw: 40 },
  { id: "nanumea", district: "Nanumea", country: "Tuvalu", tz: 12, genKw: 30 },
  { id: "nukufetau", district: "Nukufetau", country: "Tuvalu", tz: 12, genKw: 22 },
  { id: "suva", district: "Suva", country: "Fiji", tz: 12, genKw: 60 },
  { id: "kadavu", district: "Kadavu", country: "Fiji", tz: 12, genKw: 45 },
  { id: "labasa", district: "Labasa", country: "Fiji", tz: 12, genKw: 38 },
  { id: "nukualofa", district: "Nuku'alofa", country: "Tonga", tz: 13, genKw: 50 },
  { id: "eua", district: "'Eua", country: "Tonga", tz: 13, genKw: 35 },
  { id: "vavau", district: "Vava'u", country: "Tonga", tz: 13, genKw: 42 },
  { id: "portvila", district: "Port Vila", country: "Vanuatu", tz: 11, genKw: 55 },
  { id: "malekula", district: "Malekula", country: "Vanuatu", tz: 11, genKw: 25 },
  { id: "tanna", district: "Tanna", country: "Vanuatu", tz: 11, genKw: 28 },
  { id: "tarawa", district: "Tarawa", country: "Kiribati", tz: 12, genKw: 48 },
  { id: "abaiang", district: "Abaiang", country: "Kiribati", tz: 12, genKw: 35 },
  { id: "kiritimati", district: "Kiritimati", country: "Kiribati", tz: 14, genKw: 20 },
  { id: "apia", district: "Apia", country: "Samoa", tz: 13, genKw: 58 },
  { id: "savaii", district: "Savai'i", country: "Samoa", tz: 13, genKw: 55 },
  { id: "manono", district: "Manono", country: "Samoa", tz: 13, genKw: 18 },
];
const locById = (id) => DEMO_LOCATIONS.find((l) => l.id === id);

// Fraction of the generator's size a village typically draws, by local hour: low
// overnight, a morning bump, a bigger evening peak (cooking, lighting, TV).
function diurnalFraction(hour) {
  const h = ((hour % 24) + 24) % 24;
  const morning = Math.exp(-((h - 8) ** 2) / 8);
  const evening = Math.exp(-((h - 19) ** 2) / 6) * 1.3;
  return Math.min(1, 0.22 + 0.55 * morning + 0.68 * evening);
}
const localHourAt = (loc, date) => (date.getUTCHours() + date.getUTCMinutes() / 60 + loc.tz + 24) % 24;

const tabHandlers = {};   // other files register: tabHandlers.map = () => {...}

// Slides the lime pill behind the active tab.
function moveTabTick(btn) {
  const tick = document.querySelector(".tab-tick");
  const nav = btn && btn.parentElement;
  if (!tick || !nav) return;
  const navRect = nav.getBoundingClientRect(), r = btn.getBoundingClientRect();
  tick.style.width = r.width + "px";
  tick.style.transform = `translateX(${r.left - navRect.left}px)`;
}

// One page per view: "home" is the full-screen hero, the rest are the app panels.
// The view lives in the URL hash (/#plan) so links, refresh and the back button all work.
const VIEWS = ["home", "live", "plan", "map", "alerts"];
function showView(view) {
  if (!VIEWS.includes(view)) view = "home";
  document.body.dataset.view = view;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === view));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + view));
  moveTabTick(document.querySelector(`.tabs button[data-tab="${view}"]`));
  window.scrollTo(0, 0);
  if (tabHandlers[view]) tabHandlers[view]();
}
function goTo(view) {
  history.pushState(null, "", view === "home" ? location.pathname : "#" + view);
  showView(view);
}
document.querySelectorAll(".tabs button").forEach((btn) => btn.addEventListener("click", () => goTo(btn.dataset.tab)));
document.querySelectorAll("[data-goto]").forEach((link) => link.addEventListener("click", (e) => {
  e.preventDefault();
  goTo(link.dataset.goto);
}));
window.addEventListener("popstate", () => showView(location.hash.slice(1)));
// Wait until every script has registered its tab handler (the map only builds itself when shown).
document.addEventListener("DOMContentLoaded", () => showView(location.hash.slice(1)));
window.addEventListener("load", () => moveTabTick(document.querySelector('.tabs button[aria-selected="true"]')));
window.addEventListener("resize", () => moveTabTick(document.querySelector('.tabs button[aria-selected="true"]')));
