// Guard alerts: polls every 5 seconds. Owner: Mech B (rules: Data Sci, messages: Mech A).
(() => {
  async function refresh() {
    try {
      const list = await api("/api/alerts");
      const real = list.filter((a) => a.type !== "no_data");
      const badge = document.getElementById("alert-count");
      badge.hidden = real.length === 0;
      badge.textContent = real.length;
      document.getElementById("alert-list").innerHTML = list.length ? list.map((a) => `
        <article class="alert ${a.severity}">
          <div><h3>${esc(a.title)}</h3>
            <p class="facts">${Object.entries(a.facts).map(([k, v]) => `${esc(k.replaceAll("_", " "))}: ${esc(v)}`).join("; ")}</p></div>
          <div class="sms"><small>Text to technician</small>${esc(a.sms)}</div>
        </article>`).join("") : `<p class="explain">All clear. No problems in the last 30 minutes.</p>`;
    } catch (e) { /* server not reachable: the live screen already says so */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
