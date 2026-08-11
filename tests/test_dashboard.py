from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "dashboard" / "app.js"
INDEX_HTML = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"


def test_dashboard_starts_persistent_geolocation_before_initial_refresh():
    source = APP_JS.read_text(encoding="utf-8")
    startup = source[source.rfind("bindEvents();") :]

    assert "navigator.geolocation.watchPosition(" in source
    assert startup.index("startGpsTracking();") < startup.index("refreshAll();")
    assert "enableHighAccuracy: true" in source
    assert "maximumAge: 5000" in source
    assert "GPS_DIAGNOSTIC_WAIT_MS = 15000" in source


def test_live_geolocation_uses_scanner_position_endpoint():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function persistLocalScannerPosition")
    end = source.index("\nfunction handleGpsPosition", start)
    implementation = source[start:end]

    assert "/position`" in implementation
    assert 'method: "POST"' in implementation
    assert 'source: "browser_geolocation"' in implementation
    assert 'method: "PATCH"' not in implementation


def test_dashboard_exposes_location_pipeline_diagnostics():
    source = APP_JS.read_text(encoding="utf-8")

    assert "Secure context:" in source
    assert "Permission:" in source
    assert "Watcher:" in source
    assert "Backend save:" in source
    assert 'gps.status = "save_error"' in source
    assert 'api("/api/browser/location-diagnostic"' in source


def test_dashboard_exposes_firmware_radio_and_transport_telemetry():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'kv("Firmware", latestHeartbeat.firmware_version' in source
    assert 'kv("Reset reason", scannerHealth.reset_reason' in source
    assert 'kv("Radio", scannerHealth.radio_state' in source
    assert 'kv("Scan callbacks", scannerHealth.scan_callback_count' in source
    assert 'kv("Last advertisement age"' in source
    assert 'kv("Largest heap block"' in source
    assert 'kv("Transport status / duration"' in source
    assert 'kv("Backlog drain frames"' in source
    assert 'kv("JSON frame overflows"' in source


def test_geolocation_timestamp_falls_back_when_browser_epoch_is_invalid():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function normalizedGeolocationTimestamp")
    end = source.index("\nfunction describeGeolocationError", start)
    implementation = source[start:end]

    assert "GPS_TIMESTAMP_MAX_SKEW_MS = 5 * 60 * 1000" in source
    assert "Math.abs(timestamp - receivedAt) <= GPS_TIMESTAMP_MAX_SKEW_MS" in implementation
    assert "return receivedAt" in implementation
    assert "state.gps.lastFixAt = normalizedGeolocationTimestamp(position, receivedAt)" in source
    assert "const capturedAt = normalizedGeolocationTimestamp(position)" in source


def test_present_ble_count_remains_compatible_with_a_running_older_backend():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function renderOverview")
    end = source.index("\nasync function loadDevices", start)
    implementation = source[start:end]

    assert "data.present_ble_records ??" in implementation
    assert "Number(data.active_devices || 0)" in implementation
    assert "Number(data.active_unresolved_identities || 0)" in implementation


def test_device_and_map_views_hide_transient_random_broadcasts_by_default():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'params.set("include_transient", String(Boolean(includeTransient || includeExpired)))' in source
    assert 'api("/api/devices?include_transient=false")' in source
    assert 'id="includeTransient"' in INDEX_HTML.read_text(encoding="utf-8")


def test_live_map_refresh_preserves_operator_viewport():
    source = APP_JS.read_text(encoding="utf-8")
    render_start = source.index("function renderRealMap")
    render_end = source.index("\nfunction applyRequestedMapViewport", render_start)
    render_implementation = source[render_start:render_end]
    fit_start = render_end
    fit_end = source.index("\nfunction requestMapFit", fit_start)
    fit_implementation = source[fit_start:fit_end]

    assert "state.mapLayers.clearLayers()" in render_implementation
    assert "applyRequestedMapViewport(focusBounds)" in render_implementation
    assert ".fitBounds(" not in render_implementation
    assert ".setView(" not in render_implementation
    assert "if (!state.mapFitRequested && !canFitFirstAvailableData) return" in fit_implementation
    assert "state.map.fitBounds(" in fit_implementation
    assert "state.map.setView(" in fit_implementation


def test_map_fit_is_explicit_and_manual_navigation_cancels_pending_fit():
    source = APP_JS.read_text(encoding="utf-8")

    assert "mapFitRequested: true" in source
    assert "mapUserInteracted: false" in source
    assert '["pointerdown", "wheel", "touchstart", "keydown"]' in source
    assert 'state.map.on("dragstart", preserveOperatorViewport)' in source
    assert '$("#fitMapBtn").addEventListener("click", requestMapFit)' in source
    assert "state.selectedMapScannerId = event.target.value;\n    requestMapFit();" in source


def test_tracking_stream_rehydrates_history_after_every_sse_connection():
    source = APP_JS.read_text(encoding="utf-8")
    connect_start = source.index("function connectTrackingEvents")
    connect_end = source.index("\nasync function renewTrackingLease", connect_start)
    implementation = source[connect_start:connect_end]

    assert "async function rehydrateTrackingSession" in source
    assert 'source.addEventListener("connected"' in implementation
    assert "rehydrateTrackingSession(sessionId);" in implementation
    assert "new Map(" in source[source.index("async function rehydrateTrackingSession"):connect_start]
    assert "sample.sample_id" in source[source.index("async function rehydrateTrackingSession"):connect_start]
    assert "position.position_id" in source[source.index("async function rehydrateTrackingSession"):connect_start]


def test_tracking_freshness_and_trend_use_capture_time_windows():
    source = APP_JS.read_text(encoding="utf-8")
    trend_start = source.index("function trackingTrend")
    trend_end = source.index("\nfunction prepareFinderAudio", trend_start)
    trend = source[trend_start:trend_end]
    sample_start = source.index("function handleTrackingSample")
    sample_end = source.index("\nfunction handleTrackingSessionState", sample_start)
    sample_handler = source[sample_start:sample_end]

    assert "trend_current_seconds || 4" in trend
    assert "trend_previous_seconds || 12" in trend
    assert "new Date(sample.observed_at).getTime()" in trend
    assert "new Date(sample.observed_at).getTime()" in sample_handler
    assert "tracking.lastReceivedAt = Date.now()" not in sample_handler
    assert "sample_stale_seconds || 12" in source
    assert "sample_stale_seconds || 6" not in source


def test_signal_finder_audio_uses_loud_df_style_loop_and_stale_mute():
    source = APP_JS.read_text(encoding="utf-8")
    audio_start = source.index("function createFinderToneBuffer")
    audio_end = source.index("\nfunction stopTrackingRuntime", audio_start)
    implementation = source[audio_start:audio_end]

    assert "const FINDER_TONE_FREQUENCY_HZ = 440" in source
    assert "const FINDER_TONE_DURATION_SECONDS = 0.4" in source
    assert "const FINDER_TONE_ATTACK_SECONDS = 0.02" in source
    assert "const FINDER_TONE_RELEASE_SECONDS = 0.04" in source
    assert "const FINDER_TONE_MAX_GAIN = 0.42" in source
    assert "const FINDER_TONE_GAIN_EXPONENT = 1.35" in source
    assert "context.createBufferSource()" in implementation
    assert "source.loop = true" in implementation
    assert "context.createOscillator()" not in implementation
    assert "tracking.soundEnabled && !stale" in implementation
    assert "FINDER_TONE_MAX_GAIN * audibleLevel ** FINDER_TONE_GAIN_EXPONENT" in implementation
