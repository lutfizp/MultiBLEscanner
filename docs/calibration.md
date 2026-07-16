# RSSI Location Evidence

## Implemented Models

The backend now keeps two separate evidence paths:

1. **RSSI sequence evidence** from [Scientific Reports](https://www.nature.com/articles/s41598-022-06201-y): two consecutive five-reading windows, absolute change, beta weighting, `tanh` `rssi_metric`, and RSSI-only reliability.
2. **Radial distance baseline** from the ESP32 BLE evaluation in [ELKHA](https://jurnal.untan.ac.id/index.php/Elkha/article/view/97739):

   `d = 10 ^ ((A - RSSI) / (10 n))`

   The implementation uses the paper's baseline `A = -47 dBm` at one metre and `n = 2`.

The constants are internal literature-baseline values, not operator settings or per-scanner calibration fields. The API records the model name, constants, current window mean, RSSI metric, and validation status with every estimate.

## What The Map Shows

The device's stored scanner-location snapshot is the centre of the estimate. The modeled distance is rendered as a radial uncertainty ring. One scanner has no bearing information, so the system does not place the device at an invented north/east/forward point. Editing or moving that same scanner does not move an established device anchor; a newer accepted observation from a different scanner identity can move it.

For the ELKHA experiment, the reported useful range was approximately four metres in clear line-of-sight conditions. Distances outside that range still appear as model output, but the API marks them `outside_published_baseline_range`; they must not be read as accurate indoor measurements.

The API stores:

- raw RSSI and the current five-sample window mean;
- both RSSI windows, absolute dB change, scanner weights, `rssi_metric`, and reliability;
- `estimated_distance_m` from the journal baseline;
- `distance_model_status` and its provenance;
- stored scanner-anchor latitude/longitude for the radial estimate;
- `location_confidence = 0` for exact coordinates because radius is not a point.

Before ten observations are available for a scanner, the sequence metric is explicitly `window_not_ready`. The distance baseline can still produce a value from the available RSSI, but its sequence reliability remains unavailable.

## Why This Is Still An Estimate

BLE RSSI changes with transmitter power, antenna pattern, device orientation, body placement, multipath, walls, fading, and hardware. The UKS research [Inferring proximity from Bluetooth Low Energy RSSI with Unscented Kalman Smoothers](https://arxiv.org/abs/2007.05057) models RSSI sequences for this reason and describes direct RSSI proximity as problematic across devices and environments.

The ELKHA constants are therefore a reproducible baseline, not a universal physical law. A measured site calibration or fingerprint dataset is required for reliable room-level or metre-level accuracy. Multiple fixed scanners are required to reduce the radial uncertainty and estimate a coordinate.

## Capture Floor

The firmware `rssi_min` value is only a receiver capture floor. It decides which weak packets are accepted and is not part of the distance equation. The local default is `-85 dBm`, selected from the deployment audit because most accumulated rotating identities were never observed above that level. It is a coverage/noise boundary, not a universal distance claim.
