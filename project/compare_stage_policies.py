#!/usr/bin/env python3
"""Compare STAGE mesh, fixed, and workload-aware K=1/K=2/K=4 policies."""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import create_stage_mesh_baselines as mesh_designs
import run_stage_mesh_booksim as mesh_runner
import run_stage_fixed_booksim as fixed_runner
import run_stage_workload_aware_booksim as aware_runner


BUDGETS = (5, 15, 25, 45)
REQUESTED_K_VALUES = (1, 2, 4)
POLICIES = ("mesh", "fixed", "workload_aware")
EPS = 1e-9
COMPARISON_OUTPUT = ROOT / "results" / "stage_policy_comparison.csv"
SUMMARY_OUTPUT = ROOT / "results" / "stage_policy_summary.csv"
METRIC_FIELDS = (
    "analytical_latency",
    "analytical_throughput",
    "booksim_low_load",
    "booksim_low_latency",
    "booksim_low_hops",
    "booksim_low_accepted_rate",
    "last_stable_load",
    "last_stable_latency",
    "last_stable_accepted_rate",
    "knee_load",
    "knee_latency",
)
COMPARISON_FIELDS = (
    "workload",
    "budget_mm",
    "policy",
    "requested_k",
    "actual_k",
    "link_1",
    "link_2",
    "link_3",
    "link_4",
    "total_wire_length_mm",
    *METRIC_FIELDS,
    "latency_gain_vs_mesh_percent",
    "hop_gain_vs_mesh_percent",
    "analytical_latency_gain_vs_mesh_percent",
    "aware_latency_gain_vs_fixed_percent",
    "aware_hop_gain_vs_fixed_percent",
    "source_result_file",
    "reused_result",
)
SUMMARY_FIELDS = (
    "workload",
    "budget_mm",
    "requested_k",
    "mesh_latency",
    "mesh_hops",
    "mesh_knee_load",
    "fixed_actual_k",
    "fixed_total_wire_mm",
    "fixed_latency",
    "fixed_hops",
    "fixed_knee_load",
    "fixed_gain_vs_mesh_percent",
    "aware_actual_k",
    "aware_total_wire_mm",
    "aware_latency",
    "aware_hops",
    "aware_knee_load",
    "aware_gain_vs_mesh_percent",
    "aware_gain_vs_fixed_percent",
    "aware_hop_gain_vs_fixed_percent",
)


class ComparisonError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ComparisonError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def finite_number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{description} must be numeric; found {value!r}")
    converted = float(value)
    if not math.isfinite(converted):
        fail(f"{description} must be finite")
    return converted


def average_metric(point: Any, section: str, path: Path) -> float | None:
    if not isinstance(point, dict):
        return None
    nested = point.get(section)
    if not isinstance(nested, dict) or "avg" not in nested:
        return None
    return finite_number(nested["avg"], f"{section}/avg in {path}")


