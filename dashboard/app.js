const state = {
  overview: null,
  devices: [],
  mapDevices: [],
  scanners: [],
  events: [],
  diagnostics: null,
  runtimeConfig: { app_timezone: "Asia/Jakarta" },
  currentView: "overview",
  selectedMapScannerId: null,
  locationPrompted: false,
  map: null,
  mapLayers: null,
  mapAddressKey: null,
  selectedMapDeviceId: null,
  live: null,
};

let gpsWatchId = null;
let lastGpsSaveTime = 0;
let lastGpsPosition = null;
const GPS_SAVE_INTERVAL_MS = 10000;
const GPS_MOVE_THRESHOLD_M = 30;

const titles = {
  overview: "Overview",
  devices: "Live Devices",
  scanners: "Scanner Management",
  locations: "Location View",
  events: "Event Timeline",
  diagnostics: "Diagnostics",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));



async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${text}`);
  }
  return response.json();
}

function formatDate(value) {
  if (!value) return "-";
  const normalized = typeof value === "string" && /^\d{4}-\d{2}-\d{2}T.*(?<!Z)(?<![+-]\d{2}:\d{2})$/.test(value)
    ? `${value}Z`
    : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("id-ID", {
    timeZone: state.runtimeConfig?.app_timezone || "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(date);
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(digits);
}

function formatConfidence(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${formatNumber(Number(value) * 100, 0)}%`;
}

function formatProximity(device) {
  const model = device.proximity_model || {};
  const band = device.proximity_band || model.band || "unknown";
  const metric = Number(model.rssi_metric);
  const reliability = Number(model.signal_reliability);
  const distance = Number(model.estimated_distance_m ?? device.estimated_distance_m);
  const evidence = Number.isFinite(metric)
    ? `RSSI change ${formatNumber(metric, 2)} · reliability ${formatNumber(reliability * 100, 0)}%`
    : `RSSI window ${model.window_size || 5} samples not ready`;
  const distanceText = Number.isFinite(distance) ? `model ${formatNumber(distance, 1)} m` : "distance unavailable";
  return `${badge(band)} <span class="proximity-meta">${escapeHtml(rssiSignalLabel(device.smoothed_rssi))}; ${escapeHtml(distanceText)} · ${escapeHtml(evidence)}</span>`;
}

function rssiSignalLabel(rssi) {
  if (!Number.isFinite(Number(rssi))) return "RSSI unavailable";
  if (Number(rssi) >= -60) return "RSSI strong";
  if (Number(rssi) >= -75) return "RSSI moderate";
  if (Number(rssi) >= -88) return "RSSI weak";
  return "RSSI very weak";
}

function badge(value) {
  const text = value || "unknown";
  const label = String(text).replaceAll("_", " ");
  let tone = "";
  if (["online", "active", "returned", "signal_stable", "signal_strong"].includes(text)) tone = "good";
  if (["temporarily_missing", "probably_moving", "signal_moderate", "signal_weak"].includes(text)) tone = "warn";
  if (["signal_very_weak"].includes(text)) tone = "danger";
  if (["offline", "moving", "ignored"].includes(text)) tone = "danger";
  return `<span class="badge ${tone}">${escapeHtml(label)}</span>`;
}

function statusDot(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const tone = normalized === "offline" ? "offline" : normalized === "temporarily_missing" ? "warning" : "online";
  return `<span class="device-status-dot ${tone}" aria-label="${escapeHtml(normalized)}"></span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function haversineDistance(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function setView(view) {
  state.currentView = view;
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === view));
  $$(".nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#viewTitle").textContent = titles[view];
  refreshView(view);
}

async function refreshAll() {
  await loadRuntimeConfig();
  await Promise.allSettled([
    loadOverview(),
    loadDevices(),
    loadMapDevices(),
    loadScanners(),
    loadEvents(),
    loadDiagnostics(),
  ]);
  renderLocations();
}

async function loadRuntimeConfig() {
  try {
    state.runtimeConfig = await api("/api/runtime-config");
  } catch {
    state.runtimeConfig = { app_timezone: "Asia/Jakarta" };
  }
}

async function refreshView(view = state.currentView) {
  if (view === "overview") await loadOverview();
  if (view === "devices") await loadDevices();
  if (view === "scanners") await loadScanners();
  if (view === "locations") {
    await Promise.allSettled([loadMapDevices(), loadScanners()]);
    renderLocations();
  }
  if (view === "events") await loadEvents();
  if (view === "diagnostics") await loadDiagnostics();
}

function hasCoordinates(item) {
  return item?.latitude != null && item?.latitude !== "" &&
         item?.longitude != null && item?.longitude !== "" &&
         Number.isFinite(Number(item.latitude)) && 
         Number.isFinite(Number(item.longitude));
}



function deviceBelongsToScannerLocation(device, scanner) {
  return !scanner || device.current_scanner_id === scanner.id;
}

async function loadOverview() {
  state.overview = await api("/api/overview");
  renderOverview();
}

function renderOverview() {
  const data = state.overview || {};
  const metrics = [
    ["Total scanners", data.scanner_total],
    ["Online scanners", data.scanner_online],
    ["Offline scanners", data.scanner_offline],
    ["Active devices", data.active_devices],
    ["Active unresolved IDs", data.active_unresolved_identities],
    ["Moving devices", data.moving_devices],
    ["Temporarily missing", data.missing_devices],
    ["Offline confirmed devices", data.offline_device_records],
    ["Expired random IDs", data.expired_random_identities],
    ["Ignored devices", data.ignored_devices],
    ["Obs/min", data.observation_rate_per_minute],
  ];
  $("#metricGrid").innerHTML = metrics
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value ?? 0}</strong></div>`)
    .join("");
  $("#recentEvents").innerHTML = renderEventItems(data.recent_events || []);
}

