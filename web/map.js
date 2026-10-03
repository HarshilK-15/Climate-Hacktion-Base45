// Savings map + portfolio totals. Owner: Mech B (data: Data Sci).
(() => {
  let map = null;
  let islands = [];
  let metric = "money";
  const markers = [];   // { marker, island } in the same order as islands

  const METRICS = {
    money: { label: "Money saved / yr", fmt: (v) => money(Math.round(v)), value: (i) => i.design.money_saved_per_year_p50 },
    litres: { label: "Diesel cut / yr", fmt: (v) => fmt(Math.round(v)) + " L", value: (i) => i.design.litres_saved_p50 },
    co2: { label: "CO₂ avoided / yr", fmt: (v) => fmt(Math.round(v)) + " t", value: (i) => i.design.litres_saved_p50 * 2.68 / 1000 },
  };

  const lerp = (a, b, t) => Math.round(a + (b - a) * t);
  function colorFor(t) {
    const from = [122, 91, 69], to = [63, 143, 85];   // --diesel -> --leaf
    return `rgb(${lerp(from[0], to[0], t)},${lerp(from[1], to[1], t)},${lerp(from[2], to[2], t)})`;
  }

  function compareBar(island) {
    const todayCost = island.today.diesel_cost_per_year;
    const solarCost = Math.max(todayCost - island.design.money_saved_per_year_p50, 0);
    const max = Math.max(todayCost, solarCost, 1);
    const row = (label, val, color) => `
      <div class="cmp"><div class="cmp-head"><span>${esc(label)}</span><b>${money(val)}/yr</b></div>
        <div class="cmp-bar"><i style="width:${Math.max(4, 100 * val / max)}%; background:${color}"></i></div></div>`;
    return row("Diesel today", todayCost, "var(--diesel)") + row("With solar + battery", solarCost, "var(--reef)");
  }

  function renderMetric() {
    const m = METRICS[metric];
    const values = islands.map(m.value);
    const lo = Math.min(...values), hi = Math.max(...values, lo + 1);
    markers.forEach(({ marker }, idx) => {
      const t = (values[idx] - lo) / (hi - lo);
      marker.setStyle({ radius: 9 + t * 16, fillColor: colorFor(t) });
    });
    document.getElementById("map-legend").innerHTML = `
      <span class="legend-label">${esc(m.label)}</span>
      <span class="legend-bar"></span>
      <span class="legend-range"><span>${m.fmt(lo)}</span><span>${m.fmt(hi)}</span></span>`;

    const order = islands.map((isl, idx) => idx).sort((a, b) => values[b] - values[a]);
    document.getElementById("island-list").innerHTML = order.map((idx) => {
      const isl = islands[idx];
      return `<button type="button" class="island-row" data-idx="${idx}">
        <span class="island-name">${esc(isl.site.name)}<small>${esc(isl.site.country)}</small></span>
        <span class="island-val">${m.fmt(values[idx])}</span>
      </button>`;
    }).join("");
    document.querySelectorAll(".island-row").forEach((row) => {
      const idx = Number(row.dataset.idx);
      row.addEventListener("click", () => focusIsland(idx));
      row.addEventListener("mouseenter", () => markers[idx].marker.setStyle({ weight: 4 }));
      row.addEventListener("mouseleave", () => markers[idx].marker.setStyle({ weight: 2 }));
    });
  }

  function focusIsland(idx) {
    const { marker, island } = markers[idx];
    const lon = island.site.lon < 0 ? island.site.lon + 360 : island.site.lon;
    map.flyTo([island.site.lat, lon], 6, { duration: 0.6 });
    marker.openPopup();
  }

  document.querySelectorAll(".metric-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      metric = btn.dataset.metric;
      document.querySelectorAll(".metric-btn").forEach((b) => b.setAttribute("aria-pressed", b === btn));
      renderMetric();
    });
  });

  async function show() {
    if (!map) {
      map = L.map("map", { worldCopyJump: false }).setView([-12, 178], 4);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 10, attribution: "© OpenStreetMap contributors" }).addTo(map);
      try {
        const p = await api("/api/portfolio");
        islands = p.islands;
        islands.forEach((i) => {
          const lon = i.site.lon < 0 ? i.site.lon + 360 : i.site.lon;
          const marker = L.circleMarker([i.site.lat, lon], { radius: 10, color: COLORS.ink, fillOpacity: 0.9, weight: 2 })
            .addTo(map)
            .bindPopup(`<strong>${esc(i.site.name)}, ${esc(i.site.country)}</strong><br>
              ${fmt(i.design.solar_kw, 1)} kW solar + ${fmt(i.design.battery_kwh, 1)} kWh battery.
              Pays back in ${fmt(i.design.payback_years, 1)} years.
              ${compareBar(i)}`);
          markers.push({ marker, island: i });
        });
        renderMetric();
        const t = p.totals;
        document.getElementById("totals").innerHTML = `
          <p><small>Islands in this portfolio</small><span class="big">${t.islands}</span></p>
          <p><small>Diesel saved per year (typical)</small><span class="big">${fmt(t.litres_saved_p50)} L</span>
            <small>at least ${fmt(t.litres_saved_p90)} L in a bad year</small></p>
          <p><small>Money saved per year</small><span class="big">${money(t.money_saved_per_year_p50)}</span></p>
          <p><small>One investment for all of them</small><span class="big">${money(t.purchase_cost)}</span></p>
          <p><small>CO₂ avoided per year</small><span class="big">${fmt(t.tonnes_co2_avoided_p50)} t</span></p>
          <p><small>Island figures are illustrative until measured.</small></p>`;
      } catch (e) {
        document.getElementById("totals").innerHTML = `<p>Couldn't load the islands: ${esc(e.message)}</p>`;
      }
    }
    setTimeout(() => map.invalidateSize(), 50);
  }
  tabHandlers.map = show;
})();
