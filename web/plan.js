// Plan screen: description -> form -> simulation -> results. Owner: Mech B (AI parts: Mech A).
(() => {
  const $ = (id) => document.getElementById(id);
  const FIELDS = ["households", "generator_kw", "diesel_litres_per_month", "diesel_price_per_litre", "other_kw"];
  const aud = (n) => "AUD " + fmt(n);              // the engine works in Australian dollars
  let sites = [];
  let weekChart = null;

  function fillFromSite(s) {
    FIELDS.forEach((f) => ($(f).value = s[f] ?? ""));
    $("has_clinic").checked = !!s.has_clinic;
    $("has_school").checked = !!s.has_school;
  }

  // Fields the AI filled stay highlighted until the person edits them.
  function markAiFilled(ids) {
    document.querySelectorAll(".ai-filled").forEach((el) => el.classList.remove("ai-filled"));
    ids.forEach((id) => $(id) && $(id).classList.add("ai-filled"));
  }
  document.getElementById("plan-form").addEventListener("input", (e) => e.target.classList.remove("ai-filled"));

  function showNote(html, kind) {
    const note = $("describe-note");
    note.className = "ai-note" + (kind ? " " + kind : "");
    note.innerHTML = html;
    note.hidden = !html;
  }

  api("/api/sites").then((list) => {
    sites = list;
    $("site").innerHTML = list.map((s) => `<option value="${s.id}">${esc(s.name)}, ${esc(s.country)}</option>`).join("");
    fillFromSite(list[0]);
  });
  $("site").addEventListener("change", () => {
    markAiFilled([]);
    fillFromSite(sites.find((s) => s.id === $("site").value));
  });

  $("describe-btn").addEventListener("click", async () => {
    const text = $("describe").value.trim();
    if (!text) { showNote("Write a sentence or two about the island first.", "warn-note"); $("describe").focus(); return; }
    showNote('<span class="ai-badge">✦ AI</span> Reading your description…', "busy");
    $("describe-btn").disabled = true;
    try {
      const out = await api("/api/describe", { method: "POST", body: { text } });
      const s = out.site || {};
      const filled = [];
      // The description names the island: select it and start from its own numbers
      if (s.site_id && sites.some((x) => x.id === s.site_id)) {
        $("site").value = s.site_id;
        fillFromSite(sites.find((x) => x.id === s.site_id));
        filled.push("site");
      }
      FIELDS.forEach((f) => { if (s[f] !== null && s[f] !== undefined) { $(f).value = s[f]; filled.push(f); } });
      ["has_clinic", "has_school"].forEach((f) => {
        if (typeof s[f] === "boolean") { $(f).checked = s[f]; filled.push(f); }
      });
      markAiFilled(filled);
      const who = out.source === "ai" ? '<span class="ai-badge">✦ AI</span>' : '<span class="ai-badge rules">Rules</span>';
      const how = out.source === "ai" ? "The AI filled" : "Simple rules filled (AI is off)";
      let html = `${who} <b>${how} ${filled.length} field${filled.length === 1 ? "" : "s"}</b>, highlighted below. Check them, then simulate.`;
      if (out.question) html += `<span class="ai-question"><b>One more thing:</b> ${esc(out.question)}</span>`;
      (out.warnings || []).forEach((w) => { html += `<span class="ai-warning">${esc(w)}</span>`; });
      showNote(html, out.question ? "asks" : "");
    } catch (e) {
      showNote("Couldn't read it: " + esc(e.message), "warn-note");
    } finally {
      $("describe-btn").disabled = false;
    }
  });

  $("plan-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { site_id: $("site").value, has_clinic: $("has_clinic").checked, has_school: $("has_school").checked,
      use_live_shape: $("use_live_shape").checked };
    FIELDS.forEach((f) => { if ($(f).value !== "") body[f] = Number($(f).value); });
    $("plan-btn").disabled = true;
    $("plan-btn").textContent = "Simulating every hour of 20 years…";
    $("plan-out").innerHTML = `<div class="empty loading"><h2>Simulating 20 years of weather…</h2>
      <p>Testing solar and battery sizes against every hour of real sunshine, then the AI writes a summary and every
        number in it is checked.</p></div>`;
    try {
      render(await api("/api/plan", { method: "POST", body }));
    } catch (err) {
      $("plan-out").innerHTML = `<div class="empty"><h2>The simulation didn't run</h2><p>${esc(err.message)}</p></div>`;
    } finally {
      $("plan-btn").disabled = false;
      $("plan-btn").textContent = "Simulate 20 years of weather";
    }
  });

  // The explanation, with every number marked: the agent sends it as safe HTML (text escaped, only <mark> added).
  function storyHtml(ex) {
    if (ex.html) return ex.html.split(/(?:<br>\s*){2,}/).map((p) => `<p>${p}</p>`).join("");
    return ex.text.split(/\n\s*\n/).map((p) => `<p>${esc(p)}</p>`).join("");
  }

  function render(r) {
    const d = r.design, t = r.today, n = r.no_fuel_ship, ex = r.explanation, chk = ex.check;
    const synthetic = r.weather.startsWith("SYNTHETIC");
    const ai = ex.source === "ai";
    const checked = chk.numbers_checked ?? (chk.numbers ? chk.numbers.length : null);
    const tile = (label, value, cls = "") => `<div class="${cls}"><dt>${label}</dt><dd>${value}</dd></div>`;
    $("plan-out").innerHTML = `
      <p class="headline">Install <b>${fmt(d.solar_kw, 1)} kW</b> of solar and a <b>${fmt(d.battery_kwh, 1)} kWh</b>
        battery to cut diesel by <b>${fmt(d.percent_diesel_cut_p50, 1)}%</b>.</p>
      <dl class="ledger">
        ${tile("diesel per year today", fmt(t.diesel_litres_per_year) + " L")}
        ${tile("saved in a typical year", fmt(d.litres_saved_p50) + " L")}
        ${tile("saved even in a bad year (P90)", fmt(d.litres_saved_p90) + " L")}
        ${tile("saved per year", aud(d.money_saved_per_year_p50))}
        ${tile("to buy and install", aud(d.purchase_cost))}
        ${tile("to pay for itself", d.payback_years ? fmt(d.payback_years, 1) + " yrs" : "–")}
        ${tile("of power from the sun", fmt(d.solar_share_percent, 1) + "%")}
        ${tile("clinic powered, cloudiest week, no fuel ship", fmt(n.days_clinic_powered, 1) + " / 7 days", "clinic")}
      </dl>
      <div class="result-row">
        <div class="week"><h2>A typical week with the new system</h2><div class="chart-wrap"><canvas id="week-chart"></canvas></div></div>
        <article class="story ${ai ? "by-ai" : "by-template"}" aria-label="Plain-English summary">
          <header class="story-head">
            ${ai ? `<span class="ai-badge big">✦ Written by AI</span><span class="story-model">${esc(ex.model || "")}</span>`
                 : `<span class="ai-badge big rules">Written from the engine's template</span>`}
          </header>
          <div class="story-text">${storyHtml(ex)}</div>
          <p class="check ${chk.passed ? "" : "fail"}">${chk.passed
            ? `<span class="tick" aria-hidden="true">✓</span> ${checked === null ? "Every" : (checked === 1 ? "The 1" : "All " + checked)}
               number${checked === 1 ? "" : "s"} checked against the simulation.
               <span class="check-hint">Hover a highlighted number to see where it came from.</span>`
            : "Unchecked numbers: " + esc(chk.unknown_numbers.join(", "))}</p>
        </article>
      </div>
      <p class="source">Weather: <span class="${synthetic ? "warn" : ""}">${esc(r.weather)}</span>.
        Demand: ${fmt(r.load.daily_kwh, 1)} kWh a day, ${esc(r.load.basis)}; daily pattern: ${esc(r.load.pattern)}.
        Generator ${fmt(r.load.generator_kw)} kW. Diesel at AUD ${fmt(r.assumptions.diesel_price_per_litre, 2)} per litre.</p>`;
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
