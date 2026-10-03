// Guard alerts: the real single-sensor feed every 5s, plus a simulated fleet-wide
// watch over the demo districts from the Live tab. Owner: Mech B (rules: Data Sci,
// messages: Mech A).
(() => {
  // A handful of simulated "output is dipping" incidents, each solvable in a click.
  const FLEET_INCIDENTS = [
    { id: "nanumea", dropPercent: 18, severity: "medium", minutesAgo: 6 },
    { id: "malekula", dropPercent: 34, severity: "high", minutesAgo: 2 },
    { id: "abaiang", dropPercent: 9, severity: "low", minutesAgo: 14 },
    { id: "vavau", dropPercent: 22, severity: "medium", minutesAgo: 9 },
    { id: "tanna", dropPercent: 41, severity: "high", minutesAgo: 1 },
  ];
  const ACTIONS = [
    { key: "backup", label: "Kick in backup generator", result: "Backup generator engaged — output back to normal in about 4 minutes." },
    { key: "battery", label: "Draw from battery reserve", result: "Battery reserve covering the gap — no generator needed." },
    { key: "tech", label: "Flag for a technician visit", result: "Logged for the next site visit; the technician has been texted." },
    { key: "dismiss", label: "Dismiss as a false reading", result: "Dismissed as a false reading." },
  ];
  const resolved = {};   // incident id -> { label, result }

  function renderFleet() {
    document.getElementById("fleet-alert-list").innerHTML = FLEET_INCIDENTS.map((inc) => {
      const loc = locById(inc.id);
      const fix = resolved[inc.id];
      const expectedKwNum = loc.genKw * diurnalFraction(localHourAt(loc, new Date()));
      const expectedKw = expectedKwNum.toFixed(1);
      const seenKw = (expectedKwNum * (1 - inc.dropPercent / 100)).toFixed(1);
      if (fix) {
        return `<article class="alert ${inc.severity} is-resolved">
          <div><h3>${esc(loc.district)}, ${esc(loc.country)}</h3>
            <p class="facts">${esc(fix.result)}</p></div>
        </article>`;
      }
      return `<article class="alert ${inc.severity}">
        <div><h3>${esc(loc.district)}, ${esc(loc.country)}: output down ${inc.dropPercent}%</h3>
          <p class="facts">detected ${inc.minutesAgo} min ago; expected about ${expectedKw} kW for this time of
            day, seeing about ${seenKw} kW</p>
          <div class="fix-actions">${ACTIONS.map((a) =>
            `<button type="button" class="fix-btn" data-id="${inc.id}" data-key="${a.key}">${a.label}</button>`).join("")}</div>
        </div>
      </article>`;
    }).join("");
    document.querySelectorAll(".fix-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = ACTIONS.find((a) => a.key === btn.dataset.key);
        const card = btn.closest(".alert");
        card.classList.add("is-leaving");
        setTimeout(() => {
          resolved[btn.dataset.id] = action;
          renderFleet();
          updateBadge();
        }, 280);
      });
    });
  }

  function updateBadge() {
    const openFleet = FLEET_INCIDENTS.filter((i) => !resolved[i.id]).length;
    const badge = document.getElementById("alert-count");
    const real = Number(badge.dataset.real || 0);
    const total = real + openFleet;
    badge.hidden = total === 0;
    badge.textContent = total;
  }

  async function refresh() {
    try {
      const list = await api("/api/alerts");
      const real = list.filter((a) => a.type !== "no_data");
      document.getElementById("alert-count").dataset.real = real.length;
      updateBadge();
      document.getElementById("alert-list").innerHTML = list.length ? list.map((a) => `
        <article class="alert ${a.severity}">
          <div><h3>${esc(a.title)}</h3>
            <p class="facts">${Object.entries(a.facts).map(([k, v]) => `${esc(k.replaceAll("_", " "))}: ${esc(v)}`).join("; ")}</p></div>
          <div class="sms"><small>Text to technician</small>${esc(a.sms)}</div>
        </article>`).join("") : `<p class="explain">All clear. No problems in the last 30 minutes.</p>`;
    } catch (e) { /* server not reachable: the live screen already says so */ }
  }
  renderFleet();
  updateBadge();
  refresh();
  setInterval(refresh, 5000);
})();