def parse_result(path: Path) -> dict[str, float | None]:
    if not path.is_file():
        fail(f"Missing result JSON: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot parse result {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"Result must be a JSON object: {path}")
    for section in ("latency", "throughput", "booksim_simulation"):
        if not isinstance(data.get(section), dict):
            fail(f"Result {path} is missing valid {section}")

    analytical_latency = finite_number(
        data["latency"].get("avg"), f"latency/avg in {path}"
    )
    analytical_throughput = finite_number(
        data["throughput"].get("aggregate_throughput"),
        f"throughput/aggregate_throughput in {path}",
    )
    points: list[tuple[float, float, float, float]] = []
    for raw_load, point in data["booksim_simulation"].items():
        try:
            load = float(raw_load)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(load):
            fail(f"Non-finite offered load in {path}: {raw_load!r}")
        latency = average_metric(point, "packet_latency", path)
        hops = average_metric(point, "hops", path)
        accepted = average_metric(point, "accepted_packet_rate", path)
        # BookSim may retain an empty marker for a failed high-load probe.  It
        # is numeric but is not a tested metric point and cannot define a knee.
        if latency is None or hops is None or accepted is None:
            continue
        points.append((load, latency, hops, accepted))
    if not points:
        fail(f"Result contains no complete numeric offered-load points: {path}")
    points.sort(key=lambda point: point[0])
    if len({point[0] for point in points}) != len(points):
        fail(f"Duplicate numeric offered-load values in {path}")
    low = points[0]
    knee_index = next(
        (index for index, point in enumerate(points) if point[1] >= 2.0 * low[1]),
        None,
    )
    if knee_index == 0:
        fail(f"Low-load point cannot be its own latency knee in {path}")
    stable = points[-1] if knee_index is None else points[knee_index - 1]
    knee = None if knee_index is None else points[knee_index]
    return {
        "analytical_latency": analytical_latency,
        "analytical_throughput": analytical_throughput,
        "booksim_low_load": low[0],
        "booksim_low_latency": low[1],
        "booksim_low_hops": low[2],
        "booksim_low_accepted_rate": low[3],
        "last_stable_load": stable[0],
        "last_stable_latency": stable[1],
        "last_stable_accepted_rate": stable[3],
        "knee_load": None if knee is None else knee[0],
        "knee_latency": None if knee is None else knee[1],
    }


def improvement(baseline: float, candidate: float, description: str) -> float:
    if abs(baseline) <= EPS:
        fail(f"Cannot normalize {description} against zero")
    return 100.0 * (baseline - candidate) / baseline


def link_fields(selected: list[dict[str, Any]], actual_k: int) -> dict[str, str]:
    ordered = sorted(selected, key=lambda row: int(row["step"]))
    if len(ordered) != actual_k:
        fail(f"Selected-link count {len(ordered)} differs from actual_k={actual_k}")
    links = [f"{int(row['u'])}<->{int(row['v'])}" for row in ordered]
    if len(set(links)) != len(links):
        fail("Duplicate selected shortcut name")
    links.extend(["none"] * (4 - len(links)))
    return {f"link_{index}": links[index - 1] for index in range(1, 5)}


def validate_inputs(
    workloads: tuple[str, ...],
    fixed_experiments: list[dict[str, Any]],
    aware_experiments: list[dict[str, Any]],
) -> None:
    expected_keys = {
        (workload, requested_k, budget)
        for workload in workloads
        for requested_k in REQUESTED_K_VALUES
        for budget in BUDGETS
    }
    fixed_keys = {
        (row["workload"], row["requested_k"], row["budget"])
        for row in fixed_experiments
    }
    aware_keys = {
        (row["workload"], row["requested_k"], row["budget"])
        for row in aware_experiments
    }
    if fixed_keys != expected_keys or aware_keys != expected_keys:
        fail("Fixed/aware workload-budget-K input matrix is incomplete")

    mesh_experiments = mesh_runner.build_experiments()
    if {row["workload"] for row in mesh_experiments} != set(workloads):
        fail("Mesh workload set differs from fixed/aware workload set")
    mesh_runner.validate_designs(mesh_experiments)
    fixed_runner.validate_designs(fixed_experiments)
    aware_runner.validate_designs(aware_experiments)

    for requested_k in REQUESTED_K_VALUES:
        for budget in BUDGETS:
            signatures = {
                row["signature"]
                for row in fixed_experiments
                if row["requested_k"] == requested_k and row["budget"] == budget
            }
            if len(signatures) != 1:
                fail(f"Fixed topology is workload-dependent for K={requested_k} B{budget}")
    for row in (*fixed_experiments, *aware_experiments):
        if not 0 <= int(row["actual_k"]) <= int(row["requested_k"]):
            fail(f"actual_k exceeds requested_k in {row}")
        if float(row["total_wire"]) > int(row["budget"]) + EPS:
            fail(f"Wire length exceeds budget in {row}")


def build_rows() -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    workloads = mesh_designs.discover_workloads()
    fixed_experiments = fixed_runner.build_experiments()
    aware_experiments = aware_runner.build_experiments()
    if {row["workload"] for row in fixed_experiments} != set(workloads):
        fail("Fixed STAGE workloads differ from dynamically discovered workloads")
    if {row["workload"] for row in aware_experiments} != set(workloads):
        fail("Workload-aware STAGE workloads differ from dynamically discovered workloads")
    validate_inputs(workloads, fixed_experiments, aware_experiments)

    fixed = {
        (row["workload"], row["requested_k"], row["budget"]): row
        for row in fixed_experiments
    }
    aware = {
        (row["workload"], row["requested_k"], row["budget"]): row
        for row in aware_experiments
    }
    metric_cache: dict[Path, dict[str, float | None]] = {}

    def metrics(path: Path) -> dict[str, float | None]:
        if path not in metric_cache:
            metric_cache[path] = parse_result(path)
        return metric_cache[path]

    rows: list[dict[str, Any]] = []
    for workload in workloads:
        mesh_path = ROOT / "results" / f"stage_mesh_{workload}.json"
        mesh_metrics = metrics(mesh_path)
        for budget in BUDGETS:
            for requested_k in REQUESTED_K_VALUES:
                key = (workload, requested_k, budget)
                physical = {
                    "fixed": fixed[key],
                    "workload_aware": aware[key],
                }
                policy_rows: dict[str, dict[str, Any]] = {}
                for policy in POLICIES:
                    if policy == "mesh":
                        result_path = mesh_path
                        policy_metrics = mesh_metrics
                        actual_k = 0
                        wire = 0.0
                        links = {f"link_{index}": "none" for index in range(1, 5)}
                        reused = False
                    else:
                        experiment = physical[policy]
                        result_path = experiment["result"]
                        policy_metrics = metrics(result_path)
                        actual_k = int(experiment["actual_k"])
                        wire = float(experiment["total_wire"])
                        links = link_fields(experiment["selected"], actual_k)
                        reused = bool(experiment["duplicate"])
                    row = {
                        "workload": workload,
                        "budget_mm": budget,
                        "policy": policy,
                        "requested_k": requested_k,
                        "actual_k": actual_k,
                        **links,
                        "total_wire_length_mm": wire,
                        **policy_metrics,
                        "latency_gain_vs_mesh_percent": 0.0,
                        "hop_gain_vs_mesh_percent": 0.0,
                        "analytical_latency_gain_vs_mesh_percent": 0.0,
                        "aware_latency_gain_vs_fixed_percent": None,
                        "aware_hop_gain_vs_fixed_percent": None,
                        "source_result_file": relative(result_path),
                        "reused_result": reused,
                    }
                    if policy != "mesh":
                        row["latency_gain_vs_mesh_percent"] = improvement(
                            mesh_metrics["booksim_low_latency"],
                            policy_metrics["booksim_low_latency"],
                            f"{workload} B{budget} K{requested_k} {policy} latency vs mesh",
                        )
                        row["hop_gain_vs_mesh_percent"] = improvement(
                            mesh_metrics["booksim_low_hops"],
                            policy_metrics["booksim_low_hops"],
                            f"{workload} B{budget} K{requested_k} {policy} hops vs mesh",
                        )
                        row["analytical_latency_gain_vs_mesh_percent"] = improvement(
                            mesh_metrics["analytical_latency"],
                            policy_metrics["analytical_latency"],
                            f"{workload} B{budget} K{requested_k} analytical latency vs mesh",
                        )
                    policy_rows[policy] = row

                fixed_row = policy_rows["fixed"]
                aware_row = policy_rows["workload_aware"]
                aware_row["aware_latency_gain_vs_fixed_percent"] = improvement(
                    fixed_row["booksim_low_latency"],
                    aware_row["booksim_low_latency"],
                    f"{workload} B{budget} K{requested_k} aware latency vs fixed",
                )
                aware_row["aware_hop_gain_vs_fixed_percent"] = improvement(
                    fixed_row["booksim_low_hops"],
                    aware_row["booksim_low_hops"],
                    f"{workload} B{budget} K{requested_k} aware hops vs fixed",
                )
                rows.extend(policy_rows[policy] for policy in POLICIES)
    validate_rows(rows, workloads, fixed_experiments)
    return workloads, rows


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=EPS, abs_tol=EPS)


