from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean
from typing import Any


ADDRESS_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


@dataclass(frozen=True)
class ProcessingSettings:
    presence_missing_seconds: int = 45
    presence_offline_seconds: int = 180


@dataclass(frozen=True)
class MovementResult:
    status: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class ProximityResult:
    band: str
    distance_m: float | None
    confidence: float
    probabilities: dict[str, float]
    distance_range_m: tuple[float, float] | None
    method: str


@dataclass(frozen=True)
class RSSIWindowMetrics:
    """RSSI-only sequence metric from the published time-window method.

    The method compares consecutive windows of RSSI means for each scanner.
    It is a relative movement/reliability signal, not a distance estimator.
    """

    window_size: int
    window_ready: bool
    observed_anchor_count: int
    anchor_count: int
    current_window_means: dict[str, float]
    previous_window_means: dict[str, float]
    absolute_changes_db: dict[str, float]
    weights: dict[str, float]
    weighted_mean_change_db: float | None
    rssi_metric: float | None
    reliability: float | None
    movement_threshold: float
    method: str = "scientific_reports_rssi_window_metric_v1"


SIGNAL_BAND_THRESHOLDS: tuple[tuple[str, int], ...] = (
    ("signal_strong", -60),
    ("signal_moderate", -75),
    ("signal_weak", -88),
)

# These are algorithm constants from the cited RSSI-only experiment, not
# operator calibration fields. The paper used five readings per window and
# selected beta=0.85 and an RSSI movement threshold of 0.6 for its test data.
RSSI_WINDOW_SIZE = 5
RSSI_WEIGHT_BASE = 0.85
RSSI_MOVEMENT_THRESHOLD = 0.6
RSSI_MOVEMENT_ENTER_THRESHOLD = 0.65
RSSI_MOVEMENT_EXIT_THRESHOLD = 0.45
RSSI_MOVEMENT_DWELL_OBSERVATIONS = 2
SIGNAL_BAND_HYSTERESIS_DB = 2.0

# ESP32 BLE indoor asset-tracking baseline reported by Al-Maktary et al.
# (ELKHA, 2025): A is the measured RSSI at 1 m and n is the path-loss
# exponent. These are internal literature-baseline constants, not user
# calibration settings. The paper reports its useful accuracy range only up
# to approximately four metres in clear line-of-sight conditions.
JOURNAL_REFERENCE_RSSI_DBM = -47.0
JOURNAL_PATH_LOSS_EXPONENT = 2.0
JOURNAL_VALIDATED_DISTANCE_M = 4.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None, fallback: datetime | None = None) -> datetime:
    if value is None:
        return fallback or utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_address(address: str | None) -> str | None:
    if not address:
        return None
    candidate = address.strip().lower()
    if ADDRESS_RE.match(candidate):
        return candidate
    return candidate


