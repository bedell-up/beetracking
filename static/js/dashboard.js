/* ── BeeTracker Dashboard ─────────────────────────────────────────────────── */

const AUTO_REFRESH_MS = 5 * 60 * 1000;

let stationChart = null;
let timeChart    = null;
let autoTimer    = null;

// Dynamic group state (updated from API on every loadAll)
let currentGroups = { group1: [], group2: [] };

// Snapshot of last loaded data, used for PDF export
let _snap = { stationsData: null, movements: null, recent: null, cacheInfo: null };

// Setup tab working state (separate from currentGroups so changes aren't live until saved)
let setupState = { group1: [], group2: [], all_stations: [] };

// ── Helpers ──────────────────────────────────────────────────────────────────

function isG2(station) { return currentGroups.group2.includes(station.toUpperCase()); }

function fmtDatetime(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (isNaN(d)) return isoStr;
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function fmtDate(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function qs(sel) { return document.querySelector(sel); }

function dateParams() {
  const s = qs("#date-start").value;
  const e = qs("#date-end").value;
  const p = new URLSearchParams();
  if (s) p.set("start", s + "T00:00:00");
  if (e) p.set("end",   e + "T23:59:59");
  p.set("granularity", qs("#granularity-select").value);
  return p;
}

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchTab(tabName) {
  qs("#tab-dashboard").hidden = (tabName !== "dashboard");
  qs("#tab-setup").hidden     = (tabName !== "setup");
  qs("#tab-project").hidden   = (tabName !== "project");
  qs("#filter-bar").hidden    = (tabName !== "dashboard");

  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });

  if (tabName === "setup") loadSetup();
}

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// ── Station bar chart ─────────────────────────────────────────────────────────

function buildStationChart(counts) {
  const labels = Object.keys(counts);
  const values = Object.values(counts);
  const colors = labels.map(l => isG2(l) ? "#3b82f6" : "#22c55e");
  const ctx = qs("#chart-station").getContext("2d");

  if (stationChart) stationChart.destroy();
  stationChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "Detections", data: values, backgroundColor: colors, borderRadius: 6, borderSkipped: false }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => `Station ${items[0].label}`,
            label: item => ` ${item.raw} detections`,
          },
        },
      },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#f3f4f6" } },
        x: { grid: { display: false } },
      },
    },
  });
}

// ── Time series line chart ────────────────────────────────────────────────────

function buildTimeChart(series) {
  const labels = series.map(d => d.label);
  const values = series.map(d => d.count);
  const ctx = qs("#chart-time").getContext("2d");

  if (timeChart) timeChart.destroy();
  timeChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Detections",
        data: values,
        borderColor: "#16a34a",
        backgroundColor: "rgba(22,163,74,.08)",
        borderWidth: 2.5,
        pointRadius: values.length > 60 ? 0 : 3,
        pointHoverRadius: 5,
        fill: true,
        tension: 0.35,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { mode: "index", intersect: false },
      },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#f3f4f6" } },
        x: { ticks: { maxTicksLimit: 10, maxRotation: 30, font: { size: 10 } }, grid: { display: false } },
      },
    },
  });
}

// ── Station status pills ──────────────────────────────────────────────────────

function renderStationStatus(statusMap, group1, group2) {
  const container = qs("#station-grid");
  container.innerHTML = "";

  function renderGroup(stations, label) {
    const lbl = document.createElement("div");
    lbl.className = "station-group-label";
    lbl.textContent = label;
    container.appendChild(lbl);

    stations.forEach(s => {
      const info = statusMap[s] || { state: "inactive", last_seen: null };
      const pill = document.createElement("div");
      pill.className = `station-pill ${info.state} ${isG2(s) ? "g2-pill" : ""}`;
      const lastText = info.last_seen ? fmtDatetime(info.last_seen) : "No data";
      pill.innerHTML = `
        <div class="station-dot"></div>
        <div>
          <div class="station-pill-label">Station ${s}</div>
          <div class="station-pill-sub">${lastText}</div>
        </div>`;
      container.appendChild(pill);
    });
  }

  renderGroup(group1, "Group 1");
  renderGroup(group2, "Group 2");
}

// ── Movements table ───────────────────────────────────────────────────────────