def validate_rows(
    rows: list[dict[str, Any]],
    workloads: tuple[str, ...],
    fixed_experiments: list[dict[str, Any]],
) -> None:
    expected_count = len(workloads) * len(BUDGETS) * len(REQUESTED_K_VALUES) * len(POLICIES)
    if len(rows) != expected_count:
        fail(f"Expected {expected_count} comparison rows; found {len(rows)}")
    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["workload"], row["budget_mm"], row["requested_k"])].append(row)
    expected = {
        (workload, budget, requested_k)
        for workload in workloads
        for budget in BUDGETS
        for requested_k in REQUESTED_K_VALUES
    }
    if set(grouped) != expected:
        fail("Comparison workload-budget-K matrix is incomplete")

    for key, group in grouped.items():
        indexed = {row["policy"]: row for row in group}
        if len(group) != 3 or set(indexed) != set(POLICIES):
            fail(f"Comparison group {key} lacks exactly one row per policy")
        mesh, fixed, aware = (indexed[policy] for policy in POLICIES)
        if mesh["actual_k"] != 0 or mesh["total_wire_length_mm"] != 0.0:
            fail(f"Mesh row has shortcuts/wire for {key}")
        if any(mesh[f"link_{index}"] != "none" for index in range(1, 5)):
            fail(f"Mesh row has selected links for {key}")
        for policy_row in (fixed, aware):
            if not 0 <= policy_row["actual_k"] <= policy_row["requested_k"]:
                fail(f"Invalid actual_k in {policy_row}")
            if policy_row["total_wire_length_mm"] > key[1] + EPS:
                fail(f"Wire budget exceeded in {policy_row}")
            checks = (
                ("latency_gain_vs_mesh_percent", "booksim_low_latency"),
                ("hop_gain_vs_mesh_percent", "booksim_low_hops"),
                ("analytical_latency_gain_vs_mesh_percent", "analytical_latency"),
            )
            for output_field, metric_field in checks:
                expected_gain = improvement(mesh[metric_field], policy_row[metric_field], key)
                if not close(policy_row[output_field], expected_gain):
                    fail(f"Wrong same-workload mesh normalization in {output_field} for {key}")
        expected_latency = improvement(fixed["booksim_low_latency"], aware["booksim_low_latency"], key)
        expected_hops = improvement(fixed["booksim_low_hops"], aware["booksim_low_hops"], key)
        if not close(aware["aware_latency_gain_vs_fixed_percent"], expected_latency):
            fail(f"Wrong aware-vs-fixed latency comparison for {key}")
        if not close(aware["aware_hop_gain_vs_fixed_percent"], expected_hops):
            fail(f"Wrong aware-vs-fixed hop comparison for {key}")

    # Recheck fixed workload independence from the physical endpoint signature,
    # not merely from the printed chiplet-pair link labels.
    for requested_k in REQUESTED_K_VALUES:
        for budget in BUDGETS:
            signatures = {
                row["signature"]
                for row in fixed_experiments
                if row["requested_k"] == requested_k and row["budget"] == budget
            }
            if len(signatures) != 1:
                fail(f"Fixed endpoint topology changed across workloads for K={requested_k} B{budget}")


