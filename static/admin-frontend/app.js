const API = "/api/admin";
const tokenKey = "eventer_admin_token";

const state = { token: sessionStorage.getItem(tokenKey), selectedBuildingId: null, selectedSourceId: null };
let tables = {};

function authHeaders() {
  return state.token ? { Authorization: `Token ${state.token}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, { ...options, headers: { ...authHeaders(), ...(options.headers || {}) } });
  if (res.status === 401 || res.status === 403) { logout(); throw new Error("Unauthorized"); }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showPage(name) {
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.getElementById(`${name}-page`).classList.remove("hidden");
  document.querySelectorAll("#sidebar nav button[data-page]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === name);
  });
  if (name === "dashboard") loadDashboard();
  if (name === "events") loadEvents();
  if (name === "unresolved") loadUnresolved();
  if (name === "buildings") loadBuildings();
  if (name === "aliases") loadSuggestions();
  if (name === "sources") loadSources();
}

function showHelpSection(sectionId) {
  showPage("help");
  requestAnimationFrame(() => {
    const el = document.getElementById(sectionId);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function logout() {
  state.token = null;
  sessionStorage.removeItem(tokenKey);
  document.getElementById("sidebar").style.display = "none";
  showPage("login");
}

function login(token) {
  state.token = token;
  sessionStorage.setItem(tokenKey, token);
  document.getElementById("sidebar").style.display = "block";
  showPage("dashboard");
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const res = await fetch(`${API}/login/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: fd.get("username"), password: fd.get("password") }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.non_field_errors?.[0] || "Login failed");
    login(data.token);
  } catch (err) {
    document.getElementById("login-error").textContent = err.message;
  }
});

document.getElementById("logout-btn").addEventListener("click", logout);
document.querySelectorAll("#sidebar nav button[data-page]").forEach((btn) => {
  btn.addEventListener("click", () => showPage(btn.dataset.page));
});

document.querySelectorAll(".help-link").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    showHelpSection(link.dataset.help);
  });
});

async function loadDashboard() {
  const data = await api("/dashboard/");
  document.getElementById("dashboard-stats").innerHTML = `
    <div class="stat-card">Events Today<strong>${data.events_today}</strong></div>
    <div class="stat-card">Active This Week<strong>${data.events_this_week}</strong></div>
    <div class="stat-card">Scrape Health (24h)<strong>${data.scrape_health_pct}%</strong></div>
    <div class="stat-card">Unresolved Locations<strong>${data.unresolved_location_count}</strong></div>`;
  document.getElementById("recent-scrapes").innerHTML = data.recent_scrapes.map((s) =>
    `<div class="stat-card"><strong>${s.label}</strong> — ${s.last_scrape_status || "n/a"}<br><small>${s.last_scraped_at || ""}</small><br>${s.last_scrape_log || ""}</div>`
  ).join("") || "<p>No recent scrapes.</p>";
}

function destroyTable(name) {
  if (tables[name]) { tables[name].destroy(); delete tables[name]; }
}

async function loadEvents() {
  const data = await api("/events/");
  destroyTable("events");
  tables.events = new Tabulator("#events-table", {
    data, layout: "fitColumns", selectable: true, height: "500px",
    columns: [
      { title: "Name", field: "event_name", editor: "input" },
      { title: "Category", field: "category", editor: "input" },
      { title: "Start", field: "start_time" },
      { title: "Active", field: "is_active", formatter: "tickCross" },
      { title: "Verified", field: "is_verified", formatter: "tickCross" },
      { title: "Save", formatter: () => "Save", width: 70, cellClick: async (_, cell) => {
        const row = cell.getRow().getData();
        await api(`/events/${row.id}/`, { method: "PATCH", body: JSON.stringify(row) });
      }},
    ],
  });
}

async function bulkEventAction(action) {
  const rows = tables.events.getSelectedData();
  if (!rows.length) return alert("Select events first");
  await api("/events/bulk/", { method: "POST", body: JSON.stringify({ action, ids: rows.map((r) => r.id) }) });
  loadEvents();
}

document.getElementById("bulk-verify").addEventListener("click", () => bulkEventAction("verify"));
document.getElementById("bulk-deactivate").addEventListener("click", () => bulkEventAction("deactivate"));
document.getElementById("new-event-btn").addEventListener("click", async () => {
  const name = prompt("Event name?");
  if (!name) return;
  await api("/events/", {
    method: "POST",
    body: JSON.stringify({
      event_name: name,
      start_time: new Date().toISOString(),
      end_time: new Date(Date.now() + 3600000).toISOString(),
      category: "club_org_meeting",
      other_info: {},
      is_active: true,
      is_verified: false,
    }),
  });
  loadEvents();
});

async function loadUnresolved() {
  const data = await api("/events/unresolved/");
  destroyTable("unresolved");
  tables.unresolved = new Tabulator("#unresolved-table", {
    data, layout: "fitColumns", height: "400px",
    columns: [
      { title: "Event", field: "event_name" },
      { title: "Location", field: "unresolved_location" },
      { title: "Assign Building ID", field: "building", editor: "input" },
      { title: "Save Alias", formatter: () => "Assign", cellClick: async (_, cell) => {
        const row = cell.getRow().getData();
        const buildingId = row.building;
        if (!buildingId) return alert("Enter building ID");
        await api(`/events/${row.id}/`, {
          method: "PATCH",
          body: JSON.stringify({ building: parseInt(buildingId, 10), unresolved_location: null }),
        });
        if (confirm("Save as alias for future scrapes?")) {
          await api(`/buildings/${buildingId}/aliases/`, {
            method: "POST",
            body: JSON.stringify({ alias: row.unresolved_location, source: "admin" }),
          });
        }
        loadUnresolved();
      }},
    ],
  });
}

