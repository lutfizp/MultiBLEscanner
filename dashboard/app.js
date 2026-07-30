const state = {
  overview: null,
  devices: [],
  mapDevices: [],
  scanners: [],
  events: [],
  diagnostics: null,
  runtimeConfig: {
    app_timezone: "Asia/Jakarta",
    local_scanner_id: "scn_dev_lab_001",
  },
  currentView: "overview",
  selectedMapScannerId: null,
  locationPrompted: false,
  map: null,
  mapLayers: null,
  trackingLayers: null,
  mapFitRequested: true,
  mapHasFittedData: false,
  mapUserInteracted: false,
  mapAddressKey: null,
  selectedMapDeviceId: null,
  live: null,
  refreshes: new Map(),
  liveRefreshTimer: null,
  gps: {
    watchId: null,
    latestPosition: null,
    receivedAt: 0,
    status: "starting",
    permissionState: "unknown",
    lastAttemptAt: 0,
    lastFixAt: 0,
    lastError: null,
    lastSaveError: null,
    lastSavedAt: 0,
    lastSavedPosition: null,
    savePromise: null,
    savePending: false,
    diagnosticTimer: null,
    lastDiagnosticSignature: null,
    waiters: new Set(),
  },
  tracking: {
    device: null,
    session: null,
    samples: [],
    positions: [],
    source: null,
    leaseTimer: null,
    staleTimer: null,
    positionWatchId: null,
    lastPosition: null,
    lastReceivedAt: 0,
    mode: "fixed",
    soundEnabled: true,
    audioContext: null,
    oscillator: null,
    gain: null,
    notice: "",
    positionSending: false,
    shouldFitMap: false,
  },
};

const TRACKING_POSITION_INTERVAL_MS = 3000;
const TRACKING_POSITION_MOVE_M = 2;
const GPS_WATCH_OPTIONS = {
  enableHighAccuracy: true,
  maximumAge: 5000,
};
const GPS_DIAGNOSTIC_WAIT_MS = 15000;
const GPS_REGULAR_SAVE_INTERVAL_MS = 5000;
const GPS_MINIMUM_SAVE_INTERVAL_MS = 1500;
const GPS_MOVE_SAVE_THRESHOLD_M = 5;
const GPS_POSITION_WAIT_MS = 60000;
const GPS_POSITION_CACHE_MS = 30000;
const GPS_TIMESTAMP_MAX_SKEW_MS = 5 * 60 * 1000;

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
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = parsed.detail || text;
    } catch {
      // Keep the server response as plain text.
    }
    throw new Error(`${response.status} ${detail}`);
  }
  if (response.status === 204) return null;
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
    state.runtimeConfig = {
      app_timezone: "Asia/Jakarta",
      local_scanner_id: "scn_dev_lab_001",
    };
  }
}

