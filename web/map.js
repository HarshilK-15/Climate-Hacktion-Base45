// Pacific map + portfolio totals. Owner: Mech B (data: Data Sci).
(() => {
  let map = null;
  async function show() {
    if (!map) {
      map = L.map("map", { worldCopyJump: false }).setView([-12, 178], 4);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 10, attribution: "© OpenStreetMap contributors" }).addTo(map);
      try {
        const p = await api("/api/portfolio");
        p.islands.forEach((i) => {
          const lon = i.site.lon < 0 ? i.site.lon + 360 : i.site.lon;   // keep the Pacific together
          L.circleMarker([i.site.lat, lon], { radius: 9 + Math.sqrt(i.design.litres_saved_p50) / 40,
            color: COLORS.lagoon, fillColor: COLORS.sun, fillOpacity: 0.9, weight: 2 })
            .addTo(map)
            .bindPopup(`<strong>${esc(i.site.name)}, ${esc(i.site.country)}</strong><br>
              ${fmt(i.design.solar_kw, 1)} kW solar + ${fmt(i.design.battery_kwh, 1)} kWh battery<br>
              Saves ${fmt(i.design.litres_saved_p50)} L of diesel a year (${fmt(i.design.percent_diesel_cut_p50, 1)}%)<br>
              Costs ${money(i.design.purchase_cost)}, pays back in ${fmt(i.design.payback_years, 1)} years`);
        });
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