function renderMovements(movements) {
  const tbody = qs("#movements-body");
  qs("#movement-count").textContent = movements.length;

  if (!movements.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-row">No cross-campus movements found in this date range.</td></tr>`;
    return;
  }

  tbody.innerHTML = movements.map(m => {
    const g1chips = m.group1_stations.map(s => `<span class="station-chip">${s}</span>`).join("");
    const g2chips = m.group2_stations.map(s => `<span class="station-chip g2">${s}</span>`).join("");
    return `<tr>
      <td><span class="tag-chip">${m.tag_id}</span></td>
      <td>${m.total_detections.toLocaleString()}</td>
      <td>${g1chips}</td>
      <td>${g2chips}</td>
      <td style="white-space:nowrap;font-size:.75rem">${fmtDatetime(m.first_seen)}</td>
      <td style="white-space:nowrap;font-size:.75rem">${fmtDatetime(m.last_seen)}</td>
    </tr>`;
  }).join("");
}

// ── Recent feed ───────────────────────────────────────────────────────────────

function renderFeed(items) {
  const wrap = qs("#feed-wrap");
  if (!items.length) {
    wrap.innerHTML = `<div class="feed-empty">No detections to display.</div>`;
    return;
  }
  wrap.innerHTML = items.map(r => {
    const g2 = isG2(r.station);
    return `<div class="feed-item">
      <div class="feed-station ${g2 ? "g2" : ""}">${r.station}</div>
      <div class="feed-tag">${r.tag_id}</div>
      <div class="feed-time">${fmtDatetime(r.ts_iso)}</div>
    </div>`;
  }).join("");
}

// ── Summary stats ─────────────────────────────────────────────────────────────

function updateStats(counts, statusMap, movementsLen, recent) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const activeStations = Object.values(statusMap).filter(v => v.state === "active").length;
  const uniqueBees = new Set(recent.map(r => r.tag_id)).size;

  qs("#stat-total").textContent         = total.toLocaleString();
  qs("#stat-bees").textContent          = uniqueBees.toLocaleString();
  qs("#stat-active-stations").textContent = `${activeStations} / ${Object.keys(statusMap).length}`;
  qs("#stat-travelers").textContent     = movementsLen.toLocaleString();
}

// ── Cache status ──────────────────────────────────────────────────────────────

function updateCacheBadge(info) {
  const el = qs("#cache-status");
  if (info.error) {
    el.textContent = `⚠ Error: ${info.error.slice(0, 60)}`;
    el.style.color = "#f87171";
    return;
  }
  if (!info.fetched_at) { el.textContent = "No data yet"; return; }
  const d = new Date(info.fetched_at);
  el.textContent = `${info.row_count.toLocaleString()} rows · fetched ${fmtDatetime(d.toISOString())}`;
  el.style.color = "";
  qs("#last-updated").textContent = `Last data fetch: ${d.toLocaleString()}`;
}

// ── Core load ─────────────────────────────────────────────────────────────────