async function loadDevices() {
  const params = new URLSearchParams();
  const status = $("#deviceStatusFilter")?.value;
  const includeIgnored = $("#includeIgnored")?.checked;
  const includeExpired = $("#includeExpired")?.checked;
  if (status) params.set("status", status);
  if (includeIgnored) params.set("include_ignored", "true");
  if (includeExpired) params.set("include_expired", "true");
  state.devices = await api(`/api/devices?${params.toString()}`);
  renderDevices();
}

async function loadMapDevices() {
  state.mapDevices = await api("/api/devices");
}

function renderDevices() {
  const query = ($("#deviceSearch")?.value || "").toLowerCase();
  const rows = state.devices
    .filter((device) => {
      const haystack = [
        device.alias,
        device.display_name,
        device.primary_address,
        device.status,
        device.current_zone,
        device.vendor,
        device.category,
        device.gatt_enrichment?.manufacturer_name,
        device.gatt_enrichment?.model_number,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    })
        .map((device) => {
      const name = device.alias || device.display_name || device.primary_address || "Unknown";
      return `<tr data-device-id="${device.id}">
        <td><strong>${escapeHtml(name)}</strong><br><small>${escapeHtml(formatNameSource(device.display_name_source))} · ${escapeHtml(device.primary_address || "-")}</small></td>
        <td>${badge(device.status)}</td>
        <td>${escapeHtml(device.vendor || "-")}<br><small>${escapeHtml(device.category || "-")}</small></td>
        <td>${badge(device.movement_status)}</td>
        <td>${escapeHtml(device.current_scanner_id || "-")}</td>
        <td>${escapeHtml(device.current_zone || "-")}</td>
        <td>${formatProximity(device)}</td>
        <td>${formatNumber(device.smoothed_rssi, 1)} dBm</td>
        <td>${formatDate(device.last_seen_at)}</td>
      </tr>`;
    })
    .join("");
  $("#deviceRows").innerHTML = rows || `<tr><td colspan="9">No devices found.</td></tr>`;
  $$("#deviceRows tr[data-device-id]").forEach((row) => {
    row.addEventListener("click", () => showDeviceDetail(row.dataset.deviceId));
  });
}

async function showDeviceDetail(deviceId) {
  state.selectedMapDeviceId = deviceId;
  const detail = await api(`/api/devices/${deviceId}`);
  const device = detail.device;
  const latestObservation = detail.recent_observations?.[0] || null;
  const gatt = device.gatt_enrichment || null;
  const panel = $("#deviceDetail");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div class="panel-header">
      <h2 id="deviceDialogTitle">${escapeHtml(device.alias || device.display_name || "Device detail")}</h2>
      <div class="detail-actions">
        <button data-action="mark_known">Known</button>
        <button data-action="${device.ignored ? "unignore" : "mark_ignored"}">${device.ignored ? "Unignore" : "Ignore"}</button>
      </div>
    </div>
    <div class="detail-grid">
      ${kv("Status", badge(device.status))}
      ${kv("Movement", badge(device.movement_status))}
      ${kv("SIG Company", escapeHtml(device.vendor ? `${device.vendor}${device.manufacturer_company_id ? ` (${device.manufacturer_company_id})` : ""}` : "Not raw-verified"))}
      ${kv("Category", escapeHtml(device.category || "-"))}
      ${kv("Name source", escapeHtml(formatNameSource(device.display_name_source)))}
      ${kv("GATT status", gatt ? badge(gatt.status) : "Not attempted")}
      ${kv("GATT manufacturer", escapeHtml(gatt?.manufacturer_name || "-"))}
      ${kv("GATT model", escapeHtml(gatt?.model_number || "-"))}
      ${kv("Signal band", formatProximity(device))}
      ${kv("Signal evidence", escapeHtml(device.proximity_model?.confidence_basis || "RSSI window not ready"))}
      ${kv("RSSI change metric", formatNumber(device.proximity_model?.rssi_metric, 2))}
      ${kv("Signal reliability", formatConfidence(device.proximity_model?.signal_reliability))}
      ${kv("RSSI windows", device.proximity_model?.window_ready ? `${device.proximity_model.anchor_count} scanner(s) · ${device.proximity_model.window_size} samples` : "Waiting for two windows")}
      ${kv("Modeled distance", `${formatNumber(device.estimated_distance_m, 1)} m`)}
      ${kv("Distance model status", escapeHtml(device.proximity_model?.distance_model_status || "unknown"))}
      ${kv("RSSI", `${formatNumber(device.smoothed_rssi, 1)} dBm`)}
      ${kv("Radio identity", formatIdentityBasis(device.identity_basis))}
      ${kv("Latest capture", formatCaptureProvenance(latestObservation?.capture_provenance))}
      ${kv("Timestamp evidence", formatTimeProvenance(latestObservation?.time_provenance))}
      ${kv("Location anchor", escapeHtml([device.location_anchor?.zone, device.location_anchor?.scanner_id].filter(Boolean).join(" / ") || "Not available"))}
      ${kv("Anchor recorded", formatDate(device.location_anchor?.anchored_at))}
      ${kv("First seen", formatDate(device.first_seen_at))}
      ${kv("Last seen", formatDate(device.last_seen_at))}
    </div>
    <h3>Signal History</h3>
    <canvas id="signalChart" width="720" height="180"></canvas>
    <h3>Observed Identities</h3>
    <div class="event-list">${detail.observed_identities.map((identity) => `
      <div class="event-item">
        <strong>${escapeHtml(identity.address || "No address")}</strong>
        <span>${escapeHtml(identity.address_type || "-")} ${identity.randomized_address ? "randomized" : ""}</span>
        <span>${escapeHtml(formatManufacturerEvidence(identity))}</span>
        ${identity.manufacturer_profile?.find_my ? `<span>Find My: ${escapeHtml(identity.manufacturer_profile.find_my.payload_type)} · battery ${escapeHtml(identity.manufacturer_profile.find_my.battery_status)}</span>` : ""}
        ${identity.manufacturer_profile?.airdrop ? `<span>Apple Nearby/AirDrop-style payload</span>` : ""}
        <small>${escapeHtml((identity.service_uuids || []).join(", "))}</small>
      </div>`).join("")}</div>
    <h3>Direct Device Information</h3>
    <div class="event-list">${(detail.device_enrichments || []).map((enrichment) => `
      <div class="event-item">
        <strong>${escapeHtml(enrichment.device_name || enrichment.model_number || "BLE GATT attempt")}</strong>
        <span>${badge(enrichment.status)} · ${escapeHtml(enrichment.transport || "ble_gatt")} · ${formatDate(enrichment.enriched_at)}</span>
        <span>${escapeHtml([
          enrichment.manufacturer_name,
          enrichment.model_number,
          enrichment.firmware_revision ? `firmware ${enrichment.firmware_revision}` : null,
          enrichment.hardware_revision ? `hardware ${enrichment.hardware_revision}` : null,
          enrichment.software_revision ? `software ${enrichment.software_revision}` : null,
        ].filter(Boolean).join(" · ") || "No readable standard identity characteristic")}</span>
        ${enrichment.serial_number ? `<span>Serial: ${escapeHtml(enrichment.serial_number)}</span>` : ""}
        ${enrichment.pnp_id ? `<span>PnP ID: ${escapeHtml(enrichment.pnp_id)}</span>` : ""}
        <small>${escapeHtml(enrichment.error_code || `${enrichment.attempt_duration_ms ?? "-"} ms`)}</small>
      </div>`).join("") || "<div class=\"event-item\">No GATT enrichment attempt recorded.</div>"}</div>
    <h3>Identity Correlations</h3>
    <div class="event-list">${(detail.identity_correlations || []).map((correlation) => `
      <div class="event-item">
        <strong>${escapeHtml(formatCorrelationMethod(correlation.method))}</strong>
        <span>${badge(correlation.status)} · ${escapeHtml(correlationEvidenceSummary(correlation))}</span>
        <small>${escapeHtml(correlationMetrics(correlation))}</small>
      </div>`).join("") || "<div class=\"event-item\">No accepted or statistical identity correlation.</div>"}</div>
    <h3>Location History</h3>
    <div class="event-list">${(detail.location_history || []).map((location) => `
      <div class="event-item">
        <strong>${escapeHtml(location.zone || location.scanner_id || "Unknown location")}</strong>
        <span>${escapeHtml(location.scanner_id || "-")} · ${escapeHtml(location.proximity_band || "unknown")} · ${location.details?.updates_current_anchor ? "anchor updated" : "observation only"}</span>
        <small>${formatProximity({ estimated_distance_m: location.estimated_distance_m, proximity_band: location.proximity_band, smoothed_rssi: location.details?.smoothed_rssi, proximity_model: location.details?.proximity_model })} · ${formatDate(location.estimated_at)}</small>
      </div>`).join("") || "<div class=\"event-item\">No location history.</div>"}</div>
    <h3>Device Events</h3>
    <div class="event-list">${renderEventItems(detail.events || [])}</div>
  `;
  panel.querySelectorAll("button[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/devices/correlation", {
        method: "POST",
        body: JSON.stringify({ source_logical_device_id: device.id, action: button.dataset.action }),
      });
      await showDeviceDetail(device.id);
      await loadDevices();
    });
  });
  const dialog = $("#deviceDialog");
  if (!dialog.open) dialog.showModal();
  drawSignalChart(detail.recent_observations || []);
}

function formatIdentityBasis(basis) {
  if (basis === "observed_randomized_address_not_correlated") {
    return "Observed randomized address";
  }
  if (basis === "unresolved_randomized_address") return "Unresolved randomized address";
  if (basis === "observed_stable_address") return "Observed stable address";
  if (basis === "operator_confirmed_identity") return "Operator-confirmed identity";
  if (basis === "gatt_stable_identifier") return "GATT stable identifier";
  if (basis === "correlated_randomized_identity") return "Correlated randomized identity";
  return "Observed address";
}

function formatNameSource(source) {
  if (source === "operator_alias") return "Operator alias";
  if (source === "ble_gatt_device_name") return "BLE GATT Device Name";
  if (source === "advertising_local_name_or_address") return "Advertising name / address";
  return "Unknown source";
}

function formatCorrelationMethod(method) {
  if (method === "approved_ad_token_carryover") return "Approved AD token carryover";
  if (method === "akiyama_time_rssi_linear_assignment_v1") return "RSSI-time assignment proposal";
  return method || "Unknown method";
}

function correlationEvidenceSummary(correlation) {
  if (correlation.method === "approved_ad_token_carryover") {
    return `rule ${correlation.details?.rule_id || "-"}; ${correlation.details?.token_bit_length || "-"} bit token`;
  }
  return correlation.details?.automatic_acceptance
    ? "validated automatic-acceptance policy"
    : "review-only; not an automatic merge";
}

function correlationMetrics(correlation) {
  const values = [];
  if (Number.isFinite(Number(correlation.time_difference_seconds))) values.push(`time ${formatNumber(correlation.time_difference_seconds, 2)} s`);
  if (Number.isFinite(Number(correlation.rssi_difference_db))) values.push(`RSSI residual ${formatNumber(correlation.rssi_difference_db, 2)} dB`);
  if (Number.isFinite(Number(correlation.assignment_cost))) values.push(`cost ${formatNumber(correlation.assignment_cost, 2)}`);
  if (Number.isFinite(Number(correlation.alpha))) values.push(`alpha ${formatNumber(correlation.alpha, 4)}`);
  return values.join(" · ") || "No probabilistic score";
}

function formatManufacturerEvidence(identity) {
  const profile = identity.manufacturer_profile || {};
  if (profile.company_name) {
    const companyId = profile.company_id ? ` (${profile.company_id})` : "";
    return `SIG company: ${profile.company_name}${companyId}; verified raw ADV`;
  }
  if (identity.manufacturer_evidence === "legacy_payload_layout_unverified") {
    return "SIG company withheld; legacy scanner API data is not raw-verified";
  }
  if (identity.manufacturer_evidence === "raw_payload_not_captured") {
    return "SIG company unavailable; raw ADV was not captured";
  }
  if (identity.manufacturer_evidence === "partial_with_parse_errors") {
    return "SIG company withheld; raw ADV parse was incomplete";
  }
  return "SIG company unavailable";
}

function formatCaptureProvenance(capture) {
  if (!capture) return "No capture metadata";
  if (capture.capture_status === "verified") {
    const parser = capture.ad_parser || {};
    const response = parser.scan_response_captured ? ` + scan response ${parser.scan_response_payload_length || 0} B` : "";
    return `Verified ADV ${parser.advertising_payload_length || 0} B${response}`;
  }
  if (capture.capture_status === "partial_with_parse_errors") return "Partial; parse errors preserved";
  if (capture.capture_status === "legacy_payload_layout_unverified") return "Legacy payload layout; not parsed";
  return "Raw payload not captured";
}

function formatTimeProvenance(time) {
  if (!time) return "No timestamp provenance";
  if (time.time_quality === "trusted") return "Scanner clock synchronized";
  return "Server receive time fallback";
}

function kv(label, value) {
  return `<div class="kv"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`;
}

function drawSignalChart(observations) {
  const canvas = $("#signalChart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#d7e0e5";
  ctx.beginPath();
  for (let y = 20; y <= 160; y += 35) {
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
  }
  ctx.stroke();
  const points = observations.slice().reverse().map((item, index) => ({ x: index, y: item.rssi }));
  if (points.length < 2) return;
  const min = -100;
  const max = -30;
  ctx.strokeStyle = "#0b7285";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (index / (points.length - 1)) * (canvas.width - 24) + 12;
    const y = canvas.height - 12 - ((point.y - min) / (max - min)) * (canvas.height - 24);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function loadScanners() {
  state.scanners = await api("/api/scanners");
  renderScanners();
  if (lastGpsPosition) {
    applyGpsPosition(lastGpsPosition.lat, lastGpsPosition.lon);
  }
}

function renderScanners() {
  $("#scannerRows").innerHTML = state.scanners
    .map((scanner) => `<tr data-scanner-id="${scanner.id}">
      <td><strong>${escapeHtml(scanner.display_name)}</strong><br><small>${escapeHtml(scanner.id)}</small></td>
      <td>${badge(scanner.status)}</td>
      <td>${escapeHtml([scanner.building, scanner.floor, scanner.room, scanner.zone].filter(Boolean).join(" / ") || "-")}</td>
      <td>${escapeHtml(scanner.firmware_version || "-")}</td>
      <td>${formatDate(scanner.last_heartbeat_at)}</td>
    </tr>`)
    .join("") || `<tr><td colspan="5">No scanners registered.</td></tr>`;
  $$("#scannerRows tr[data-scanner-id]").forEach((row) => {
    row.addEventListener("click", () => fillScannerForm(row.dataset.scannerId));
  });
}

function fillScannerForm(scannerId) {
  const scanner = state.scanners.find((item) => item.id === scannerId);
  if (!scanner) return;
  const form = $("#scannerForm");
  for (const [key, value] of Object.entries(scanner)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = value ?? "";
  }
}

async function saveScanner(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const scannerId = form.elements.id.value;
  if (!scannerId) return;
  const payload = {};
  ["display_name", "building", "floor", "room", "zone", "maintenance_notes"].forEach((name) => {
    payload[name] = form.elements[name].value || null;
  });
  ["latitude", "longitude", "indoor_x", "indoor_y", "orientation_deg"].forEach((name) => {
    payload[name] = form.elements[name].value ? Number(form.elements[name].value) : null;
  });
  payload.enabled = form.elements.enabled.checked;
  if (!payload.enabled && !confirm("Disable this scanner?")) return;
  await api(`/api/scanners/${scannerId}`, { method: "PATCH", body: JSON.stringify(payload) });
  await loadScanners();
}

function renderLocations() {
  updateMapScannerSelect();
  const selectedScanner = getSelectedMapScanner();
  const locatedScanners = state.scanners.filter(hasCoordinates);
  const selectedDevices = selectedScanner
    ? state.mapDevices.filter((device) => deviceBelongsToScannerLocation(device, selectedScanner))
    : state.mapDevices;

  $("#mapNotice").innerHTML = mapNotice(selectedScanner, selectedDevices.length);
  renderRealMap(locatedScanners, selectedScanner, selectedDevices);
  $("#localCoverage").innerHTML = renderLocalCoverage(selectedScanner, selectedDevices);

  $$("#localCoverage [data-device-id]").forEach((item) => {
    item.addEventListener("click", () => showDeviceDetail(item.dataset.deviceId));
  });
}

function initRealMap() {
  if (state.map || !window.L) return;
  const canvas = $("#locationCanvas");
  if (!canvas || canvas.offsetWidth === 0) return; // Prevent init when hidden

  state.map = L.map("locationCanvas", {
    preferCanvas: true,
    zoomControl: true,
  }).setView([-2.5489, 118.0149], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(state.map);
  state.mapLayers = L.layerGroup().addTo(state.map);
  setTimeout(() => state.map.invalidateSize(), 50);
}

function renderRealMap(locatedScanners, selectedScanner, selectedDevices) {
  initRealMap();
  if (!state.map || !state.mapLayers) {
    if (!window.L) $("#locationCanvas").innerHTML = "Leaflet map script failed to load.";
    return;
  }
  state.mapLayers.clearLayers();
  const bounds = [];

  for (const device of selectedDevices) {
    const anchor = deviceAnchor(device);
    const distance = Number(device.estimated_distance_m);
    if (!hasCoordinates(anchor) || !Number.isFinite(distance) || distance <= 0) continue;
    L.circle([Number(anchor.latitude), Number(anchor.longitude)], {
      radius: Math.min(distance, 1000),
      color: deviceMarkerColor(device.status),
      weight: 1,
      opacity: 0.3,
      fillOpacity: 0.025,
      interactive: false,
    }).addTo(state.mapLayers);
  }

  for (const scanner of locatedScanners) {
    const latLng = [Number(scanner.latitude), Number(scanner.longitude)];
    const isSelected = selectedScanner?.id === scanner.id;
    const marker = L.marker(latLng, { icon: scannerIcon(scanner, isSelected) })
      .bindPopup(scannerPopup(scanner))
      .addTo(state.mapLayers);
    bounds.push(latLng);
  }

  for (const group of groupDevicesByAnchor(selectedDevices)) {
    const latLng = [group.latitude, group.longitude];
    L.marker(latLng, {
      icon: deviceStackIcon(group.devices),
      zIndexOffset: 500,
    })
      .bindPopup(deviceStackPopup(group.devices), { maxWidth: 360 })
      .addTo(state.mapLayers);
    bounds.push(latLng);
  }

  if (selectedScanner && hasCoordinates(selectedScanner)) {
    if (bounds.length > 1) {
      state.map.fitBounds(bounds, { padding: [48, 48], maxZoom: 18 });
    } else {
      state.map.setView([Number(selectedScanner.latitude), Number(selectedScanner.longitude)], 18);
    }
    reverseGeocodeScanner(selectedScanner);
  } else if (bounds.length) {
    state.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
    $("#addressPanel").innerHTML = "";
  } else {
    state.map.setView([-2.5489, 118.0149], 5);
    $("#addressPanel").innerHTML = "";
  }
  setTimeout(() => state.map.invalidateSize(), 50);
}

function deviceAnchor(device) {
  const anchor = device.location_anchor || {};
  return {
    latitude: anchor.latitude ?? device.latitude,
    longitude: anchor.longitude ?? device.longitude,
  };
}

function groupDevicesByAnchor(devices) {
  const groups = new Map();
  for (const device of devices) {
    const anchor = deviceAnchor(device);
    if (!hasCoordinates(anchor)) continue;
    const latitude = Number(anchor.latitude);
    const longitude = Number(anchor.longitude);
    const key = `${latitude.toFixed(6)}:${longitude.toFixed(6)}`;
    if (!groups.has(key)) groups.set(key, { latitude, longitude, devices: [] });
    groups.get(key).devices.push(device);
  }
  return Array.from(groups.values());
}

function deviceMarkerColor(status) {
  if (["offline", "identity_expired", "ignored"].includes(status)) return "#c43d4d";
  if (status === "temporarily_missing") return "#c47d13";
  return "#087f8c";
}

function deviceStackIcon(devices) {
  const statuses = devices.map((device) => device.status);
  const tone = statuses.every((status) => ["offline", "identity_expired", "ignored"].includes(status))
    ? "offline"
    : statuses.some((status) => status === "temporarily_missing")
      ? "warning"
      : "online";
  return L.divIcon({
    className: "",
    html: `<span class="device-stack ${tone}" title="${devices.length} Bluetooth device(s)">${devices.length}</span>`,
    iconSize: [36, 36],
    iconAnchor: [-10, 18],
    popupAnchor: [28, -12],
  });
}

function deviceStackPopup(devices) {
  const rows = devices
    .slice()
    .sort((a, b) => String(a.status).localeCompare(String(b.status)))
    .map((device) => {
      const name = device.alias || device.display_name || device.primary_address || "BLE device";
      return `<button type="button" class="map-device-button" data-map-device-id="${escapeHtml(device.id)}">
        <span>${statusDot(device.status)}<strong>${escapeHtml(name)}</strong></span>
        <small>${escapeHtml(device.primary_address || "-")} · ${escapeHtml(String(device.status || "unknown").replaceAll("_", " "))}</small>
      </button>`;
    }).join("");
  return `<div class="map-device-popup"><div class="map-device-popup-title">${devices.length} anchored device${devices.length === 1 ? "" : "s"}</div>${rows}</div>`;
}

function scannerIcon(scanner, active) {
  const status = String(scanner.status || "registered").toLowerCase();
  return L.divIcon({
    className: "",
    html: `<span class="scanner-pin ${escapeHtml(status)}" title="${escapeHtml(scanner.display_name)}: ${escapeHtml(status)}${active ? " (selected)" : ""}"></span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function scannerPopup(scanner) {
  return `
    <strong>${escapeHtml(scanner.display_name)}</strong>
    <div>${badge(scanner.status)}</div>
    <div>${escapeHtml([scanner.building, scanner.floor, scanner.room, scanner.zone].filter(Boolean).join(" / ") || "-")}</div>
    <div>${formatNumber(scanner.latitude, 6)}, ${formatNumber(scanner.longitude, 6)}</div>
  `;
}

async function reverseGeocodeScanner(scanner) {
  const lat = Number(scanner.latitude);
  const lon = Number(scanner.longitude);
  const key = `${scanner.id}:${lat.toFixed(6)},${lon.toFixed(6)}`;
  if (state.mapAddressKey === key) return;
  state.mapAddressKey = key;
  $("#addressPanel").innerHTML = `
    ${addressItem("Coordinates", `${formatNumber(lat, 6)}, ${formatNumber(lon, 6)}`)}
    ${addressItem("Address", "Resolving from OpenStreetMap...")}
  `;
  try {
    const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&zoom=18&addressdetails=1`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const address = data.address || {};
    $("#addressPanel").innerHTML = `
      ${addressItem("Coordinates", `${formatNumber(lat, 6)}, ${formatNumber(lon, 6)}`)}
      ${addressItem("Jalan/Area", address.road || address.neighbourhood || address.hamlet || "-")}
      ${addressItem("Kelurahan/Desa", address.village || address.suburb || address.quarter || address.neighbourhood || "-")}
      ${addressItem("Kecamatan/Distrik", address.city_district || address.district || address.municipality || address.county || "-")}
      ${addressItem("Kota/Kabupaten", address.city || address.town || address.regency || address.county || "-")}
      ${addressItem("Provinsi", address.state || "-")}
      ${addressItem("Kode Pos", address.postcode || "-")}
      ${addressItem("Alamat OSM", data.display_name || "-")}
    `;
  } catch (error) {
    $("#addressPanel").innerHTML = `
      ${addressItem("Coordinates", `${formatNumber(lat, 6)}, ${formatNumber(lon, 6)}`)}
      ${addressItem("Address", "Reverse geocoding unavailable. Map tiles still show local labels when online.")}
    `;
  }
}

function addressItem(label, value) {
  return `<div class="address-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function updateMapScannerSelect() {
  const select = $("#mapScannerSelect");
  if (!select) return;
  const previous = state.selectedMapScannerId || select.value || state.scanners[0]?.id || "";
  select.innerHTML = state.scanners
    .map((scanner) => `<option value="${escapeHtml(scanner.id)}">${escapeHtml(scanner.display_name)} (${escapeHtml(scanner.status)})</option>`)
    .join("");
  if (state.scanners.some((scanner) => scanner.id === previous)) {
    select.value = previous;
  }
  state.selectedMapScannerId = select.value || state.scanners[0]?.id || null;
}

function getSelectedMapScanner() {
  const selectedId = state.selectedMapScannerId || $("#mapScannerSelect")?.value || state.scanners[0]?.id;
  return state.scanners.find((scanner) => scanner.id === selectedId) || state.scanners[0] || null;
}

function mapNotice(scanner, deviceCount) {
  if (!scanner) {
    return "No scanner is registered yet.";
  }
  if (!hasCoordinates(scanner)) {
    return `Scanner <strong>${escapeHtml(scanner.display_name)}</strong> has no coordinates configured yet.`;
  }
  return `<strong>${escapeHtml(scanner.display_name)}</strong> · ${deviceCount} anchored device${deviceCount === 1 ? "" : "s"}`;
}

function renderLocalCoverage(scanner, devices) {
  if (!scanner) {
    return `<div class="coverage-card">No scanner selected.</div>`;
  }
  const scannerName = escapeHtml(scanner.display_name);
  if (!hasCoordinates(scanner)) {
    return `<div class="coverage-card"><h3>${scannerName}</h3><p>Coordinates are not set yet.</p></div>`;
  }
  const list = devices.map((device) => {
    const name = device.alias || device.display_name || device.primary_address || "Unknown BLE device";
    return `<button class="device-chip" data-device-id="${escapeHtml(device.id)}">
      <span class="device-chip-name">${statusDot(device.status)}<strong>${escapeHtml(name)}</strong></span>
      <span>${escapeHtml(device.current_zone || device.current_scanner_id || "Unanchored")} · ${formatDate(device.location_anchor?.anchored_at)}</span>
      <span>${formatProximity(device)} · ${formatNumber(device.smoothed_rssi, 1)} dBm</span>
    </button>`;
  }).join("") || `<div class="device-chip">No Bluetooth observations for this scanner yet.</div>`;

  return `
    <div class="coverage-card">
      <h3>Devices anchored to ${scannerName}</h3>
      <div class="coverage-list">${list}</div>
    </div>
  `;
}



async function applyGpsPosition(lat, lon) {
  const scanner = getSelectedMapScanner();
  if (!scanner) return;
  const needsUpdate = !hasCoordinates(scanner)
    || haversineDistance(Number(scanner.latitude), Number(scanner.longitude), lat, lon) > GPS_MOVE_THRESHOLD_M;
  const cooldown = Date.now() - lastGpsSaveTime > GPS_SAVE_INTERVAL_MS;
  if (needsUpdate && cooldown) {
    lastGpsSaveTime = Date.now();
    await api(`/api/scanners/${scanner.id}`, {
      method: "PATCH",
      body: JSON.stringify({ latitude: lat, longitude: lon }),
    });
    // Jangan panggil loadScanners() yang memanggil applyGpsPosition lagi (infinite loop), 
    // cukup ubah state dan render ulang
    scanner.latitude = lat;
    scanner.longitude = lon;
    renderLocations();
  }
}

function startGpsTracking() {
  if (gpsWatchId !== null || !navigator.geolocation) return;
  gpsWatchId = navigator.geolocation.watchPosition(
    async (position) => {
      const lat = Number(position.coords.latitude.toFixed(6));
      const lon = Number(position.coords.longitude.toFixed(6));
      lastGpsPosition = { lat, lon };
      await applyGpsPosition(lat, lon);
    },
    (error) => {
      console.warn("GPS tracking error:", error.message);
      // Remove IP Fallback completely as requested by user.
      const notice = document.getElementById("mapNotice");
      if (notice) {
        notice.innerHTML = `<div style="color: #ef4444; font-weight: 500; padding: 12px; background: #fef2f2; border: 1px solid #f87171; border-radius: 8px;">
          ❌ GPS Gagal: <strong>${error.message}</strong><br>
          Pastikan Anda memberikan izin akses lokasi di browser, DAN pastikan "Location Services" aktif di System Settings (khususnya untuk macOS).
        </div>`;
      }
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 }
  );
}

async function detectScannerLocation(options = {}) {
  const scanner = getSelectedMapScanner();
  if (!scanner) return;

  const saveLocation = async (lat, lon) => {
    const payload = {
      latitude: lat,
      longitude: lon,
      building: scanner.building,
      room: scanner.room,
      zone: scanner.zone,
    };
    await api(`/api/scanners/${scanner.id}`, { method: "PATCH", body: JSON.stringify(payload) });
    await loadScanners();
    renderLocations();
    fillScannerForm(scanner.id);
  };

  if (!navigator.geolocation) {
    if (!options.quiet) alert("Browser geolocation not available");
    return;
  }

  navigator.geolocation.getCurrentPosition(
    async (position) => {
      const lat = Number(position.coords.latitude.toFixed(6));
      const lon = Number(position.coords.longitude.toFixed(6));
      await saveLocation(lat, lon);
    },
    async (err) => {
      if (!options.quiet) alert(`GPS error: ${err.message}`);
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
  );
}
async function loadEvents() {
  const params = new URLSearchParams();
  const eventType = $("#eventTypeFilter")?.value;
  if (eventType) params.set("event_type", eventType);
  state.events = await api(`/api/events?${params.toString()}`);
  $("#eventList").innerHTML = renderEventItems(state.events);
}

function renderEventItems(events) {
  if (!events.length) return `<div class="event-item">No events.</div>`;
  return events
    .map((event) => `<div class="event-item">
      <strong>${escapeHtml(event.event_type)}</strong>
      <span>${escapeHtml(event.reason || "")}</span>
      <time>${formatDate(event.occurred_at)}</time>
      <small>${escapeHtml([event.previous_state, event.new_state].filter(Boolean).join(" → "))}</small>
    </div>`)
    .join("");
}

async function loadDiagnostics() {
  state.diagnostics = await api("/api/diagnostics");
  renderDiagnostics();
}

function renderDiagnostics() {
  const data = state.diagnostics || {};
  const processing = data.processing || {};
  const identities = data.identity_counts || {};
  $("#diagnosticsPanel").innerHTML = `
    ${kv("Server", data.server?.status || "unknown")}
    ${kv("Database", data.database?.status || "unknown")}
    ${kv("Observations", processing.observation_count ?? 0)}
    ${kv("Events", processing.event_count ?? 0)}
    ${kv("Invalid payloads", processing.invalid_payload_count ?? 0)}
    ${kv("Observed identities", identities.observed_identities ?? 0)}
    ${kv("Randomized identities", identities.randomized_address_identities ?? 0)}
    ${kv("Logical devices", identities.logical_devices ?? 0)}
    ${kv("Identity correlations", identities.identity_correlations ?? 0)}
    ${kv("Latest heartbeat", data.latest_heartbeat ? formatDate(data.latest_heartbeat.received_at) : "-")}
  `;
}

function connectLive() {
  if (state.live) state.live.close();
  const source = new EventSource("/api/live/events");
  state.live = source;
  source.addEventListener("connected", () => setLiveState("Connected", "good"));
  source.addEventListener("ping", () => setLiveState("Connected", "good"));
  ["scanner_heartbeat", "observations_ingested", "scanner_updated", "device_correlation_changed", "runtime_state_changed"].forEach((type) => {
    source.addEventListener(type, () => refreshView(state.currentView));
  });
  source.onerror = () => {
    setLiveState("Reconnecting", "warning");
  };
}

function setLiveState(text, tone) {
  $("#liveText").textContent = text;
  const dot = $("#liveState");
  dot.className = "status-dot";
  if (tone === "warning") dot.classList.add("warning");
  if (tone !== "good" && tone !== "warning") dot.classList.add("muted");
}

function bindEvents() {
  $$(".nav button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("#refreshBtn").addEventListener("click", () => refreshView());
  $("#deviceSearch").addEventListener("input", renderDevices);
  $("#deviceStatusFilter").addEventListener("change", loadDevices);
  $("#includeIgnored").addEventListener("change", loadDevices);
  $("#includeExpired").addEventListener("change", loadDevices);
  $("#eventFilterBtn").addEventListener("click", loadEvents);
  $("#scannerForm").addEventListener("submit", saveScanner);
  $("#detectScannerLocationFormBtn").addEventListener("click", async () => {
    const scannerId = $("#scannerForm").elements.id.value;
    if (scannerId) {
      state.selectedMapScannerId = scannerId;
    }
    await detectScannerLocation();
  });
  $("#mapScannerSelect").addEventListener("change", (event) => {
    state.selectedMapScannerId = event.target.value;
    renderLocations();
  });
  $("#detectScannerLocationBtn").addEventListener("click", () => detectScannerLocation());
  $("#closeDeviceDialog").addEventListener("click", () => $("#deviceDialog").close());
  $("#deviceDialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  $("#locationCanvas").addEventListener("click", (event) => {
    const button = event.target.closest("[data-map-device-id]");
    if (button) showDeviceDetail(button.dataset.mapDeviceId);
  });
}

bindEvents();
refreshAll();
connectLive();
setInterval(() => refreshView(state.currentView), 30000);
startGpsTracking();