def normalize_hex(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower().replace(" ", "")
    if stripped.startswith("0x"):
        stripped = stripped[2:]
    if not stripped:
        return None
    if len(stripped) % 2 != 0:
        stripped = f"0{stripped}"
    int(stripped, 16)
    return stripped


def is_randomized_address(address_type: str | None, address: str | None = None) -> bool:
    if address_type:
        lowered = address_type.lower()
        if any(token in lowered for token in ("random", "private", "rpa", "resolvable", "non-resolvable")):
            return True
        if "public" in lowered:
            return False
    return False


def is_synthetic_address_pattern(address: str | None) -> bool:
    normalized = normalize_address(address)
    if not normalized or not ADDRESS_RE.match(normalized):
        return False

    octets = [int(part, 16) for part in normalized.split(":")]
    if len(set(octets)) == 1:
        return True

    deltas = [right - left for left, right in zip(octets, octets[1:])]
    return len(set(deltas)) == 1 and deltas[0] in {1, 0x11, 0x22}


def signal_band_from_rssi(rssi: int | float | None) -> str:
    """Classify received signal strength without treating it as distance."""
    if rssi is None:
        return "unknown"
    for band, threshold in SIGNAL_BAND_THRESHOLDS:
        if float(rssi) >= threshold:
            return band
    return "signal_very_weak"


def signal_band_with_hysteresis(
    rssi: int | float | None,
    previous_band: str | None,
) -> str:
    """Hold a signal band near a threshold to avoid boundary chatter."""
    raw_band = signal_band_from_rssi(rssi)
    if rssi is None or previous_band not in {
        "signal_strong",
        "signal_moderate",
        "signal_weak",
        "signal_very_weak",
    }:
        return raw_band

    value = float(rssi)
    if previous_band == "signal_strong":
        return "signal_moderate" if value < -60 - SIGNAL_BAND_HYSTERESIS_DB else previous_band
    if previous_band == "signal_moderate":
        if value >= -60 + SIGNAL_BAND_HYSTERESIS_DB:
            return "signal_strong"
        if value < -75 - SIGNAL_BAND_HYSTERESIS_DB:
            return "signal_weak"
        return previous_band
    if previous_band == "signal_weak":
        if value >= -75 + SIGNAL_BAND_HYSTERESIS_DB:
            return "signal_moderate"
        if value < -88 - SIGNAL_BAND_HYSTERESIS_DB:
            return "signal_very_weak"
        return previous_band
    return "signal_weak" if value >= -88 + SIGNAL_BAND_HYSTERESIS_DB else previous_band


def movement_result_from_metrics(
    metrics: RSSIWindowMetrics,
    previous_status: str | None,
) -> MovementResult:
    """Apply entry/exit hysteresis to the published RSSI change metric."""
    if metrics.rssi_metric is None:
        return MovementResult(previous_status or "stationary", 0.0, "rssi_window_not_ready")

    moving_before = previous_status == "probably_moving"
    if moving_before:
        if metrics.rssi_metric <= RSSI_MOVEMENT_EXIT_THRESHOLD:
            return MovementResult(
                "signal_stable",
                round(metrics.reliability or 0.0, 2),
                "paper_rssi_metric_below_exit_threshold",
            )
        return MovementResult(
            "probably_moving",
            round(metrics.rssi_metric, 2),
            "movement_hysteresis_held",
        )

    if metrics.rssi_metric >= RSSI_MOVEMENT_ENTER_THRESHOLD:
        return MovementResult(
            "probably_moving",
            round(metrics.rssi_metric, 2),
            "paper_rssi_metric_above_entry_threshold",
        )
    if metrics.rssi_metric <= RSSI_MOVEMENT_EXIT_THRESHOLD:
        return MovementResult(
            "signal_stable",
            round(metrics.reliability or 0.0, 2),
            "paper_rssi_metric_below_exit_threshold",
        )
    return MovementResult(
        previous_status or "stationary",
        round(metrics.reliability or 0.0, 2),
        "movement_hysteresis_held",
    )


def infer_proximity_from_rssi(
    rssi: int | float | None,
    metrics: RSSIWindowMetrics | None = None,
) -> ProximityResult:
    """Return a journal-baseline radial distance estimate plus RSSI evidence.

    The log-distance equation produces a model estimate, not a measured range:
    d = 10 ** ((A - RSSI) / (10 * n)). It is surfaced with its provenance and
    validation limit so callers cannot mistake it for an exact coordinate.
    """
    distance = estimate_journal_distance_m(rssi)
    distance_range = None
    if distance is not None and distance <= JOURNAL_VALIDATED_DISTANCE_M:
        distance_range = (
            round(max(0.1, distance * 0.75), 2),
            round(distance * 1.25, 2),
        )
    return ProximityResult(
        band=signal_band_from_rssi(rssi),
        distance_m=distance,
        confidence=metrics.reliability if metrics and metrics.reliability is not None else 0.0,
        probabilities={},
        distance_range_m=distance_range,
        method="journal_esp32_log_distance_baseline_v1",
    )


def proximity_band(distance_m: float | None, rssi: int | float | None = None) -> str:
    """Compatibility name for the API field; returns a signal band only."""
    return signal_band_from_rssi(rssi)


def estimate_journal_distance_m(rssi: int | float | None) -> float | None:
    """Invert the published ESP32 BLE log-distance baseline."""
    if rssi is None:
        return None
    distance = 10 ** (
        (JOURNAL_REFERENCE_RSSI_DBM - float(rssi))
        / (10.0 * JOURNAL_PATH_LOSS_EXPONENT)
    )
    if not math.isfinite(distance):
        return None
    return round(max(0.1, min(distance, 1000.0)), 2)


def rssi_window_metrics(samples_by_scanner: dict[str, list[int | float]]) -> RSSIWindowMetrics:
    """Calculate the paper's RSSI change metric over consecutive windows.

    For each scanner, the input list must be chronological and include the
    current observation. A scanner contributes only when both the current and
    preceding five-sample windows are available. The one-scanner case is the
    valid n=1 form of the paper's vector calculation: its weight is 1.0.
    """
    current_window_means: dict[str, float] = {}
    previous_window_means: dict[str, float] = {}
    absolute_changes_db: dict[str, float] = {}

    for scanner_id, samples in samples_by_scanner.items():
        values = [float(value) for value in samples]
        if not values:
            continue
        current_window_means[scanner_id] = round(fmean(values[-RSSI_WINDOW_SIZE:]), 2)
        if len(values) < RSSI_WINDOW_SIZE * 2:
            continue
        previous_window_means[scanner_id] = round(
            fmean(values[-(RSSI_WINDOW_SIZE * 2) : -RSSI_WINDOW_SIZE]),
            2,
        )
        absolute_changes_db[scanner_id] = round(
            abs(current_window_means[scanner_id] - previous_window_means[scanner_id]),
            2,
        )

    weights: dict[str, float] = {}
    weighted_mean_change_db: float | None = None
    rssi_metric: float | None = None
    reliability: float | None = None
    anchors = sorted(absolute_changes_db)
    if anchors:
        raw_weights = {
            scanner_id: RSSI_WEIGHT_BASE ** abs(current_window_means[scanner_id])
            for scanner_id in anchors
        }
        weight_total = sum(raw_weights.values())
        if weight_total > 0:
            weights = {
                scanner_id: round(raw_weights[scanner_id] / weight_total, 6)
                for scanner_id in anchors
            }
            # Equations (4)-(6) in the paper: weighted change, mean over n,
            # then tanh to map the RSSI change metric into [0, 1).
            weighted_mean_change_db = round(
                sum(weights[scanner_id] * absolute_changes_db[scanner_id] for scanner_id in anchors)
                / len(anchors),
                4,
            )
            rssi_metric = round(math.tanh(weighted_mean_change_db), 4)
            # RSSI-only specialization of Eq. (7): activity weight=0,
            # RSSI weight=1, therefore reliability = 1 - rssi_metric.
            reliability = round(1.0 - rssi_metric, 4)

    return RSSIWindowMetrics(
        window_size=RSSI_WINDOW_SIZE,
        window_ready=bool(anchors),
        observed_anchor_count=len(current_window_means),
        anchor_count=len(anchors),
        current_window_means=current_window_means,
        previous_window_means=previous_window_means,
        absolute_changes_db=absolute_changes_db,
        weights=weights,
        weighted_mean_change_db=weighted_mean_change_db,
        rssi_metric=rssi_metric,
        reliability=reliability,
        movement_threshold=RSSI_MOVEMENT_THRESHOLD,
    )


def evaluate_presence_status(
    previous_status: str | None,
    last_seen_at: datetime | None,
    now: datetime,
    settings: ProcessingSettings,
) -> tuple[str, str] | None:
    if last_seen_at is None:
        return None
    elapsed = (ensure_utc(now) - ensure_utc(last_seen_at)).total_seconds()
    if elapsed >= settings.presence_offline_seconds and previous_status != "offline":
        return "offline", "offline_threshold_elapsed"
    if elapsed >= settings.presence_missing_seconds and previous_status not in {"temporarily_missing", "offline"}:
        return "temporarily_missing", "missing_threshold_elapsed"
    return None


def observed_again_status(previous_status: str | None) -> tuple[str, str] | None:
    if previous_status in {"temporarily_missing", "offline", "disappeared"}:
        return "returned", "device_observed_after_absence"
    if previous_status in {None, "unknown"}:
        return "newly_detected", "first_logical_observation"
    return None