async function loadAll() {
  const params = dateParams();

  try {
    const [stationsData, series, movements, recent, cacheInfo] = await Promise.all([
      fetchJSON(`/api/stations?${params}`),
      fetchJSON(`/api/timeseries?${params}`),
      fetchJSON(`/api/movements?${params}`),
      fetchJSON(`/api/recent`),
      fetchJSON(`/api/status`),
    ]);

    currentGroups = { group1: stationsData.group1, group2: stationsData.group2 };
    _snap = { stationsData, movements, recent, cacheInfo };

    buildStationChart(stationsData.counts);
    buildTimeChart(series);
    renderStationStatus(stationsData.status, stationsData.group1, stationsData.group2);
    renderMovements(movements);
    renderFeed(recent);
    updateStats(stationsData.counts, stationsData.status, movements.length, recent);
    updateCacheBadge(cacheInfo);

  } catch (err) {
    console.error("Dashboard load error:", err);
    qs("#cache-status").textContent = `⚠ ${err.message}`;
    qs("#cache-status").style.color = "#f87171";
  }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────

function resetAutoRefresh() {
  clearInterval(autoTimer);
  autoTimer = setInterval(loadAll, AUTO_REFRESH_MS);
}

// ── Setup tab ─────────────────────────────────────────────────────────────────

async function loadSetup() {
  const data = await fetchJSON("/api/groups");
  setupState = { group1: [...data.group1], group2: [...data.group2], all_stations: data.all_stations };
  renderSetup();
}

function renderSetup() {
  const g1Set = new Set(setupState.group1);
  const g2Set = new Set(setupState.group2);
  const unassigned = setupState.all_stations.filter(s => !g1Set.has(s) && !g2Set.has(s));

  renderZone("zone-g1", setupState.group1, "chip-g1");
  renderZone("zone-g2", setupState.group2, "chip-g2");
  renderZone("zone-unassigned", unassigned, "chip-unassigned");
}

function renderZone(zoneId, stations, chipClass) {
  const zone = document.getElementById(zoneId);
  if (!stations.length) {
    zone.innerHTML = `<div class="setup-drop-hint">Drop station here</div>`;
    return;
  }
  zone.innerHTML = stations.map(s => `
    <div class="setup-chip ${chipClass}" draggable="true" data-station="${s}">
      Station ${s}
    </div>`).join("");
}

// Drag-and-drop via event delegation on document
let _dragStation = null;

document.addEventListener("dragstart", e => {
  const chip = e.target.closest(".setup-chip");
  if (!chip) return;
  _dragStation = chip.dataset.station;
  chip.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
});

document.addEventListener("dragend", e => {
  const chip = e.target.closest(".setup-chip");
  if (chip) chip.classList.remove("dragging");
  document.querySelectorAll(".setup-drop-zone").forEach(z => z.classList.remove("drag-over"));
  _dragStation = null;
});

document.addEventListener("dragover", e => {
  const zone = e.target.closest(".setup-drop-zone");
  if (!zone) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  document.querySelectorAll(".setup-drop-zone").forEach(z => z.classList.remove("drag-over"));
  zone.classList.add("drag-over");
});

document.addEventListener("dragleave", e => {
  const zone = e.target.closest(".setup-drop-zone");
  if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove("drag-over");
});

document.addEventListener("drop", e => {
  const zone = e.target.closest(".setup-drop-zone");
  if (!zone || !_dragStation) return;
  e.preventDefault();
  zone.classList.remove("drag-over");
  moveStation(_dragStation, zone.dataset.zone);
});

function moveStation(station, targetZone) {
  setupState.group1 = setupState.group1.filter(s => s !== station);
  setupState.group2 = setupState.group2.filter(s => s !== station);
  if (targetZone === "g1") setupState.group1.push(station);
  else if (targetZone === "g2") setupState.group2.push(station);
  renderSetup();
}

async function saveGroups() {
  const btn = qs("#btn-save-groups");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const r = await fetch("/api/groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group1: setupState.group1, group2: setupState.group2 }),
    });
    const saved = await r.json();
    setupState.group1 = saved.group1;
    setupState.group2 = saved.group2;
    currentGroups = { group1: saved.group1, group2: saved.group2 };

    const msg = qs("#setup-saved-msg");
    msg.classList.add("visible");
    setTimeout(() => msg.classList.remove("visible"), 3000);

    await loadAll();
  } finally {
    btn.disabled = false;
    btn.textContent = "Save Configuration";
  }
}

async function resetGroups() {
  if (!confirm("Reset groups to the original A–D / E–H configuration?")) return;
  setupState.group1 = ["A", "B", "C", "D"];
  setupState.group2 = ["E", "F", "G", "H"];
  renderSetup();
}

qs("#btn-save-groups").addEventListener("click", saveGroups);
qs("#btn-reset-groups").addEventListener("click", resetGroups);

// ── PDF export ────────────────────────────────────────────────────────────────