async function performViewRefresh(view) {
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

async function refreshView(view = state.currentView) {
  const active = state.refreshes.get(view);
  if (active) {
    active.pending = true;
    return active.promise;
  }

  const refresh = { pending: false, promise: null };
  refresh.promise = (async () => {
    try {
      do {
        refresh.pending = false;
        await performViewRefresh(view);
      } while (refresh.pending);
    } finally {
      state.refreshes.delete(view);
    }
  })();
  state.refreshes.set(view, refresh);
  return refresh.promise;
}

function scheduleLiveRefresh() {
  if (state.liveRefreshTimer !== null) {
    clearTimeout(state.liveRefreshTimer);
  }
  state.liveRefreshTimer = setTimeout(() => {
    state.liveRefreshTimer = null;
    refreshView(state.currentView).catch((error) => {
      console.error("Unable to refresh dashboard view:", error);
    });
  }, 150);
}

function hasCoordinates(item) {
  return item?.latitude != null && item?.latitude !== "" &&
         item?.longitude != null && item?.longitude !== "" &&
         Number.isFinite(Number(item.latitude)) && 
         Number.isFinite(Number(item.longitude));
}



function deviceBelongsToScannerLocation(device, scanner) {
  if (!scanner) return true;
  const anchoredScannerId = device.location_anchor?.scanner_id;
  return anchoredScannerId
    ? anchoredScannerId === scanner.id
    : device.current_scanner_id === scanner.id;
}

async function loadOverview() {
  state.overview = await api("/api/overview");
  renderOverview();
}

function renderOverview() {
  const data = state.overview || {};
  const presentBleRecords =
    data.present_ble_records ??
    Number(data.active_devices || 0) + Number(data.active_unresolved_identities || 0);
  const metrics = [
    ["Total scanners", data.scanner_total],
    ["Online scanners", data.scanner_online],
    ["Offline scanners", data.scanner_offline],
    ["Present radio identities", presentBleRecords],
    ["Visible device candidates", data.visible_device_candidates ?? data.active_devices],
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
  const includeTransient = $("#includeTransient")?.checked;
  const includeExpired = $("#includeExpired")?.checked;
  if (status) params.set("status", status);
  if (includeIgnored) params.set("include_ignored", "true");
  params.set("include_transient", String(Boolean(includeTransient || includeExpired)));
  if (includeExpired) params.set("include_expired", "true");
  state.devices = await api(`/api/devices?${params.toString()}`);
  renderDevices();
}

async function loadMapDevices() {
  state.mapDevices = await api("/api/devices?include_transient=false");
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
      const selected = device.id === state.selectedMapDeviceId ? " selected" : "";
      return `<tr data-device-id="${device.id}" class="${selected.trim()}">
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
    row.addEventListener("click", () => selectDevice(row.dataset.deviceId));
  });
}

function openDeviceDrawer() {
  $("#deviceDrawer").classList.remove("hidden");
  $("#deviceDrawer").setAttribute("aria-hidden", "false");
  $("#deviceDrawerBackdrop").classList.remove("hidden");
  document.body.classList.add("drawer-open");
}

function closeDeviceDrawer() {
  $("#deviceDrawer").classList.add("hidden");
  $("#deviceDrawer").setAttribute("aria-hidden", "true");
  $("#deviceDrawerBackdrop").classList.add("hidden");
  document.body.classList.remove("drawer-open");
}

async function selectDevice(deviceId) {
  state.selectedMapDeviceId = deviceId;
  if ($("#mapDeviceSelect")) $("#mapDeviceSelect").value = deviceId;
  renderDevices();
  renderTrackingOverlay();
  try {
    await showDeviceDetail(deviceId);
  } catch (error) {
    console.error("Unable to load device detail:", error);
  }
}

async function showDeviceDetail(deviceId) {
  state.selectedMapDeviceId = deviceId;
  const detail = await api(`/api/devices/${deviceId}`);
  const device = detail.device;
  const latestObservation = detail.recent_observations?.[0] || null;
  const gatt = device.gatt_enrichment || null;
  const trackingThisDevice = state.tracking.session?.logical_device_id === device.id;
  const selectedMode = trackingThisDevice ? state.tracking.session.mode : state.tracking.mode;
  const panel = $("#deviceDetail");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div class="panel-header">
      <div class="detail-heading">
        <h2 id="deviceDialogTitle">${escapeHtml(device.alias || device.display_name || "Device detail")}</h2>
        <small>${escapeHtml(device.primary_address || "No observed address")}</small>
      </div>
      <div class="detail-actions">
        <button data-correlation-action="mark_known">Known</button>
        <button data-correlation-action="${device.ignored ? "unignore" : "mark_ignored"}">${device.ignored ? "Unignore" : "Ignore"}</button>
      </div>
    </div>
    <div class="tracking-launch">
      <div class="segmented-control" aria-label="Tracking measurement mode">
        <button type="button" data-tracking-mode="fixed" aria-pressed="${selectedMode === "fixed"}" ${trackingThisDevice ? "disabled" : ""}>Fixed</button>
        <button type="button" data-tracking-mode="walk" aria-pressed="${selectedMode === "walk"}" ${trackingThisDevice ? "disabled" : ""}>Walk</button>
      </div>
      <button type="button" class="primary-action" data-track-device ${device.ignored ? "disabled" : ""}>
        ${trackingThisDevice ? "Show Signal Finder" : "Track Signal"}
      </button>
      <span id="deviceTrackingError" class="inline-error"></span>
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
        ${(identity.manufacturer_profile?.continuity_subtypes || []).length ? `<span>Apple Continuity: ${escapeHtml(identity.manufacturer_profile.continuity_subtypes.join(", "))}</span>` : ""}
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
  panel.querySelectorAll("button[data-correlation-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/devices/correlation", {
        method: "POST",
        body: JSON.stringify({
          source_logical_device_id: device.id,
          action: button.dataset.correlationAction,
        }),
      });
      await showDeviceDetail(device.id);
      await loadDevices();
    });
  });
  panel.querySelectorAll("[data-tracking-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tracking.mode = button.dataset.trackingMode;
      panel.querySelectorAll("[data-tracking-mode]").forEach((option) => {
        option.setAttribute("aria-pressed", String(option === button));
      });
    });
  });
  panel.querySelector("[data-track-device]")?.addEventListener("click", async () => {
    const errorNode = $("#deviceTrackingError");
    errorNode.textContent = "";
    try {
      if (trackingThisDevice) {
        focusActiveSignalFinder();
      } else {
        await startSignalFinder(device, state.tracking.mode);
      }
    } catch (error) {
      errorNode.textContent = error.message;
    }
  });
  openDeviceDrawer();
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
  if (method === "apple_continuity_transition_v1") return "Apple Continuity transition proposal";
  if (method === "akiyama_time_rssi_linear_assignment_v1") return "RSSI-time assignment proposal";
  return method || "Unknown method";
}

function correlationEvidenceSummary(correlation) {
  if (correlation.method === "approved_ad_token_carryover") {
    return `rule ${correlation.details?.rule_id || "-"}; ${correlation.details?.token_bit_length || "-"} bit token`;
  }
  if (correlation.method === "apple_continuity_transition_v1") {
    const subtypes = correlation.details?.subtype_overlap || [];
    return `${subtypes.join(", ") || "Continuity"}; possible match only`;
  }
  return correlation.details?.automatic_acceptance
    ? "validated automatic-acceptance policy"
    : "review-only; not an automatic merge";
}

function correlationMetrics(correlation) {
  const values = [];
  if (Number.isFinite(Number(correlation.details?.evidence_score))) values.push(`evidence score ${formatNumber(correlation.details.evidence_score, 2)}`);
  if (Number.isFinite(Number(correlation.time_difference_seconds))) values.push(`time ${formatNumber(correlation.time_difference_seconds, 2)} s`);
  if (Number.isFinite(Number(correlation.rssi_difference_db))) values.push(`RSSI residual ${formatNumber(correlation.rssi_difference_db, 2)} dB`);
  if (correlation.details?.handoff_iv_delta != null && Number.isFinite(Number(correlation.details.handoff_iv_delta))) values.push(`Handoff IV +${correlation.details.handoff_iv_delta}`);
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
  const cachedPosition = freshGpsPosition();
  if (cachedPosition) {
    persistLocalScannerPosition(cachedPosition).catch((error) => {
      console.error("Unable to persist the cached scanner position:", error);
    });
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

  updateMapDeviceSelect(selectedDevices);
  $("#mapNotice").innerHTML = mapNotice(selectedScanner, selectedDevices.length);
  renderGpsStatus();
  renderRealMap(locatedScanners, selectedScanner, selectedDevices);
  $("#localCoverage").innerHTML = renderLocalCoverage(selectedScanner, selectedDevices);

  $$("#localCoverage [data-device-id]").forEach((item) => {
    item.addEventListener("click", () => selectDevice(item.dataset.deviceId));
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
  state.trackingLayers = L.layerGroup().addTo(state.map);
  const preserveOperatorViewport = () => {
    state.mapFitRequested = false;
    state.mapUserInteracted = true;
    state.tracking.shouldFitMap = false;
  };
  const mapContainer = state.map.getContainer();
  ["pointerdown", "wheel", "touchstart", "keydown"].forEach((eventName) => {
    mapContainer.addEventListener(eventName, preserveOperatorViewport, { passive: true });
  });
  state.map.on("dragstart", preserveOperatorViewport);
  setTimeout(() => state.map.invalidateSize({ pan: false }), 50);
}

function renderRealMap(locatedScanners, selectedScanner, selectedDevices) {
  initRealMap();
  if (!state.map || !state.mapLayers) {
    if (!window.L) $("#locationCanvas").innerHTML = "Leaflet map script failed to load.";
    return;
  }
  state.mapLayers.clearLayers();
  const focusBounds = [];

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
    L.marker(latLng, { icon: scannerIcon(scanner, isSelected) })
      .bindPopup(scannerPopup(scanner))
      .addTo(state.mapLayers);
    if (!selectedScanner || isSelected) focusBounds.push(latLng);
  }

  for (const group of groupDevicesByAnchor(selectedDevices)) {
    const latLng = [group.latitude, group.longitude];
    L.marker(latLng, {
      icon: deviceStackIcon(group.devices, state.selectedMapDeviceId),
      zIndexOffset: 500,
    })
      .bindPopup(deviceStackPopup(group.devices), { maxWidth: 360 })
      .addTo(state.mapLayers);
    focusBounds.push(latLng);
  }

  if (selectedScanner && hasCoordinates(selectedScanner)) {
    reverseGeocodeScanner(selectedScanner);
  } else {
    $("#addressPanel").innerHTML = "";
  }
  applyRequestedMapViewport(focusBounds);
  renderTrackingOverlay();
  setTimeout(() => state.map.invalidateSize({ pan: false }), 50);
}

function applyRequestedMapViewport(focusBounds) {
  const canFitFirstAvailableData = (
    focusBounds.length > 0
    && !state.mapHasFittedData
    && !state.mapUserInteracted
  );
  if (!state.mapFitRequested && !canFitFirstAvailableData) return;

  state.mapFitRequested = false;
  state.mapUserInteracted = false;
  if (focusBounds.length > 1) {
    state.map.fitBounds(focusBounds, { padding: [48, 48], maxZoom: 18 });
    state.mapHasFittedData = true;
  } else if (focusBounds.length === 1) {
    state.map.setView(focusBounds[0], 18);
    state.mapHasFittedData = true;
  } else {
    state.map.setView([-2.5489, 118.0149], 5);
  }
}

function requestMapFit() {
  state.mapFitRequested = true;
  state.mapUserInteracted = false;
  if (state.currentView === "locations") renderLocations();
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

function deviceStackIcon(devices, selectedDeviceId = null) {
  const statuses = devices.map((device) => device.status);
  const tone = statuses.every((status) => ["offline", "identity_expired", "ignored"].includes(status))
    ? "offline"
    : statuses.some((status) => status === "temporarily_missing")
      ? "warning"
      : "online";
  const selected = devices.some((device) => device.id === selectedDeviceId) ? " selected" : "";
  return L.divIcon({
    className: "",
    html: `<span class="device-stack ${tone}${selected}" title="${devices.length} Bluetooth device(s)">${devices.length}</span>`,
    iconSize: [42, 42],
    iconAnchor: [21, 21],
    popupAnchor: [0, -18],
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

function updateMapDeviceSelect(devices) {
  const select = $("#mapDeviceSelect");
  if (!select) return;
  const previous = state.selectedMapDeviceId || "";
  const options = devices
    .slice()
    .sort((left, right) => {
      const leftName = left.alias || left.display_name || left.primary_address || "";
      const rightName = right.alias || right.display_name || right.primary_address || "";
      return leftName.localeCompare(rightName);
    })
    .map((device) => {
      const name = device.alias || device.display_name || device.primary_address || "BLE device";
      return `<option value="${escapeHtml(device.id)}">${escapeHtml(name)} · ${escapeHtml(String(device.status || "unknown").replaceAll("_", " "))}</option>`;
    })
    .join("");
  select.innerHTML = `<option value="">Select a Bluetooth device</option>${options}`;
  if (devices.some((device) => device.id === previous)) {
    select.value = previous;
  }
}

function getSelectedMapScanner() {
  const selectedId = state.selectedMapScannerId || $("#mapScannerSelect")?.value || state.scanners[0]?.id;
  return state.scanners.find((scanner) => scanner.id === selectedId) || state.scanners[0] || null;
}

function mapNotice(scanner, deviceCount) {
  if (!scanner) {
    return 'No scanner is registered yet.<span id="mapGpsStatus" class="map-gps-status"></span>';
  }
  if (!hasCoordinates(scanner)) {
    return `Scanner <strong>${escapeHtml(scanner.display_name)}</strong> has no coordinates configured yet.<span id="mapGpsStatus" class="map-gps-status"></span>`;
  }
  return `<strong>${escapeHtml(scanner.display_name)}</strong> · ${deviceCount} anchored device${deviceCount === 1 ? "" : "s"}<span id="mapGpsStatus" class="map-gps-status"></span>`;
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
    const selected = device.id === state.selectedMapDeviceId ? " selected" : "";
    return `<button class="device-chip${selected}" data-device-id="${escapeHtml(device.id)}">
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



function trackingSessionIsActive(session = state.tracking.session) {
  return Boolean(session && [
    "arming",
    "waiting_for_advertisement",
    "live",
    "stale",
    "scanner_offline",
    "identity_changed",
  ].includes(session.state));
}

function newClientId(prefix) {
  const randomId = window.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${randomId}`;
}

function eventPayload(event) {
  try {
    const parsed = JSON.parse(event.data);
    return parsed.payload || parsed;
  } catch {
    return null;
  }
}

function medianValue(values) {
  const sorted = values.slice().sort((left, right) => left - right);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

function trackingTrend(samples) {
  const current = samples.slice(-5).map((sample) => Number(sample.smoothed_rssi));
  const previous = samples.slice(-10, -5).map((sample) => Number(sample.smoothed_rssi));
  if (current.length < 5 || previous.length < 5) {
    return { label: "Collecting signal window", delta: null };
  }
  const delta = medianValue(current) - medianValue(previous);
  if (delta >= 3) return { label: `Signal stronger by ${formatNumber(delta, 1)} dB`, delta };
  if (delta <= -3) return { label: `Signal weaker by ${formatNumber(Math.abs(delta), 1)} dB`, delta };
  return { label: `Signal steady within ${formatNumber(Math.abs(delta), 1)} dB`, delta };
}

function prepareFinderAudio() {
  const tracking = state.tracking;
  if (tracking.audioContext) {
    tracking.audioContext.resume();
    tracking.soundEnabled = true;
    return true;
  }
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) {
    tracking.soundEnabled = false;
    return false;
  }
  const context = new AudioContext();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = 320;
  gain.gain.value = 0;
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start();
  context.resume();
  tracking.audioContext = context;
  tracking.oscillator = oscillator;
  tracking.gain = gain;
  tracking.soundEnabled = true;
  return true;
}

function updateFinderAudio(level, stale = false) {
  const tracking = state.tracking;
  if (!tracking.audioContext || !tracking.gain || !tracking.oscillator) return;
  const now = tracking.audioContext.currentTime;
  const audibleLevel = tracking.soundEnabled && !stale
    ? Math.max(0, Math.min(1, Number(level) || 0))
    : 0;
  tracking.oscillator.frequency.setTargetAtTime(300 + audibleLevel * 900, now, 0.04);
  tracking.gain.gain.setTargetAtTime(0.14 * audibleLevel ** 2, now, 0.08);
}

function releaseFinderAudio() {
  const tracking = state.tracking;
  if (tracking.gain && tracking.audioContext) {
    tracking.gain.gain.setTargetAtTime(0, tracking.audioContext.currentTime, 0.03);
  }
  if (tracking.audioContext) {
    const context = tracking.audioContext;
    setTimeout(() => context.close().catch(() => {}), 120);
  }
  tracking.audioContext = null;
  tracking.oscillator = null;
  tracking.gain = null;
}

function stopTrackingRuntime() {
  const tracking = state.tracking;
  if (tracking.source) tracking.source.close();
  if (tracking.leaseTimer) clearInterval(tracking.leaseTimer);
  if (tracking.staleTimer) clearInterval(tracking.staleTimer);
  if (tracking.positionWatchId !== null && tracking.positionWatchId !== undefined) {
    navigator.geolocation?.clearWatch(tracking.positionWatchId);
  }
  tracking.source = null;
  tracking.leaseTimer = null;
  tracking.staleTimer = null;
  tracking.positionWatchId = null;
  tracking.lastPosition = null;
  tracking.positionSending = false;
  updateFinderAudio(0, true);
}

function clearSignalFinder() {
  stopTrackingRuntime();
  releaseFinderAudio();
  Object.assign(state.tracking, {
    device: null,
    session: null,
    samples: [],
    positions: [],
    lastReceivedAt: 0,
    notice: "",
    shouldFitMap: false,
  });
  $("#signalFinder").classList.add("hidden");
  state.trackingLayers?.clearLayers();
}

async function stopSignalFinder(reason = "operator_stopped", preserveAudio = false) {
  const tracking = state.tracking;
  const session = tracking.session;
  stopTrackingRuntime();
  if (trackingSessionIsActive(session)) {
    try {
      const stopped = await api(
        `/api/tracking-sessions/${encodeURIComponent(session.id)}?reason=${encodeURIComponent(reason)}`,
        { method: "DELETE" },
      );
      tracking.session = stopped;
    } catch (error) {
      console.warn("Tracking stop was not acknowledged:", error.message);
    }
  }
  if (preserveAudio) {
    Object.assign(tracking, {
      device: null,
      session: null,
      samples: [],
      positions: [],
      lastReceivedAt: 0,
      notice: "",
    });
    state.trackingLayers?.clearLayers();
    return;
  }
  clearSignalFinder();
}

function mergeTrackingPosition(position) {
  if (!position?.position_id) return;
  const tracking = state.tracking;
  const index = tracking.positions.findIndex((item) => item.position_id === position.position_id);
  if (index >= 0) tracking.positions[index] = position;
  else tracking.positions.push(position);
  tracking.positions = tracking.positions
    .slice(-500)
    .sort((left, right) => new Date(left.observed_at) - new Date(right.observed_at));
}

function handleTrackingSample(sample) {
  const tracking = state.tracking;
  if (!sample || sample.session_id !== tracking.session?.id || sample.delayed) return;
  if (!tracking.samples.some((item) => item.sample_id === sample.sample_id)) {
    tracking.samples.push(sample);
    tracking.samples = tracking.samples
      .slice(-240)
      .sort((left, right) => new Date(left.observed_at) - new Date(right.observed_at));
  }
  tracking.lastReceivedAt = Date.now();
  tracking.session.state = "live";
  renderSignalFinder();
  renderTrackingOverlay();
}

function handleTrackingSessionState(update) {
  const tracking = state.tracking;
  if (!update) return;
  const sessionId = update.id || update.session_id;
  if (sessionId !== tracking.session?.id) return;
  tracking.session = {
    ...tracking.session,
    ...update,
    id: sessionId,
    assignments: update.assignments || tracking.session.assignments,
  };
  if (!trackingSessionIsActive(tracking.session)) {
    stopTrackingRuntime();
  }
  renderSignalFinder();
  renderTrackingOverlay();
}

function connectTrackingEvents(sessionId) {
  const tracking = state.tracking;
  if (tracking.source) tracking.source.close();
  const source = new EventSource(`/api/tracking-sessions/${encodeURIComponent(sessionId)}/events`);
  tracking.source = source;
  source.addEventListener("connected", () => {
    tracking.notice = "";
    renderSignalFinder();
  });
  source.addEventListener("tracking_sample", (event) => handleTrackingSample(eventPayload(event)));
  source.addEventListener("scanner_position", (event) => {
    mergeTrackingPosition(eventPayload(event));
    renderSignalFinder();
    renderTrackingOverlay();
  });
  source.addEventListener("session_state", (event) => handleTrackingSessionState(eventPayload(event)));
  source.onerror = () => {
    tracking.notice = "Live stream reconnecting";
    renderSignalFinder();
  };
}

async function renewTrackingLease() {
  const tracking = state.tracking;
  if (!trackingSessionIsActive()) return;
  try {
    const session = await api(
      `/api/tracking-sessions/${encodeURIComponent(tracking.session.id)}/lease`,
      { method: "POST" },
    );
    tracking.session = { ...tracking.session, ...session };
  } catch (error) {
    tracking.notice = error.message;
    if (error.message.startsWith("409") || error.message.startsWith("404")) {
      tracking.session.state = "expired";
      stopTrackingRuntime();
    }
    renderSignalFinder();
  }
}

function startWalkPositionCapture() {
  const tracking = state.tracking;
  if (tracking.session?.mode !== "walk") return;
  const availabilityError = browserGeolocationAvailabilityError();
  if (availabilityError) {
    tracking.notice = availabilityError;
    renderSignalFinder();
    return;
  }
  const scannerId = tracking.session.assignments?.[0]?.scanner_id;
  if (!scannerId) return;
  tracking.positionWatchId = navigator.geolocation.watchPosition(
    async (position) => {
      const latitude = Number(position.coords.latitude);
      const longitude = Number(position.coords.longitude);
      const capturedAt = normalizedGeolocationTimestamp(position);
      const previous = tracking.lastPosition;
      const moved = previous
        ? haversineDistance(previous.latitude, previous.longitude, latitude, longitude)
        : Infinity;
      const elapsed = previous ? capturedAt - previous.capturedAt : Infinity;
      if (
        tracking.positionSending
        || (elapsed < TRACKING_POSITION_INTERVAL_MS && moved < TRACKING_POSITION_MOVE_M)
      ) {
        return;
      }
      tracking.positionSending = true;
      const payload = {
        position_id: newClientId("walk"),
        scanner_id: scannerId,
        observed_at: new Date(capturedAt).toISOString(),
        latitude,
        longitude,
        accuracy_m: Number(position.coords.accuracy) || 0,
      };
      try {
        const recorded = await api(
          `/api/tracking-sessions/${encodeURIComponent(tracking.session.id)}/positions`,
          { method: "POST", body: JSON.stringify(payload) },
        );
        tracking.lastPosition = { latitude, longitude, capturedAt };
        tracking.notice = "";
        mergeTrackingPosition(recorded);
        renderSignalFinder();
        renderTrackingOverlay();
      } catch (error) {
        tracking.notice = `Walk position rejected: ${error.message}`;
        renderSignalFinder();
      } finally {
        tracking.positionSending = false;
      }
    },
    (error) => {
      tracking.notice = `Walk position unavailable: ${describeGeolocationError(error)}`;
      renderSignalFinder();
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 1000 },
  );
}

async function startSignalFinder(device, mode = "fixed") {
  prepareFinderAudio();
  if (state.tracking.session) {
    await stopSignalFinder("replaced_by_operator", true);
  }
  let session = null;
  let hydrated = null;
  try {
    session = await api(`/api/devices/${encodeURIComponent(device.id)}/tracking-sessions`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
    hydrated = await api(`/api/tracking-sessions/${encodeURIComponent(session.id)}`);
  } catch (error) {
    if (session?.id) {
      try {
        await api(
          `/api/tracking-sessions/${encodeURIComponent(session.id)}?reason=dashboard_start_failed`,
          { method: "DELETE" },
        );
      } catch {
        // The lease bounds an unacknowledged cleanup.
      }
    }
    releaseFinderAudio();
    throw error;
  }
  const samples = (hydrated.samples || []).filter((sample) => !sample.delayed);
  Object.assign(state.tracking, {
    device,
    session: hydrated,
    samples: samples.slice(-240),
    positions: hydrated.positions || [],
    lastReceivedAt: 0,
    mode: hydrated.mode,
    notice: "",
    shouldFitMap: true,
  });
  const latestSample = samples.at(-1);
  if (
    latestSample
    && Date.now() - new Date(latestSample.observed_at).getTime()
      <= (hydrated.sample_stale_seconds || 6) * 1000
  ) {
    state.tracking.lastReceivedAt = Date.now();
  }
  connectTrackingEvents(hydrated.id);
  state.tracking.leaseTimer = setInterval(renewTrackingLease, 10000);
  state.tracking.staleTimer = setInterval(renderSignalFinder, 250);
  if (hydrated.mode === "walk") startWalkPositionCapture();
  state.selectedMapDeviceId = device.id;
  state.selectedMapScannerId = hydrated.assignments?.[0]?.scanner_id || state.selectedMapScannerId;
  closeDeviceDrawer();
  $("#signalFinder").classList.remove("hidden");
  setView("locations");
  renderSignalFinder();
  renderLocations();
}

function focusActiveSignalFinder() {
  const assignment = state.tracking.session?.assignments?.[0];
  if (assignment) state.selectedMapScannerId = assignment.scanner_id;
  state.tracking.shouldFitMap = true;
  closeDeviceDrawer();
  setView("locations");
  renderSignalFinder();
  renderLocations();
}

function renderSignalFinder() {
  const tracking = state.tracking;
  const session = tracking.session;
  if (!session || !tracking.device) return;
  $("#signalFinder").classList.remove("hidden");
  const latest = tracking.samples.at(-1);
  const staleAfterMs = (session.sample_stale_seconds || 6) * 1000;
  const stale = !latest
    || !tracking.lastReceivedAt
    || Date.now() - tracking.lastReceivedAt > staleAfterMs;
  const level = stale ? 0 : Math.max(0, Math.min(1, Number(latest.signal_level) || 0));
  const stateName = stale && session.state === "live" ? "stale" : session.state;
  const name = tracking.device.alias
    || tracking.device.display_name
    || tracking.device.primary_address
    || "BLE device";
  const trend = trackingTrend(tracking.samples);
  const meter = $(".signal-meter");

  $("#finderTargetName").textContent = name;
  $("#finderSessionState").textContent = String(stateName || "unknown").replaceAll("_", " ");
  $("#finderRssi").textContent = stale ? "Waiting" : `${formatNumber(latest.smoothed_rssi, 1)} dBm`;
  $("#finderTrend").textContent = stale ? "No recent accepted advertisement" : trend.label;
  $("#finderMode").textContent = session.mode === "walk"
    ? `Walk measurement · ${tracking.positions.length} positions`
    : "Fixed measurement anchor";
  $("#finderMeterFill").style.width = `${Math.round(level * 100)}%`;
  meter.setAttribute("aria-valuenow", String(Math.round(level * 100)));
  $("#finderSoundBtn").textContent = tracking.soundEnabled ? "Mute" : "Sound";
  $("#stopFinderBtn").textContent = trackingSessionIsActive(session) ? "Stop" : "Close";

  const dot = $("#finderStateDot");
  dot.className = "status-dot";
  if (stateName === "scanner_offline" || !trackingSessionIsActive(session)) dot.classList.add("muted");
  else if (stale || stateName !== "live") dot.classList.add("warning");

  const stateMessages = {
    arming: "Focus command pending at the assigned scanner",
    waiting_for_advertisement: "Scanner armed; waiting for an accepted target identity",
    stale: `No accepted target advertisement in the last ${session.sample_stale_seconds || 6} seconds`,
    scanner_offline: "Assigned scanner is offline",
    identity_changed: "Accepted target identities changed; scanner configuration is refreshing",
    expired: "Tracking lease expired",
    stopped: "Tracking stopped",
  };
  $("#finderMessage").textContent = tracking.notice
    || stateMessages[stateName]
    || `${tracking.samples.length} focused RSSI samples received`;
  updateFinderAudio(level, stale || stateName !== "live");
  drawFinderChart(tracking.samples);
}

function drawFinderChart(samples) {
  const canvas = $("#finderChart");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#d8dfe2";
  context.lineWidth = 1;
  context.beginPath();
  for (let y = 16; y < canvas.height; y += 24) {
    context.moveTo(0, y);
    context.lineTo(canvas.width, y);
  }
  context.stroke();
  const values = samples.slice(-80);
  if (values.length < 2) return;
  context.strokeStyle = "#087f8c";
  context.lineWidth = 2;
  context.beginPath();
  values.forEach((sample, index) => {
    const x = (index / (values.length - 1)) * (canvas.width - 16) + 8;
    const rssi = Math.max(-105, Math.min(-35, Number(sample.smoothed_rssi)));
    const y = canvas.height - 8 - ((rssi + 105) / 70) * (canvas.height - 16);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
}

function trackingSignalColor(level) {
  if (!Number.isFinite(Number(level))) return "#7b888e";
  if (Number(level) >= 0.67) return "#258050";
  if (Number(level) >= 0.34) return "#ad6a08";
  return "#bd3346";
}

function nearestTrackingSample(position, maximumDifferenceMs = 5000) {
  const capturedAt = new Date(position.observed_at).getTime();
  let nearest = null;
  let nearestDifference = Infinity;
  for (const sample of state.tracking.samples) {
    const difference = Math.abs(new Date(sample.observed_at).getTime() - capturedAt);
    if (difference < nearestDifference) {
      nearest = sample;
      nearestDifference = difference;
    }
  }
  return nearestDifference <= maximumDifferenceMs ? nearest : null;
}

function renderTrackingOverlay() {
  if (!state.map || !state.trackingLayers) return;
  state.trackingLayers.clearLayers();
  const tracking = state.tracking;
  if (!tracking.session || !tracking.device) return;
  const assignment = tracking.session.assignments?.[0];
  const points = [];

  if (
    assignment
    && assignment.fixed_latitude !== null
    && assignment.fixed_latitude !== undefined
    && assignment.fixed_longitude !== null
    && assignment.fixed_longitude !== undefined
    && Number.isFinite(Number(assignment.fixed_latitude))
    && Number.isFinite(Number(assignment.fixed_longitude))
  ) {
    const anchor = [Number(assignment.fixed_latitude), Number(assignment.fixed_longitude)];
    L.marker(anchor, {
      icon: L.divIcon({
        className: "",
        html: '<span class="finder-anchor" title="Fixed RSSI measurement anchor"></span>',
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      }),
      zIndexOffset: 1200,
    })
      .bindTooltip("Focused RSSI measurement anchor")
      .on("click", () => selectDevice(tracking.device.id))
      .addTo(state.trackingLayers);
    points.push(anchor);

    const distance = Number(tracking.device.estimated_distance_m);
    if (tracking.session.mode === "fixed" && Number.isFinite(distance) && distance > 0) {
      L.circle(anchor, {
        radius: Math.min(distance, 1000),
        color: "#087f8c",
        dashArray: "6 5",
        weight: 2,
        opacity: 0.7,
        fillOpacity: 0.035,
        interactive: false,
      }).addTo(state.trackingLayers);
    }
  }

  if (tracking.session.mode === "walk" && tracking.positions.length) {
    const path = tracking.positions.map((position) => [
      Number(position.latitude),
      Number(position.longitude),
    ]);
    L.polyline(path, {
      color: "#39484f",
      weight: 3,
      opacity: 0.72,
    }).addTo(state.trackingLayers);
    for (const position of tracking.positions) {
      const latLng = [Number(position.latitude), Number(position.longitude)];
      const sample = nearestTrackingSample(position);
      const color = trackingSignalColor(sample?.signal_level);
      L.circle(latLng, {
        radius: Math.max(1, Number(position.accuracy_m) || 0),
        color,
        weight: 1,
        opacity: 0.3,
        fillOpacity: 0.02,
        interactive: false,
      }).addTo(state.trackingLayers);
      L.circleMarker(latLng, {
        radius: 5,
        color: "#ffffff",
        weight: 2,
        fillColor: color,
        fillOpacity: 1,
      })
        .bindTooltip(sample ? `${formatNumber(sample.smoothed_rssi, 1)} dBm` : "Position sample")
        .addTo(state.trackingLayers);
      points.push(latLng);
    }

    const strongest = tracking.samples.reduce((best, sample) => {
      if (!best || Number(sample.rssi) > Number(best.rssi)) return sample;
      return best;
    }, null);
    if (strongest) {
      const strongestAt = new Date(strongest.observed_at).getTime();
      const nearestPosition = tracking.positions.reduce((best, position) => {
        const difference = Math.abs(new Date(position.observed_at).getTime() - strongestAt);
        return !best || difference < best.difference ? { position, difference } : best;
      }, null);
      if (nearestPosition && nearestPosition.difference <= 10000) {
        const latLng = [
          Number(nearestPosition.position.latitude),
          Number(nearestPosition.position.longitude),
        ];
        L.circleMarker(latLng, {
          radius: 11,
          color: "#172126",
          weight: 3,
          fillColor: "#ffffff",
          fillOpacity: 0.35,
        })
          .bindTooltip(`Strongest measured point · ${formatNumber(strongest.rssi, 1)} dBm`)
          .addTo(state.trackingLayers);
      }
    }
  }

  if (tracking.shouldFitMap && points.length) {
    tracking.shouldFitMap = false;
    if (points.length === 1) state.map.setView(points[0], 18);
    else state.map.fitBounds(points, { padding: [60, 60], maxZoom: 19 });
  }
}

function browserGeolocationAvailabilityError() {
  if (window.isSecureContext === false) {
    return "Browser location requires a secure origin. Open the HTTPS dashboard URL printed by run.py.";
  }
  if (!navigator.geolocation) {
    return "This browser does not expose the Geolocation API.";
  }
  return "";
}

function normalizedGeolocationTimestamp(position, receivedAt = Date.now()) {
  const timestamp = Number(position?.timestamp);
  if (
    Number.isFinite(timestamp)
    && Math.abs(timestamp - receivedAt) <= GPS_TIMESTAMP_MAX_SKEW_MS
  ) {
    return timestamp;
  }
  return receivedAt;
}

function describeGeolocationError(error) {
  const detail = String(error?.message || "").trim();
  switch (Number(error?.code)) {
    case 1:
      return "Location permission is blocked. Allow location access for this site and enable Location Services for the browser in macOS System Settings.";
    case 2:
      return "The browser could not determine a position from Location Services or nearby Wi-Fi. Keep Wi-Fi enabled and verify macOS Location Services.";
    case 3:
      return "The location request timed out before the browser produced a position.";
    default:
      return detail || "The browser returned an unknown geolocation error.";
  }
}

function gpsStatusSummary() {
  const gps = state.gps;
  const accuracy = Number(gps.latestPosition?.coords?.accuracy);
  const fixTime = gps.lastFixAt ? new Date(gps.lastFixAt).toLocaleTimeString("id-ID") : "";
  switch (gps.status) {
    case "insecure":
      return "Location blocked: HTTPS required";
    case "unsupported":
      return "Location API unavailable";
    case "permission_denied":
      return "Location permission blocked";
    case "unavailable":
      return "macOS position unavailable; watcher active";
    case "timeout":
      return "Location fix delayed; watcher active";
    case "fix_received":
      return `Location fix received${Number.isFinite(accuracy) ? ` · ±${Math.round(accuracy)} m` : ""}`;
    case "saving":
      return `Saving scanner location${Number.isFinite(accuracy) ? ` · ±${Math.round(accuracy)} m` : ""}`;
    case "live":
      return `Location live${Number.isFinite(accuracy) ? ` · ±${Math.round(accuracy)} m` : ""}${fixTime ? ` · ${fixTime}` : ""}`;
    case "save_error":
      return "Location fix received; backend save failed";
    case "stopped":
      return "Location watcher stopped";
    case "waiting":
    default:
      return "Waiting for macOS location fix";
  }
}

function renderGpsStatus() {
  const gps = state.gps;
  const button = $("#gpsStatusBtn");
  const text = $("#gpsStatusText");
  const dot = $("#gpsStatusDot");
  const summary = gpsStatusSummary();
  if (text) text.textContent = summary;
  if (dot) {
    dot.className = "status-dot";
    if (gps.status !== "live") {
      dot.classList.add(
        ["permission_denied", "insecure", "unsupported", "save_error"].includes(gps.status)
          ? "muted"
          : "warning",
      );
    }
  }
  if (button) {
    const position = gps.latestPosition;
    const diagnostics = [
      `Secure context: ${window.isSecureContext ? "yes" : "no"}`,
      `Permission: ${gps.permissionState}`,
      `Watcher: ${gps.watchId === null ? "inactive" : "active"}`,
      position && gps.lastFixAt
        ? `Last fix: ${new Date(gps.lastFixAt).toISOString()}`
        : "Last fix: none",
      position ? `Accuracy: ${Number(position.coords.accuracy)} m` : "Accuracy: none",
      gps.lastSavedAt
        ? `Backend save: ${new Date(gps.lastSavedAt).toISOString()}`
        : "Backend save: none",
      gps.lastError ? `Browser error: ${describeGeolocationError(gps.lastError)}` : "",
      gps.lastSaveError ? `Backend error: ${gps.lastSaveError.message}` : "",
    ].filter(Boolean);
    button.title = diagnostics.join("\n");
  }
  const mapStatus = $("#mapGpsStatus");
  if (mapStatus) mapStatus.textContent = summary;
  publishGpsDiagnostic();
}

function publishGpsDiagnostic() {
  const gps = state.gps;
  const position = gps.latestPosition;
  const errorCode = Number(gps.lastError?.code);
  const accuracy = Number(position?.coords?.accuracy);
  const payload = {
    recorded_at: new Date().toISOString(),
    stage: gps.status,
    page_origin: window.location.origin,
    secure_context: Boolean(window.isSecureContext),
    permission_state: gps.permissionState,
    watcher_active: gps.watchId !== null,
    visibility_state: document.visibilityState || "unknown",
    position_timestamp: position && gps.lastFixAt
      ? new Date(gps.lastFixAt).toISOString()
      : null,
    accuracy_m: Number.isFinite(accuracy) && accuracy >= 0 ? accuracy : null,
    error_code: Number.isFinite(errorCode) ? errorCode : null,
    error_message: gps.lastError
      ? String(gps.lastError.message || describeGeolocationError(gps.lastError)).slice(0, 500)
      : gps.lastSaveError
      ? String(gps.lastSaveError.message || gps.lastSaveError).slice(0, 500)
      : null,
  };
  const signature = JSON.stringify({ ...payload, recorded_at: null });
  if (signature === gps.lastDiagnosticSignature) return;
  gps.lastDiagnosticSignature = signature;
  api("/api/browser/location-diagnostic", {
    method: "POST",
    body: JSON.stringify(payload),
  }).catch((error) => {
    console.error("Unable to publish browser location diagnostics:", error);
  });
}

async function inspectGeolocationPermission() {
  if (!navigator.permissions?.query) return;
  try {
    const permission = await navigator.permissions.query({ name: "geolocation" });
    const applyPermission = () => {
      state.gps.permissionState = permission.state || "unknown";
      if (permission.state === "denied") state.gps.status = "permission_denied";
      renderGpsStatus();
    };
    applyPermission();
    permission.addEventListener?.("change", applyPermission);
  } catch {
    state.gps.permissionState = "not_reported_by_browser";
    renderGpsStatus();
  }
}

function localScannerId() {
  return state.runtimeConfig?.local_scanner_id || "scn_dev_lab_001";
}

function getLocalScanner() {
  const configuredId = localScannerId();
  return state.scanners.find((scanner) => scanner.id === configuredId)
    || (state.scanners.length === 1 ? state.scanners[0] : null);
}

function freshGpsPosition() {
  if (
    !state.gps.latestPosition
    || Date.now() - state.gps.receivedAt > GPS_POSITION_CACHE_MS
  ) {
    return null;
  }
  return state.gps.latestPosition;
}

function resolveGpsWaiters(position) {
  for (const waiter of state.gps.waiters) {
    clearTimeout(waiter.timeoutId);
    waiter.resolve(position);
  }
  state.gps.waiters.clear();
}

function rejectGpsWaiters(error) {
  for (const waiter of state.gps.waiters) {
    clearTimeout(waiter.timeoutId);
    waiter.reject(error);
  }
  state.gps.waiters.clear();
}

async function persistLocalScannerPosition(position, options = {}) {
  const scanner = getLocalScanner();
  if (!scanner || !position?.coords) {
    renderGpsStatus();
    return null;
  }

  const latitude = Number(position.coords.latitude);
  const longitude = Number(position.coords.longitude);
  const accuracy = Number(position.coords.accuracy);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    throw new Error("Browser returned invalid scanner coordinates");
  }
  if (!Number.isFinite(accuracy) || accuracy < 0) {
    throw new Error("Browser returned invalid scanner-position accuracy");
  }

  const gps = state.gps;
  if (gps.savePromise) {
    gps.savePending = true;
    await gps.savePromise;
    return options.force
      ? persistLocalScannerPosition(gps.latestPosition || position, { force: true })
      : null;
  }

  const now = Date.now();
  const elapsed = now - gps.lastSavedAt;
  const previous = gps.lastSavedPosition;
  const moved = previous
    ? haversineDistance(previous.latitude, previous.longitude, latitude, longitude)
    : Infinity;
  if (
    !options.force
    && previous
    && (
      elapsed < GPS_MINIMUM_SAVE_INTERVAL_MS
      || (elapsed < GPS_REGULAR_SAVE_INTERVAL_MS && moved < GPS_MOVE_SAVE_THRESHOLD_M)
    )
  ) {
    gps.status = "live";
    renderGpsStatus();
    return null;
  }

  const observedAt = (
    position === gps.latestPosition && gps.lastFixAt
      ? gps.lastFixAt
      : normalizedGeolocationTimestamp(position, now)
  );
  const payload = {
    observed_at: new Date(observedAt).toISOString(),
    latitude,
    longitude,
    accuracy_m: accuracy,
    source: "browser_geolocation",
  };
  gps.status = "saving";
  gps.lastSaveError = null;
  renderGpsStatus();
  const operation = api(
    `/api/scanners/${encodeURIComponent(scanner.id)}/position`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  gps.savePromise = operation;
  try {
    const saved = await operation;
    gps.lastSavedAt = now;
    gps.lastSavedPosition = { latitude, longitude };
    gps.status = "live";
    gps.lastSaveError = null;
    const index = state.scanners.findIndex((item) => item.id === scanner.id);
    if (index >= 0) state.scanners[index] = { ...state.scanners[index], ...saved };
    if (state.currentView === "scanners") renderScanners();
    if (state.currentView === "locations") renderLocations();
    const formScannerId = $("#scannerForm")?.elements?.id?.value;
    if (formScannerId === scanner.id) fillScannerForm(scanner.id);
    renderGpsStatus();
    return saved;
  } catch (error) {
    gps.status = "save_error";
    gps.lastSaveError = error;
    renderGpsStatus();
    throw error;
  } finally {
    gps.savePromise = null;
    if (gps.savePending) {
      gps.savePending = false;
      const pending = gps.latestPosition;
      if (pending && pending !== position) {
        persistLocalScannerPosition(pending).catch((error) => {
          console.error("Unable to persist the pending scanner position:", error);
        });
      }
    }
  }
}

function handleGpsPosition(position) {
  if (state.gps.diagnosticTimer !== null) {
    clearTimeout(state.gps.diagnosticTimer);
    state.gps.diagnosticTimer = null;
  }
  const receivedAt = Date.now();
  state.gps.latestPosition = position;
  state.gps.receivedAt = receivedAt;
  state.gps.lastFixAt = normalizedGeolocationTimestamp(position, receivedAt);
  state.gps.status = "fix_received";
  state.gps.lastError = null;
  renderGpsStatus();
  resolveGpsWaiters(position);
  persistLocalScannerPosition(position).catch((error) => {
    console.error("Unable to persist the live scanner position:", error);
  });
}

function startGpsTracking() {
  const availabilityError = browserGeolocationAvailabilityError();
  if (availabilityError) {
    state.gps.lastError = new Error(availabilityError);
    state.gps.status = window.isSecureContext === false ? "insecure" : "unsupported";
    renderGpsStatus();
    return;
  }
  if (state.gps.watchId !== null) return;
  state.gps.status = "waiting";
  state.gps.lastAttemptAt = Date.now();
  state.gps.lastError = null;
  inspectGeolocationPermission();
  renderGpsStatus();
  try {
    state.gps.watchId = navigator.geolocation.watchPosition(
      handleGpsPosition,
      (error) => {
        state.gps.lastError = error;
        const code = Number(error?.code);
        state.gps.status = code === 1
          ? "permission_denied"
          : code === 2
          ? "unavailable"
          : "timeout";
        if (code === 1) {
          state.gps.permissionState = "denied";
          rejectGpsWaiters(error);
          navigator.geolocation.clearWatch(state.gps.watchId);
          state.gps.watchId = null;
        }
        renderGpsStatus();
      },
      GPS_WATCH_OPTIONS,
    );
    state.gps.diagnosticTimer = setTimeout(() => {
      if (!state.gps.latestPosition && state.gps.watchId !== null) {
        state.gps.status = "timeout";
        renderGpsStatus();
      }
    }, GPS_DIAGNOSTIC_WAIT_MS);
    renderGpsStatus();
  } catch (error) {
    state.gps.lastError = error;
    state.gps.status = "unsupported";
    rejectGpsWaiters(error);
    renderGpsStatus();
  }
}

function stopGpsTracking() {
  if (state.gps.diagnosticTimer !== null) {
    clearTimeout(state.gps.diagnosticTimer);
    state.gps.diagnosticTimer = null;
  }
  if (state.gps.watchId !== null && navigator.geolocation) {
    navigator.geolocation.clearWatch(state.gps.watchId);
  }
  state.gps.watchId = null;
  state.gps.status = "stopped";
  renderGpsStatus();
  rejectGpsWaiters(new Error("Dashboard closed before a scanner position was available"));
}

function waitForGpsPosition() {
  const cached = freshGpsPosition();
  if (cached) return Promise.resolve(cached);

  startGpsTracking();
  if (state.gps.watchId === null && state.gps.lastError) {
    return Promise.reject(state.gps.lastError);
  }
  return new Promise((resolve, reject) => {
    const waiter = { resolve, reject, timeoutId: null };
    waiter.timeoutId = setTimeout(() => {
      state.gps.waiters.delete(waiter);
      reject(state.gps.lastError || { code: 3, message: "" });
    }, GPS_POSITION_WAIT_MS);
    state.gps.waiters.add(waiter);
  });
}

async function detectScannerLocation(options = {}) {
  const controls = [
    $("#detectScannerLocationFormBtn"),
    $("#detectScannerLocationBtn"),
  ].filter(Boolean);
  for (const control of controls) {
    control.dataset.idleLabel ||= control.textContent;
    control.disabled = true;
    control.textContent = "Locating...";
  }

  try {
    const position = await waitForGpsPosition();
    await persistLocalScannerPosition(position, { force: true });
    state.selectedMapScannerId = getLocalScanner()?.id || state.selectedMapScannerId;
    renderLocations();
    return {
      latitude: Number(position.coords.latitude),
      longitude: Number(position.coords.longitude),
      accuracy_m: Number(position.coords.accuracy),
    };
  } catch (error) {
    if (!options.quiet) {
      alert(`Location unavailable: ${describeGeolocationError(error)}`);
    }
    return null;
  } finally {
    for (const control of controls) {
      control.disabled = false;
      control.textContent = control.dataset.idleLabel;
    }
  }
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
  const browserLocation = data.browser_location || {};
  const scannerPosition = (data.scanner_positions || []).find(
    (position) => position.scanner_id === localScannerId(),
  );
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
    ${kv("Scanner position source", scannerPosition?.source || "not reported")}
    ${kv("Scanner position fix", scannerPosition?.observed_at ? formatDate(scannerPosition.observed_at) : "-")}
    ${kv("Scanner position age", scannerPosition?.age_seconds === null || scannerPosition?.age_seconds === undefined ? "-" : `${formatNumber(scannerPosition.age_seconds, 1)} s`)}
    ${kv("Scanner position accuracy", scannerPosition?.accuracy_m === null || scannerPosition?.accuracy_m === undefined ? "-" : `${formatNumber(scannerPosition.accuracy_m, 1)} m`)}
    ${kv("Browser location stage", browserLocation.stage || "not reported")}
    ${kv("Browser location origin", browserLocation.page_origin || "-")}
    ${kv("Browser location permission", browserLocation.permission_state || "-")}
    ${kv("Browser location watcher", browserLocation.watcher_active === undefined ? "-" : browserLocation.watcher_active ? "active" : "inactive")}
    ${kv("Browser location error", browserLocation.error_message || "-")}
  `;
}

function connectLive() {
  if (state.live) state.live.close();
  const source = new EventSource("/api/live/events");
  state.live = source;
  source.addEventListener("connected", () => setLiveState("Connected", "good"));
  source.addEventListener("ping", () => setLiveState("Connected", "good"));
  ["scanner_heartbeat", "scanner_position_updated", "observations_ingested", "scanner_updated", "device_correlation_changed", "device_tracking_changed", "runtime_state_changed"].forEach((type) => {
    source.addEventListener(type, scheduleLiveRefresh);
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
  $("#gpsStatusBtn").addEventListener("click", () => detectScannerLocation());
  $("#deviceSearch").addEventListener("input", renderDevices);
  $("#deviceStatusFilter").addEventListener("change", loadDevices);
  $("#includeIgnored").addEventListener("change", loadDevices);
  $("#includeTransient").addEventListener("change", loadDevices);
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
    requestMapFit();
  });
  $("#mapDeviceSelect").addEventListener("change", (event) => {
    if (event.target.value) selectDevice(event.target.value);
  });
  $("#detectScannerLocationBtn").addEventListener("click", () => detectScannerLocation());
  $("#fitMapBtn").addEventListener("click", requestMapFit);
  $("#closeDeviceDrawer").addEventListener("click", closeDeviceDrawer);
  $("#deviceDrawerBackdrop").addEventListener("click", closeDeviceDrawer);
  $("#locationCanvas").addEventListener("click", (event) => {
    const button = event.target.closest("[data-map-device-id]");
    if (button) selectDevice(button.dataset.mapDeviceId);
  });
  $("#finderSoundBtn").addEventListener("click", () => {
    if (state.tracking.soundEnabled) {
      state.tracking.soundEnabled = false;
      updateFinderAudio(0, true);
    } else {
      prepareFinderAudio();
      state.tracking.soundEnabled = true;
    }
    renderSignalFinder();
  });
  $("#stopFinderBtn").addEventListener("click", () => stopSignalFinder());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#deviceDrawer").classList.contains("hidden")) {
      closeDeviceDrawer();
    }
  });
  window.addEventListener("beforeunload", () => {
    if (state.liveRefreshTimer !== null) clearTimeout(state.liveRefreshTimer);
    stopTrackingRuntime();
    stopGpsTracking();
  });
}

bindEvents();
startGpsTracking();
refreshAll();
connectLive();
setInterval(() => {
  refreshView(state.currentView).catch((error) => {
    console.error("Unable to refresh dashboard view:", error);
  });
}, 30000);
