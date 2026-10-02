// Plan screen: description -> form -> simulation -> results. Owner: Mech B (AI parts: Mech A).
(() => {
  const $ = (id) => document.getElementById(id);
  const FIELDS = ["households", "generator_kw", "diesel_litres_per_month", "diesel_price_per_litre", "other_kw"];
  let sites = [];
  let weekChart = null;

  function fillFromSite(s) {
    FIELDS.forEach((f) => ($(f).value = s[f] ?? ""));
    $("has_clinic").checked = !!s.has_clinic;
    $("has_school").checked = !!s.has_school;
  }

  api("/api/sites").then((list) => {
    sites = list;
    $("site").innerHTML = list.map((s) => `<option value="${s.id}">${esc(s.name)}, ${esc(s.country)}</option>`).join("");
    fillFromSite(list[0]);
  });
  $("site").addEventListener("change", () => fillFromSite(sites.find((s) => s.id === $("site").value)));

  $("describe-btn").addEventListener("click", async () => {
    const text = $("describe").value.trim();
    if (!text) { $("describe-note").textContent = "Write a sentence or two first."; return; }
    $("describe-note").textContent = "Reading your description…";
    try {
      const out = await api("/api/describe", { method: "POST", body: { text } });
      let filled = 0;
      FIELDS.forEach((f) => { if (out.site[f] !== null && out.site[f] !== undefined) { $(f).value = out.site[f]; filled++; } });
      $("has_clinic").checked = out.site.has_clinic;
      $("has_school").checked = out.site.has_school;
      $("describe-note").textContent = `Filled ${filled} fields (${out.source === "ai" ? "AI" : "simple rules"}). Check them before running.` + (out.note ? " " + out.note : "");
    } catch (e) { $("describe-note").textContent = "Couldn't read it: " + e.message; }
  });

  $("plan-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { site_id: $("site").value, has_clinic: $("has_clinic").checked, has_school: $("has_school").checked,
      use_live_shape: $("use_live_shape").checked };
    FIELDS.forEach((f) => { if ($(f).value !== "") body[f] = Number($(f).value); });
    $("plan-btn").disabled = true;
    $("plan-btn").textContent = "Simulating every hour of 20 years…";
    try {
      render(await api("/api/plan", { method: "POST", body }));
    } catch (err) {
      $("plan-out").innerHTML = `<div class="empty"><h2>The simulation didn't run</h2><p>${esc(err.message)}</p></div>`;
    } finally {
      $("plan-btn").disabled = false;
      $("plan-btn").textContent = "Simulate 20 years of weather";
    }
  });

  function render(r) {
    const d = r.design, t = r.today, n = r.no_fuel_ship;
    const synthetic = r.weather.startsWith("SYNTHETIC");
    const paras = r.explanation.text.split(/\n\s*\n/).map((p) => `<p>${esc(p)}</p>`).join("");
    const chk = r.explanation.check;
    $("plan-out").innerHTML = `
      <p class="headline">Install ${fmt(d.solar_kw, 1)} kW of solar and a ${fmt(d.battery_kwh, 1)} kWh battery
        to cut diesel by ${fmt(d.percent_diesel_cut_p50, 1)}%.</p>
      <div class="figures">
        <div><b>${fmt(t.diesel_litres_per_year)} L</b><span>diesel per year today</span></div>
        <div><b>${fmt(d.litres_saved_p50)} L</b><span>saved in a typical year</span></div>
        <div><b>${fmt(d.litres_saved_p90)} L</b><span>saved even in a bad year (P90)</span></div>
        <div><b>${money(d.money_saved_per_year_p50)}</b><span>saved per year</span></div>
        <div><b>${money(d.purchase_cost)}</b><span>to buy and install</span></div>
        <div><b>${d.payback_years ? fmt(d.payback_years, 1) + " yrs" : "–"}</b><span>to pay for itself</span></div>
        <div><b>${fmt(d.solar_share_percent, 1)}%</b><span>of power from the sun</span></div>
        <div class="clinic"><b>${fmt(n.days_clinic_powered, 1)} / 7 days</b><span>clinic powered in the cloudiest week, no fuel ship</span></div>
      </div>
      <div class="week"><h2>A typical week with the new system</h2><div class="chart-wrap"><canvas id="week-chart"></canvas></div></div>
      <div class="story">${paras}
        <p class="check ${chk.passed ? "" : "fail"}">${r.explanation.source === "ai" ? "Written by AI" : "Written from a template"};
          ${chk.passed ? "every number checked against the simulation." : "unchecked numbers: " + esc(chk.unknown_numbers.join(", "))}</p>
      </div>
      <p class="source">Weather: <span class="${synthetic ? "warn" : ""}">${esc(r.weather)}</span>.
        Demand: ${fmt(r.load.daily_kwh, 1)} kWh a day, ${esc(r.load.basis)}; daily pattern: ${esc(r.load.pattern)}.
        Generator ${fmt(r.load.generator_kw)} kW. Diesel at $${fmt(r.assumptions.diesel_price_per_litre, 2)} per litre.</p>`;
    drawWeek(r.week_trace);
  }

  function drawWeek(tr) {
    if (weekChart) weekChart.destroy();
    const hours = tr.load.map((_, i) => i);
    const ds = (label, data, color) => ({ label, data, backgroundColor: color, borderColor: color, fill: true,
      pointRadius: 0, borderWidth: 0, stack: "s", tension: 0.25 });
    weekChart = new Chart(document.getElementById("week-chart"), {
      type: "line",
      data: { labels: hours, datasets: [
        ds("Solar", tr.solar, COLORS.sun), ds("Battery", tr.battery, COLORS.reef), ds("Diesel", tr.diesel, COLORS.diesel),
        { label: "Island demand", data: tr.load, borderColor: COLORS.lagoon, borderWidth: 2, pointRadius: 0, fill: false, stack: "load", tension: 0.25 },
      ] },
      options: { animation: false, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { callback: (v) => (v % 24 === 0 ? "Day " + (v / 24 + 1) : ""), autoSkip: false, maxRotation: 0 }, grid: { display: false } },
          y: { stacked: true, beginAtZero: true, title: { display: true, text: "kW" } },
        },
        plugins: { tooltip: { callbacks: { title: (it) => `Day ${Math.floor(it[0].parsed.x / 24) + 1}, ${it[0].parsed.x % 24}:00`,
          label: (it) => `${it.dataset.label}: ${fmt(it.parsed.y, 1)} kW` } } } },
    });
  }
})();
