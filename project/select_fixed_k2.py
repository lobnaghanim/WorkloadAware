"""Select up to two fixed links per budget using uniform reference traffic."""

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PROJECT_DIR))

from select_fixed_k1 import (
    REFERENCE_TRAFFIC_NAME,
    evaluate,
    load_uniform_reference,
)
from select_k2_workload_aware import (
    BUDGETS,
    EXPECTED_INITIAL_CANDIDATES,
    REQUESTED_K,
    greedy_configuration,
    load_physical_context,
)


SELECTION_OUTPUT = REPO_ROOT / "results/k2_fixed_selections.csv"
SUMMARY_OUTPUT = REPO_ROOT / "results/k2_fixed_summary.csv"
SELECTION_POLICY = (
    "greedy_min_latency_subject_to_total_wire_budget_phy_and_baseline_max_load"
)
EPS = 1e-9
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
    design = dict(physical["design"])
    design["design_name"] = REFERENCE_TRAFFIC_NAME
    context = {
        "design": design,
        "chiplets": physical["chiplets"],
        "placement": physical["placement"],
        "topology": physical["topology"],
        "packaging": physical["packaging"],
        "technologies": physical["technologies"],
        "traffic": traffic,
    }
    return context, traffic_source


def evaluate_reference_baseline(physical, context):
    evaluated = evaluate(physical["topology"], context)
    baseline = {
        "avg_latency": evaluated["avg_latency"],
        "aggregate_throughput": evaluated["throughput"],
        "max_link_load": evaluated["max_load"],
        "total_routed_traffic": evaluated["total_routed_traffic"],
        "traffic_wire_cost": evaluated["traffic_wire_cost"],
    }
    for field, value in baseline.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(f"Uniform-reference baseline {field} is not numeric")
        if not math.isfinite(value):
            fail(f"Uniform-reference baseline {field} is not finite")
    return baseline


def make_rows(budget, selected, final_metrics, total_wire, baseline):
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

    def link_value(index, field, default=None):
        return selected[index][field] if len(selected) > index else default

    summary = {
        "budget_mm": budget,
        "requested_k": REQUESTED_K,
        "actual_k": actual_k,
        "link_1": (
            f"{selected[0]['u']}<->{selected[0]['v']}" if actual_k >= 1 else "none"
        ),
        "link_1_phy_pair": (
            f"{selected[0]['phy_u']}<->{selected[0]['phy_v']}"
            if actual_k >= 1
            else None
        ),
        "link_1_length_mm": link_value(0, "length_mm"),
        "link_2": (
            f"{selected[1]['u']}<->{selected[1]['v']}" if actual_k >= 2 else "none"
        ),
        "link_2_phy_pair": (
            f"{selected[1]['phy_u']}<->{selected[1]['phy_v']}"
            if actual_k >= 2
            else None
        ),
        "link_2_length_mm": link_value(1, "length_mm"),
        "total_wire_length_mm": total_wire,
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
    }
    return step_rows, summary


def print_baseline(baseline, traffic_source):
    print("=" * 92)
    print("FIXED K=2 GREEDY SELECTION — UNIFORM REFERENCE")
    print("=" * 92)
    print(f"Reference traffic: {REFERENCE_TRAFFIC_NAME} ({traffic_source})")
    print(f"Baseline average latency: {baseline['avg_latency']:.3f}")
    print(f"Baseline analytical throughput: {baseline['aggregate_throughput']:.3f}")
    print(f"Baseline maximum directed-link load: {baseline['max_link_load']:.3f}")
    print(f"Baseline total routed traffic: {baseline['total_routed_traffic']:.3f}")
    print(f"Baseline traffic-wire cost: {baseline['traffic_wire_cost']:.3f}")


def print_configuration(budget, selected, summary):
    print()
    print(f"Wire budget: {budget:.0f} mm")
    print(f"Requested K: {REQUESTED_K}")
    for step in range(REQUESTED_K):
        print()
        if step >= len(selected):
            print(f"Link {step + 1}: none")
            continue
        link = selected[step]
        print(f"Link {step + 1}: {link['u']} <-> {link['v']}")
        print(f"PHYs: {link['phy_u']} <-> {link['phy_v']}")
        print(f"Length: {link['length_mm']:.3f} mm")
    print()
    print(f"Actual K: {summary['actual_k']}")
    print(f"Total wire: {summary['total_wire_length_mm']:.3f} mm")
    print(f"Final average latency: {summary['final_reference_avg_latency']:.3f}")
    print(f"Latency improvement: {summary['reference_latency_gain_percent']:.2f}%")
    print(f"Maximum link load: {summary['final_reference_max_link_load']:.3f}")
    print(f"Analytical throughput: {summary['analytical_throughput']:.3f}")


