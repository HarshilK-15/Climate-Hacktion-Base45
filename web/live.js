// Live power screen: polls the server every 2 seconds, or shows a simulated district
// from DEMO_LOCATIONS (common.js) when one is picked from the dropdown. Owner: Mech B.
(() => {
  let minutes = 15;
  const REAL_DEMO_GENERATOR_W = 3000;   // matches guard.rules.DEMO_GENERATOR_W: the home circuit plays "the generator"
  const locationSelect = document.getElementById("live-location");

  locationSelect.appendChild(new Option("Live sensor (real device)", "real"));
  const byCountry = {};
  DEMO_LOCATIONS.forEach((loc) => (byCountry[loc.country] ??= []).push(loc));
  Object.keys(byCountry).forEach((country) => {
    const group = document.createElement("optgroup");
    group.label = country;
    byCountry[country].forEach((loc) => group.appendChild(new Option(loc.district, loc.id)));
    locationSelect.appendChild(group);
  });
  locationSelect.value = DEMO_LOCATIONS[0].id;   // open on a lively simulated district, not a blank real sensor

  const chart = new Chart(document.getElementById("live-chart"), {
    type: "line",
    data: { datasets: [{ label: "Watts", data: [], borderColor: COLORS.lagoon, backgroundColor: "rgba(29,95,128,.12)",
      fill: true, pointRadius: 0, borderWidth: 2, tension: 0.2 }] },
    options: {
      animation: false, maintainAspectRatio: false, parsing: false,
      scales: {
        x: { type: "linear", ticks: { callback: (v) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) } },
        y: { beginAtZero: true, title: { display: true, text: "Watts" } },
      },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (items) => new Date(items[0].parsed.x).toLocaleTimeString(),
        label: (item) => fmt(item.parsed.y) + " W" } } },
    },
  });

  document.getElementById("live-minutes").addEventListener("change", (e) => {
    minutes = Number(e.target.value);
    document.getElementById("live-window").textContent = e.target.selectedOptions[0].text.replace(" min", "");
    refresh();
  });
  locationSelect.addEventListener("change", refresh);

  // One fake reading for a district at a given instant: diurnal curve for its real
  // local hour (so e.g. Vanuatu and Kiribati can show different parts of their day
  // at the same moment), plus jitter so the line isn't dead flat.
  function mockPoint(loc, date) {
    const hour = localHourAt(loc, date);
    const frac = diurnalFraction(hour) * (0.94 + 0.12 * Math.random());
    const watts = Math.max(0, loc.genKw * 1000 * frac);
    return { ts: date.toISOString(), watts: Math.round(watts), amps: Math.round((watts / 230) * 100) / 100 };
  }

  function mockSeries(loc, mins) {
    const now = Date.now();
    const spanMs = mins * 60000;
    const step = Math.max(5000, Math.floor(spanMs / 240));
    const rows = [];
    for (let t = now - spanMs; t <= now; t += step) rows.push(mockPoint(loc, new Date(t)));
    return rows;
  }

  // The gauge dial: fraction of the generator's size currently being drawn.
  function applyGauge(watts, genKw, label) {
    const pct = watts != null && genKw > 0 ? Math.max(0, Math.min(100, Math.round((watts / (genKw * 1000)) * 100))) : 0;
    document.getElementById("live-gauge").style.setProperty("--pct", pct);
    document.getElementById("live-cap-line").innerHTML = watts != null
      ? `<b>${pct}%</b> of the ${esc(label)}'s capacity` : "";
  }

  async function refresh() {
    const picked = locationSelect.value;
    if (picked !== "real") return refreshSimulated(locById(picked));
    try {
      const [status, rows] = await Promise.all([api("/api/status"), api(`/api/readings?minutes=${minutes}`)]);
      renderStatus(status.last ? { ...status.last, online: status.online, age_s: status.age_s } : null, status);
      applyGauge(status.last ? status.last.watts : null, REAL_DEMO_GENERATOR_W / 1000, `${REAL_DEMO_GENERATOR_W / 1000} kW demo circuit`);
      renderSeries(rows);
    } catch (e) {
      document.getElementById("live-status-text").textContent = "Can't reach the server: " + e.message;
    }
  }

  function refreshSimulated(loc) {
    const now = new Date();
    const last = mockPoint(loc, now);
    const ageS = 1 + Math.round(Math.random() * 3);   // "different times": a touch of realistic lag per district
    const hour = localHourAt(loc, now);
    const hh = String(Math.floor(hour)).padStart(2, "0"), mm = String(Math.round((hour % 1) * 60)).padStart(2, "0");
    renderStatus({ ...last, device_id: `sim-${loc.id}`, online: true, age_s: ageS }, { online: true, age_s: ageS, last });
    applyGauge(last.watts, loc.genKw, `${fmt(loc.genKw)} kW generator`);
    document.getElementById("live-device").textContent =
      `Simulated sensor on ${loc.district}, ${loc.country} (local time ${hh}:${mm})`;
    document.getElementById("live-device").classList.add("warn");
    renderSeries(mockSeries(loc, minutes));
  }

  function renderStatus(last, status) {
    const st = document.getElementById("live-status");
    const txt = document.getElementById("live-status-text");
    if (!last) {
      st.className = "status"; txt.textContent = "Waiting for the sensor";
    } else if (status.online) {
      st.className = "status online"; txt.textContent = `Live, updated ${status.age_s}s ago`;
    } else {
      st.className = "status offline"; txt.textContent = `No signal for ${fmt(status.age_s)}s`;
    }
    if (last) {
      const big = last.watts >= 10000;
      document.getElementById("live-watts").textContent = big ? fmt(last.watts / 1000, 1) : fmt(last.watts);
      document.querySelector(".watts .unit").textContent = big ? "kW" : "W";
      document.getElementById("live-amps").textContent = fmt(last.amps, 2);
      if (locationSelect.value === "real") {
        const sim = /^(sim|fake)-/.test(last.device_id);
        const dev = document.getElementById("live-device");
        dev.textContent = sim ? `Simulated sensor (${last.device_id}): no hardware, model data`
                              : `Sensor: ${last.device_id}`;
        dev.classList.toggle("warn", sim);
      }
    }
  }

  function renderSeries(rows) {
    const pts = rows.map((r) => ({ x: Date.parse(r.ts), y: r.watts }));
    chart.data.datasets[0].data = pts;
    chart.update();
    if (pts.length) {
      const w = pts.map((p) => p.y);
      document.getElementById("live-min").textContent = fmt(Math.min(...w)) + " W";
      document.getElementById("live-max").textContent = fmt(Math.max(...w)) + " W";
      let kwh = 0;   // energy = power x time between readings
      for (let i = 1; i < pts.length; i++) {
        const dtH = Math.min(pts[i].x - pts[i - 1].x, 60000) / 3600000;
        kwh += (pts[i].y / 1000) * dtH;
      }
      document.getElementById("live-kwh").textContent = fmt(kwh, 3) + " kWh";
    }
  }

  refresh();
  setInterval(refresh, 2000);
})();