async function loadBuildings() {
  const data = await api("/buildings/");
  destroyTable("buildings");
  tables.buildings = new Tabulator("#buildings-table", {
    data, layout: "fitColumns", height: "350px",
    columns: [
      { title: "ID", field: "id", width: 60 },
      { title: "Name", field: "official_name", editor: "input" },
      { title: "Lat", field: "lat", editor: "number" },
      { title: "Lng", field: "lng", editor: "number" },
      { title: "GeoJSON ID", field: "geojson_id", editor: "input" },
      { title: "Aliases", field: "aliases", formatter: (c) => c.getValue().map((a) => a.alias).join(", ") },
      { title: "Edit Aliases", formatter: () => "Aliases", cellClick: (_, cell) => openAliasPanel(cell.getRow().getData()) },
      { title: "Save", formatter: () => "Save", cellClick: async (_, cell) => {
        const row = cell.getRow().getData();
        await api(`/buildings/${row.id}/`, { method: "PATCH", body: JSON.stringify(row) });
      }},
    ],
  });
}

async function openAliasPanel(building) {
  state.selectedBuildingId = building.id;
  document.getElementById("alias-panel").classList.remove("hidden");
  document.getElementById("alias-building-name").textContent = building.official_name;
  const aliases = await api(`/buildings/${building.id}/aliases/`);
  destroyTable("aliases");
  tables.aliases = new Tabulator("#aliases-table", {
    data: aliases, layout: "fitColumns", height: "200px",
    columns: [
      { title: "Alias", field: "alias" },
      { title: "Source", field: "source" },
      { title: "Delete", formatter: () => "Delete", cellClick: async (_, cell) => {
        const row = cell.getRow().getData();
        await api(`/buildings/${building.id}/aliases/${row.id}/`, { method: "DELETE" });
        openAliasPanel(building);
      }},
    ],
  });
}

document.getElementById("add-alias-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  await api(`/buildings/${state.selectedBuildingId}/aliases/`, {
    method: "POST",
    body: JSON.stringify({ alias: fd.get("alias"), source: fd.get("source") }),
  });
  e.target.reset();
  const building = await api(`/buildings/${state.selectedBuildingId}/`);
  openAliasPanel(building);
});

document.getElementById("new-building-btn").addEventListener("click", async () => {
  const name = prompt("Official building name?");
  if (!name) return;
  await api("/buildings/", {
    method: "POST",
    body: JSON.stringify({ official_name: name, lat: 43.7, lng: -72.29, geojson_id: name.toLowerCase().replace(/\s+/g, "-") }),
  });
  loadBuildings();
});

async function loadSuggestions() {
  const data = await api("/aliases/suggestions/");
  destroyTable("suggestions");
  tables.suggestions = new Tabulator("#suggestions-table", {
    data: data.map((d) => ({ location: d.unresolved_location, count: d.count })),
    layout: "fitColumns",
    columns: [
      { title: "Unresolved Location", field: "location" },
      { title: "Count", field: "count", width: 80 },
    ],
  });
}

async function loadSources() {
  const data = await api("/scrape-sources/");
  destroyTable("sources");
  tables.sources = new Tabulator("#sources-table", {
    data, layout: "fitColumns", height: "300px",
    columns: [
      { title: "Label", field: "label" },
      { title: "URL", field: "url" },
      { title: "Active", field: "is_active", formatter: "tickCross" },
      { title: "Status", field: "last_scrape_status" },
      { title: "Last Scraped", field: "last_scraped_at" },
      { title: "Edit", formatter: () => "Edit", cellClick: (_, cell) => openSourceEditor(cell.getRow().getData()) },
    ],
  });
}

function openSourceEditor(source) {
  state.selectedSourceId = source.id;
  document.getElementById("source-editor").classList.remove("hidden");
  const form = document.getElementById("source-form");
  form.id.value = source.id;
  form.label.value = source.label;
  form.url.value = source.url;
  form.scrape_interval_hours.value = source.scrape_interval_hours;
  form.is_active.checked = source.is_active;
  document.getElementById("selector-config").value = JSON.stringify(source.selector_config || {}, null, 2);
  document.getElementById("source-logs").textContent = source.last_scrape_log || "";
}

document.getElementById("source-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  let selector_config = {};
  try { selector_config = JSON.parse(document.getElementById("selector-config").value); }
  catch { return alert("Invalid JSON in selector config"); }
  const payload = {
    label: fd.get("label"),
    url: fd.get("url"),
    scrape_interval_hours: parseInt(fd.get("scrape_interval_hours"), 10),
    is_active: fd.get("is_active") === "on",
    selector_config,
  };
  const id = fd.get("id");
  if (id) await api(`/scrape-sources/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
  else await api("/scrape-sources/", { method: "POST", body: JSON.stringify(payload) });
  loadSources();
});

document.getElementById("new-source-btn").addEventListener("click", () => {
  openSourceEditor({ id: "", label: "", url: "", scrape_interval_hours: 3, is_active: true, selector_config: {} });
});

document.getElementById("run-now-btn").addEventListener("click", async () => {
  if (!state.selectedSourceId) return;
  await api(`/scrape-sources/${state.selectedSourceId}/run-now/`, { method: "POST", body: "{}" });
  alert("Scrape queued");
});

document.getElementById("view-logs-btn").addEventListener("click", async () => {
  if (!state.selectedSourceId) return;
  const logs = await api(`/scrape-sources/${state.selectedSourceId}/logs/`);
  document.getElementById("source-logs").textContent = JSON.stringify(logs, null, 2);
});

if (state.token) {
  document.getElementById("sidebar").style.display = "block";
  showPage("dashboard");
} else {
  document.getElementById("sidebar").style.display = "none";
  showPage("login");
}
