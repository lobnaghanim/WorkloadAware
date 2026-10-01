"""Greedily select up to four workload-aware physical shortcut links."""

import copy
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

# Reuse the proven K=2 physical/routing/evaluation model.  K=2 output data is
# deliberately not involved in optimization decisions.
import select_k2_workload_aware as k2


WORKLOADS = k2.WORKLOADS
BUDGETS = k2.BUDGETS
REQUESTED_K = 4
EPS = k2.EPS
SELECTION_OUTPUT = REPO_ROOT / "results/k4_workload_aware_selections.csv"
SUMMARY_OUTPUT = REPO_ROOT / "results/k4_workload_aware_summary.csv"
K2_SELECTIONS = REPO_ROOT / "results/k2_workload_aware_selections.csv"
SELECTION_FIELDS = k2.SELECTION_FIELDS
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
    "final_avg_latency",
    "latency_gain_vs_baseline_percent",
    "final_max_link_load",
    "baseline_max_link_load",
    "analytical_throughput",
    "total_routed_traffic",
    "traffic_wire_cost",
)


def fail(message):
    raise RuntimeError(message)


def greedy_configuration(workload, budget, physical, workload_context, baseline):
    """Run four dynamic greedy iterations from the original physical mesh."""
    current_topology = copy.deepcopy(physical["topology"])
    occupied = {
        chiplet_id: set(phys)
        for chiplet_id, phys in physical["baseline_occupied"].items()
    }
    selected_pairs = set()
    selected = []
    cumulative_wire = 0.0
    remaining_budget = budget
    routing_regenerations = 0
    stopped_early = False

    for step in range(1, REQUESTED_K + 1):
        feasible, considered_pairs = k2.evaluate_iteration(
            current_topology,
            occupied,
            selected_pairs,
            remaining_budget,
            baseline["max_link_load"],
            physical,
            workload_context,
        )
        if step == 1 and considered_pairs != k2.EXPECTED_INITIAL_CANDIDATES:
            fail(
                f"{workload} B{budget:g} iteration 1 considered "
                f"{considered_pairs} pairs, expected {k2.EXPECTED_INITIAL_CANDIDATES}"
            )
        if not feasible:
            stopped_early = True
            break

        # This is the specified deterministic objective/tie ordering.  No
        # gain-per-mm, weighted score, or prior static ranking is used.
        best = min(
            feasible,
            key=lambda candidate: (
                candidate["avg_latency"],
                candidate["max_link_load"],
                candidate["length_mm"],
                candidate["u"],
                candidate["v"],
                candidate["phy_u"],
                candidate["phy_v"],
            ),
        )
        pair = (best["u"], best["v"])
        endpoints = (
            (best["u"], best["phy_u"]),
            (best["v"], best["phy_v"]),
        )
        if pair in physical["mesh_edges"] or pair in selected_pairs:
            fail(f"Greedy selector attempted a mesh/duplicate shortcut {pair}")
        if any(phy in occupied[chiplet] for chiplet, phy in endpoints):
            fail(f"Greedy selector attempted PHY reuse for {pair}")

        current_topology.append(copy.deepcopy(best["link"]))
        selected_pairs.add(pair)
        for chiplet, phy in endpoints:
            occupied[chiplet].add(phy)
        cumulative_wire += best["length_mm"]
        remaining_budget = budget - cumulative_wire
        if cumulative_wire > budget + EPS or remaining_budget < -EPS:
            fail(f"{workload} B{budget:g} exceeded its total wire budget")
        if best["max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"{workload} B{budget:g} exceeded its fixed K=0 load cap")

        routing = k2.generate_splif_routing(
            physical["chiplets"], physical["placement"], current_topology
        )
        if not isinstance(routing, dict) or not isinstance(routing.get("table"), dict):
            fail(f"SPLIF regeneration failed after {workload} B{budget:g} step {step}")
        routing_regenerations += 1
        best["step"] = step
        best["cumulative_wire_length_mm"] = cumulative_wire
        best["remaining_budget_mm"] = max(0.0, remaining_budget)
        best["endpoints"] = endpoints
        selected.append(best)

    if len(selected) > REQUESTED_K:
        fail(f"{workload} B{budget:g} selected more than K=4 links")
    if routing_regenerations != len(selected):
        fail(f"Routing was not regenerated after every topology change")
    if len(current_topology) != k2.EXPECTED_MESH_LINKS + len(selected):
        fail(f"Final topology size is not 24 + actual_k for {workload} B{budget:g}")

    final_metrics = k2.evaluate(current_topology, workload_context)
    if final_metrics["max_load"] > baseline["max_link_load"] + EPS:
        fail(f"Final {workload} B{budget:g} topology exceeds its K=0 load cap")
    if selected:
        last = selected[-1]
        for candidate_field, final_field in (
            ("avg_latency", "avg_latency"),
            ("max_link_load", "max_load"),
            ("throughput", "throughput"),
            ("total_routed_traffic", "total_routed_traffic"),
            ("traffic_wire_cost", "traffic_wire_cost"),
        ):
            if not k2.metrics_close(last[candidate_field], final_metrics[final_field]):
                fail(f"Final metric mismatch for {workload} B{budget:g}: {candidate_field}")

    shortcut_endpoints = [endpoint for item in selected for endpoint in item["endpoints"]]
    if len(shortcut_endpoints) != len(set(shortcut_endpoints)):
        fail(f"{workload} B{budget:g} reuses a shortcut PHY endpoint")
    if len(selected_pairs) != len(selected):
        fail(f"{workload} B{budget:g} contains duplicate shortcuts")
    return selected, final_metrics, cumulative_wire, current_topology, stopped_early


def make_output_rows(workload, budget, selected, final_metrics, wire, baseline):
    actual_k = len(selected)
    step_rows = []
    for link in selected:
        step_rows.append(
            {
                "workload": workload,
                "budget_mm": budget,
                "requested_k": REQUESTED_K,
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
                "latency_gain_vs_baseline_percent": k2.gain_percent(
                    baseline["avg_latency"], link["avg_latency"]
                ),
                "max_link_load_after_step": link["max_link_load"],
                "baseline_max_link_load": baseline["max_link_load"],
                "analytical_throughput_after_step": link["throughput"],
                "total_routed_traffic_after_step": link["total_routed_traffic"],
                "traffic_wire_cost_after_step": link["traffic_wire_cost"],
            }
        )

    summary = {
        "workload": workload,
        "budget_mm": budget,
        "requested_k": REQUESTED_K,
        "actual_k": actual_k,
        "total_wire_length_mm": wire,
        "final_avg_latency": final_metrics["avg_latency"],
        "latency_gain_vs_baseline_percent": k2.gain_percent(
            baseline["avg_latency"], final_metrics["avg_latency"]
        ),
        "final_max_link_load": final_metrics["max_load"],
        "baseline_max_link_load": baseline["max_link_load"],
        "analytical_throughput": final_metrics["throughput"],
        "total_routed_traffic": final_metrics["total_routed_traffic"],
        "traffic_wire_cost": final_metrics["traffic_wire_cost"],
        "_final_topology_size": k2.EXPECTED_MESH_LINKS + actual_k,
    }
    for index in range(1, REQUESTED_K + 1):
        if index <= actual_k:
            link = selected[index - 1]
            summary[f"link_{index}"] = f"{link['u']}<->{link['v']}"
            summary[f"link_{index}_phy_pair"] = f"{link['phy_u']}<->{link['phy_v']}"
            summary[f"link_{index}_length_mm"] = link["length_mm"]
        else:
            summary[f"link_{index}"] = "none"
            summary[f"link_{index}_phy_pair"] = None
            summary[f"link_{index}_length_mm"] = None
    return step_rows, summary


def print_configuration(budget, baseline, selected, summary, stopped_early):
    print(f"\nWire budget: {budget:.0f} mm")
    print(f"Requested K: {REQUESTED_K}")
    for index, link in enumerate(selected, start=1):
        print(f"\nLink {index}: {link['u']} <-> {link['v']}")
        print(f"PHYs: {link['phy_u']} <-> {link['phy_v']}")
        print(f"Length: {link['length_mm']:.3f} mm")
    if stopped_early:
        print("\nNo further feasible link under remaining constraints.")
    print(f"\nActual K: {summary['actual_k']}")
    print(f"Total wire length: {summary['total_wire_length_mm']:.3f} mm")
    print(f"Remaining budget: {budget - summary['total_wire_length_mm']:.3f} mm")
    print(f"Final average latency: {summary['final_avg_latency']:.3f}")
    print(f"Latency improvement: {summary['latency_gain_vs_baseline_percent']:.2f}%")
    print(f"Maximum link load: {summary['final_max_link_load']:.3f}")
    print(f"Analytical throughput: {summary['analytical_throughput']:.3f}")


def validate_outputs(step_rows, summaries, physical):
    expected_keys = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if len(summaries) != 16:
        fail(f"Expected exactly 16 K=4 summaries; found {len(summaries)}")
    summary_by_key = {}
    steps_by_key = defaultdict(list)
    for summary in summaries:
        key = (summary["workload"], summary["budget_mm"])
        if key in summary_by_key:
            fail(f"Duplicate K=4 summary for {key}")
        summary_by_key[key] = summary
        if summary["requested_k"] != 4 or not 0 <= summary["actual_k"] <= 4:
            fail(f"Invalid requested_k/actual_k for {key}")
        if summary["total_wire_length_mm"] > summary["budget_mm"] + EPS:
            fail(f"Total wire budget exceeded for {key}")
        if summary["final_max_link_load"] > summary["baseline_max_link_load"] + EPS:
            fail(f"Original workload load cap exceeded for {key}")
        if summary["_final_topology_size"] != 24 + summary["actual_k"]:
            fail(f"Final topology size is invalid for {key}")
    if set(summary_by_key) != expected_keys:
        fail("K=4 summary workload/budget matrix is incomplete")

    for row in step_rows:
        key = (row["workload"], row["budget_mm"])
        steps_by_key[key].append(row)
        if row["requested_k"] != 4:
            fail(f"Selection row requested_k is not 4 for {key}")
        if row["cumulative_wire_length_mm"] > row["budget_mm"] + EPS:
            fail(f"Cumulative wire budget exceeded for {key}")
        if row["max_link_load_after_step"] > row["baseline_max_link_load"] + EPS:
            fail(f"Step load cap exceeded for {key}")
        if tuple(sorted((row["u"], row["v"]))) in physical["mesh_edges"]:
            fail(f"Existing mesh edge selected for {key}")

    for key in expected_keys:
        summary = summary_by_key[key]
        rows = sorted(steps_by_key.get(key, []), key=lambda row: row["step"])
        if len(rows) != summary["actual_k"]:
            fail(f"Selection count disagrees with actual_k for {key}")
        if [row["step"] for row in rows] != list(range(1, len(rows) + 1)):
            fail(f"Non-contiguous selection steps for {key}")
        if any(row["actual_k"] != summary["actual_k"] for row in rows):
            fail(f"Step rows do not contain final actual_k for {key}")
        pairs = [(row["u"], row["v"]) for row in rows]
        endpoints = [
            endpoint
            for row in rows
            for endpoint in ((row["u"], row["phy_u"]), (row["v"], row["phy_v"]))
        ]
        if len(pairs) != len(set(pairs)):
            fail(f"Duplicate shortcut selected for {key}")
        if len(endpoints) != len(set(endpoints)):
            fail(f"PHY endpoint reused for {key}")
        if rows:
            if not math.isclose(
                rows[-1]["cumulative_wire_length_mm"],
                summary["total_wire_length_mm"],
                rel_tol=k2.METRIC_REL_TOL,
                abs_tol=k2.METRIC_ABS_TOL,
            ):
                fail(f"Final cumulative wire disagrees with summary for {key}")

    workload_counts = defaultdict(int)
    for summary in summaries:
        workload_counts[summary["workload"]] += 1
    if any(workload_counts[workload] != 4 for workload in WORKLOADS):
        fail(f"Every workload must have exactly four summaries: {dict(workload_counts)}")


def validate_k2_prefix(step_rows):
    """Check K=2 only after K=4 decisions have already been completed."""
    try:
        with K2_SELECTIONS.open(newline="", encoding="utf-8") as input_file:
            k2_rows = list(csv.DictReader(input_file))
    except OSError as error:
        fail(f"Cannot read optional K=2 consistency input {K2_SELECTIONS}: {error}")
    k4_groups = defaultdict(list)
    k2_groups = defaultdict(list)
    for row in step_rows:
        k4_groups[(row["workload"], float(row["budget_mm"]))].append(row)
    for row in k2_rows:
        key = (row["workload"].strip(), float(row["budget_mm"]))
        k2_groups[key].append(row)
    expected_keys = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(k2_groups) != expected_keys:
        fail("K=2 consistency file does not contain the expected workload/budget matrix")
    checked = 0
    for key in expected_keys:
        prior = sorted(k2_groups[key], key=lambda row: int(row["step"]))
        current = sorted(k4_groups.get(key, []), key=lambda row: row["step"])
        if len(current) < len(prior):
            fail(f"K=4 trajectory stops before the proven K=2 trajectory for {key}")
        for index, old in enumerate(prior):
            old_signature = tuple(int(old[field]) for field in ("u", "v", "phy_u", "phy_v"))
            new = current[index]
            new_signature = tuple(new[field] for field in ("u", "v", "phy_u", "phy_v"))
            if new_signature != old_signature:
                fail(
                    f"K=4/K=2 greedy-prefix inconsistency for {key} step {index + 1}: "
                    f"K=2={old_signature}, K=4={new_signature}"
                )
            checked += 1
    print(f"K=2 greedy-prefix selections verified: {checked}")


def write_csv(path, fields, rows):
    try:
        with path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as error:
        fail(f"Cannot write output CSV {path}: {error}")


def main():
    physical = k2.load_physical_context()
    if physical["initial_candidate_count"] != 96:
        fail("Initial non-mesh candidate-pair count is not 96")
    print(f"Validated initial non-mesh candidate count: {physical['initial_candidate_count']}")
    all_steps = []
    all_summaries = []
    for workload in WORKLOADS:
        print("\n" + "=" * 88)
        print(f"K=4 WORKLOAD-AWARE SELECTION - {workload.upper()}")
        print("=" * 88)
        workload_context = k2.make_workload_context(physical, workload)
        baseline, _ = k2.load_baseline(workload, workload_context, physical["topology"])
        for budget in BUDGETS:
            selected, metrics, wire, _, stopped_early = greedy_configuration(
                workload, budget, physical, workload_context, baseline
            )
            step_rows, summary = make_output_rows(
                workload, budget, selected, metrics, wire, baseline
            )
            all_steps.extend(step_rows)
            all_summaries.append(summary)
            print_configuration(budget, baseline, selected, summary, stopped_early)

    validate_outputs(all_steps, all_summaries, physical)
    validate_k2_prefix(all_steps)
    write_csv(SELECTION_OUTPUT, SELECTION_FIELDS, all_steps)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, all_summaries)
    print(f"\nSelection rows: {len(all_steps)}")
    print(f"Configuration-summary rows: {len(all_summaries)}")
    print(f"Saved: {SELECTION_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print(f"Saved: {SUMMARY_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print("K=4 WORKLOAD-AWARE SELECTION COMPLETE")


if __name__ == "__main__":
    main()