def validate_outputs(step_rows, summaries, physical, baseline):
    if physical["node_count"] != 16:
        fail("Fixed K=2 validation expected exactly 16 chiplets")
    if len(physical["topology"]) != 24 or len(physical["mesh_edges"]) != 24:
        fail("Fixed K=2 validation expected exactly 24 original mesh links")
    if physical["initial_candidate_count"] != EXPECTED_INITIAL_CANDIDATES:
        fail("Fixed K=2 validation expected exactly 96 initial candidates")
    if len(summaries) != 4 or {summary["budget_mm"] for summary in summaries} != set(BUDGETS):
        fail("Fixed K=2 summary must contain exactly one row per budget")

    grouped = defaultdict(list)
    for row in step_rows:
        grouped[row["budget_mm"]].append(row)
        pair = tuple(sorted((row["u"], row["v"])))
        if pair in physical["mesh_edges"]:
            fail(f"Fixed K=2 selected existing mesh edge {pair}")
        if row["cumulative_wire_length_mm"] > row["budget_mm"] + EPS:
            fail(f"Fixed K=2 cumulative wire exceeds B{row['budget_mm']:g}")
        if row["reference_max_link_load_after_step"] > baseline["max_link_load"] + EPS:
            fail(f"Fixed K=2 intermediate load exceeds original baseline cap")
        if row["reference_traffic"] != REFERENCE_TRAFFIC_NAME:
            fail("Fixed K=2 step row has an invalid reference traffic label")
        if row["selection_policy"] != SELECTION_POLICY:
            fail("Fixed K=2 step row has an invalid selection policy label")

    for summary in summaries:
        budget = summary["budget_mm"]
        rows = sorted(grouped.get(budget, []), key=lambda row: row["step"])
        if not 0 <= summary["actual_k"] <= REQUESTED_K:
            fail(f"Fixed K=2 actual_k is invalid for B{budget:g}")
        if len(rows) != summary["actual_k"]:
            fail(f"Fixed K=2 step count disagrees with actual_k for B{budget:g}")
        if summary["total_wire_length_mm"] > budget + EPS:
            fail(f"Fixed K=2 summary wire exceeds B{budget:g}")
        if summary["final_reference_max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"Fixed K=2 final load exceeds original cap for B{budget:g}")
        pairs = [(row["u"], row["v"]) for row in rows]
        if len(pairs) != len(set(pairs)):
            fail(f"Fixed K=2 has a duplicate shortcut for B{budget:g}")
        endpoints = [
            endpoint
            for row in rows
            for endpoint in ((row["u"], row["phy_u"]), (row["v"], row["phy_v"]))
        ]
        if len(endpoints) != len(set(endpoints)):
            fail(f"Fixed K=2 reuses a PHY endpoint for B{budget:g}")
        if summary["reference_traffic"] != REFERENCE_TRAFFIC_NAME:
            fail("Fixed K=2 summary has an invalid reference traffic label")
        if summary["selection_policy"] != SELECTION_POLICY:
            fail("Fixed K=2 summary has an invalid selection policy label")


def write_csv(path, fields, rows):
    try:
        with path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as error:
        fail(f"Cannot write fixed K=2 output {path}: {error}")


def main():
    physical = load_physical_context()
    reference_context, traffic_source = build_reference_context(physical)
    baseline = evaluate_reference_baseline(physical, reference_context)
    print_baseline(baseline, traffic_source)
    print(f"Initial non-mesh candidates: {physical['initial_candidate_count']}")

    all_steps = []
    summaries = []
    for budget in BUDGETS:
        selected, final_metrics, total_wire = greedy_configuration(
            REFERENCE_TRAFFIC_NAME,
            budget,
            physical,
            reference_context,
            baseline,
        )
        step_rows, summary = make_rows(
            budget, selected, final_metrics, total_wire, baseline
        )
        all_steps.extend(step_rows)
        summaries.append(summary)
        print_configuration(budget, selected, summary)

    validate_outputs(all_steps, summaries, physical, baseline)
    write_csv(SELECTION_OUTPUT, SELECTION_FIELDS, all_steps)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summaries)
    print()
    print(f"Selection rows: {len(all_steps)}")
    print(f"Configuration-summary rows: {len(summaries)}")
    print(f"Saved: {SELECTION_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print(f"Saved: {SUMMARY_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print("FIXED K=2 SELECTION COMPLETE")


if __name__ == "__main__":
    main()
