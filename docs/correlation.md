# Address-Rotation Correlation

## Evidence Paths

The system preserves the observed Bluetooth address and raw packet separately from any logical device relationship. It has three correlation paths.

### Approved AD Token Carryover

An operator can configure a protocol-specific rule for a locally unique static token in a raw AD structure. A valid rule requires:

- `rule_id`
- `ad_type`, for example `0xff`
- `offset_bytes`
- `length_bytes` of at least 5 bytes (40 bits)
- a `company_id` or `service_uuid` scope

The service hashes the selected bytes and stores only that hash in correlation evidence. It accepts a carryover only when exactly one predecessor has the same approved token in the configured time window and the predecessor has met the configured token-observation minimum. This is an operator assertion that the selected field is stable and unique for that protocol; the service does not infer it from a name, vendor, or ordinary service UUID.

### Apple Continuity Transition Proposals

Verified Apple company data is parsed as one or more published Type-Length-Value Continuity messages. The parser records subtype and bounded protocol evidence for Nearby Info, Nearby Action, Handoff, Tethering Target Presence, Magic Switch, Proximity Pairing, AirPlay, and other captured types. Potentially persistent broadcast fields are stored in correlation evidence as scoped SHA-256 hashes rather than duplicated raw values.

When a new random address appears, the live processor evaluates only earlier identities from the same scanner within a 30-second transition window. Candidate evidence can include:

- a short-lived Nearby Info or Nearby Action authentication-tag hash crossing the address change;
- a Tethering Target or Magic Switch token hash;
- a Handoff IV that remains equal or advances by no more than 32 modulo 65,536;
- overlapping Continuity subtypes;
- transition time and boundary RSSI continuity;
- an equal directly read GATT model;
- an equal Proximity Pairing model code.

Protocol transition evidence is required unless GATT model, subtype, time, and RSSI all agree. The highest-scoring candidate is stored as `apple_continuity_transition_v1` with candidate count, runner-up score, score margin, and every contributing signal. Its status is always `proposal`; it never merges records or claims a confirmed physical device. Common model names and subtype similarity alone are insufficient.

This implementation follows the published Apple Continuity TLV layout and documented address-transition artifacts from Celosia and Cunche, *Discontinued Privacy: Leaks in Apple BLE Continuity Protocols*, and the Handoff sequence findings from Martin et al., *Handoff All Your Privacy*. Those papers demonstrate linkable protocol artifacts; they do not make every observation uniquely attributable.

### RSSI-Time Assignment

For uncorrelated randomized addresses observed by the same scanner with trusted time, the service implements the cost described by Akiyama and Taniguchi:

`c(a_i, a_j) = sqrt(tau_ij^2 + (alpha * rho_ij)^2)`

- `tau_ij` is the time from predecessor last observation to successor first observation.
- `rho_ij` is the mean absolute successor RSSI residual against a linear regression fitted to the predecessor's final RSSI window.
- The service solves candidate pairs globally and adds explicit unmatched choices, so it never forces every address into a match.

`alpha` can be supplied from a deployment calibration. Otherwise the service records a per-run 90th-percentile width match between candidate time gaps and residuals. That scaling is useful to reproduce the paper's construction, but it is not a portable probability or an acceptance threshold.

The result is always a `proposal` by default. It does not merge logical records or move a map location. Enable automatic acceptance only after collecting labelled local rotations, choosing a maximum cost on held-out data, and documenting the false-link rate for the deployment. Apple Continuity proposals remain non-automatic regardless of this statistical setting.

## Location Continuity

When a carryover is accepted, all historical observations and location estimates remain in chronological order under the canonical logical device. The latest accepted observation becomes its current scanner/zone. Thus, an accepted Tebet record later observed in Bekasi updates to Bekasi while retaining the earlier Tebet history.

No correlation path can derive a device coordinate or direction from one scanner's RSSI. A multi-scanner coordinate estimate requires a separately calibrated, time-synchronized deployment with enough non-collinear anchors.
