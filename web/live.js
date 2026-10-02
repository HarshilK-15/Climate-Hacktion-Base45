// Live power screen: polls the server every 2 seconds. Owner: Mech B.
(() => {
  let minutes = 15;
  const chart = new Chart(document.getElementById("live-chart"), {
    type: "line",
    data: { datasets: [{ label: "Watts", data: [], borderColor: COLORS.lagoon, backgroundColor: "rgba(15,76,92,.12)",
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

  async function refresh() {
    try {
      const [status, rows] = await Promise.all([api("/api/status"), api(`/api/readings?minutes=${minutes}`)]);
      const st = document.getElementById("live-status");
      const txt = document.getElementById("live-status-text");
      if (!status.last) {
        st.className = "status"; txt.textContent = "Waiting for the sensor";
      } else if (status.online) {
        st.className = "status online"; txt.textContent = `Live, updated ${status.age_s}s ago`;
      } else {
        st.className = "status offline"; txt.textContent = `No signal for ${fmt(status.age_s)}s`;
      }
      if (status.last) {
        const big = status.last.watts >= 10000;
        document.getElementById("live-watts").textContent = big ? fmt(status.last.watts / 1000, 1) : fmt(status.last.watts);
        document.querySelector(".watts .unit").textContent = big ? "kW" : "W";
        document.getElementById("live-amps").textContent = fmt(status.last.amps, 2);
        const sim = /^(sim|fake)-/.test(status.last.device_id);
        const dev = document.getElementById("live-device");
        dev.textContent = sim ? `Simulated sensor (${status.last.device_id}): no hardware, model data`
                              : `Sensor: ${status.last.device_id}`;
        dev.classList.toggle("warn", sim);
      }
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
    } catch (e) {
      document.getElementById("live-status-text").textContent = "Can't reach the server: " + e.message;
    }
  }
  refresh();
  setInterval(refresh, 2000);
})();