function exportPDF() {
  const { stationsData, movements, recent, cacheInfo } = _snap;
  if (!stationsData) { alert("No data loaded yet — please wait for the dashboard to finish loading."); return; }

  const now = new Date().toLocaleString("en-US", { dateStyle: "long", timeStyle: "short" });
  const fetchedAt = cacheInfo?.fetched_at ? new Date(cacheInfo.fetched_at).toLocaleString() : "—";

  const total = Object.values(stationsData.counts).reduce((a, b) => a + b, 0);
  const activeCount = Object.values(stationsData.status).filter(v => v.state === "active").length;
  const totalStations = Object.keys(stationsData.status).length;
  const uniqueBees = new Set(recent.map(r => r.tag_id)).size;

  const g1 = new Set(stationsData.group1);
  const g2 = new Set(stationsData.group2);

  const stationRows = Object.entries(stationsData.counts).map(([s, count]) => {
    const group = g1.has(s) ? "Group 1" : g2.has(s) ? "Group 2" : "Unassigned";
    const status = stationsData.status[s] || {};
    const lastSeen = status.last_seen ? fmtDatetime(status.last_seen) : "—";
    const active = status.state === "active" ? "●" : "○";
    return `<tr>
      <td>Station ${s}</td>
      <td>${group}</td>
      <td>${count.toLocaleString()}</td>
      <td>${active} ${lastSeen}</td>
    </tr>`;
  }).join("");

  const movementRows = movements.length
    ? movements.map(m => `<tr>
        <td>${m.tag_id}</td>
        <td>${m.total_detections}</td>
        <td>${m.group1_stations.join(", ")}</td>
        <td>${m.group2_stations.join(", ")}</td>
        <td>${fmtDatetime(m.first_seen)}</td>
        <td>${fmtDatetime(m.last_seen)}</td>
      </tr>`).join("")
    : `<tr><td colspan="6" style="text-align:center;color:#9ca3af">No cross-campus movements in this period</td></tr>`;

  const recentRows = recent.map(r => `<tr>
    <td>Station ${r.station}</td>
    <td>${r.tag_id}</td>
    <td>${fmtDatetime(r.ts_iso)}</td>
  </tr>`).join("");

  const startVal = qs("#date-start").value;
  const endVal   = qs("#date-end").value;
  const rangeNote = (startVal || endVal)
    ? `<span>Date filter: ${startVal || "—"} → ${endVal || "—"}</span>`
    : "";

  document.getElementById("print-area").innerHTML = `
    <div class="pr-header">
      <div class="pr-title">Bombus vosnesenskii Tracking Report</div>
      <div class="pr-subtitle">AprilTag Detection Dashboard · University of Portland</div>
      <div class="pr-meta">Generated: ${now} &nbsp;·&nbsp; Data as of: ${fetchedAt} ${rangeNote}</div>
    </div>

    <div class="pr-section">
      <div class="pr-section-title">Summary</div>
      <div class="pr-summary-grid">
        <div class="pr-summary-cell"><div class="pr-summary-val">${total.toLocaleString()}</div><div class="pr-summary-lbl">Total Detections</div></div>
        <div class="pr-summary-cell"><div class="pr-summary-val">${uniqueBees}</div><div class="pr-summary-lbl">Unique Bees</div></div>
        <div class="pr-summary-cell"><div class="pr-summary-val">${activeCount}/${totalStations}</div><div class="pr-summary-lbl">Active Stations (24 h)</div></div>
        <div class="pr-summary-cell"><div class="pr-summary-val">${movements.length}</div><div class="pr-summary-lbl">Cross-Campus Travelers</div></div>
      </div>
    </div>

    <div class="pr-section">
      <div class="pr-section-title">Station Detection Counts</div>
      <table class="pr-table">
        <thead><tr><th>Station</th><th>Group</th><th>Detections</th><th>Last Active</th></tr></thead>
        <tbody>${stationRows}</tbody>
      </table>
    </div>

    <div class="pr-section">
      <div class="pr-section-title">Cross-Campus Movements (${movements.length} bees)</div>
      <table class="pr-table">
        <thead><tr><th>Tag ID</th><th>Detections</th><th>Group 1 Stations</th><th>Group 2 Stations</th><th>First Seen</th><th>Last Seen</th></tr></thead>
        <tbody>${movementRows}</tbody>
      </table>
    </div>

    <div class="pr-section">
      <div class="pr-section-title">Recent Detections (last ${recent.length})</div>
      <table class="pr-table">
        <thead><tr><th>Station</th><th>Tag ID</th><th>Timestamp</th></tr></thead>
        <tbody>${recentRows}</tbody>
      </table>
    </div>

    <div class="pr-footer">BeeTracker · University of Portland · Generated ${now}</div>
  `;

  window.print();
}

qs("#btn-export-pdf").addEventListener("click", exportPDF);

// ── Event listeners ───────────────────────────────────────────────────────────

qs("#btn-refresh").addEventListener("click", async () => {
  const btn = qs("#btn-refresh");
  const svg = btn.querySelector("svg");
  btn.disabled = true;
  svg.classList.add("spin");
  try {
    await fetch("/api/refresh", { method: "POST" });
    await loadAll();
    resetAutoRefresh();
  } finally {
    btn.disabled = false;
    svg.classList.remove("spin");
  }
});

qs("#btn-apply").addEventListener("click", () => { loadAll(); resetAutoRefresh(); });

qs("#btn-clear").addEventListener("click", () => {
  qs("#date-start").value = "";
  qs("#date-end").value   = "";
  loadAll();
  resetAutoRefresh();
});

// ── Boot ──────────────────────────────────────────────────────────────────────

loadAll();
resetAutoRefresh();
