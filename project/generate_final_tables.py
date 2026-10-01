#!/usr/bin/env python3
"""Generate publication-ready final CSV tables from completed experiments."""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUTPUT_DIR = RESULTS / "tables"
INPUTS = {
    "synthetic_comparison": RESULTS / "all_k_policy_comparison.csv",
    "synthetic_summary": RESULTS / "all_k_summary.csv",
    "multiseed": RESULTS / "multiseed_summary.csv",
    "stage_comparison": RESULTS / "stage_policy_comparison.csv",
    "stage_summary": RESULTS / "stage_policy_summary.csv",
    "physical_cost": RESULTS / "physical_cost_summary.csv",
    "overhead": RESULTS / "physical_cost_overhead.csv",
}
K_VALUES = {1, 2, 4}
BUDGETS = {5, 15, 25, 45}
POLICIES = {"mesh", "fixed", "workload_aware"}
EPS = 1e-9


class TableError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise TableError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_csv(name: str, required: set[str]) -> list[dict[str, str]]:
    path = INPUTS[name]
    if not path.is_file():
        fail(f"Missing required input: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                fail(f"{path} lacks columns: {', '.join(missing)}")
            return list(reader)
    except OSError as exc:
        fail(f"Cannot read {path}: {exc}")


def numeric(value: Any, description: str, optional: bool = False) -> float | None:
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
    converted = numeric(value, description)
    result = int(converted)
    if result != converted:
        fail(f"{description} must be an integer")
    return result


def write_table(filename: str, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> Path:
    path = OUTPUT_DIR / filename
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as exc:
        fail(f"Cannot write table {path}: {exc}")
    if not path.is_file() or path.stat().st_size == 0:
        fail(f"Table was not saved successfully: {path}")
    return path


POLICY_TABLE_FIELDS = (
    "workload", "budget", "requested_k", "fixed_actual_k", "aware_actual_k",
    "fixed_wire", "aware_wire", "mesh_latency", "fixed_latency", "aware_latency",
    "fixed_gain_vs_mesh", "aware_gain_vs_mesh", "aware_gain_vs_fixed",
    "fixed_knee", "aware_knee",
)


def policy_table(rows: list[dict[str, str]], stage: bool) -> list[dict[str, Any]]:
    latency_names = (
        ("mesh_latency", "fixed_latency", "aware_latency")
        if stage else ("mesh_bs_latency", "fixed_bs_latency", "aware_bs_latency")
    )
    fixed_gain = "fixed_gain_vs_mesh_percent" if stage else "fixed_latency_gain_vs_mesh_percent"
    aware_gain = "aware_gain_vs_mesh_percent" if stage else "aware_latency_gain_vs_mesh_percent"
    direct_gain = "aware_gain_vs_fixed_percent" if stage else "aware_latency_gain_vs_fixed_percent"
    required = {
        "workload", "budget_mm", "requested_k", "fixed_actual_k", "aware_actual_k",
        "fixed_total_wire_mm", "aware_total_wire_mm", *latency_names, fixed_gain,
        aware_gain, direct_gain, "fixed_knee_load", "aware_knee_load",
    }
    output = []
    keys = set()
    for raw in rows:
        missing = required - set(raw)
        if missing:
            fail(f"Policy summary row lacks fields: {sorted(missing)}")
        budget = integer(raw["budget_mm"], "policy table budget")
        requested_k = integer(raw["requested_k"], "policy table K")
        key = (raw["workload"], budget, requested_k)
        if budget not in BUDGETS or requested_k not in K_VALUES or key in keys:
            fail(f"Invalid or duplicate policy-summary key: {key}")
        keys.add(key)
        output.append(
            {
                "workload": raw["workload"],
                "budget": budget,
                "requested_k": requested_k,
                "fixed_actual_k": integer(raw["fixed_actual_k"], "fixed actual_k"),
                "aware_actual_k": integer(raw["aware_actual_k"], "aware actual_k"),
                "fixed_wire": numeric(raw["fixed_total_wire_mm"], "fixed wire"),
                "aware_wire": numeric(raw["aware_total_wire_mm"], "aware wire"),
                "mesh_latency": numeric(raw[latency_names[0]], "mesh latency"),
                "fixed_latency": numeric(raw[latency_names[1]], "fixed latency"),
                "aware_latency": numeric(raw[latency_names[2]], "aware latency"),
                "fixed_gain_vs_mesh": numeric(raw[fixed_gain], "fixed gain"),
                "aware_gain_vs_mesh": numeric(raw[aware_gain], "aware gain"),
                "aware_gain_vs_fixed": numeric(raw[direct_gain], "aware-vs-fixed gain"),
                "fixed_knee": numeric(raw["fixed_knee_load"], "fixed knee", optional=True),
                "aware_knee": numeric(raw["aware_knee_load"], "aware knee", optional=True),
            }
        )
    workloads = {key[0] for key in keys}
    expected = len(workloads) * len(BUDGETS) * len(K_VALUES)
    if len(output) != expected:
        fail(f"Policy table matrix has {len(output)} rows; expected {expected}")
    return output


SELECTED_LINK_FIELDS = (
    "workload_family", "workload", "budget_mm", "requested_k", "policy",
    "actual_k", "link_1", "link_2", "link_3", "link_4", "total_wire_length_mm",
)


def selected_links_table() -> list[dict[str, Any]]:
    required = {
        "workload", "budget_mm", "requested_k", "policy", "actual_k",
        "link_1", "link_2", "link_3", "link_4", "total_wire_length_mm",
    }
    output = []
    for family, input_name in (("synthetic", "synthetic_comparison"), ("stage", "stage_comparison")):
        for raw in read_csv(input_name, required):
            budget = integer(raw["budget_mm"], "selected-links budget")
            requested_k = integer(raw["requested_k"], "selected-links K")
            actual_k = integer(raw["actual_k"], "selected-links actual_k")
            policy = raw["policy"]
            wire = numeric(raw["total_wire_length_mm"], "selected-links wire")
            if policy not in POLICIES or requested_k not in K_VALUES or budget not in BUDGETS:
                fail("Unexpected selected-links comparison dimensions")
            if not 0 <= actual_k <= requested_k or wire > budget + EPS:
                fail("Invalid actual_k or wire budget in selected-links table")
            if policy == "mesh" and (actual_k != 0 or abs(wire) > EPS):
                fail("Mesh has physical shortcut usage in selected-links table")
            output.append(
                {
                    "workload_family": family,
                    "workload": raw["workload"],
                    "budget_mm": budget,
                    "requested_k": requested_k,
                    "policy": policy,
                    "actual_k": actual_k,
                    **{f"link_{index}": raw[f"link_{index}"] for index in range(1, 5)},
                    "total_wire_length_mm": wire,
                }
            )
    if len(output) != 252 or len({tuple(row[field] for field in SELECTED_LINK_FIELDS[:5]) for row in output}) != 252:
        fail(f"Selected-links table must contain 252 unique logical rows; found {len(output)}")
    return output


PHYSICAL_OVERHEAD_FIELDS = (
    "area_4phy_mm2", "area_8phy_mm2", "area_overhead_percent", "power_4phy_w",
    "power_8phy_w", "power_overhead_percent", "phy_count_4phy", "phy_count_8phy",
    "phy_count_increase", "phy_count_increase_percent",
)


def physical_overhead_table() -> list[dict[str, Any]]:
    rows = read_csv(
        "overhead",
        {"record_type", "architecture", "chiplet_area_mm2", "chiplet_power_w", "phy_count",
         "area_overhead_percent", "power_overhead_percent", "phy_count_increase",
         "phy_count_increase_percent"},
    )
    architectures = {row["architecture"]: row for row in rows if row["record_type"] == "architecture"}
    overheads = [row for row in rows if row["record_type"] == "overhead"]
    if set(architectures) != {"4PHY", "8PHY"} or len(overheads) != 1:
        fail("Physical overhead input lacks 4PHY/8PHY/overhead records")
    arch4, arch8, overhead = architectures["4PHY"], architectures["8PHY"], overheads[0]
    return [
        {
            "area_4phy_mm2": numeric(arch4["chiplet_area_mm2"], "4PHY area"),
            "area_8phy_mm2": numeric(arch8["chiplet_area_mm2"], "8PHY area"),
            "area_overhead_percent": numeric(overhead["area_overhead_percent"], "area overhead"),
            "power_4phy_w": numeric(arch4["chiplet_power_w"], "4PHY power", optional=True),
            "power_8phy_w": numeric(arch8["chiplet_power_w"], "8PHY power", optional=True),
            "power_overhead_percent": numeric(overhead["power_overhead_percent"], "power overhead", optional=True),
            "phy_count_4phy": integer(arch4["phy_count"], "4PHY count"),
            "phy_count_8phy": integer(arch8["phy_count"], "8PHY count"),
            "phy_count_increase": integer(overhead["phy_count_increase"], "PHY increase"),
            "phy_count_increase_percent": numeric(overhead["phy_count_increase_percent"], "PHY increase percent"),
        }
    ]


MULTISEED_FIELDS = (
    "workload", "budget", "requested_k", "mean_gain_percent", "std_gain_percent",
    "min_gain_percent", "max_gain_percent", "unique_topologies", "most_common_topology",
    "topology_frequency",
)


def multiseed_table() -> list[dict[str, Any]]:
    required = {
        "workload", "budget_mm", "requested_k", "mean_latency_gain_percent",
        "std_latency_gain_percent", "min_latency_gain_percent", "max_latency_gain_percent",
        "unique_selected_topologies", "most_common_selected_topology",
        "most_common_topology_seed_fraction",
    }
    output = []
    for raw in read_csv("multiseed", required):
        workload = raw["workload"]
        budget = integer(raw["budget_mm"], "multiseed budget")
        requested_k = integer(raw["requested_k"], "multiseed K")
        if workload not in {"permutation", "hotspot"} or budget not in BUDGETS or requested_k not in K_VALUES:
            fail("Unexpected multiseed dimensions")
        output.append(
            {
                "workload": workload,
                "budget": budget,
                "requested_k": requested_k,
                "mean_gain_percent": numeric(raw["mean_latency_gain_percent"], "mean gain"),
                "std_gain_percent": numeric(raw["std_latency_gain_percent"], "gain std"),
                "min_gain_percent": numeric(raw["min_latency_gain_percent"], "min gain"),
                "max_gain_percent": numeric(raw["max_latency_gain_percent"], "max gain"),
                "unique_topologies": integer(raw["unique_selected_topologies"], "unique topologies"),
                "most_common_topology": raw["most_common_selected_topology"],
                "topology_frequency": numeric(raw["most_common_topology_seed_fraction"], "topology frequency"),
            }
        )
    if len(output) != 2 * len(BUDGETS) * len(K_VALUES):
        fail(f"Multiseed final table must have 24 rows; found {len(output)}")
    return output


def validate_physical_cost_input() -> None:
    rows = read_csv(
        "physical_cost",
        {"workload_family", "workload", "budget_mm", "policy", "requested_k",
         "actual_k", "total_wire_length_mm", "used_spare_phy_endpoints"},
    )
    if len(rows) != 252 or {row["workload_family"] for row in rows} != {"synthetic", "stage"}:
        fail("Physical-cost input is incomplete or mixes workload families")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for path in INPUTS.values():
        if not path.is_file():
            fail(f"Missing required CSV input: {path}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    validate_physical_cost_input()
    synthetic_raw = read_csv("synthetic_summary", {"workload", "budget_mm", "requested_k"})
    stage_raw = read_csv("stage_summary", {"workload", "budget_mm", "requested_k"})
    tables = [
        write_table("synthetic_policy_summary.csv", POLICY_TABLE_FIELDS, policy_table(synthetic_raw, False)),
        write_table("stage_policy_summary_final.csv", POLICY_TABLE_FIELDS, policy_table(stage_raw, True)),
        write_table("selected_links_summary.csv", SELECTED_LINK_FIELDS, selected_links_table()),
        write_table("physical_overhead_summary.csv", PHYSICAL_OVERHEAD_FIELDS, physical_overhead_table()),
        write_table("multiseed_robustness_final.csv", MULTISEED_FIELDS, multiseed_table()),
    ]
    print("FINAL PROJECT TABLES COMPLETE")
    print(f"Tables generated: {len(tables)}")
    for path in tables:
        print(f"  {relative(path)}")


if __name__ == "__main__":
    try:
        main()
    except (TableError, OSError, KeyError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit(f"Final table generation failed: {exc}") from exc
