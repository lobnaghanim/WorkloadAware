#!/usr/bin/env python3
"""Run the proven workload-aware shortcut optimizer on STAGE traffic.

For each workload/budget, the deterministic greedy trajectory is evaluated up
to K=4 once.  Its K=1 and K=2 prefixes are exactly the configurations separate
runs would select because requested K is not part of feasibility or tie
ordering.  All physical, SPLIF, and analytical metric logic is reused from the
existing K=2 implementation.
"""

from __future__ import annotations

import copy
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
import select_k2_workload_aware as proven


BUDGETS = (5.0, 15.0, 25.0, 45.0)
REQUESTED_K_VALUES = (1, 2, 4)
MAX_K = max(REQUESTED_K_VALUES)
CONVERSION_VALIDATION = ROOT / "results" / "stage_conversion_validation.csv"
SELECTION_OUTPUT = ROOT / "results" / "stage_workload_aware_selections.csv"
SUMMARY_OUTPUT = ROOT / "results" / "stage_workload_aware_summary.csv"
EPS = proven.EPS

SELECTION_FIELDS = (
    "workload",
    "budget_mm",
    "requested_k",
    "actual_k",
    "step",
    "u",
    "v",
    "phy_u",
    "phy_v",
    "link_length_mm",
    "cumulative_wire_length_mm",
    "remaining_budget_mm",
    "link_latency_cycles",
    "avg_latency_after_step",
    "latency_gain_vs_baseline_percent",
    "max_link_load_after_step",
    "baseline_max_link_load",
    "analytical_throughput_after_step",
    "total_routed_traffic_after_step",
    "traffic_wire_cost_after_step",
)

SUMMARY_FIELDS = (
    "workload",
    "budget_mm",
    "requested_k",
    "actual_k",
    "link_1",
    "link_1_phy_pair",
    "link_1_length_mm",
    "link_2",
    "link_2_phy_pair",
    "link_2_length_mm",
    "link_3",
    "link_3_phy_pair",
    "link_3_length_mm",
    "link_4",
    "link_4_phy_pair",
    "link_4_length_mm",
    "total_wire_length_mm",
    "baseline_avg_latency",
    "final_avg_latency",
    "latency_gain_vs_baseline_percent",
    "baseline_max_link_load",
    "final_max_link_load",
    "baseline_throughput",
    "analytical_throughput",
    "total_routed_traffic",
    "traffic_wire_cost",
)


class StageOptimizationError(RuntimeError):
    """Raised when a STAGE optimization input or invariant is invalid."""


def fail(message: str) -> None:
    raise StageOptimizationError(message)


def read_conversion_rows() -> list[dict[str, str]]:
    if not CONVERSION_VALIDATION.is_file():
        fail(f"Missing STAGE conversion validation: {CONVERSION_VALIDATION}")
    try:
        with CONVERSION_VALIDATION.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        fail(f"Cannot read STAGE conversion validation: {exc}")
    if not rows:
        fail("STAGE conversion validation contains no workloads")
    seen: set[str] = set()
    accepted: list[dict[str, str]] = []
    for row in rows:
        workload = str(row.get("workload", "")).strip()
        if not workload or workload in seen:
            fail(f"Missing or duplicate workload in conversion validation: {workload!r}")
        seen.add(workload)
        valid = str(row.get("conversion_valid", "")).strip().lower()
        if valid not in {"true", "1", "yes"}:
            fail(f"STAGE conversion did not pass for workload {workload}")
        accepted.append(row)
    return accepted


