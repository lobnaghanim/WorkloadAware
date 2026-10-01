#!/usr/bin/env python3
"""Generate final quantitative conclusions from completed project datasets."""

from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
INPUTS = {
    "synthetic_summary": RESULTS / "all_k_summary.csv",
    "synthetic_comparison": RESULTS / "all_k_policy_comparison.csv",
    "multiseed": RESULTS / "multiseed_summary.csv",
    "stage_summary": RESULTS / "stage_policy_summary.csv",
    "stage_comparison": RESULTS / "stage_policy_comparison.csv",
    "physical_cost": RESULTS / "physical_cost_summary.csv",
    "overhead": RESULTS / "physical_cost_overhead.csv",
}
TABLES = {
    "synthetic": RESULTS / "tables" / "synthetic_policy_summary.csv",
    "stage": RESULTS / "tables" / "stage_policy_summary_final.csv",
    "links": RESULTS / "tables" / "selected_links_summary.csv",
    "overhead": RESULTS / "tables" / "physical_overhead_summary.csv",
    "multiseed": RESULTS / "tables" / "multiseed_robustness_final.csv",
}
SUMMARY_OUTPUT = RESULTS / "final_result_summary.csv"
FINDINGS_OUTPUT = RESULTS / "final_key_findings.txt"
K_VALUES = (1, 2, 4)
BUDGETS = (5, 15, 25, 45)
POLICIES = ("mesh", "fixed", "workload_aware")
EPS = 1e-9
DESCRIPTIVE_NEGLIGIBLE_PERCENT = 0.1
SUMMARY_FIELDS = (
    "category", "workload", "requested_k", "budget_mm", "mesh_latency",
    "fixed_latency", "aware_latency", "fixed_gain_vs_mesh_percent",
    "aware_gain_vs_mesh_percent", "aware_gain_vs_fixed_percent", "fixed_actual_k",
    "aware_actual_k", "fixed_wire_mm", "aware_wire_mm", "fixed_knee", "aware_knee",
    "notes",
)


class FinalAnalysisError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise FinalAnalysisError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


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
        fail(f"Non-finite {description}")
    return converted


def integer(value: Any, description: str) -> int:
    converted = number(value, description)
    result = int(converted)
    if result != converted:
        fail(f"{description} must be an integer")
    return result


def mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        fail("Cannot calculate an arithmetic mean of an empty set")
    return sum(items) / len(items)


def optional_mean(values: Iterable[float | None]) -> float | None:
    items = [value for value in values if value is not None]
    return None if not items else mean(items)


def improvement(baseline: float, candidate: float) -> float:
    if abs(baseline) <= EPS:
        fail("Cannot calculate an improvement relative to zero")
    return 100.0 * (baseline - candidate) / baseline


def normalize_policy_summary(path: Path, family: str) -> list[dict[str, Any]]:
    if family == "synthetic":
        names = {
            "mesh_latency": "mesh_bs_latency",
            "mesh_hops": "mesh_hops",
            "mesh_knee": "mesh_knee_load",
            "fixed_latency": "fixed_bs_latency",
            "fixed_hops": "fixed_hops",
            "fixed_knee": "fixed_knee_load",
            "fixed_gain": "fixed_latency_gain_vs_mesh_percent",
            "aware_latency": "aware_bs_latency",
            "aware_hops": "aware_hops",
            "aware_knee": "aware_knee_load",
            "aware_gain": "aware_latency_gain_vs_mesh_percent",
            "direct_gain": "aware_latency_gain_vs_fixed_percent",
        }
    else:
        names = {
            "mesh_latency": "mesh_latency",
            "mesh_hops": "mesh_hops",
            "mesh_knee": "mesh_knee_load",
            "fixed_latency": "fixed_latency",
            "fixed_hops": "fixed_hops",
            "fixed_knee": "fixed_knee_load",
            "fixed_gain": "fixed_gain_vs_mesh_percent",
            "aware_latency": "aware_latency",
            "aware_hops": "aware_hops",
            "aware_knee": "aware_knee_load",
            "aware_gain": "aware_gain_vs_mesh_percent",
            "direct_gain": "aware_gain_vs_fixed_percent",
        }
    required = {
        "workload", "budget_mm", "requested_k", "fixed_actual_k", "fixed_total_wire_mm",
        "aware_actual_k", "aware_total_wire_mm", *names.values(),
    }
    rows = []
    seen = set()
    for raw in read_csv(path, required, f"{family} policy summary"):
        workload = raw["workload"]
        budget = integer(raw["budget_mm"], f"{family} budget")
        requested_k = integer(raw["requested_k"], f"{family} requested_k")
        key = (workload, budget, requested_k)
        if budget not in BUDGETS or requested_k not in K_VALUES or key in seen:
            fail(f"Invalid or duplicate {family} policy key: {key}")
        seen.add(key)
        row = {
            "family": family,
            "workload": workload,
            "budget_mm": budget,
            "requested_k": requested_k,
            "mesh_latency": number(raw[names["mesh_latency"]], "mesh latency"),
            "mesh_hops": number(raw[names["mesh_hops"]], "mesh hops"),
            "mesh_knee": number(raw[names["mesh_knee"]], "mesh knee", optional=True),
            "fixed_latency": number(raw[names["fixed_latency"]], "fixed latency"),
            "fixed_hops": number(raw[names["fixed_hops"]], "fixed hops"),
            "fixed_knee": number(raw[names["fixed_knee"]], "fixed knee", optional=True),
            "fixed_gain": number(raw[names["fixed_gain"]], "fixed gain"),
            "fixed_actual_k": integer(raw["fixed_actual_k"], "fixed actual_k"),
            "fixed_wire": number(raw["fixed_total_wire_mm"], "fixed wire"),
            "aware_latency": number(raw[names["aware_latency"]], "aware latency"),
            "aware_hops": number(raw[names["aware_hops"]], "aware hops"),
            "aware_knee": number(raw[names["aware_knee"]], "aware knee", optional=True),
            "aware_gain": number(raw[names["aware_gain"]], "aware gain"),
            "direct_gain": number(raw[names["direct_gain"]], "aware-vs-fixed gain"),
            "aware_actual_k": integer(raw["aware_actual_k"], "aware actual_k"),
            "aware_wire": number(raw["aware_total_wire_mm"], "aware wire"),
        }
        if row["fixed_actual_k"] > requested_k or row["aware_actual_k"] > requested_k:
            fail(f"actual_k exceeds requested_k for {family} {key}")
        if row["fixed_wire"] > budget + EPS or row["aware_wire"] > budget + EPS:
            fail(f"Wire budget exceeded for {family} {key}")
        checks = (
            (row["fixed_gain"], improvement(row["mesh_latency"], row["fixed_latency"])),
            (row["aware_gain"], improvement(row["mesh_latency"], row["aware_latency"])),
            (row["direct_gain"], improvement(row["fixed_latency"], row["aware_latency"])),
        )
        if any(not math.isclose(actual, expected, rel_tol=EPS, abs_tol=EPS) for actual, expected in checks):
            fail(f"Performance gain mismatch for {family} {key}")
        rows.append(row)
    workloads = {row["workload"] for row in rows}
    expected = len(workloads) * len(BUDGETS) * len(K_VALUES)
    if len(rows) != expected:
        fail(f"{family} summary has {len(rows)} rows; expected {expected}")
    return rows


