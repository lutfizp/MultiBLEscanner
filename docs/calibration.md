# RSSI Location Evidence

## Implemented Models

The backend now keeps two separate evidence paths:

1. **RSSI sequence evidence** from [Scientific Reports](https://www.nature.com/articles/s41598-022-06201-y): two consecutive five-reading windows, absolute change, beta weighting, `tanh` `rssi_metric`, and RSSI-only reliability.
2. **Radial distance baseline** from the ESP32 BLE evaluation in [ELKHA](https://jurnal.untan.ac.id/index.php/Elkha/article/view/97739):

   `d = 10 ^ ((A - RSSI) / (10 n))`

   The implementation uses the paper's baseline `A = -47 dBm` at one metre and `n = 2`.

The constants are internal literature-baseline values, not operator settings or per-scanner calibration fields. The API records the model name, constants, current window mean, RSSI metric, and validation status with every estimate.

## What The Map Shows

The device's stored scanner-location snapshot is the centre of the estimate. The modeled distance is rendered as a radial uncertainty ring. One scanner has no bearing information, so the system does not place the device at an invented north/east/forward point. Editing or live-updating a scanner position alone does not move a device. A newer accepted BLE observation snapshots the scanner's latest reported position, whether it comes from the same moved scanner or a different scanner.

For the ELKHA experiment, the reported useful range was approximately four metres in clear line-of-sight conditions. Distances outside that range still appear as model output, but the API marks them `outside_published_baseline_range`; they must not be read as accurate indoor measurements.

The API stores:

- raw RSSI and the current five-sample window mean;
- both RSSI windows, absolute dB change, scanner weights, `rssi_metric`, and reliability;
- `estimated_distance_m` from the journal baseline;
- `distance_model_status` and its provenance;
- stored scanner-anchor latitude/longitude for the radial estimate;
- `location_confidence = 0` for exact coordinates because radius is not a point.

Before ten observations are available for a scanner, the sequence metric is explicitly `window_not_ready`. The distance baseline can still produce a value from the available RSSI, but its sequence reliability remains unavailable.

## Focused Tracking Scale

Signal Finder uses a separate relative display scale:

```text
level = clamp((EMA_RSSI - (-85)) / ((-45) - (-85)), 0, 1)
```

The EMA coefficient is `0.35`. Firmware can produce at most one sample per accepted target every 200 ms. The backend uses a six-second freshness window because real CP2102 acceptance measurements reached the server 3.1-4.5 seconds after RF capture while normal observation traffic shared the serial link. Older or out-of-order samples remain stored but are excluded from live feedback. Trend text compares two consecutive five-sample medians and reports a change only at 3 dB or more.

The `-85` and `-45 dBm` bounds control a meter and audio tone. They are not a distance calibration and do not alter the journal radial model, normal movement status, or stored location. A stronger level means only that the selected accepted identity was measured more strongly at that scanner.

Fixed mode places measurements at the scanner-coordinate snapshot. Walk mode pairs measurements with browser GPS positions when the browser is physically co-located with the moving scanner. The strongest measured point is a search clue, not an estimated transmitter coordinate.

## Why This Is Still An Estimate

BLE RSSI changes with transmitter power, antenna pattern, device orientation, body placement, multipath, walls, fading, and hardware. The UKS research [Inferring proximity from Bluetooth Low Energy RSSI with Unscented Kalman Smoothers](https://arxiv.org/abs/2007.05057) models RSSI sequences for this reason and describes direct RSSI proximity as problematic across devices and environments.

The ELKHA constants are therefore a reproducible baseline, not a universal physical law. A measured site calibration or fingerprint dataset is required for reliable room-level or metre-level accuracy. Multiple fixed scanners are required to reduce the radial uncertainty and estimate a coordinate.

## Capture Floor

Firmware captures factual radio observations down to its practical `-110 dBm` receiver floor. RSSI is not used as the admission rule for the Devices or Location view. The backend preserves weak observations, while unresolved manufacturer-only random broadcasts are classified as transient and hidden from those views by default. A directly captured Local Name can promote display visibility without promoting durable identity. This separates RF collection from operator-facing device promotion and does not claim that any RSSI threshold proves physical identity.