def make_stage_context(
    physical: dict[str, Any], workload: str, conversion_row: dict[str, str]
) -> dict[str, Any]:
    chiplet_path = ROOT / "inputs" / "traffic_by_chiplet" / f"traffic_stage_{workload}.json"
    unit_path = ROOT / "inputs" / "traffic_by_unit" / f"traffic_stage_{workload}.json"
    if not chiplet_path.is_file() or not unit_path.is_file():
        fail(f"Missing converted STAGE traffic for {workload}")
    recorded_chiplet = conversion_row.get("traffic_by_chiplet_path")
    recorded_unit = conversion_row.get("traffic_by_unit_path")
    if recorded_chiplet and recorded_chiplet != chiplet_path.relative_to(ROOT).as_posix():
        fail(f"Conversion validation points to unexpected chiplet traffic for {workload}")
    if recorded_unit and recorded_unit != unit_path.relative_to(ROOT).as_posix():
        fail(f"Conversion validation points to unexpected unit traffic for {workload}")

    chiplet_traffic = hlp.read_json(str(chiplet_path))
    unit_traffic = hlp.read_json(str(unit_path))
    if not isinstance(chiplet_traffic, dict) or not chiplet_traffic:
        fail(f"Malformed or empty chiplet traffic for {workload}")
    if not isinstance(unit_traffic, dict) or not unit_traffic:
        fail(f"Malformed or empty unit traffic for {workload}")
    for pair, value in chiplet_traffic.items():
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(chiplet, int) and 0 <= chiplet < 16 for chiplet in pair)
            or pair[0] == pair[1]
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            fail(f"Invalid converted chiplet traffic entry for {workload}: {pair!r}={value!r}")
    reaggregated = hlp.convert_by_unit_traffic_to_by_chiplet_traffic(unit_traffic)
    if set(reaggregated) != set(chiplet_traffic):
        fail(f"Unit/chiplet traffic pair sets differ for {workload}")
    for pair, value in chiplet_traffic.items():
        if not math.isclose(
            float(reaggregated[pair]), float(value), rel_tol=1e-11, abs_tol=1e-11
        ):
            fail(f"Unit traffic does not reaggregate for {workload} pair {pair}")

    design = copy.deepcopy(physical["design"])
    design["design_name"] = f"project_stage_workload_aware_{workload}"
    design["traffic_by_chiplet"] = chiplet_path.relative_to(ROOT).as_posix()
    design["traffic_by_unit"] = unit_path.relative_to(ROOT).as_posix()
    return {
        "design": design,
        "chiplets": physical["chiplets"],
        "placement": physical["placement"],
        "topology": physical["topology"],
        "packaging": physical["packaging"],
        "technologies": physical["technologies"],
        "traffic": chiplet_traffic,
    }


def baseline_for_workload(
    physical: dict[str, Any], context: dict[str, Any]
) -> dict[str, float]:
    metrics = proven.evaluate(physical["topology"], context)
    baseline = {
        "avg_latency": float(metrics["avg_latency"]),
        "throughput": float(metrics["throughput"]),
        "max_link_load": float(metrics["max_load"]),
        "total_routed_traffic": float(metrics["total_routed_traffic"]),
        "traffic_wire_cost": float(metrics["traffic_wire_cost"]),
    }
    if baseline["avg_latency"] <= 0 or baseline["max_link_load"] <= 0:
        fail(f"Invalid STAGE K=0 baseline metrics: {baseline}")
    if any(not math.isfinite(value) for value in baseline.values()):
        fail(f"Non-finite STAGE K=0 baseline metrics: {baseline}")
    return baseline


def selection_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        candidate["avg_latency"],
        candidate["max_link_load"],
        candidate["length_mm"],
        candidate["u"],
        candidate["v"],
        candidate["phy_u"],
        candidate["phy_v"],
    )