def topology_index(path: Path, family: str) -> dict[tuple[str, int, int, str], tuple[str, ...]]:
    required = {
        "workload", "budget_mm", "requested_k", "policy", "actual_k",
        "link_1", "link_2", "link_3", "link_4",
    }
    indexed = {}
    for raw in read_csv(path, required, f"{family} policy comparison"):
        policy = raw["policy"]
        if policy not in POLICIES:
            fail(f"Unexpected policy in {family} comparison: {policy}")
        actual_k = integer(raw["actual_k"], "comparison actual_k")
        links = tuple(sorted(raw[f"link_{index}"].strip() for index in range(1, actual_k + 1)))
        key = (
            raw["workload"], integer(raw["budget_mm"], "comparison budget"),
            integer(raw["requested_k"], "comparison K"), policy,
        )
        if key in indexed:
            fail(f"Duplicate comparison key: {family} {key}")
        indexed[key] = links
    return indexed


def validate_final_tables(synthetic: list[dict[str, Any]], stage: list[dict[str, Any]]) -> None:
    specifications = {
        "synthetic": (48, {"workload", "budget", "requested_k", "mesh_latency"}),
        "stage": (36, {"workload", "budget", "requested_k", "mesh_latency"}),
        "links": (252, {"workload_family", "workload", "policy", "requested_k"}),
        "overhead": (1, {"area_4phy_mm2", "area_8phy_mm2", "area_overhead_percent"}),
        "multiseed": (24, {"workload", "budget", "requested_k", "mean_gain_percent"}),
    }
    for name, (expected_count, required) in specifications.items():
        rows = read_csv(TABLES[name], required, f"final {name} table")
        if len(rows) != expected_count:
            fail(f"Final {name} table has {len(rows)} rows; expected {expected_count}")
    # Verify representative table metrics against the source summaries.
    source = {(row["workload"], row["budget_mm"], row["requested_k"]): row for row in synthetic}
    for raw in read_csv(TABLES["synthetic"], {"workload", "budget", "requested_k", "aware_latency"}, "synthetic final table"):
        key = (raw["workload"], integer(raw["budget"], "table budget"), integer(raw["requested_k"], "table K"))
        if key not in source or not math.isclose(number(raw["aware_latency"], "table aware latency"), source[key]["aware_latency"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Final synthetic table/source mismatch for {key}")
    if len(stage) != 36:
        fail("STAGE source summary must contain 36 configurations")


def aggregate_row(category: str, rows: list[dict[str, Any]], workload: str = "", requested_k: int | None = None,
                  budget: int | None = None, notes: str = "") -> dict[str, Any]:
    if not rows:
        fail(f"Cannot build empty aggregate row: {category}")
    return {
        "category": category,
        "workload": workload,
        "requested_k": requested_k,
        "budget_mm": budget,
        "mesh_latency": mean(row["mesh_latency"] for row in rows),
        "fixed_latency": mean(row["fixed_latency"] for row in rows),
        "aware_latency": mean(row["aware_latency"] for row in rows),
        "fixed_gain_vs_mesh_percent": mean(row["fixed_gain"] for row in rows),
        "aware_gain_vs_mesh_percent": mean(row["aware_gain"] for row in rows),
        "aware_gain_vs_fixed_percent": mean(row["direct_gain"] for row in rows),
        "fixed_actual_k": mean(row["fixed_actual_k"] for row in rows),
        "aware_actual_k": mean(row["aware_actual_k"] for row in rows),
        "fixed_wire_mm": mean(row["fixed_wire"] for row in rows),
        "aware_wire_mm": mean(row["aware_wire"] for row in rows),
        "fixed_knee": optional_mean(row["fixed_knee"] for row in rows),
        "aware_knee": optional_mean(row["aware_knee"] for row in rows),
        "notes": notes or f"Arithmetic mean across {len(rows)} configurations.",
    }


def topology_adaptation_rows(
    synthetic: list[dict[str, Any]], topology: dict[tuple[str, int, int, str], tuple[str, ...]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output = []
    stats = []
    workloads = sorted({row["workload"] for row in synthetic})
    indexed = {(row["workload"], row["budget_mm"], row["requested_k"]): row for row in synthetic}
    for budget in BUDGETS:
        for requested_k in K_VALUES:
            different = []
            identical = []
            for workload in workloads:
                fixed = topology[(workload, budget, requested_k, "fixed")]
                aware = topology[(workload, budget, requested_k, "workload_aware")]
                target = different if fixed != aware else identical
                target.append(indexed[(workload, budget, requested_k)]["direct_gain"])
            all_rows = [indexed[(workload, budget, requested_k)] for workload in workloads]
            notes = (
                f"Different chiplet-pair link sets: {len(different)}/{len(workloads)} "
                f"({100.0 * len(different) / len(workloads):.2f}%); "
                f"mean aware gain when different={optional_mean(different)}%; "
                f"when identical={optional_mean(identical)}%."
            )
            output.append(aggregate_row("synthetic_topology_adaptation", all_rows, requested_k=requested_k, budget=budget, notes=notes))
            stats.append(
                {
                    "budget": budget, "k": requested_k, "different_count": len(different),
                    "workload_count": len(workloads), "different_mean": optional_mean(different),
                    "identical_mean": optional_mean(identical),
                }
            )
    return output, stats


def build_final_summary(
    synthetic: list[dict[str, Any]], stage: list[dict[str, Any]], topology_stats_rows: list[dict[str, Any]],
    multiseed: list[dict[str, str]], overhead: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = [aggregate_row("synthetic_overall", synthetic)]
    for workload in sorted({row["workload"] for row in synthetic}):
        group = [row for row in synthetic if row["workload"] == workload]
        rows.append(aggregate_row("synthetic_workload", group, workload=workload))
    for requested_k in K_VALUES:
        group = [row for row in synthetic if row["requested_k"] == requested_k]
        rows.append(aggregate_row("synthetic_k", group, requested_k=requested_k))
    for budget in BUDGETS:
        group = [row for row in synthetic if row["budget_mm"] == budget]
        rows.append(aggregate_row("synthetic_budget", group, budget=budget))

    rows.append(aggregate_row("stage_overall", stage))
    for workload in sorted({row["workload"] for row in stage}):
        group = [row for row in stage if row["workload"] == workload]
        rows.append(aggregate_row("stage_workload", group, workload=workload))
    for requested_k in K_VALUES:
        group = [row for row in stage if row["requested_k"] == requested_k]
        rows.append(aggregate_row("stage_k", group, requested_k=requested_k))
    for budget in BUDGETS:
        group = [row for row in stage if row["budget_mm"] == budget]
        rows.append(aggregate_row("stage_budget", group, budget=budget))
    rows.extend(topology_stats_rows)

    blank = {field: None for field in SUMMARY_FIELDS}
    for workload in sorted({row["workload"] for row in multiseed}):
        group = [row for row in multiseed if row["workload"] == workload]
        result = dict(blank)
        result.update(
            {
                "category": "multiseed_workload", "workload": workload,
                "aware_gain_vs_mesh_percent": mean(number(row["mean_latency_gain_percent"], "multiseed gain") for row in group),
                "aware_actual_k": mean(number(row["mean_actual_k"], "multiseed actual_k") for row in group),
                "aware_wire_mm": mean(number(row["mean_total_wire_length_mm"], "multiseed wire") for row in group),
                "notes": (
                    f"Arithmetic mean across {len(group)} seed-summary groups; mean seed standard deviation="
                    f"{mean(number(row['std_latency_gain_percent'], 'multiseed std') for row in group):.6f}%."
                ),
            }
        )
        rows.append(result)
    result = dict(blank)
    result.update(
        {
            "category": "physical_overhead",
            "notes": (
                f"4PHY-to-8PHY area overhead={overhead['area_overhead_percent']:.6f}%; "
                f"power overhead={overhead['power_overhead_percent']:.6f}%; "
                f"PHY-count increase={overhead['phy_count_increase']} "
                f"({overhead['phy_count_increase_percent']:.2f}%)."
            ),
        }
    )
    rows.append(result)
    return rows


def fmt(value: float | None, decimals: int = 3) -> str:
    return "unavailable" if value is None else f"{value:.{decimals}f}"


def config_name(row: dict[str, Any]) -> str:
    return f"{row['workload']} B{row['budget_mm']} K={row['requested_k']}"


def transition_analysis(rows: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    latency_field = f"{policy}_latency"
    wire_field = f"{policy}_wire"
    actual_field = f"{policy}_actual_k"
    indexed = {(row["workload"], row["budget_mm"], row["requested_k"]): row for row in rows}
    transitions = []
    for workload in sorted({row["workload"] for row in rows}):
        for budget in BUDGETS:
            for previous_k, current_k in ((1, 2), (2, 4)):
                previous = indexed[(workload, budget, previous_k)]
                current = indexed[(workload, budget, current_k)]
                gain = improvement(previous[latency_field], current[latency_field])
                transitions.append(
                    {
                        "workload": workload, "budget": budget, "previous_k": previous_k,
                        "current_k": current_k, "gain": gain,
                        "wire_added": current[wire_field] - previous[wire_field],
                        "links_added": current[actual_field] - previous[actual_field],
                    }
                )
    return {
        "rows": transitions,
        "negligible": [row for row in transitions if abs(row["gain"]) <= DESCRIPTIVE_NEGLIGIBLE_PERCENT],
        "no_link_increase": [row for row in transitions if row["links_added"] == 0],
        "worse": [row for row in transitions if row["gain"] < -EPS],
    }


def budget_analysis(rows: list[dict[str, Any]], topology: dict[tuple[str, int, int, str], tuple[str, ...]], policy: str) -> dict[str, Any]:
    indexed = {(row["workload"], row["budget_mm"], row["requested_k"]): row for row in rows}
    topology_policy = "workload_aware" if policy == "aware" else policy
    transitions = []
    for workload in sorted({row["workload"] for row in rows}):
        for requested_k in K_VALUES:
            for previous_budget, current_budget in zip(BUDGETS, BUDGETS[1:]):
                previous = indexed[(workload, previous_budget, requested_k)]
                current = indexed[(workload, current_budget, requested_k)]
                same_topology = topology[(workload, previous_budget, requested_k, topology_policy)] == topology[(workload, current_budget, requested_k, topology_policy)]
                transitions.append(
                    {
                        "workload": workload, "k": requested_k, "previous_budget": previous_budget,
                        "current_budget": current_budget, "latency_change": current[f"{policy}_latency"] - previous[f"{policy}_latency"],
                        "wire_change": current[f"{policy}_wire"] - previous[f"{policy}_wire"],
                        "knee_change": (
                            None if previous[f"{policy}_knee"] is None or current[f"{policy}_knee"] is None
                            else current[f"{policy}_knee"] - previous[f"{policy}_knee"]
                        ),
                        "actual_change": current[f"{policy}_actual_k"] - previous[f"{policy}_actual_k"],
                        "same_topology": same_topology,
                    }
                )
    return {
        "rows": transitions,
        "unchanged": [row for row in transitions if row["same_topology"] and abs(row["latency_change"]) <= EPS],
        "knee_decrease": [row for row in transitions if row["knee_change"] is not None and row["knee_change"] < -EPS],
    }


def physical_analysis() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = {
        "workload_family", "workload", "budget_mm", "policy", "requested_k", "actual_k",
        "total_wire_length_mm", "spare_phy_utilization_percent", "latency_gain_per_mm",
        "hop_gain_per_mm", "previous_requested_k", "added_wire_vs_previous_k_mm",
        "incremental_latency_gain_percent", "incremental_latency_gain_per_added_mm",
    }
    rows = []
    for raw in read_csv(INPUTS["physical_cost"], required, "physical-cost summary"):
        rows.append(
            {
                "family": raw["workload_family"], "workload": raw["workload"],
                "budget": integer(raw["budget_mm"], "physical budget"), "policy": raw["policy"],
                "k": integer(raw["requested_k"], "physical K"), "actual_k": integer(raw["actual_k"], "physical actual_k"),
                "wire": number(raw["total_wire_length_mm"], "physical wire"),
                "phy_util": number(raw["spare_phy_utilization_percent"], "PHY utilization"),
                "latency_per_mm": number(raw["latency_gain_per_mm"], "latency gain/mm", optional=True),
                "hop_per_mm": number(raw["hop_gain_per_mm"], "hop gain/mm", optional=True),
                "previous_k": number(raw["previous_requested_k"], "previous K", optional=True),
                "added_wire": number(raw["added_wire_vs_previous_k_mm"], "added wire", optional=True),
                "incremental_gain": number(raw["incremental_latency_gain_percent"], "incremental gain", optional=True),
                "incremental_per_mm": number(raw["incremental_latency_gain_per_added_mm"], "incremental gain/mm", optional=True),
            }
        )
    usage = [row for row in rows if row["policy"] != "mesh"]
    diminishing = [
        row for row in usage
        if row["added_wire"] is not None and row["added_wire"] > EPS
        and row["incremental_gain"] is not None
        and row["incremental_gain"] <= DESCRIPTIVE_NEGLIGIBLE_PERCENT
    ]
    return rows, {
        "usage": usage,
        "mean_wire": mean(row["wire"] for row in usage),
        "mean_actual_k": mean(row["actual_k"] for row in usage),
        "mean_phy_util": mean(row["phy_util"] for row in usage),
        "mean_latency_per_mm": optional_mean(row["latency_per_mm"] for row in usage),
        "mean_hop_per_mm": optional_mean(row["hop_per_mm"] for row in usage),
        "diminishing": diminishing,
        "worse_with_added_wire": [row for row in diminishing if row["incremental_gain"] < -EPS],
    }


def generate_findings(
    synthetic: list[dict[str, Any]], stage: list[dict[str, Any]],
    synth_topology: dict[tuple[str, int, int, str], tuple[str, ...]], topology_stats: list[dict[str, Any]],
    multiseed: list[dict[str, str]], overhead: dict[str, Any], physical_stats: dict[str, Any],
) -> str:
    all_groups = synthetic + stage
    synth_fixed_wins = [row for row in synthetic if row["direct_gain"] < -EPS]
    synth_equal = [row for row in synthetic if abs(row["direct_gain"]) <= DESCRIPTIVE_NEGLIGIBLE_PERCENT]
    stage_fixed_wins = [row for row in stage if row["direct_gain"] < -EPS]
    stage_equal = [row for row in stage if abs(row["direct_gain"]) <= DESCRIPTIVE_NEGLIGIBLE_PERCENT]
    aware_beats_mesh = [row for row in all_groups if row["aware_gain"] > EPS]
    aware_beats_fixed = [row for row in all_groups if row["direct_gain"] > EPS]
    best_direct = max(all_groups, key=lambda row: row["direct_gain"])
    worst_direct = min(all_groups, key=lambda row: row["direct_gain"])

    synthetic_workloads = sorted({row["workload"] for row in synthetic})
    stage_workloads = sorted({row["workload"] for row in stage})
    synth_k = transition_analysis(synthetic, "aware")
    fixed_k = transition_analysis(synthetic, "fixed")
    synth_budget = budget_analysis(synthetic, synth_topology, "aware")
    fixed_budget = budget_analysis(synthetic, synth_topology, "fixed")

    under_requested = {}
    for family, rows in (("synthetic", synthetic), ("stage", stage)):
        for policy in ("fixed", "aware"):
            under_requested[(family, policy)] = sum(row[f"{policy}_actual_k"] < row["requested_k"] for row in rows)

    knee_pairs = [row for row in all_groups if row["mesh_knee"] is not None and row["aware_knee"] is not None]
    knee_higher = sum(row["aware_knee"] > row["mesh_knee"] + EPS for row in knee_pairs)
    knee_lower = sum(row["aware_knee"] < row["mesh_knee"] - EPS for row in knee_pairs)
    knee_equal = len(knee_pairs) - knee_higher - knee_lower

    lines = [
        "FINAL QUANTITATIVE FINDINGS",
        "===========================",
        "",
        "Method note: all aggregate values below are arithmetic means of the completed logical configurations. ",
        f"'Effectively equal' and 'negligible' use a descriptive +/-{DESCRIPTIVE_NEGLIGIBLE_PERCENT:.1f}% latency threshold; this is not a statistical test.",
        "The congestion metric is the derived latency knee from tested offered-load points.",
        "",
        "1. Main result",
        "--------------",
        f"Across {len(all_groups)} logical workload/budget/requested_k configurations ({len(synthetic)} synthetic, {len(stage)} STAGE), "
        f"Workload-Aware improved on Mesh in {len(aware_beats_mesh)}/{len(all_groups)} cases ({100.0*len(aware_beats_mesh)/len(all_groups):.2f}%) "
        f"with a mean gain of {mean(row['aware_gain'] for row in all_groups):.3f}%.",
        f"Workload-Aware improved on Fixed in {len(aware_beats_fixed)}/{len(all_groups)} cases ({100.0*len(aware_beats_fixed)/len(all_groups):.2f}%) "
        f"with a mean gain of {mean(row['direct_gain'] for row in all_groups):.3f}%.",
        f"The best observed Workload-Aware gain vs Fixed was {best_direct['direct_gain']:.3f}% at {best_direct['family']} {config_name(best_direct)}; "
        f"the worst was {worst_direct['direct_gain']:.3f}% at {worst_direct['family']} {config_name(worst_direct)}.",
        "",
        "Synthetic workload results:",
    ]
    for workload in synthetic_workloads:
        group = [row for row in synthetic if row["workload"] == workload]
        best_latency = min(group, key=lambda row: row["aware_latency"])
        best_gain = max(group, key=lambda row: row["direct_gain"])
        lines.append(
            f"- {workload}: mean latencies Mesh/Fixed/Workload-Aware="
            f"{mean(row['mesh_latency'] for row in group):.3f}/{mean(row['fixed_latency'] for row in group):.3f}/{mean(row['aware_latency'] for row in group):.3f} cycles; "
            f"mean hops={mean(row['mesh_hops'] for row in group):.3f}/{mean(row['fixed_hops'] for row in group):.3f}/{mean(row['aware_hops'] for row in group):.3f}; "
            f"mean Workload-Aware gain vs Mesh={mean(row['aware_gain'] for row in group):.3f}%, "
            f"vs Fixed={mean(row['direct_gain'] for row in group):.3f}%; best latency={best_latency['aware_latency']:.3f} cycles "
            f"at B{best_latency['budget_mm']} K={best_latency['requested_k']} (actual_k={best_latency['aware_actual_k']}, wire={best_latency['aware_wire']:.3f} mm); "
            f"best gain vs Fixed={best_gain['direct_gain']:.3f}% at B{best_gain['budget_mm']} K={best_gain['requested_k']}.")
    lines.extend(
        [
            f"Fixed beat Workload-Aware in {len(synth_fixed_wins)}/{len(synthetic)} synthetic configurations; "
            f"{len(synth_equal)}/{len(synthetic)} were effectively equal by the descriptive threshold.",
            "Negative synthetic cases: " + (
                ", ".join(f"{config_name(row)} ({row['direct_gain']:.3f}%)" for row in synth_fixed_wins)
                if synth_fixed_wins else "none"
            ) + ".",
            "",
            "2. Workload-awareness result",
            "----------------------------",
        ]
    )
    for stat in topology_stats:
        lines.append(
            f"- B{stat['budget']} K={stat['k']}: different chiplet-pair link sets for "
            f"{stat['different_count']}/{stat['workload_count']} workloads ({100.0*stat['different_count']/stat['workload_count']:.1f}%); "
            f"mean aware-vs-fixed gain when different={fmt(stat['different_mean'])}%, when identical={fmt(stat['identical_mean'])}%.")
    total_different = sum(stat["different_count"] for stat in topology_stats)
    total_opportunities = sum(stat["workload_count"] for stat in topology_stats)
    lines.append(
        f"Overall, Fixed and Workload-Aware used different chiplet-pair link sets in {total_different}/{total_opportunities} "
        f"synthetic workload/K/budget cases ({100.0*total_different/total_opportunities:.2f}%)."
    )
    lines.extend(
        [
            "",
            "3. K result",
            "-----------",
            f"For synthetic Workload-Aware, {len(synth_k['negligible'])}/{len(synth_k['rows'])} K transitions had <={DESCRIPTIVE_NEGLIGIBLE_PERCENT:.1f}% absolute latency change, "
            f"{len(synth_k['no_link_increase'])}/{len(synth_k['rows'])} added no actual link, and {len(synth_k['worse'])} worsened latency.",
            f"For synthetic Fixed, {len(fixed_k['negligible'])}/{len(fixed_k['rows'])} transitions were negligible, "
            f"{len(fixed_k['no_link_increase'])}/{len(fixed_k['rows'])} added no actual link, and {len(fixed_k['worse'])} worsened latency.",
            "Increasing requested_k therefore did not always increase actual_k or improve latency.",
            f"actual_k < requested_k occurred for Fixed in {under_requested[('synthetic','fixed')]}/{len(synthetic)} synthetic and "
            f"{under_requested[('stage','fixed')]}/{len(stage)} STAGE configurations; for Workload-Aware it occurred in "
            f"{under_requested[('synthetic','aware')]}/{len(synthetic)} synthetic and {under_requested[('stage','aware')]}/{len(stage)} STAGE configurations.",
            "",
            "4. Wire-budget result",
            "---------------------",
            f"Among synthetic Workload-Aware budget transitions, {len(synth_budget['unchanged'])}/{len(synth_budget['rows'])} retained the same link set and identical latency; "
            f"for Fixed this occurred in {len(fixed_budget['unchanged'])}/{len(fixed_budget['rows'])} transitions.",
            f"Mean synthetic Workload-Aware used wire rose from {mean(row['aware_wire'] for row in synthetic if row['budget_mm']==5):.3f} mm at B5 "
            f"to {mean(row['aware_wire'] for row in synthetic if row['budget_mm']==45):.3f} mm at B45, while mean gain vs Mesh changed from "
            f"{mean(row['aware_gain'] for row in synthetic if row['budget_mm']==5):.3f}% to {mean(row['aware_gain'] for row in synthetic if row['budget_mm']==45):.3f}%.",
            "A larger budget was only a constraint relaxation: the stored optimizer could stop early, so consumed wire and actual_k often remained unchanged.",
            "",
            "5. Congestion/knee result",
            "-------------------------",
            f"A derived latency knee was comparable between Mesh and Workload-Aware in {len(knee_pairs)} configurations: "
            f"Workload-Aware was higher in {knee_higher}, lower in {knee_lower}, and equal in {knee_equal}.",
            f"Across synthetic budget transitions, the Workload-Aware derived latency knee decreased in {len(synth_budget['knee_decrease'])} comparable transitions; "
            f"Fixed decreased in {len(fixed_budget['knee_decrease'])}. Longer allowed wire therefore did not uniformly improve the knee.",
            "",
            "6. Multi-seed robustness",
            "-------------------------",
        ]
    )
    for workload in sorted({row["workload"] for row in multiseed}):
        group = [row for row in multiseed if row["workload"] == workload]
        min_seed_gain = min(number(row["min_latency_gain_percent"], "min seed gain") for row in group)
        max_seed_gain = max(number(row["max_latency_gain_percent"], "max seed gain") for row in group)
        lines.append(
            f"- {workload}: mean gain across K/budget seed summaries={mean(number(row['mean_latency_gain_percent'], 'seed mean') for row in group):.3f}%, "
            f"mean within-group standard deviation={mean(number(row['std_latency_gain_percent'], 'seed std') for row in group):.3f}%, "
            f"observed group min/max gains={min_seed_gain:.3f}%/{max_seed_gain:.3f}%; unique topologies ranged "
            f"{min(integer(row['unique_selected_topologies'], 'unique topologies') for row in group)}-"
            f"{max(integer(row['unique_selected_topologies'], 'unique topologies') for row in group)}, and actual_k ranged "
            f"{min(integer(row['min_actual_k'], 'min actual_k') for row in group)}-{max(integer(row['max_actual_k'], 'max actual_k') for row in group)}; "
            f"the mean most-common-topology seed frequency was "
            f"{100.0*mean(number(row['most_common_topology_seed_fraction'], 'topology frequency') for row in group):.1f}%.")
    all_min_seed_gain = min(number(row["min_latency_gain_percent"], "all min seed gain") for row in multiseed)
    lines.append(
        f"The minimum reported gain across all randomized-seed groups was {all_min_seed_gain:.3f}%; benefits persisted in these sampled seeds, "
        "but no statistical-significance test was performed."
    )
    lines.extend(
        [
            "",
            "7. STAGE/DNN generalization",
            "---------------------------",
        ]
    )
    for workload in stage_workloads:
        group = [row for row in stage if row["workload"] == workload]
        best = max(group, key=lambda row: row["direct_gain"])
        lines.append(
            f"- {workload}: mean latencies Mesh/Fixed/Workload-Aware="
            f"{mean(row['mesh_latency'] for row in group):.3f}/{mean(row['fixed_latency'] for row in group):.3f}/{mean(row['aware_latency'] for row in group):.3f} cycles; "
            f"mean hops={mean(row['mesh_hops'] for row in group):.3f}/{mean(row['fixed_hops'] for row in group):.3f}/{mean(row['aware_hops'] for row in group):.3f}; "
            f"mean Workload-Aware gain vs Mesh={mean(row['aware_gain'] for row in group):.3f}%, "
            f"vs Fixed={mean(row['direct_gain'] for row in group):.3f}%; best vs-Fixed={best['direct_gain']:.3f}% "
            f"at B{best['budget_mm']} K={best['requested_k']} (actual_k={best['aware_actual_k']}, wire={best['aware_wire']:.3f} mm, "
            f"derived knee={fmt(best['aware_knee'])}).")
    lines.extend(
        [
            f"Across STAGE, mean Workload-Aware gain was {mean(row['aware_gain'] for row in stage):.3f}% vs Mesh and "
            f"{mean(row['direct_gain'] for row in stage):.3f}% vs Fixed. Fixed beat Workload-Aware in {len(stage_fixed_wins)}/{len(stage)} cases; "
            f"{len(stage_equal)}/{len(stage)} were effectively equal.",
            "The synthetic observation--benefit depends on workload, budget, and whether extra links are actually selected--also appears in STAGE; it is not universal dominance.",
            "",
            "8. Physical-cost tradeoff",
            "-------------------------",
            f"The actual 4PHY chiplet is {overhead['area_4phy']:.3f} mm^2 and {overhead['power_4phy']:.3f} W; the 8PHY chiplet is "
            f"{overhead['area_8phy']:.3f} mm^2 and {overhead['power_8phy']:.3f} W. Capability overhead is "
            f"{overhead['area_overhead_percent']:.3f}% area, {overhead['power_overhead_percent']:.3f}% power, and "
            f"+{overhead['phy_count_increase']} PHYs ({overhead['phy_count_increase_percent']:.1f}%).",
            f"Across {len(physical_stats['usage'])} Fixed/Workload-Aware logical configurations, mean usage was "
            f"{physical_stats['mean_wire']:.3f} mm, actual_k={physical_stats['mean_actual_k']:.3f}, and "
            f"{physical_stats['mean_phy_util']:.3f}% of the 64 spare PHY endpoints; mean descriptive efficiencies were "
            f"{fmt(physical_stats['mean_latency_per_mm'])} latency-gain percentage points/mm and {fmt(physical_stats['mean_hop_per_mm'])} hop-gain percentage points/mm.",
            f"Per-configuration added wire ranged {min(row['wire'] for row in physical_stats['usage']):.3f}-"
            f"{max(row['wire'] for row in physical_stats['usage']):.3f} mm, and actual_k ranged "
            f"{min(row['actual_k'] for row in physical_stats['usage'])}-{max(row['actual_k'] for row in physical_stats['usage'])}.",
            f"Using the stated descriptive <={DESCRIPTIVE_NEGLIGIBLE_PERCENT:.1f}% transition criterion, {len(physical_stats['diminishing'])} configurations added wire with negligible or negative incremental latency gain; "
            f"{len(physical_stats['worse_with_added_wire'])} of those worsened latency.",
            "Capability overhead (providing four spare PHYs per chiplet) is separate from usage cost (wire and two PHY endpoints per selected shortcut).",
            "",
            "9. Limitations",
            "--------------",
            "The results cover the tested 4x4, 16-chiplet architecture, four wire budgets, K={1,2,4}, the listed synthetic/STAGE workloads, and the available randomized seeds.",
            "The derived latency knee is based on tested offered-load points and is not an exact throughput boundary. Empty/unobserved knees remain unavailable.",
            "Arithmetic means summarize heterogeneous workloads; they do not imply statistical significance or universal superiority. The 0.1% equality/negligibility threshold is descriptive.",
            "Physical optimizer metrics were reused as stored; missing metrics were not fabricated, and no topology was reselected.",
            "",
            "10. Final conclusion",
            "--------------------",
            f"Workload-Aware shortcuts usually improved Mesh and averaged {mean(row['direct_gain'] for row in all_groups):.3f}% over Fixed across all tested logical configurations, "
            "but Fixed sometimes won and many higher-requested-K configurations stopped at fewer links. The measured value of adaptation is workload- and budget-dependent, "
            "with observable diminishing returns. These performance gains require a 4.393% chiplet-area and 2.439% chiplet-power capability overhead in the project data, plus only the wire/PHY usage of shortcuts actually selected.",
            "",
        ]
    )
    return "\n".join(lines)


def load_multiseed() -> list[dict[str, str]]:
    required = {
        "workload", "budget_mm", "requested_k", "mean_latency_gain_percent",
        "std_latency_gain_percent", "min_latency_gain_percent", "max_latency_gain_percent",
        "unique_selected_topologies", "min_actual_k", "max_actual_k", "mean_actual_k",
        "mean_total_wire_length_mm", "most_common_topology_seed_fraction",
    }
    rows = read_csv(INPUTS["multiseed"], required, "multiseed summary")
    if len(rows) != 24 or {row["workload"] for row in rows} != {"permutation", "hotspot"}:
        fail("Multiseed summary matrix is incomplete")
    return rows


def load_overhead() -> dict[str, Any]:
    required = {
        "record_type", "architecture", "chiplet_area_mm2", "chiplet_power_w", "phy_count",
        "area_overhead_percent", "power_overhead_percent", "phy_count_increase",
        "phy_count_increase_percent",
    }
    rows = read_csv(INPUTS["overhead"], required, "physical overhead")
    architectures = {row["architecture"]: row for row in rows if row["record_type"] == "architecture"}
    overhead_rows = [row for row in rows if row["record_type"] == "overhead"]
    if set(architectures) != {"4PHY", "8PHY"} or len(overhead_rows) != 1:
        fail("Physical-overhead records are incomplete")
    return {
        "area_4phy": number(architectures["4PHY"]["chiplet_area_mm2"], "4PHY area"),
        "area_8phy": number(architectures["8PHY"]["chiplet_area_mm2"], "8PHY area"),
        "power_4phy": number(architectures["4PHY"]["chiplet_power_w"], "4PHY power"),
        "power_8phy": number(architectures["8PHY"]["chiplet_power_w"], "8PHY power"),
        "area_overhead_percent": number(overhead_rows[0]["area_overhead_percent"], "area overhead"),
        "power_overhead_percent": number(overhead_rows[0]["power_overhead_percent"], "power overhead"),
        "phy_count_increase": integer(overhead_rows[0]["phy_count_increase"], "PHY increase"),
        "phy_count_increase_percent": number(overhead_rows[0]["phy_count_increase_percent"], "PHY increase percent"),
    }


def write_summary(rows: list[dict[str, Any]]) -> None:
    try:
        with SUMMARY_OUTPUT.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
            writer.writeheader()
            writer.writerows({field: row[field] for field in SUMMARY_FIELDS} for row in rows)
    except OSError as exc:
        fail(f"Cannot write {SUMMARY_OUTPUT}: {exc}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for path in (*INPUTS.values(), *TABLES.values()):
        if not path.is_file():
            fail(f"Missing required final-analysis input: {path}")
    synthetic = normalize_policy_summary(INPUTS["synthetic_summary"], "synthetic")
    stage = normalize_policy_summary(INPUTS["stage_summary"], "stage")
    validate_final_tables(synthetic, stage)
    synth_topology = topology_index(INPUTS["synthetic_comparison"], "synthetic")
    stage_topology = topology_index(INPUTS["stage_comparison"], "stage")
    if len(synth_topology) != 144 or len(stage_topology) != 108:
        fail("Policy comparison topology matrices are incomplete")
    topology_summary_rows, topology_stats = topology_adaptation_rows(synthetic, synth_topology)
    multiseed = load_multiseed()
    overhead = load_overhead()
    _, physical_stats = physical_analysis()
    final_rows = build_final_summary(
        synthetic, stage, topology_summary_rows, multiseed, overhead
    )
    write_summary(final_rows)
    findings = generate_findings(
        synthetic, stage, synth_topology, topology_stats, multiseed, overhead, physical_stats
    )
    try:
        FINDINGS_OUTPUT.write_text(findings, encoding="utf-8")
    except OSError as exc:
        fail(f"Cannot write {FINDINGS_OUTPUT}: {exc}")
    if not SUMMARY_OUTPUT.is_file() or not FINDINGS_OUTPUT.is_file() or FINDINGS_OUTPUT.stat().st_size == 0:
        fail("Final analysis outputs were not saved successfully")

    all_groups = synthetic + stage
    best = max(all_groups, key=lambda row: row["direct_gain"])
    print("FINAL RESULT ANALYSIS COMPLETE")
    print(f"Synthetic configurations analyzed: {len(synthetic)}")
    print(f"STAGE configurations analyzed: {len(stage)}")
    print(f"Overall average Workload-Aware gain vs Mesh: {mean(row['aware_gain'] for row in all_groups):.6f}%")
    print(f"Overall average Workload-Aware gain vs Fixed: {mean(row['direct_gain'] for row in all_groups):.6f}%")
    print(f"Best observed Workload-Aware gain vs Fixed: {best['direct_gain']:.6f}% ({best['family']} {config_name(best)})")
    print(f"4-PHY to 8-PHY area overhead: {overhead['area_overhead_percent']:.6f}%")
    print(f"4-PHY to 8-PHY power overhead: {overhead['power_overhead_percent']:.6f}%")
    print(f"Summary output: {relative(SUMMARY_OUTPUT)}")
    print(f"Findings output: {relative(FINDINGS_OUTPUT)}")


if __name__ == "__main__":
    try:
        main()
    except (FinalAnalysisError, OSError, KeyError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit(f"Final result analysis failed: {exc}") from exc
