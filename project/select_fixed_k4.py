"""Select up to four fixed links per budget using uniform reference traffic."""

import copy
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PROJECT_DIR))

# Reuse the proven physical, routing, and analytical evaluation implementation.
# No workload-specific input or result participates in the optimization.
from select_fixed_k1 import REFERENCE_TRAFFIC_NAME, evaluate, load_uniform_reference
import select_k2_workload_aware as k2


BUDGETS = k2.BUDGETS
REQUESTED_K = 4
EPS = k2.EPS
SELECTION_OUTPUT = REPO_ROOT / "results/k4_fixed_selections.csv"
SUMMARY_OUTPUT = REPO_ROOT / "results/k4_fixed_summary.csv"
K2_SELECTIONS = REPO_ROOT / "results/k2_fixed_selections.csv"
SELECTION_POLICY = (
    "greedy_min_latency_subject_to_total_wire_budget_phy_and_baseline_max_load"
)
SELECTION_FIELDS = (
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
    "reference_avg_latency_after_step",
    "reference_latency_gain_percent",
    "reference_max_link_load_after_step",
    "reference_baseline_max_link_load",
    "reference_throughput_after_step",
    "total_routed_traffic_after_step",
    "traffic_wire_cost_after_step",
    "reference_traffic",
    "selection_policy",
)
SUMMARY_FIELDS = (
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
    "final_reference_avg_latency",
    "reference_latency_gain_percent",
    "final_reference_max_link_load",
    "reference_baseline_max_link_load",
    "analytical_throughput",
    "total_routed_traffic",
    "traffic_wire_cost",
    "reference_traffic",
    "selection_policy",
)


def fail(message):
    raise RuntimeError(message)


def gain_percent(baseline_latency, candidate_latency):
    if baseline_latency <= 0:
        fail("Uniform-reference baseline average latency must be positive")
    return 100.0 * (baseline_latency - candidate_latency) / baseline_latency


def build_reference_context(physical):
    traffic, traffic_source = load_uniform_reference(physical["node_count"])
    design = copy.deepcopy(physical["design"])
    design["design_name"] = REFERENCE_TRAFFIC_NAME
    return {
        "design": design,
        "chiplets": physical["chiplets"],
        "placement": physical["placement"],
        "topology": physical["topology"],
        "packaging": physical["packaging"],
        "technologies": physical["technologies"],
        "traffic": traffic,
    }, traffic_source


def evaluate_reference_baseline(physical, context):
    metrics = evaluate(physical["topology"], context)
    baseline = {
        "avg_latency": metrics["avg_latency"],
        "aggregate_throughput": metrics["throughput"],
        "max_link_load": metrics["max_load"],
        "total_routed_traffic": metrics["total_routed_traffic"],
        "traffic_wire_cost": metrics["traffic_wire_cost"],
    }
    for field, value in baseline.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(f"Uniform-reference baseline {field} is not numeric")
        if not math.isfinite(value):
            fail(f"Uniform-reference baseline {field} is not finite")
    return baseline