def build_summary(rows: list[dict[str, Any]], workloads: tuple[str, ...]) -> list[dict[str, Any]]:
    indexed = {
        (row["workload"], row["budget_mm"], row["requested_k"], row["policy"]): row
        for row in rows
    }
    summary = []
    for workload in workloads:
        for budget in BUDGETS:
            for requested_k in REQUESTED_K_VALUES:
                mesh = indexed[(workload, budget, requested_k, "mesh")]
                fixed = indexed[(workload, budget, requested_k, "fixed")]
                aware = indexed[(workload, budget, requested_k, "workload_aware")]
                summary.append(
                    {
                        "workload": workload,
                        "budget_mm": budget,
                        "requested_k": requested_k,
                        "mesh_latency": mesh["booksim_low_latency"],
                        "mesh_hops": mesh["booksim_low_hops"],
                        "mesh_knee_load": mesh["knee_load"],
                        "fixed_actual_k": fixed["actual_k"],
                        "fixed_total_wire_mm": fixed["total_wire_length_mm"],
                        "fixed_latency": fixed["booksim_low_latency"],
                        "fixed_hops": fixed["booksim_low_hops"],
                        "fixed_knee_load": fixed["knee_load"],
                        "fixed_gain_vs_mesh_percent": fixed["latency_gain_vs_mesh_percent"],
                        "aware_actual_k": aware["actual_k"],
                        "aware_total_wire_mm": aware["total_wire_length_mm"],
                        "aware_latency": aware["booksim_low_latency"],
                        "aware_hops": aware["booksim_low_hops"],
                        "aware_knee_load": aware["knee_load"],
                        "aware_gain_vs_mesh_percent": aware["latency_gain_vs_mesh_percent"],
                        "aware_gain_vs_fixed_percent": aware["aware_latency_gain_vs_fixed_percent"],
                        "aware_hop_gain_vs_fixed_percent": aware["aware_hop_gain_vs_fixed_percent"],
                    }
                )
    expected = len(workloads) * len(BUDGETS) * len(REQUESTED_K_VALUES)
    if len(summary) != expected or len({(r["workload"], r["budget_mm"], r["requested_k"]) for r in summary}) != expected:
        fail(f"Expected {expected} unique summary rows; found {len(summary)}")
    return summary


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as exc:
        fail(f"Cannot write {path}: {exc}")


