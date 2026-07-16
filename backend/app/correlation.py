from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class LinearRegression:
    intercept: float
    slope_per_second: float
    sample_count: int

    def predict(self, observed_at: datetime, origin: datetime) -> float:
        elapsed_seconds = (observed_at - origin).total_seconds()
        return self.intercept + (self.slope_per_second * elapsed_seconds)


@dataclass(frozen=True)
class AkiyamaPairCost:
    time_difference_seconds: float
    rssi_difference_db: float
    alpha: float
    cost: float
    predecessor_sample_count: int
    successor_sample_count: int


def fit_linear_rssi_regression(points: list[tuple[datetime, int | float]]) -> tuple[LinearRegression, datetime] | None:
    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda item: item[0])
    origin = ordered[0][0]
    x_values = [(observed_at - origin).total_seconds() for observed_at, _ in ordered]
    y_values = [float(rssi) for _, rssi in ordered]
    mean_x = sum(x_values) / len(x_values)
    mean_y = sum(y_values) / len(y_values)
    denominator = sum((value - mean_x) ** 2 for value in x_values)
    if denominator <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_values, y_values)) / denominator
    intercept = mean_y - (slope * mean_x)
    return LinearRegression(intercept=intercept, slope_per_second=slope, sample_count=len(ordered)), origin


def rssi_regression_difference(
    predecessor_points: list[tuple[datetime, int | float]],
    successor_points: list[tuple[datetime, int | float]],
) -> tuple[float, int, int] | None:
    regression = fit_linear_rssi_regression(predecessor_points)
    if regression is None or not successor_points:
        return None
    model, origin = regression
    residuals = [
        abs(float(rssi) - model.predict(observed_at, origin))
        for observed_at, rssi in successor_points
    ]
    return sum(residuals) / len(residuals), model.sample_count, len(successor_points)


def akiyama_pair_cost(
    predecessor_last_seen: datetime,
    successor_first_seen: datetime,
    rssi_difference_db: float,
    alpha: float,
    search_window_seconds: float,
    predecessor_sample_count: int,
    successor_sample_count: int,
) -> AkiyamaPairCost | None:
    time_difference_seconds = (successor_first_seen - predecessor_last_seen).total_seconds()
    if time_difference_seconds < 0 or time_difference_seconds > search_window_seconds:
        return None
    cost = math.sqrt((time_difference_seconds**2) + ((alpha * rssi_difference_db) ** 2))
    return AkiyamaPairCost(
        time_difference_seconds=time_difference_seconds,
        rssi_difference_db=rssi_difference_db,
        alpha=alpha,
        cost=cost,
        predecessor_sample_count=predecessor_sample_count,
        successor_sample_count=successor_sample_count,
    )


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    bounded = min(max(quantile, 0.0), 1.0)
    position = (len(ordered) - 1) * bounded
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def alpha_from_p90_overlap(time_differences: list[float], rssi_differences: list[float]) -> float | None:
    if not time_differences or not rssi_differences:
        return None
    time_width = percentile(time_differences, 0.9)
    rssi_width = percentile(rssi_differences, 0.9)
    if time_width is None or rssi_width is None or rssi_width <= 0:
        return None
    return time_width / rssi_width


