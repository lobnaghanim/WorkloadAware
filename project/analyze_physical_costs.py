#!/usr/bin/env python3
"""Analyze shortcut capability overhead and physical usage without re-optimization."""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_COMPARISON = ROOT / "results" / "all_k_policy_comparison.csv"
SYNTHETIC_SUMMARY = ROOT / "results" / "all_k_summary.csv"
STAGE_COMPARISON = ROOT / "results" / "stage_policy_comparison.csv"
STAGE_SUMMARY = ROOT / "results" / "stage_policy_summary.csv"
OUTPUT_SUMMARY = ROOT / "results" / "physical_cost_summary.csv"
OUTPUT_OVERHEAD = ROOT / "results" / "physical_cost_overhead.csv"
DESIGN_4PHY = ROOT / "inputs" / "designs" / "design_project_mesh_4x4.json"
DESIGN_8PHY = ROOT / "inputs" / "designs" / "design_project_physical_mesh_8phy.json"
BUDGETS = (5, 15, 25, 45)
K_VALUES = (1, 2, 4)
POLICIES = ("mesh", "fixed", "workload_aware")
SPARE_PHYS_PER_CHIPLET = 4
CHIPLET_COUNT = 16
AVAILABLE_SPARE_PHYS = CHIPLET_COUNT * SPARE_PHYS_PER_CHIPLET
SPARE_PHY_IDS = {0, 2, 4, 6}
EPS = 1e-9

SUMMARY_FIELDS = (
    "workload_family", "workload", "budget_mm", "policy", "requested_k", "actual_k",
    "total_wire_length_mm", "avg_shortcut_length_mm", "min_shortcut_length_mm",
    "max_shortcut_length_mm", "total_shortcut_link_latency_cycles",
    "optimizer_cumulative_wire_length_mm", "total_used_shortcut_phys",
    "available_spare_phys", "used_spare_phy_endpoints",
    "available_spare_phy_endpoints", "spare_phy_utilization_percent",
    "spare_phy_endpoints_per_chiplet", "analytical_latency", "booksim_low_latency",
    "latency_gain_vs_mesh_percent", "latency_gain_per_mm", "booksim_low_hops",
    "hop_gain_vs_mesh_percent", "hop_gain_per_mm", "knee_load",
    "analytical_throughput", "max_link_load", "traffic_wire_cost",
    "optimizer_metric_source", "previous_requested_k", "added_wire_vs_previous_k_mm",
    "incremental_latency_gain_percent", "incremental_latency_gain_per_added_mm",
)
OVERHEAD_FIELDS = (
    "record_type", "architecture", "source_design_file", "chiplet_width_mm",
    "chiplet_height_mm", "chiplet_area_mm2", "chiplet_power_w", "phy_count",
    "placement_footprint_width_mm", "placement_footprint_height_mm",
    "placement_footprint_area_mm2", "absolute_area_difference_mm2",
    "area_overhead_percent", "absolute_power_difference_w", "power_overhead_percent",
    "phy_count_increase", "phy_count_increase_percent",
)


class PhysicalCostError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise PhysicalCostError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_json(path: Path, description: str) -> Any:
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot parse {description} {path}: {exc}")


def read_csv(path: Path, required: set[str], description: str) -> list[dict[str, str]]:
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                fail(f"{description} lacks columns: {', '.join(missing)}")
            return list(reader)
    except OSError as exc:
        fail(f"Cannot read {description} {path}: {exc}")