def shown(value: float | None, decimals: int = 3) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


def selected_links(row: dict[str, Any]) -> str:
    links = [row[f"link_{index}"] for index in range(1, 5) if row[f"link_{index}"] != "none"]
    return "none" if not links else ",".join(links)


def print_tables(rows: list[dict[str, Any]], workloads: tuple[str, ...]) -> None:
    indexed = {
        (row["workload"], row["budget_mm"], row["requested_k"], row["policy"]): row
        for row in rows
    }
    for workload in workloads:
        print(f"\nSTAGE POLICY COMPARISON - {workload}")
        print("BUDGET | K | POLICY | ACTUAL_K | WIRE | BS_LAT | GAIN_vs_MESH | HOPS | KNEE")
        for budget in BUDGETS:
            for requested_k in REQUESTED_K_VALUES:
                for policy in POLICIES:
                    row = indexed[(workload, budget, requested_k, policy)]
                    print(
                        f"{budget} | {requested_k} | {policy} | {row['actual_k']} | "
                        f"{row['total_wire_length_mm']:.3f} | {row['booksim_low_latency']:.3f} | "
                        f"{row['latency_gain_vs_mesh_percent']:.3f} | "
                        f"{row['booksim_low_hops']:.3f} | {shown(row['knee_load'])}"
                    )

    print(
        "\nWORKLOAD | BUDGET | K | FIXED_LAT | AWARE_LAT | AWARE_GAIN_vs_FIXED | "
        "FIXED_K | AWARE_K | FIXED_LINKS | AWARE_LINKS"
    )
    for workload in workloads:
        for budget in BUDGETS:
            for requested_k in REQUESTED_K_VALUES:
                fixed = indexed[(workload, budget, requested_k, "fixed")]
                aware = indexed[(workload, budget, requested_k, "workload_aware")]
                print(
                    f"{workload} | {budget} | {requested_k} | "
                    f"{fixed['booksim_low_latency']:.3f} | {aware['booksim_low_latency']:.3f} | "
                    f"{aware['aware_latency_gain_vs_fixed_percent']:.3f} | "
                    f"{fixed['actual_k']} | {aware['actual_k']} | "
                    f"{selected_links(fixed)} | {selected_links(aware)}"
                )

    print(
        "\nBUDGET | K | MESH_LAT | FIXED_LAT | AWARE_LAT | FIXED_GAIN_vs_MESH | "
        "AWARE_GAIN_vs_MESH | AWARE_GAIN_vs_FIXED"
    )
    for budget in BUDGETS:
        for requested_k in REQUESTED_K_VALUES:
            groups = {
                policy: [indexed[(workload, budget, requested_k, policy)] for workload in workloads]
                for policy in POLICIES
            }
            mean = lambda values: sum(values) / len(values)
            print(
                f"{budget} | {requested_k} | "
                f"{mean([row['booksim_low_latency'] for row in groups['mesh']]):.3f} | "
                f"{mean([row['booksim_low_latency'] for row in groups['fixed']]):.3f} | "
                f"{mean([row['booksim_low_latency'] for row in groups['workload_aware']]):.3f} | "
                f"{mean([row['latency_gain_vs_mesh_percent'] for row in groups['fixed']]):.3f} | "
                f"{mean([row['latency_gain_vs_mesh_percent'] for row in groups['workload_aware']]):.3f} | "
                f"{mean([row['aware_latency_gain_vs_fixed_percent'] for row in groups['workload_aware']]):.3f}"
            )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    workloads, rows = build_rows()
    summary = build_summary(rows, workloads)
    write_csv(COMPARISON_OUTPUT, COMPARISON_FIELDS, rows)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summary)
    print_tables(rows, workloads)
    print("\nSTAGE POLICY COMPARISON COMPLETE")
    print(f"STAGE workloads: {len(workloads)} ({', '.join(workloads)})")
    print(f"Comparison rows: {len(rows)}")
    print(f"Summary rows: {len(summary)}")
    print(f"Comparison output: {relative(COMPARISON_OUTPUT)}")
    print(f"Summary output: {relative(SUMMARY_OUTPUT)}")


if __name__ == "__main__":
    try:
        main()
    except (ComparisonError, RuntimeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE policy comparison failed: {exc}") from exc