def minimum_cost_assignment(
    costs: list[list[float]],
    blocked_cost: float,
) -> list[int]:
    """Return one selected column per row with a rectangular Hungarian assignment."""
    if not costs:
        return []
    row_count = len(costs)
    column_count = len(costs[0])
    if column_count < row_count:
        raise ValueError("assignment requires at least as many columns as rows")
    if any(len(row) != column_count for row in costs):
        raise ValueError("assignment matrix rows must have equal length")

    u = [0.0] * (row_count + 1)
    v = [0.0] * (column_count + 1)
    matched_row_for_column = [0] * (column_count + 1)
    way = [0] * (column_count + 1)

    for row in range(1, row_count + 1):
        matched_row_for_column[0] = row
        min_value = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        column = 0
        while True:
            used[column] = True
            current_row = matched_row_for_column[column]
            delta = float("inf")
            next_column = 0
            for candidate_column in range(1, column_count + 1):
                if used[candidate_column]:
                    continue
                value = costs[current_row - 1][candidate_column - 1]
                normalized_cost = blocked_cost if math.isinf(value) else value
                current = normalized_cost - u[current_row] - v[candidate_column]
                if current < min_value[candidate_column]:
                    min_value[candidate_column] = current
                    way[candidate_column] = column
                if min_value[candidate_column] < delta:
                    delta = min_value[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(column_count + 1):
                if used[candidate_column]:
                    u[matched_row_for_column[candidate_column]] += delta
                    v[candidate_column] -= delta
                else:
                    min_value[candidate_column] -= delta
            column = next_column
            if matched_row_for_column[column] == 0:
                break
        while True:
            previous_column = way[column]
            matched_row_for_column[column] = matched_row_for_column[previous_column]
            column = previous_column
            if column == 0:
                break

    selected_columns = [-1] * row_count
    for column in range(1, column_count + 1):
        row = matched_row_for_column[column]
        if row:
            selected_columns[row - 1] = column - 1
    return selected_columns


def assign_akiyama_pairs(
    predecessor_ids: list[str],
    successor_ids: list[str],
    pair_costs: dict[tuple[str, str], AkiyamaPairCost],
    unmatched_cost: float,
) -> list[tuple[str, str, AkiyamaPairCost]]:
    if not predecessor_ids or not successor_ids:
        return []
    blocked_cost = max(unmatched_cost * 1_000.0, 1_000_000.0)
    matrix: list[list[float]] = []
    for predecessor_id in predecessor_ids:
        row = [
            pair_costs.get((predecessor_id, successor_id), None).cost
            if pair_costs.get((predecessor_id, successor_id)) is not None
            else float("inf")
            for successor_id in successor_ids
        ]
        row.extend([unmatched_cost] * len(predecessor_ids))
        matrix.append(row)

    assignments = minimum_cost_assignment(matrix, blocked_cost)
    links: list[tuple[str, str, AkiyamaPairCost]] = []
    for row_index, column_index in enumerate(assignments):
        if column_index < 0 or column_index >= len(successor_ids):
            continue
        predecessor_id = predecessor_ids[row_index]
        successor_id = successor_ids[column_index]
        pair = pair_costs.get((predecessor_id, successor_id))
        if pair is not None and pair.cost < unmatched_cost:
            links.append((predecessor_id, successor_id, pair))
    return links


def parse_token_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    valid: list[dict[str, Any]] = []
    for raw_rule in value:
        if not isinstance(raw_rule, dict):
            continue
        rule_id = str(raw_rule.get("rule_id") or "").strip()
        ad_type = str(raw_rule.get("ad_type") or "").strip().lower()
        try:
            offset = int(raw_rule.get("offset_bytes"))
            length = int(raw_rule.get("length_bytes"))
        except (TypeError, ValueError):
            continue
        company_id = str(raw_rule.get("company_id") or "").lower() or None
        service_uuid = str(raw_rule.get("service_uuid") or "").lower() or None
        if (
            not rule_id
            or not ad_type.startswith("0x")
            or offset < 0
            or length < 5
            or (company_id is None and service_uuid is None)
        ):
            continue
        valid.append(
            {
                "rule_id": rule_id,
                "ad_type": ad_type,
                "company_id": company_id,
                "service_uuid": service_uuid,
                "offset_bytes": offset,
                "length_bytes": length,
            }
        )
    return valid


def extract_approved_tokens(ad_parser: Any, rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(ad_parser, dict) or not rules:
        return {}
    structures = ad_parser.get("structures")
    if not isinstance(structures, list):
        return {}
    tokens: dict[str, dict[str, Any]] = {}
    for structure in structures:
        if not isinstance(structure, dict):
            continue
        ad_type = str(structure.get("type") or "").lower()
        data = str(structure.get("data") or "").lower()
        if not data or len(data) % 2:
            continue
        for rule in rules:
            if rule["ad_type"] != ad_type:
                continue
            if rule["company_id"] and str(structure.get("company_id") or "").lower() != rule["company_id"]:
                continue
            if rule["service_uuid"] and str(structure.get("service_uuid") or "").lower() != rule["service_uuid"]:
                continue
            start = rule["offset_bytes"] * 2
            end = start + (rule["length_bytes"] * 2)
            if end > len(data):
                continue
            token_hex = data[start:end]
            if not token_hex or set(token_hex) == {"0"}:
                continue
            digest = hashlib.sha256(f"{rule['rule_id']}:{ad_type}:{token_hex}".encode("ascii")).hexdigest()
            tokens[digest] = {
                "rule_id": rule["rule_id"],
                "ad_type": ad_type,
                "company_id": structure.get("company_id"),
                "service_uuid": structure.get("service_uuid"),
                "token_hash": digest,
                "bit_length": rule["length_bytes"] * 8,
            }
    return tokens