def greedy_configuration(budget, physical, reference_context, baseline):
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
        # evaluate_iteration dynamically enumerates the remaining pairs, chooses
        # each pair's shortest currently free PHYs, regenerates SPLIF for each
        # temporary topology, and applies the unchanged K=0 load cap.
        feasible, considered_pairs = k2.evaluate_iteration(
            current_topology,
            occupied,
            selected_pairs,
            remaining_budget,
            baseline["max_link_load"],
            physical,
            reference_context,
        )
        if step == 1 and considered_pairs != k2.EXPECTED_INITIAL_CANDIDATES:
            fail(
                f"B{budget:g} iteration 1 considered {considered_pairs} pairs; "
                f"expected {k2.EXPECTED_INITIAL_CANDIDATES}"
            )
        if not feasible:
            stopped_early = True
            break

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
            fail(f"B{budget:g} exceeded its cumulative wire budget")
        if best["max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"B{budget:g} exceeded its original K=0 load cap")

        routing = k2.generate_splif_routing(
            physical["chiplets"], physical["placement"], current_topology
        )
        if not isinstance(routing, dict) or not isinstance(routing.get("table"), dict):
            fail(f"SPLIF regeneration failed after B{budget:g} step {step}")
        routing_regenerations += 1

        best["step"] = step
        best["cumulative_wire_length_mm"] = cumulative_wire
        best["remaining_budget_mm"] = max(0.0, remaining_budget)
        best["endpoints"] = endpoints
        selected.append(best)

    if len(selected) > REQUESTED_K:
        fail(f"B{budget:g} selected more than K=4 links")
    if routing_regenerations != len(selected):
        fail("Routing was not regenerated after every permanent topology change")
    if len(current_topology) != k2.EXPECTED_MESH_LINKS + len(selected):
        fail(f"Final topology size is not 24 + actual_k for B{budget:g}")

    final_metrics = evaluate(current_topology, reference_context)
    if final_metrics["max_load"] > baseline["max_link_load"] + EPS:
        fail(f"Final B{budget:g} topology exceeds the original K=0 load cap")
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
                fail(f"Final metric mismatch for B{budget:g}: {candidate_field}")

    shortcut_endpoints = [endpoint for item in selected for endpoint in item["endpoints"]]
    if len(shortcut_endpoints) != len(set(shortcut_endpoints)):
        fail(f"B{budget:g} reuses a shortcut PHY endpoint")
    if len(selected_pairs) != len(selected):
        fail(f"B{budget:g} contains duplicate shortcuts")
    return selected, final_metrics, cumulative_wire, current_topology, stopped_early


def make_output_rows(budget, selected, final_metrics, wire, baseline):
    actual_k = len(selected)
    step_rows = []
    for link in selected:
        step_rows.append(
            {
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
                "reference_avg_latency_after_step": link["avg_latency"],
                "reference_latency_gain_percent": gain_percent(
                    baseline["avg_latency"], link["avg_latency"]
                ),
                "reference_max_link_load_after_step": link["max_link_load"],
                "reference_baseline_max_link_load": baseline["max_link_load"],
                "reference_throughput_after_step": link["throughput"],
                "total_routed_traffic_after_step": link["total_routed_traffic"],
                "traffic_wire_cost_after_step": link["traffic_wire_cost"],
                "reference_traffic": REFERENCE_TRAFFIC_NAME,
                "selection_policy": SELECTION_POLICY,
            }
        )

    summary = {
        "budget_mm": budget,
        "requested_k": REQUESTED_K,
        "actual_k": actual_k,
        "total_wire_length_mm": wire,
        "final_reference_avg_latency": final_metrics["avg_latency"],
        "reference_latency_gain_percent": gain_percent(
            baseline["avg_latency"], final_metrics["avg_latency"]
        ),
        "final_reference_max_link_load": final_metrics["max_load"],
        "reference_baseline_max_link_load": baseline["max_link_load"],
        "analytical_throughput": final_metrics["throughput"],
        "total_routed_traffic": final_metrics["total_routed_traffic"],
        "traffic_wire_cost": final_metrics["traffic_wire_cost"],
        "reference_traffic": REFERENCE_TRAFFIC_NAME,
        "selection_policy": SELECTION_POLICY,
        "_final_topology_size": k2.EXPECTED_MESH_LINKS + actual_k,
    }
    for index in range(1, REQUESTED_K + 1):
        if index <= actual_k:
            link = selected[index - 1]
            summary[f"link_{index}"] = f"{link['u']}<->{link['v']}"
            summary[f"link_{index}_phy_pair"] = f"{link['phy_u']}<->{link['phy_v']}"
            summary[f"link_{index}_length_mm"] = link["length_mm"]
        else:
            summary[f"link_{index}"] = None
            summary[f"link_{index}_phy_pair"] = None
            summary[f"link_{index}_length_mm"] = None
    return step_rows, summary


def print_baseline(baseline, traffic_source, candidate_count):
    print("=" * 92)
    print("FIXED K=4 GREEDY SELECTION — UNIFORM REFERENCE")
    print("=" * 92)
    print(f"Reference traffic: {REFERENCE_TRAFFIC_NAME} ({traffic_source})")
    print(f"Baseline average latency: {baseline['avg_latency']:.3f}")
    print(f"Baseline analytical throughput: {baseline['aggregate_throughput']:.3f}")
    print(f"Baseline maximum directed-link load: {baseline['max_link_load']:.3f}")
    print(f"Baseline total routed traffic: {baseline['total_routed_traffic']:.3f}")
    print(f"Baseline traffic × wire-length cost: {baseline['traffic_wire_cost']:.3f}")
    print(f"Initial non-mesh candidates: {candidate_count}")


def print_configuration(budget, selected, summary, stopped_early):
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
    print(f"Final average latency: {summary['final_reference_avg_latency']:.3f}")
    print(f"Latency improvement: {summary['reference_latency_gain_percent']:.2f}%")
    print(f"Maximum link load: {summary['final_reference_max_link_load']:.3f}")
    print(f"Analytical throughput: {summary['analytical_throughput']:.3f}")


def validate_outputs(step_rows, summaries, physical, baseline):
    if physical["node_count"] != 16:
        fail("Fixed K=4 validation expected exactly 16 chiplets")
    if len(physical["topology"]) != 24 or len(physical["mesh_edges"]) != 24:
        fail("Fixed K=4 validation expected exactly 24 original mesh links")
    if physical["initial_candidate_count"] != 96:
        fail("Fixed K=4 validation expected exactly 96 initial candidates")
    if len(summaries) != 4:
        fail(f"Expected exactly four summary rows; found {len(summaries)}")

    summary_by_budget = {}
    steps_by_budget = defaultdict(list)
    for summary in summaries:
        budget = summary["budget_mm"]
        if budget in summary_by_budget:
            fail(f"Duplicate summary for B{budget:g}")
        summary_by_budget[budget] = summary
        if summary["requested_k"] != 4 or not 0 <= summary["actual_k"] <= 4:
            fail(f"Invalid requested_k/actual_k for B{budget:g}")
        if summary["total_wire_length_mm"] > budget + EPS:
            fail(f"Summary wire budget exceeded for B{budget:g}")
        if summary["final_reference_max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"Final load cap exceeded for B{budget:g}")
        if summary["_final_topology_size"] != 24 + summary["actual_k"]:
            fail(f"Final topology size is invalid for B{budget:g}")
        if summary["reference_traffic"] != REFERENCE_TRAFFIC_NAME:
            fail(f"Invalid reference traffic label for B{budget:g}")
        if summary["selection_policy"] != SELECTION_POLICY:
            fail(f"Invalid selection policy for B{budget:g}")
    if set(summary_by_budget) != set(BUDGETS):
        fail("Summary does not contain exactly one row for every requested budget")

    for row in step_rows:
        budget = row["budget_mm"]
        steps_by_budget[budget].append(row)
        if row["requested_k"] != 4:
            fail(f"Selection requested_k is not 4 for B{budget:g}")
        if tuple(sorted((row["u"], row["v"]))) in physical["mesh_edges"]:
            fail(f"Existing mesh edge selected for B{budget:g}")
        if row["cumulative_wire_length_mm"] > budget + EPS:
            fail(f"Cumulative wire budget exceeded for B{budget:g}")
        if row["reference_max_link_load_after_step"] > baseline["max_link_load"] + EPS:
            fail(f"Intermediate load cap exceeded for B{budget:g}")
        if row["reference_traffic"] != REFERENCE_TRAFFIC_NAME:
            fail(f"Selection used non-reference traffic for B{budget:g}")
        if row["selection_policy"] != SELECTION_POLICY:
            fail(f"Invalid selection policy for B{budget:g}")

    for budget in BUDGETS:
        summary = summary_by_budget[budget]
        rows = sorted(steps_by_budget.get(budget, []), key=lambda row: row["step"])
        if len(rows) != summary["actual_k"]:
            fail(f"Selection count disagrees with actual_k for B{budget:g}")
        if [row["step"] for row in rows] != list(range(1, len(rows) + 1)):
            fail(f"Non-contiguous selection steps for B{budget:g}")
        if any(row["actual_k"] != summary["actual_k"] for row in rows):
            fail(f"Step actual_k disagrees with summary for B{budget:g}")
        pairs = [tuple(sorted((row["u"], row["v"]))) for row in rows]
        endpoints = [
            endpoint
            for row in rows
            for endpoint in ((row["u"], row["phy_u"]), (row["v"], row["phy_v"]))
        ]
        if len(pairs) != len(set(pairs)):
            fail(f"Duplicate shortcut selected for B{budget:g}")
        if len(endpoints) != len(set(endpoints)):
            fail(f"PHY endpoint reused for B{budget:g}")
        if rows and not k2.metrics_close(
            rows[-1]["cumulative_wire_length_mm"],
            summary["total_wire_length_mm"],
        ):
            fail(f"Final cumulative wire disagrees with summary for B{budget:g}")


def validate_k2_prefix(step_rows):
    """Compare prefixes only after all K=4 optimization decisions are frozen."""
    try:
        with K2_SELECTIONS.open(newline="", encoding="utf-8") as input_file:
            prior_rows = list(csv.DictReader(input_file))
    except OSError as error:
        fail(f"Cannot read K=2 consistency input {K2_SELECTIONS}: {error}")

    k4_groups = defaultdict(list)
    k2_groups = defaultdict(list)
    for row in step_rows:
        k4_groups[float(row["budget_mm"])].append(row)
    for row_number, row in enumerate(prior_rows, start=2):
        try:
            k2_groups[float(row["budget_mm"])].append(row)
        except (KeyError, TypeError, ValueError) as error:
            fail(f"Malformed K=2 consistency row {row_number}: {error}")

    checked = 0
    for budget in BUDGETS:
        prior = sorted(k2_groups.get(budget, []), key=lambda row: int(row["step"]))
        if len(prior) != 2:
            continue
        current = sorted(k4_groups.get(budget, []), key=lambda row: row["step"])
        if len(current) < 2:
            fail(f"K=4 trajectory stops before the K=2 trajectory for B{budget:g}")
        for index in range(2):
            old_signature = tuple(
                int(prior[index][field]) for field in ("u", "v", "phy_u", "phy_v")
            )
            new_signature = tuple(
                current[index][field] for field in ("u", "v", "phy_u", "phy_v")
            )
            if new_signature != old_signature:
                fail(
                    f"K=4/K=2 greedy-prefix inconsistency for B{budget:g} "
                    f"step {index + 1}: K=2={old_signature}, K=4={new_signature}"
                )
            checked += 1
    print(f"K=2 greedy-prefix selections verified: {checked}")


def write_csv(path, fields, rows):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as error:
        fail(f"Cannot write fixed K=4 output {path}: {error}")


def main():
    physical = k2.load_physical_context()
    reference_context, traffic_source = build_reference_context(physical)
    baseline = evaluate_reference_baseline(physical, reference_context)
    print_baseline(baseline, traffic_source, physical["initial_candidate_count"])

    all_steps = []
    summaries = []
    for budget in BUDGETS:
        selected, final_metrics, wire, final_topology, stopped_early = (
            greedy_configuration(budget, physical, reference_context, baseline)
        )
        step_rows, summary = make_output_rows(
            budget, selected, final_metrics, wire, baseline
        )
        summary["_final_topology_size"] = len(final_topology)
        all_steps.extend(step_rows)
        summaries.append(summary)
        print_configuration(budget, selected, summary, stopped_early)

    validate_outputs(all_steps, summaries, physical, baseline)
    validate_k2_prefix(all_steps)
    write_csv(SELECTION_OUTPUT, SELECTION_FIELDS, all_steps)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summaries)
    print(f"\nSelection rows: {len(all_steps)}")
    print(f"Configuration-summary rows: {len(summaries)}")
    print(f"Saved: {SELECTION_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print(f"Saved: {SUMMARY_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print("FIXED K=4 SELECTION COMPLETE")


if __name__ == "__main__":
    main()