def number(value: Any, description: str, optional: bool = False) -> float | None:
    if optional and (value is None or str(value).strip() == ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        fail(f"Invalid {description}: {value!r} ({exc})")
    if not math.isfinite(converted):
        fail(f"Invalid non-finite {description}")
    return converted


def integer(value: Any, description: str) -> int:
    converted = number(value, description)
    result = int(converted)
    if converted != result:
        fail(f"{description} must be an integer; found {value!r}")
    return result


def architecture(path: Path, label: str) -> dict[str, Any]:
    design = read_json(path, f"{label} design")
    chiplets = read_json(ROOT / design["chiplets"], f"{label} chiplets")
    placement = read_json(ROOT / design["placement"], f"{label} placement")
    placed = placement.get("chiplets")
    if not isinstance(placed, list) or len(placed) != CHIPLET_COUNT:
        fail(f"{label} design must place exactly 16 chiplets")
    names = {entry.get("name") for entry in placed}
    if len(names) != 1 or next(iter(names)) not in chiplets:
        fail(f"{label} design does not use one consistent chiplet definition")
    chiplet = chiplets[next(iter(names))]
    dimensions = chiplet.get("dimensions", {})
    width = number(dimensions.get("x"), f"{label} chiplet width")
    height = number(dimensions.get("y"), f"{label} chiplet height")
    power = number(chiplet.get("power"), f"{label} chiplet power", optional=True)
    phys = chiplet.get("phys")
    if not isinstance(phys, list):
        fail(f"{label} chiplet has no PHY list")

    min_x = min(number(entry["position"]["x"], f"{label} placement x") for entry in placed)
    min_y = min(number(entry["position"]["y"], f"{label} placement y") for entry in placed)
    max_x = max(
        number(entry["position"]["x"], f"{label} placement x")
        + (height if int(entry.get("rotation", 0)) % 180 == 90 else width)
        for entry in placed
    )
    max_y = max(
        number(entry["position"]["y"], f"{label} placement y")
        + (width if int(entry.get("rotation", 0)) % 180 == 90 else height)
        for entry in placed
    )
    footprint_width = max_x - min_x
    footprint_height = max_y - min_y
    return {
        "record_type": "architecture",
        "architecture": label,
        "source_design_file": relative(path),
        "chiplet_width_mm": width,
        "chiplet_height_mm": height,
        "chiplet_area_mm2": width * height,
        "chiplet_power_w": power,
        "phy_count": len(phys),
        "placement_footprint_width_mm": footprint_width,
        "placement_footprint_height_mm": footprint_height,
        "placement_footprint_area_mm2": footprint_width * footprint_height,
        "absolute_area_difference_mm2": None,
        "area_overhead_percent": None,
        "absolute_power_difference_w": None,
        "power_overhead_percent": None,
        "phy_count_increase": None,
        "phy_count_increase_percent": None,
    }


def build_overhead() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    arch4 = architecture(DESIGN_4PHY, "4PHY")
    arch8 = architecture(DESIGN_8PHY, "8PHY")
    if arch4["phy_count"] != 4 or arch8["phy_count"] != 8:
        fail("Capability comparison did not load the actual 4-PHY and 8-PHY definitions")
    area_difference = arch8["chiplet_area_mm2"] - arch4["chiplet_area_mm2"]
    area_percent = 100.0 * area_difference / arch4["chiplet_area_mm2"]
    power_difference = None
    power_percent = None
    if arch4["chiplet_power_w"] is not None and arch8["chiplet_power_w"] is not None:
        power_difference = arch8["chiplet_power_w"] - arch4["chiplet_power_w"]
        power_percent = 100.0 * power_difference / arch4["chiplet_power_w"]
    phy_increase = arch8["phy_count"] - arch4["phy_count"]
    overhead = {field: None for field in OVERHEAD_FIELDS}
    overhead.update(
        {
            "record_type": "overhead",
            "architecture": "8PHY_vs_4PHY",
            "source_design_file": f"{relative(DESIGN_4PHY)};{relative(DESIGN_8PHY)}",
            "absolute_area_difference_mm2": area_difference,
            "area_overhead_percent": area_percent,
            "absolute_power_difference_w": power_difference,
            "power_overhead_percent": power_percent,
            "phy_count_increase": phy_increase,
            "phy_count_increase_percent": 100.0 * phy_increase / arch4["phy_count"],
        }
    )
    return [arch4, arch8, overhead], overhead


def canonical_link(u: int, v: int) -> str:
    return f"{min(u, v)}<->{max(u, v)}"


def make_step(row: dict[str, str], k1: bool = False) -> dict[str, Any]:
    length_field = "length_mm" if k1 else "link_length_mm"
    latency_value = row.get("link_latency_cycles", "")
    length = number(row[length_field], "shortcut length")
    return {
        "step": 1 if k1 else integer(row["step"], "selection step"),
        "u": integer(row["u"], "selection u"),
        "v": integer(row["v"], "selection v"),
        "phy_u": integer(row["phy_u"], "selection phy_u"),
        "phy_v": integer(row["phy_v"], "selection phy_v"),
        "length": length,
        "latency_cycles": number(latency_value, "link latency cycles", optional=True),
        "cumulative_wire": (
            length if k1 else number(row["cumulative_wire_length_mm"], "cumulative wire")
        ),
    }


def selection_inputs() -> tuple[
    dict[tuple[str, str, int, int, str], list[dict[str, Any]]],
    dict[tuple[str, str, int, int, str], dict[str, Any]],
]:
    selections: dict[tuple[str, str, int, int, str], list[dict[str, Any]]] = defaultdict(list)
    metrics: dict[tuple[str, str, int, int, str], dict[str, Any]] = {}

    fixed_k1 = read_csv(
        ROOT / "results" / "k1_fixed_selections.csv",
        {"budget_mm", "u", "v", "phy_u", "phy_v", "length_mm", "link_latency_cycles",
         "reference_max_link_load", "traffic_wire_cost"},
        "fixed K=1 selections",
    )
    generic_fixed: dict[tuple[int, int], list[dict[str, Any]]] = {}
    generic_fixed_metrics: dict[tuple[int, int], dict[str, Any]] = {}
    for row in fixed_k1:
        budget = integer(row["budget_mm"], "fixed K1 budget")
        generic_fixed[(1, budget)] = [make_step(row, k1=True)]
        generic_fixed_metrics[(1, budget)] = {
            "max_link_load": number(row["reference_max_link_load"], "fixed K1 max load"),
            "traffic_wire_cost": number(row["traffic_wire_cost"], "fixed K1 traffic-wire cost"),
            "source": "k1_fixed_selections.csv (uniform reference)",
        }

    for requested_k in (2, 4):
        rows = read_csv(
            ROOT / "results" / f"k{requested_k}_fixed_selections.csv",
            {"budget_mm", "step", "u", "v", "phy_u", "phy_v", "link_length_mm",
             "cumulative_wire_length_mm", "link_latency_cycles"},
            f"fixed K={requested_k} selections",
        )
        for row in rows:
            budget = integer(row["budget_mm"], "fixed selection budget")
            generic_fixed.setdefault((requested_k, budget), []).append(make_step(row))
        summary = read_csv(
            ROOT / "results" / f"k{requested_k}_fixed_summary.csv",
            {"budget_mm", "final_reference_max_link_load", "traffic_wire_cost"},
            f"fixed K={requested_k} summary",
        )
        for row in summary:
            budget = integer(row["budget_mm"], "fixed summary budget")
            generic_fixed_metrics[(requested_k, budget)] = {
                "max_link_load": number(row["final_reference_max_link_load"], "fixed max load"),
                "traffic_wire_cost": number(row["traffic_wire_cost"], "fixed traffic-wire cost"),
                "source": f"k{requested_k}_fixed_summary.csv (uniform reference)",
            }

    synthetic_aware_k1 = read_csv(
        ROOT / "results" / "k1_workload_aware_selections.csv",
        {"workload", "budget_mm", "u", "v", "phy_u", "phy_v", "length_mm",
         "link_latency_cycles", "max_link_load"},
        "synthetic workload-aware K=1 selections",
    )
    for row in synthetic_aware_k1:
        key = ("synthetic", row["workload"].strip(), 1, integer(row["budget_mm"], "K1 budget"), "workload_aware")
        selections[key] = [make_step(row, k1=True)]
        metrics[key] = {
            "max_link_load": number(row["max_link_load"], "K1 aware max load"),
            "traffic_wire_cost": None,
            "source": "k1_workload_aware_selections.csv (traffic-wire cost unavailable)",
        }

    for requested_k in (2, 4):
        selection_rows = read_csv(
            ROOT / "results" / f"k{requested_k}_workload_aware_selections.csv",
            {"workload", "budget_mm", "step", "u", "v", "phy_u", "phy_v",
             "link_length_mm", "cumulative_wire_length_mm", "link_latency_cycles"},
            f"synthetic aware K={requested_k} selections",
        )
        for row in selection_rows:
            key = ("synthetic", row["workload"].strip(), requested_k,
                   integer(row["budget_mm"], "aware budget"), "workload_aware")
            selections[key].append(make_step(row))
        summary_rows = read_csv(
            ROOT / "results" / f"k{requested_k}_workload_aware_summary.csv",
            {"workload", "budget_mm", "final_max_link_load", "traffic_wire_cost"},
            f"synthetic aware K={requested_k} summary",
        )
        for row in summary_rows:
            key = ("synthetic", row["workload"].strip(), requested_k,
                   integer(row["budget_mm"], "aware summary budget"), "workload_aware")
            metrics[key] = {
                "max_link_load": number(row["final_max_link_load"], "aware max load"),
                "traffic_wire_cost": number(row["traffic_wire_cost"], "aware traffic-wire cost"),
                "source": f"k{requested_k}_workload_aware_summary.csv",
            }

    stage_steps = read_csv(
        ROOT / "results" / "stage_workload_aware_selections.csv",
        {"workload", "requested_k", "budget_mm", "step", "u", "v", "phy_u", "phy_v",
         "link_length_mm", "cumulative_wire_length_mm", "link_latency_cycles"},
        "STAGE workload-aware selections",
    )
    for row in stage_steps:
        key = ("stage", row["workload"].strip(), integer(row["requested_k"], "STAGE K"),
               integer(row["budget_mm"], "STAGE budget"), "workload_aware")
        selections[key].append(make_step(row))
    stage_summary = read_csv(
        ROOT / "results" / "stage_workload_aware_summary.csv",
        {"workload", "requested_k", "budget_mm", "final_max_link_load", "traffic_wire_cost"},
        "STAGE workload-aware summary",
    )
    for row in stage_summary:
        key = ("stage", row["workload"].strip(), integer(row["requested_k"], "STAGE K"),
               integer(row["budget_mm"], "STAGE budget"), "workload_aware")
        metrics[key] = {
            "max_link_load": number(row["final_max_link_load"], "STAGE max load"),
            "traffic_wire_cost": number(row["traffic_wire_cost"], "STAGE traffic-wire cost"),
            "source": "stage_workload_aware_summary.csv",
        }

    # Fixed selections are shared by workload family and workload.  Expand them
    # only after discovering the workload names from the comparison CSVs.
    comparison_workloads = {"synthetic": set(), "stage": set()}
    for family, path in (("synthetic", SYNTHETIC_COMPARISON), ("stage", STAGE_COMPARISON)):
        rows = read_csv(path, {"workload"}, f"{family} comparison workload discovery")
        comparison_workloads[family] = {row["workload"].strip() for row in rows}
    for family, workloads in comparison_workloads.items():
        for workload in workloads:
            for requested_k in K_VALUES:
                for budget in BUDGETS:
                    key = (family, workload, requested_k, budget, "fixed")
                    selections[key] = [dict(step) for step in generic_fixed[(requested_k, budget)]]
                    metrics[key] = dict(generic_fixed_metrics[(requested_k, budget)])
    return selections, metrics


def load_comparisons() -> list[dict[str, Any]]:
    required = {
        "workload", "budget_mm", "policy", "requested_k", "actual_k", "link_1", "link_2",
        "link_3", "link_4", "total_wire_length_mm", "analytical_latency",
        "analytical_throughput", "booksim_low_latency", "booksim_low_hops", "knee_load",
        "latency_gain_vs_mesh_percent", "hop_gain_vs_mesh_percent",
    }
    rows: list[dict[str, Any]] = []
    for family, path in (("synthetic", SYNTHETIC_COMPARISON), ("stage", STAGE_COMPARISON)):
        raw_rows = read_csv(path, required, f"{family} policy comparison")
        for raw in raw_rows:
            row = {
                "workload_family": family,
                "workload": raw["workload"].strip(),
                "budget_mm": integer(raw["budget_mm"], "comparison budget"),
                "policy": raw["policy"].strip(),
                "requested_k": integer(raw["requested_k"], "comparison requested_k"),
                "actual_k": integer(raw["actual_k"], "comparison actual_k"),
                "total_wire_length_mm": number(raw["total_wire_length_mm"], "comparison wire"),
                "analytical_latency": number(raw["analytical_latency"], "analytical latency"),
                "analytical_throughput": number(raw["analytical_throughput"], "analytical throughput"),
                "booksim_low_latency": number(raw["booksim_low_latency"], "BookSim latency"),
                "booksim_low_hops": number(raw["booksim_low_hops"], "BookSim hops"),
                "knee_load": number(raw["knee_load"], "knee load", optional=True),
                "latency_gain_vs_mesh_percent": number(raw["latency_gain_vs_mesh_percent"], "latency gain"),
                "hop_gain_vs_mesh_percent": number(raw["hop_gain_vs_mesh_percent"], "hop gain"),
                "links": [raw[f"link_{index}"].strip() for index in range(1, 5)],
            }
            if row["policy"] not in POLICIES or row["requested_k"] not in K_VALUES or row["budget_mm"] not in BUDGETS:
                fail(f"Unexpected comparison dimensions: {row}")
            rows.append(row)
    return rows


def validate_against_policy_summaries(rows: list[dict[str, Any]]) -> None:
    by_family = defaultdict(list)
    for row in rows:
        by_family[row["workload_family"]].append(row)
    specifications = {
        "synthetic": (
            SYNTHETIC_SUMMARY,
            {"mesh": "mesh_bs_latency", "fixed": "fixed_bs_latency", "workload_aware": "aware_bs_latency"},
            {"fixed": ("fixed_actual_k", "fixed_total_wire_mm"), "workload_aware": ("aware_actual_k", "aware_total_wire_mm")},
        ),
        "stage": (
            STAGE_SUMMARY,
            {"mesh": "mesh_latency", "fixed": "fixed_latency", "workload_aware": "aware_latency"},
            {"fixed": ("fixed_actual_k", "fixed_total_wire_mm"), "workload_aware": ("aware_actual_k", "aware_total_wire_mm")},
        ),
    }
    for family, (path, latency_fields, physical_fields) in specifications.items():
        summary_rows = read_csv(
            path, {"workload", "budget_mm", "requested_k", *latency_fields.values()},
            f"{family} policy summary",
        )
        indexed = {
            (row["workload"].strip(), integer(row["budget_mm"], "summary budget"),
             integer(row["requested_k"], "summary K")): row
            for row in summary_rows
        }
        for comparison in by_family[family]:
            key = (comparison["workload"], comparison["budget_mm"], comparison["requested_k"])
            if key not in indexed:
                fail(f"Missing {family} policy summary group {key}")
            source = indexed[key]
            expected_latency = number(source[latency_fields[comparison["policy"]]], "summary latency")
            if not math.isclose(expected_latency, comparison["booksim_low_latency"], rel_tol=EPS, abs_tol=EPS):
                fail(f"Policy summary/comparison latency mismatch for {family} {key}")
            if comparison["policy"] in physical_fields:
                k_field, wire_field = physical_fields[comparison["policy"]]
                if integer(source[k_field], "summary actual_k") != comparison["actual_k"]:
                    fail(f"Policy summary actual_k mismatch for {family} {key}")
                if not math.isclose(number(source[wire_field], "summary wire"), comparison["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
                    fail(f"Policy summary wire mismatch for {family} {key}")


def enrich_rows(
    comparisons: list[dict[str, Any]],
    selections: dict[tuple[str, str, int, int, str], list[dict[str, Any]]],
    optimizer_metrics: dict[tuple[str, str, int, int, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched = []
    for comparison in comparisons:
        family = comparison["workload_family"]
        workload = comparison["workload"]
        requested_k = comparison["requested_k"]
        budget = comparison["budget_mm"]
        policy = comparison["policy"]
        key = (family, workload, requested_k, budget, policy)
        if policy == "mesh":
            steps: list[dict[str, Any]] = []
            metadata = {"max_link_load": None, "traffic_wire_cost": None, "source": "not applicable (mesh)"}
        else:
            if key not in selections or key not in optimizer_metrics:
                fail(f"Missing stored selection/summary metadata for {key}")
            steps = sorted(selections[key], key=lambda step: step["step"])
            metadata = optimizer_metrics[key]
        if len(steps) != comparison["actual_k"] or comparison["actual_k"] > requested_k:
            fail(f"Selection count/actual_k mismatch for {key}")
        if comparison["total_wire_length_mm"] > budget + EPS:
            fail(f"Configuration exceeds physical wire budget for {key}")
        endpoints = []
        lengths = []
        cycles = []
        for step in steps:
            if step["phy_u"] not in SPARE_PHY_IDS or step["phy_v"] not in SPARE_PHY_IDS:
                fail(f"Shortcut uses a non-spare PHY for {key}: {step}")
            endpoints.extend(((step["u"], step["phy_u"]), (step["v"], step["phy_v"])))
            lengths.append(step["length"])
            if step["latency_cycles"] is not None:
                cycles.append(step["latency_cycles"])
        if len(set(endpoints)) != len(endpoints):
            fail(f"Shortcut PHY endpoint reuse for {key}")
        selected_names = [canonical_link(step["u"], step["v"]) for step in steps]
        comparison_names = []
        for raw in comparison["links"][:comparison["actual_k"]]:
            try:
                u, v = (int(value) for value in raw.split("<->"))
            except (ValueError, AttributeError) as exc:
                fail(f"Invalid comparison shortcut {raw!r} for {key}: {exc}")
            comparison_names.append(canonical_link(u, v))
        if selected_names != comparison_names:
            fail(f"Stored selections disagree with comparison links for {key}")
        total_wire = sum(lengths)
        if not math.isclose(total_wire, comparison["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Stored shortcut lengths disagree with total wire for {key}")
        if policy == "mesh" and (comparison["actual_k"] != 0 or abs(total_wire) > EPS):
            fail(f"Mesh has nonzero physical shortcut usage for {key}")
        cumulative = None if not steps else steps[-1]["cumulative_wire"]
        if cumulative is not None and not math.isclose(cumulative, total_wire, rel_tol=EPS, abs_tol=EPS):
            fail(f"Final cumulative wire differs from selected wire for {key}")
        used = 2 * comparison["actual_k"]
        utilization = 100.0 * used / AVAILABLE_SPARE_PHYS
        if used != len(endpoints) or utilization > 100.0 + EPS:
            fail(f"Invalid spare-PHY utilization for {key}")
        latency_efficiency = (
            comparison["latency_gain_vs_mesh_percent"] / total_wire if total_wire > EPS else None
        )
        hop_efficiency = (
            comparison["hop_gain_vs_mesh_percent"] / total_wire if total_wire > EPS else None
        )
        enriched.append(
            {
                **{field: comparison[field] for field in (
                    "workload_family", "workload", "budget_mm", "policy", "requested_k", "actual_k",
                    "total_wire_length_mm", "analytical_latency", "booksim_low_latency",
                    "latency_gain_vs_mesh_percent", "booksim_low_hops", "hop_gain_vs_mesh_percent",
                    "knee_load", "analytical_throughput",
                )},
                "avg_shortcut_length_mm": None if not lengths else sum(lengths) / len(lengths),
                "min_shortcut_length_mm": None if not lengths else min(lengths),
                "max_shortcut_length_mm": None if not lengths else max(lengths),
                "total_shortcut_link_latency_cycles": None if len(cycles) != len(steps) or not cycles else sum(cycles),
                "optimizer_cumulative_wire_length_mm": cumulative,
                "total_used_shortcut_phys": used,
                "available_spare_phys": AVAILABLE_SPARE_PHYS,
                "used_spare_phy_endpoints": used,
                "available_spare_phy_endpoints": AVAILABLE_SPARE_PHYS,
                "spare_phy_utilization_percent": utilization,
                "spare_phy_endpoints_per_chiplet": used / CHIPLET_COUNT,
                "latency_gain_per_mm": latency_efficiency,
                "hop_gain_per_mm": hop_efficiency,
                "max_link_load": metadata["max_link_load"],
                "traffic_wire_cost": metadata["traffic_wire_cost"],
                "optimizer_metric_source": metadata["source"],
                "previous_requested_k": None,
                "added_wire_vs_previous_k_mm": None,
                "incremental_latency_gain_percent": None,
                "incremental_latency_gain_per_added_mm": None,
            }
        )
    return enriched


def add_diminishing_returns(rows: list[dict[str, Any]]) -> None:
    indexed = {
        (row["workload_family"], row["workload"], row["policy"], row["budget_mm"], row["requested_k"]): row
        for row in rows
    }
    groups = {(key[0], key[1], key[2], key[3]) for key in indexed}
    for family, workload, policy, budget in groups:
        for previous_k, current_k in ((1, 2), (2, 4)):
            previous = indexed[(family, workload, policy, budget, previous_k)]
            current = indexed[(family, workload, policy, budget, current_k)]
            added_wire = current["total_wire_length_mm"] - previous["total_wire_length_mm"]
            if added_wire < -EPS:
                fail(f"Wire decreases from K{previous_k} to K{current_k} for {(family, workload, policy, budget)}")
            incremental_gain = 100.0 * (
                previous["booksim_low_latency"] - current["booksim_low_latency"]
            ) / previous["booksim_low_latency"]
            current["previous_requested_k"] = previous_k
            current["added_wire_vs_previous_k_mm"] = added_wire
            current["incremental_latency_gain_percent"] = incremental_gain
            current["incremental_latency_gain_per_added_mm"] = (
                incremental_gain / added_wire if added_wire > EPS else None
            )


def validate_output(rows: list[dict[str, Any]]) -> None:
    families = defaultdict(set)
    for row in rows:
        families[row["workload_family"]].add(row["workload"])
        if row["actual_k"] > row["requested_k"]:
            fail(f"actual_k exceeds requested_k in output row {row}")
        if row["total_wire_length_mm"] > row["budget_mm"] + EPS:
            fail(f"Wire budget exceeded in output row {row}")
        if row["used_spare_phy_endpoints"] != 2 * row["actual_k"]:
            fail(f"Wrong used spare-PHY endpoint count in output row {row}")
        if row["spare_phy_utilization_percent"] > 100.0 + EPS:
            fail(f"Spare-PHY utilization exceeds 100% in output row {row}")
        if row["policy"] == "mesh" and (
            row["actual_k"] != 0 or abs(row["total_wire_length_mm"]) > EPS
            or abs(row["spare_phy_utilization_percent"]) > EPS
        ):
            fail(f"Mesh physical cost is nonzero in output row {row}")
    expected = sum(len(workloads) for workloads in families.values()) * len(BUDGETS) * len(K_VALUES) * len(POLICIES)
    if set(families) != {"synthetic", "stage"} or len(rows) != expected:
        fail(f"Physical-cost row matrix mismatch: rows={len(rows)}, expected={expected}, families={dict(families)}")
    keys = {
        (row["workload_family"], row["workload"], row["budget_mm"], row["policy"], row["requested_k"])
        for row in rows
    }
    if len(keys) != len(rows):
        fail("Duplicate logical configuration in physical-cost output")


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as exc:
        fail(f"Cannot write {path}: {exc}")


def shown(value: Any, decimals: int = 3) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


def print_tables(overhead_rows: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    arch4, arch8, overhead = overhead_rows
    print("PHYSICAL CAPABILITY OVERHEAD")
    print("ARCH | PHY_COUNT | AREA | POWER")
    print(f"4PHY | {arch4['phy_count']} | {arch4['chiplet_area_mm2']:.3f} | {shown(arch4['chiplet_power_w'])}")
    print(f"8PHY | {arch8['phy_count']} | {arch8['chiplet_area_mm2']:.3f} | {shown(arch8['chiplet_power_w'])}")
    print(
        f"OVERHEAD | +{overhead['phy_count_increase']} "
        f"({overhead['phy_count_increase_percent']:.3f}%) | "
        f"+{overhead['absolute_area_difference_mm2']:.3f} "
        f"({overhead['area_overhead_percent']:.3f}%) | "
        f"+{shown(overhead['absolute_power_difference_w'])} "
        f"({shown(overhead['power_overhead_percent'])}%)"
    )

    print("\nPOLICY | K | MEAN_WIRE | MEAN_ACTUAL_K | MEAN_PHY_UTIL | MEAN_LAT_GAIN")
    for policy in POLICIES:
        for requested_k in K_VALUES:
            group = [row for row in rows if row["policy"] == policy and row["requested_k"] == requested_k]
            mean = lambda field: sum(row[field] for row in group) / len(group)
            print(
                f"{policy} | {requested_k} | {mean('total_wire_length_mm'):.3f} | "
                f"{mean('actual_k'):.3f} | {mean('spare_phy_utilization_percent'):.3f} | "
                f"{mean('latency_gain_vs_mesh_percent'):.3f}"
            )

    indexed = {
        (row["workload_family"], row["workload"], row["policy"], row["budget_mm"], row["requested_k"]): row
        for row in rows
    }
    groups = sorted({key[:4] for key in indexed})
    print(
        "\nWORKLOAD | POLICY | BUDGET | K1_WIRE | K2_WIRE | K4_WIRE | "
        "K1_LAT | K2_LAT | K4_LAT | K1_TO_K2_GAIN_PER_ADDED_MM | K2_TO_K4_GAIN_PER_ADDED_MM"
    )
    for family, workload, policy, budget in groups:
        k1, k2, k4 = (indexed[(family, workload, policy, budget, requested_k)] for requested_k in K_VALUES)
        print(
            f"{family}:{workload} | {policy} | {budget} | "
            f"{k1['total_wire_length_mm']:.3f} | {k2['total_wire_length_mm']:.3f} | "
            f"{k4['total_wire_length_mm']:.3f} | {k1['booksim_low_latency']:.3f} | "
            f"{k2['booksim_low_latency']:.3f} | {k4['booksim_low_latency']:.3f} | "
            f"{shown(k2['incremental_latency_gain_per_added_mm'])} | "
            f"{shown(k4['incremental_latency_gain_per_added_mm'])}"
        )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    overhead_rows, overhead = build_overhead()
    selections, optimizer_metrics = selection_inputs()
    comparisons = load_comparisons()
    validate_against_policy_summaries(comparisons)
    rows = enrich_rows(comparisons, selections, optimizer_metrics)
    add_diminishing_returns(rows)
    validate_output(rows)
    write_csv(OUTPUT_SUMMARY, SUMMARY_FIELDS, rows)
    write_csv(OUTPUT_OVERHEAD, OVERHEAD_FIELDS, overhead_rows)
    print_tables(overhead_rows, rows)
    print("\nPHYSICAL COST ANALYSIS COMPLETE")
    print(f"Configurations analyzed: {len(rows)}")
    print(f"4-PHY vs 8-PHY area overhead: {overhead['area_overhead_percent']:.6f}%")
    print(f"4-PHY vs 8-PHY power overhead: {shown(overhead['power_overhead_percent'], 6)}%")
    print(f"Physical cost summary: {relative(OUTPUT_SUMMARY)}")
    print(f"Capability overhead: {relative(OUTPUT_OVERHEAD)}")


if __name__ == "__main__":
    try:
        main()
    except (PhysicalCostError, RuntimeError, OSError, KeyError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit(f"Physical cost analysis failed: {exc}") from exc