def candidate_signature(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    return tuple(
        int(candidate[field]) for field in ("u", "v", "phy_u", "phy_v")
    )


def greedy_trajectory(
    workload: str,
    budget: float,
    physical: dict[str, Any],
    context: dict[str, Any],
    baseline: dict[str, float],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], int]:
    current_topology = copy.deepcopy(physical["topology"])
    topology_snapshots = [copy.deepcopy(current_topology)]
    occupied = {
        chiplet_id: set(phys)
        for chiplet_id, phys in physical["baseline_occupied"].items()
    }
    selected_pairs: set[tuple[int, int]] = set()
    selected: list[dict[str, Any]] = []
    cumulative_wire = 0.0
    routing_regenerations = 0

    for step in range(1, MAX_K + 1):
        remaining_budget = budget - cumulative_wire
        feasible, considered_pairs = proven.evaluate_iteration(
            current_topology,
            occupied,
            selected_pairs,
            remaining_budget,
            baseline["max_link_load"],
            physical,
            context,
        )
        if step == 1 and considered_pairs != proven.EXPECTED_INITIAL_CANDIDATES:
            fail(
                f"{workload} B{budget:g}: considered {considered_pairs} initial pairs, expected 96"
            )
        if not feasible:
            break
        best = min(feasible, key=selection_key)
        # Verify enumeration order cannot influence the deterministic result.
        reverse_best = min(reversed(feasible), key=selection_key)
        if candidate_signature(best) != candidate_signature(reverse_best):
            fail(f"{workload} B{budget:g} step {step}: nondeterministic tie resolution")

        pair = (best["u"], best["v"])
        endpoints = (
            (best["u"], best["phy_u"]),
            (best["v"], best["phy_v"]),
        )
        if pair in physical["mesh_edges"] or pair in selected_pairs:
            fail(f"{workload} B{budget:g}: attempted mesh/duplicate shortcut {pair}")
        if any(phy in occupied[chiplet] for chiplet, phy in endpoints):
            fail(f"{workload} B{budget:g}: attempted PHY endpoint reuse for {pair}")

        current_topology.append(copy.deepcopy(best["link"]))
        selected_pairs.add(pair)
        for chiplet, phy in endpoints:
            occupied[chiplet].add(phy)
        cumulative_wire += best["length_mm"]
        remaining_budget = budget - cumulative_wire
        if cumulative_wire > budget + EPS or remaining_budget < -EPS:
            fail(f"{workload} B{budget:g}: shared total wire budget exceeded")
        if best["max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"{workload} B{budget:g}: STAGE K=0 load cap exceeded")

        routing = proven.generate_splif_routing(
            physical["chiplets"], physical["placement"], current_topology
        )
        if not isinstance(routing, dict) or not isinstance(routing.get("table"), dict):
            fail(f"{workload} B{budget:g}: SPLIF regeneration failed at step {step}")
        routing_regenerations += 1
        best = copy.deepcopy(best)
        best["step"] = step
        best["cumulative_wire_length_mm"] = cumulative_wire
        best["remaining_budget_mm"] = max(0.0, remaining_budget)
        best["endpoints"] = endpoints
        selected.append(best)
        topology_snapshots.append(copy.deepcopy(current_topology))

    if routing_regenerations != len(selected):
        fail(f"{workload} B{budget:g}: routing was not regenerated after every change")
    if len(current_topology) != proven.EXPECTED_MESH_LINKS + len(selected):
        fail(f"{workload} B{budget:g}: final topology is not 24 + actual_k links")
    pairs = [(link["u"], link["v"]) for link in selected]
    endpoints = [endpoint for link in selected for endpoint in link["endpoints"]]
    if len(pairs) != len(set(pairs)) or len(endpoints) != len(set(endpoints)):
        fail(f"{workload} B{budget:g}: duplicate pair or reused PHY endpoint")
    return selected, topology_snapshots, routing_regenerations


def gain_percent(baseline_latency: float, final_latency: float) -> float:
    return 100.0 * (baseline_latency - final_latency) / baseline_latency


def rows_for_configuration(
    workload: str,
    budget: float,
    requested_k: int,
    trajectory: list[dict[str, Any]],
    topology_snapshots: list[list[dict[str, Any]]],
    baseline: dict[str, float],
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actual_k = min(requested_k, len(trajectory))
    selected = trajectory[:actual_k]
    final_metrics = proven.evaluate(topology_snapshots[actual_k], context)
    if final_metrics["max_load"] > baseline["max_link_load"] + EPS:
        fail(f"{workload} B{budget:g} K={requested_k}: final load exceeds STAGE baseline cap")
    if len(topology_snapshots[actual_k]) != proven.EXPECTED_MESH_LINKS + actual_k:
        fail(f"{workload} B{budget:g} K={requested_k}: invalid final topology size")
    if selected:
        last = selected[-1]
        comparisons = (
            (last["avg_latency"], final_metrics["avg_latency"], "average latency"),
            (last["max_link_load"], final_metrics["max_load"], "maximum load"),
            (last["throughput"], final_metrics["throughput"], "throughput"),
            (
                last["total_routed_traffic"],
                final_metrics["total_routed_traffic"],
                "total routed traffic",
            ),
            (last["traffic_wire_cost"], final_metrics["traffic_wire_cost"], "traffic-wire cost"),
        )
        for stored, recomputed, metric in comparisons:
            if not proven.metrics_close(stored, recomputed):
                fail(
                    f"{workload} B{budget:g} K={requested_k}: recomputed {metric} mismatch"
                )

    step_rows: list[dict[str, Any]] = []
    for link in selected:
        step_rows.append(
            {
                "workload": workload,
                "budget_mm": budget,
                "requested_k": requested_k,
                "actual_k": actual_k,
                "step": link["step"],
                "u": link["u"],
                "v": link["v"],
                "phy_u": link["phy_u"],
                "phy_v": link["phy_v"],
                "link_length_mm": link["length_mm"],
                "cumulative_wire_length_mm": link["cumulative_wire_length_mm"],
                "remaining_budget_mm": link["remaining_budget_mm"],
                "link_latency_cycles": link["link_latency_cycles"],
                "avg_latency_after_step": link["avg_latency"],
                "latency_gain_vs_baseline_percent": gain_percent(
                    baseline["avg_latency"], link["avg_latency"]
                ),
                "max_link_load_after_step": link["max_link_load"],
                "baseline_max_link_load": baseline["max_link_load"],
                "analytical_throughput_after_step": link["throughput"],
                "total_routed_traffic_after_step": link["total_routed_traffic"],
                "traffic_wire_cost_after_step": link["traffic_wire_cost"],
            }
        )

    total_wire = selected[-1]["cumulative_wire_length_mm"] if selected else 0.0
    summary: dict[str, Any] = {
        "workload": workload,
        "budget_mm": budget,
        "requested_k": requested_k,
        "actual_k": actual_k,
        "total_wire_length_mm": total_wire,
        "baseline_avg_latency": baseline["avg_latency"],
        "final_avg_latency": final_metrics["avg_latency"],
        "latency_gain_vs_baseline_percent": gain_percent(
            baseline["avg_latency"], final_metrics["avg_latency"]
        ),
        "baseline_max_link_load": baseline["max_link_load"],
        "final_max_link_load": final_metrics["max_load"],
        "baseline_throughput": baseline["throughput"],
        "analytical_throughput": final_metrics["throughput"],
        "total_routed_traffic": final_metrics["total_routed_traffic"],
        "traffic_wire_cost": final_metrics["traffic_wire_cost"],
    }
    for index in range(1, MAX_K + 1):
        if index <= actual_k:
            link = selected[index - 1]
            summary[f"link_{index}"] = f"{link['u']}<->{link['v']}"
            summary[f"link_{index}_phy_pair"] = f"{link['phy_u']}<->{link['phy_v']}"
            summary[f"link_{index}_length_mm"] = link["length_mm"]
        else:
            summary[f"link_{index}"] = "none"
            summary[f"link_{index}_phy_pair"] = ""
            summary[f"link_{index}_length_mm"] = ""
    return step_rows, summary


def print_configuration(summary: dict[str, Any]) -> None:
    selected_links = [
        summary[f"link_{index}"]
        for index in range(1, int(summary["actual_k"]) + 1)
    ]
    print(f"Budget: {float(summary['budget_mm']):g} mm")
    print(f"Requested K: {summary['requested_k']}")
    print(f"Actual K: {summary['actual_k']}")
    print(f"Selected links: {', '.join(selected_links) if selected_links else 'none'}")
    print(f"Total wire: {float(summary['total_wire_length_mm']):.6f} mm")
    print(f"Baseline latency: {float(summary['baseline_avg_latency']):.6f}")
    print(f"Final latency: {float(summary['final_avg_latency']):.6f}")
    print(
        "Latency improvement: "
        f"{float(summary['latency_gain_vs_baseline_percent']):.6f}%"
    )
    print(f"Baseline max load: {float(summary['baseline_max_link_load']):.6f}")
    print(f"Final max load: {float(summary['final_max_link_load']):.6f}")
    print(f"Analytical throughput: {float(summary['analytical_throughput']):.6f}")
    print()


def validate_all_outputs(
    workloads: list[str],
    step_rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    physical: dict[str, Any],
) -> None:
    expected = {
        (workload, budget, requested_k)
        for workload in workloads
        for budget in BUDGETS
        for requested_k in REQUESTED_K_VALUES
    }
    if len(summaries) != len(expected):
        fail(f"Expected {len(expected)} summary rows; found {len(summaries)}")
    summary_by_key: dict[tuple[str, float, int], dict[str, Any]] = {}
    steps_by_key: dict[tuple[str, float, int], list[dict[str, Any]]] = defaultdict(list)
    for summary in summaries:
        key = (
            str(summary["workload"]),
            float(summary["budget_mm"]),
            int(summary["requested_k"]),
        )
        if key in summary_by_key:
            fail(f"Duplicate summary row for {key}")
        summary_by_key[key] = summary
        actual_k = int(summary["actual_k"])
        requested_k = int(summary["requested_k"])
        if not 0 <= actual_k <= requested_k:
            fail(f"Invalid actual_k/requested_k for {key}")
        if float(summary["total_wire_length_mm"]) > float(summary["budget_mm"]) + EPS:
            fail(f"Total wire budget exceeded for {key}")
        if float(summary["final_max_link_load"]) > float(summary["baseline_max_link_load"]) + EPS:
            fail(f"STAGE baseline load cap exceeded for {key}")
    if set(summary_by_key) != expected:
        fail("Summary workload/budget/K matrix is incomplete")

    for row in step_rows:
        key = (str(row["workload"]), float(row["budget_mm"]), int(row["requested_k"]))
        steps_by_key[key].append(row)
        if tuple(sorted((int(row["u"]), int(row["v"])))) in physical["mesh_edges"]:
            fail(f"Mesh edge selected for {key}")
        if float(row["cumulative_wire_length_mm"]) > float(row["budget_mm"]) + EPS:
            fail(f"Cumulative wire budget exceeded for {key}")
        if float(row["max_link_load_after_step"]) > float(row["baseline_max_link_load"]) + EPS:
            fail(f"Per-step STAGE load cap exceeded for {key}")

    for key, summary in summary_by_key.items():
        rows = sorted(steps_by_key.get(key, []), key=lambda row: int(row["step"]))
        actual_k = int(summary["actual_k"])
        if len(rows) != actual_k:
            fail(f"Selection-row count differs from actual_k for {key}")
        if [int(row["step"]) for row in rows] != list(range(1, actual_k + 1)):
            fail(f"Non-contiguous greedy steps for {key}")
        if any(int(row["actual_k"]) != actual_k for row in rows):
            fail(f"Selection rows contain inconsistent actual_k for {key}")
        pairs = [(int(row["u"]), int(row["v"])) for row in rows]
        endpoints = [
            endpoint
            for row in rows
            for endpoint in (
                (int(row["u"]), int(row["phy_u"])),
                (int(row["v"]), int(row["phy_v"])),
            )
        ]
        if len(pairs) != len(set(pairs)):
            fail(f"Duplicate shortcut in {key}")
        if len(endpoints) != len(set(endpoints)):
            fail(f"PHY endpoint reuse in {key}")
        if rows and not proven.metrics_close(
            float(rows[-1]["cumulative_wire_length_mm"]),
            float(summary["total_wire_length_mm"]),
        ):
            fail(f"Final cumulative wire mismatch for {key}")

    # Verify every K=1/K=2 result is the exact prefix of the K=4 trajectory.
    for workload in workloads:
        for budget in BUDGETS:
            groups = {
                requested_k: sorted(
                    steps_by_key.get((workload, budget, requested_k), []),
                    key=lambda row: int(row["step"]),
                )
                for requested_k in REQUESTED_K_VALUES
            }
            longest = groups[4]
            for requested_k in (1, 2):
                current = groups[requested_k]
                if len(current) > len(longest):
                    fail(f"Greedy prefix length mismatch for {workload} B{budget:g}")
                for index, row in enumerate(current):
                    left = tuple(int(row[field]) for field in ("u", "v", "phy_u", "phy_v"))
                    right = tuple(
                        int(longest[index][field])
                        for field in ("u", "v", "phy_u", "phy_v")
                    )
                    if left != right:
                        fail(
                            f"K={requested_k}/K=4 greedy-prefix inconsistency for "
                            f"{workload} B{budget:g} step {index + 1}"
                        )


def write_output(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row[field] for field in fields} for row in rows)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        conversion_rows = read_conversion_rows()
        workloads = [str(row["workload"]).strip() for row in conversion_rows]
        physical = proven.load_physical_context()
        if physical["node_count"] != 16 or len(physical["topology"]) != 24:
            fail("Baseline physical topology is not the required 16-chiplet, 24-link mesh")
        if physical["initial_candidate_count"] != 96:
            fail("Initial non-mesh candidate count is not 96")
        print("Validated physical baseline: 16 chiplets, 24 mesh links, 96 non-mesh pairs")
        print()

        all_steps: list[dict[str, Any]] = []
        all_summaries: list[dict[str, Any]] = []
        for conversion_row in conversion_rows:
            workload = str(conversion_row["workload"]).strip()
            print("=" * 88)
            print(f"STAGE WORKLOAD-AWARE OPTIMIZATION — {workload.upper()}")
            print("=" * 88)
            context = make_stage_context(physical, workload, conversion_row)
            baseline = baseline_for_workload(physical, context)
            print(
                "K=0 baseline: "
                f"latency={baseline['avg_latency']:.6f}, "
                f"throughput={baseline['throughput']:.6f}, "
                f"max_load={baseline['max_link_load']:.6f}, "
                f"routed_traffic={baseline['total_routed_traffic']:.6f}, "
                f"traffic_wire_cost={baseline['traffic_wire_cost']:.6f}"
            )
            print()
            for budget in BUDGETS:
                print(
                    f"Evaluating shared greedy trajectory for {workload}, "
                    f"budget {budget:g} mm (up to K=4)...",
                    flush=True,
                )
                trajectory, snapshots, routing_regenerations = greedy_trajectory(
                    workload, budget, physical, context, baseline
                )
                if routing_regenerations != len(trajectory):
                    fail(f"Routing-regeneration audit failed for {workload} B{budget:g}")
                for requested_k in REQUESTED_K_VALUES:
                    steps, summary = rows_for_configuration(
                        workload,
                        budget,
                        requested_k,
                        trajectory,
                        snapshots,
                        baseline,
                        context,
                    )
                    all_steps.extend(steps)
                    all_summaries.append(summary)
                    print_configuration(summary)

        validate_all_outputs(workloads, all_steps, all_summaries, physical)
        write_output(SELECTION_OUTPUT, SELECTION_FIELDS, all_steps)
        write_output(SUMMARY_OUTPUT, SUMMARY_FIELDS, all_summaries)
        expected_count = len(workloads) * len(BUDGETS) * len(REQUESTED_K_VALUES)
        print(f"Summary rows: {len(all_summaries)} (expected {expected_count})")
        print(f"Selection rows: {len(all_steps)}")
        print(f"Saved: {SELECTION_OUTPUT.relative_to(ROOT).as_posix()}")
        print(f"Saved: {SUMMARY_OUTPUT.relative_to(ROOT).as_posix()}")
        print("Deterministic ordering and K-prefix consistency: PASS")
        print("STAGE WORKLOAD-AWARE OPTIMIZATION COMPLETE")
        return 0
    except (StageOptimizationError, RuntimeError, OSError, KeyError, ValueError) as exc:
        print(f"STAGE workload-aware optimization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
