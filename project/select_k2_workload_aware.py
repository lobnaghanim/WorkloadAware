"""Greedy workload-aware selection of up to two physical shortcut links."""

import copy
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
from select_fixed_k1 import (
    SPARE_PHYS,
    evaluate,
    generate_splif_routing,
    physical_distance,
)


DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
SELECTION_OUTPUT = REPO_ROOT / "results/k2_workload_aware_selections.csv"
SUMMARY_OUTPUT = REPO_ROOT / "results/k2_workload_aware_summary.csv"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5.0, 15.0, 25.0, 45.0)
REQUESTED_K = 2
EXPECTED_CHIPLETS = 16
EXPECTED_MESH_LINKS = 24
EXPECTED_INITIAL_CANDIDATES = 96
EPS = 1e-9
METRIC_REL_TOL = 1e-9
METRIC_ABS_TOL = 1e-9
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
    "link_2",
    "total_wire_length_mm",
    "final_avg_latency",
    "latency_gain_vs_baseline_percent",
    "final_max_link_load",
    "analytical_throughput",
    "total_routed_traffic",
    "traffic_wire_cost",
)


def fail(message):
    raise RuntimeError(message)


def read_json(path, description):
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot parse {description} {path}: {error}")
    if not isinstance(data, dict):
        fail(f"Malformed {description} {path}: expected a JSON object")
    return data


def finite_number(value, description):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{description} must be numeric; found {value!r}")
    if not math.isfinite(value):
        fail(f"{description} must be finite; found {value!r}")
    return float(value)


def metrics_close(left, right):
    return math.isclose(
        left, right, rel_tol=METRIC_REL_TOL, abs_tol=METRIC_ABS_TOL
    )


def load_physical_context():
    design = hlp.read_json(str(DESIGN_FILE))
    chiplets = hlp.read_json(str(REPO_ROOT / design["chiplets"]))
    placement = hlp.read_json(str(REPO_ROOT / design["placement"]))
    topology = hlp.read_json(str(REPO_ROOT / design["topology"]))
    packaging = hlp.read_json(str(REPO_ROOT / design["packaging"]))
    technologies = hlp.read_json(str(REPO_ROOT / design["technologies"]))

    node_count = len(placement["chiplets"])
    if node_count != EXPECTED_CHIPLETS:
        fail(f"Expected exactly 16 chiplets; found {node_count}")
    if len(topology) != EXPECTED_MESH_LINKS:
        fail(
            f"Expected baseline topology to contain {EXPECTED_MESH_LINKS} links; "
            f"found {len(topology)}"
        )

    mesh_edges = set()
    baseline_occupied = {chiplet_id: set() for chiplet_id in range(node_count)}
    for link in topology:
        ep1, ep2 = link["ep1"], link["ep2"]
        if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
            fail("The physical mesh baseline must contain only chiplet-chiplet links")
        u, v = ep1["outer_id"], ep2["outer_id"]
        if not 0 <= u < node_count or not 0 <= v < node_count or u == v:
            fail(f"Invalid baseline mesh link: {u}<->{v}")
        edge = tuple(sorted((u, v)))
        if edge in mesh_edges:
            fail(f"Duplicate baseline mesh edge: {edge}")
        mesh_edges.add(edge)
        baseline_occupied[u].add(ep1["inner_id"])
        baseline_occupied[v].add(ep2["inner_id"])

    if len(mesh_edges) != EXPECTED_MESH_LINKS:
        fail(f"Expected 24 unique mesh edges; found {len(mesh_edges)}")
    initial_candidates = node_count * (node_count - 1) // 2 - len(mesh_edges)
    if initial_candidates != EXPECTED_INITIAL_CANDIDATES:
        fail(
            f"Expected {EXPECTED_INITIAL_CANDIDATES} initial non-mesh candidates; "
            f"found {initial_candidates}"
        )

    for chiplet_id, chiplet_desc in enumerate(placement["chiplets"]):
        phys = chiplets[chiplet_desc["name"]]["phys"]
        for phy_id in SPARE_PHYS:
            if not 0 <= phy_id < len(phys):
                fail(f"Spare PHY {phy_id} is invalid on chiplet {chiplet_id}")
            if phy_id in baseline_occupied[chiplet_id]:
                fail(f"Spare PHY {phy_id} is occupied on baseline chiplet {chiplet_id}")

    return {
        "design": design,
        "chiplets": chiplets,
        "placement": placement,
        "topology": topology,
        "packaging": packaging,
        "technologies": technologies,
        "node_count": node_count,
        "mesh_edges": mesh_edges,
        "baseline_occupied": baseline_occupied,
        "initial_candidate_count": initial_candidates,
    }


def make_workload_context(physical, workload):
    chiplet_traffic_path = (
        REPO_ROOT
        / f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
    )
    unit_traffic_path = (
        REPO_ROOT / f"inputs/traffic_by_unit/traffic_project_{workload}.json"
    )
    if not chiplet_traffic_path.is_file() or not unit_traffic_path.is_file():
        fail(f"Missing fixed traffic input for workload {workload}")

    design = copy.deepcopy(physical["design"])
    design["design_name"] = f"project_k2_workload_aware_{workload}"
    design["traffic_by_chiplet"] = chiplet_traffic_path.relative_to(REPO_ROOT).as_posix()
    design["traffic_by_unit"] = unit_traffic_path.relative_to(REPO_ROOT).as_posix()
    return {
        "design": design,
        "chiplets": physical["chiplets"],
        "placement": physical["placement"],
        "topology": physical["topology"],
        "packaging": physical["packaging"],
        "technologies": physical["technologies"],
        "traffic": hlp.read_json(str(chiplet_traffic_path)),
    }


def load_baseline(workload, workload_context, baseline_topology):
    path = REPO_ROOT / f"results/physical_baseline_{workload}.json"
    stored = read_json(path, f"{workload} physical baseline")
    for field in (
        "avg_latency",
        "aggregate_throughput",
        "max_link_load",
        "total_routed_traffic",
        "traffic_wire_cost",
    ):
        if field not in stored:
            fail(f"Malformed baseline {path}: missing {field!r}")
        stored[field] = finite_number(stored[field], f"{field} in {path}")

    recomputed = evaluate(baseline_topology, workload_context)
    checks = {
        "avg_latency": recomputed["avg_latency"],
        "aggregate_throughput": recomputed["throughput"],
        "max_link_load": recomputed["max_load"],
        "total_routed_traffic": recomputed["total_routed_traffic"],
        "traffic_wire_cost": recomputed["traffic_wire_cost"],
    }
    for field, recomputed_value in checks.items():
        if not metrics_close(stored[field], recomputed_value):
            fail(
                f"Stored {workload} baseline {field}={stored[field]} does not "
                f"match recomputed value {recomputed_value}"
            )
    return stored, recomputed


def best_available_phy_pair(u, v, occupied, context):
    options = []
    for phy_u in SPARE_PHYS:
        if phy_u in occupied[u]:
            continue
        for phy_v in SPARE_PHYS:
            if phy_v in occupied[v]:
                continue
            length = physical_distance(
                u,
                phy_u,
                v,
                phy_v,
                context["placement"],
                context["chiplets"],
                context["packaging"],
            )
            options.append((length, phy_u, phy_v))
    return min(options) if options else None


def candidate_link(u, v, phy_u, phy_v):
    return {
        "ep1": {
            "type": "chiplet",
            "outer_id": u,
            "inner_id": phy_u,
        },
        "ep2": {
            "type": "chiplet",
            "outer_id": v,
            "inner_id": phy_v,
        },
    }


def evaluate_iteration(
    current_topology,
    occupied,
    selected_pairs,
    remaining_budget,
    baseline_cap,
    physical,
    workload_context,
):
    feasible = []
    considered_pairs = 0
    for u in range(physical["node_count"]):
        for v in range(u + 1, physical["node_count"]):
            pair = (u, v)
            if pair in physical["mesh_edges"] or pair in selected_pairs:
                continue
            considered_pairs += 1
            phy_choice = best_available_phy_pair(u, v, occupied, workload_context)
            if phy_choice is None:
                continue
            estimated_length, phy_u, phy_v = phy_choice
            if estimated_length > remaining_budget + EPS:
                continue

            link = candidate_link(u, v, phy_u, phy_v)
            temporary_topology = copy.deepcopy(current_topology)
            temporary_topology.append(link)
            metrics = evaluate(temporary_topology, workload_context)
            node_u, node_v = ("chiplet", u), ("chiplet", v)
            actual_length = metrics["link_lengths"][(node_u, node_v)]
            if not metrics_close(estimated_length, actual_length):
                fail(
                    f"Physical length mismatch for candidate {u}<->{v}: "
                    f"estimated {estimated_length}, RapidChiplet {actual_length}"
                )
            if metrics["max_load"] > baseline_cap + EPS:
                continue
            feasible.append(
                {
                    "u": u,
                    "v": v,
                    "phy_u": phy_u,
                    "phy_v": phy_v,
                    "length_mm": actual_length,
                    "link_latency_cycles": metrics["link_latencies"][(node_u, node_v)],
                    "avg_latency": metrics["avg_latency"],
                    "max_link_load": metrics["max_load"],
                    "throughput": metrics["throughput"],
                    "total_routed_traffic": metrics["total_routed_traffic"],
                    "traffic_wire_cost": metrics["traffic_wire_cost"],
                    "link": link,
                }
            )
    return feasible, considered_pairs


def greedy_configuration(workload, budget, physical, workload_context, baseline):
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

    for step in range(1, REQUESTED_K + 1):
        feasible, considered_pairs = evaluate_iteration(
            current_topology,
            occupied,
            selected_pairs,
            remaining_budget,
            baseline["max_link_load"],
            physical,
            workload_context,
        )
        if step == 1 and considered_pairs != EXPECTED_INITIAL_CANDIDATES:
            fail(
                f"{workload} B{budget:g} iteration 1 considered "
                f"{considered_pairs} candidates, expected 96"
            )
        if not feasible:
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
        endpoint_u = (best["u"], best["phy_u"])
        endpoint_v = (best["v"], best["phy_v"])
        if pair in physical["mesh_edges"] or pair in selected_pairs:
            fail(f"Greedy selector attempted duplicate/mesh link {pair}")
        if best["phy_u"] in occupied[best["u"]] or best["phy_v"] in occupied[best["v"]]:
            fail(f"Greedy selector attempted PHY reuse for {pair}")

        current_topology.append(copy.deepcopy(best["link"]))
        selected_pairs.add(pair)
        occupied[best["u"]].add(best["phy_u"])
        occupied[best["v"]].add(best["phy_v"])
        cumulative_wire += best["length_mm"]
        remaining_budget = budget - cumulative_wire
        if cumulative_wire > budget + EPS or remaining_budget < -EPS:
            fail(f"{workload} B{budget:g} exceeded its total wire budget")
        if best["max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"{workload} B{budget:g} exceeded its original baseline load cap")

        # Explicitly regenerate SPLIF after permanently changing the topology.
        permanent_routing = generate_splif_routing(
            physical["chiplets"], physical["placement"], current_topology
        )
        if not isinstance(permanent_routing, dict) or "table" not in permanent_routing:
            fail(f"Routing regeneration failed after {workload} B{budget:g} step {step}")
        routing_regenerations += 1
        best["step"] = step
        best["cumulative_wire_length_mm"] = cumulative_wire
        best["remaining_budget_mm"] = max(0.0, remaining_budget)
        best["endpoints"] = (endpoint_u, endpoint_v)
        selected.append(best)

    if len(selected) > REQUESTED_K:
        fail(f"{workload} B{budget:g} selected more than K=2 links")
    if routing_regenerations != len(selected):
        fail(f"Routing was not regenerated after every permanent topology change")

    final_metrics = evaluate(current_topology, workload_context)
    if final_metrics["max_load"] > baseline["max_link_load"] + EPS:
        fail(f"Final {workload} B{budget:g} topology exceeds baseline load cap")
    if selected:
        last = selected[-1]
        for candidate_field, final_field in (
            ("avg_latency", "avg_latency"),
            ("max_link_load", "max_load"),
            ("throughput", "throughput"),
            ("total_routed_traffic", "total_routed_traffic"),
            ("traffic_wire_cost", "traffic_wire_cost"),
        ):
            if not metrics_close(last[candidate_field], final_metrics[final_field]):
                fail(
                    f"Final metric mismatch after routing regeneration for "
                    f"{workload} B{budget:g}: {candidate_field}"
                )

    endpoints = [endpoint for link in selected for endpoint in link["endpoints"]]
    if len(endpoints) != len(set(endpoints)):
        fail(f"{workload} B{budget:g} reuses a shortcut PHY endpoint")
    if len(selected_pairs) != len(selected):
        fail(f"{workload} B{budget:g} contains duplicate selected links")

    return selected, final_metrics, cumulative_wire


def gain_percent(baseline_latency, candidate_latency):
    if baseline_latency <= 0:
        fail("Baseline average latency must be positive")
    return 100.0 * (baseline_latency - candidate_latency) / baseline_latency


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

    link_names = [f"{link['u']}<->{link['v']}" for link in selected]
    summary = {
        "workload": workload,
        "budget_mm": budget,
        "requested_k": REQUESTED_K,
        "actual_k": actual_k,
        "link_1": link_names[0] if len(link_names) >= 1 else "none",
        "link_2": link_names[1] if len(link_names) >= 2 else "none",
        "total_wire_length_mm": wire,
        "final_avg_latency": final_metrics["avg_latency"],
        "latency_gain_vs_baseline_percent": gain_percent(
            baseline["avg_latency"], final_metrics["avg_latency"]
        ),
        "final_max_link_load": final_metrics["max_load"],
        "analytical_throughput": final_metrics["throughput"],
        "total_routed_traffic": final_metrics["total_routed_traffic"],
        "traffic_wire_cost": final_metrics["traffic_wire_cost"],
    }
    return step_rows, summary


def print_configuration(workload, budget, baseline, selected, summary):
    print()
    print(f"Wire budget: {budget:.0f} mm")
    print(f"Baseline average latency: {baseline['avg_latency']:.3f}")
    print(f"Baseline maximum link load: {baseline['max_link_load']:.3f}")
    print(f"Requested K: {REQUESTED_K}")
    for step in range(1, REQUESTED_K + 1):
        print()
        if step > len(selected):
            print(f"Link {step}: none")
            continue
        link = selected[step - 1]
        print(f"Link {step}: {link['u']} <-> {link['v']}")
        print(f"PHYs: {link['phy_u']} <-> {link['phy_v']}")
        print(f"Length: {link['length_mm']:.3f} mm")
        print(f"Cumulative wire: {link['cumulative_wire_length_mm']:.3f} mm")
    print()
    print(f"Actual K: {summary['actual_k']}")
    print(f"Final average latency: {summary['final_avg_latency']:.3f}")
    print(
        f"Latency improvement: {summary['latency_gain_vs_baseline_percent']:.2f}%"
    )
    print(f"Maximum link load: {summary['final_max_link_load']:.3f}")
    print(f"Analytical throughput: {summary['analytical_throughput']:.3f}")


def validate_outputs(step_rows, summaries, physical):
    if len(summaries) != len(WORKLOADS) * len(BUDGETS):
        fail(f"Expected 16 configuration summaries; found {len(summaries)}")
    summary_groups = defaultdict(list)
    step_groups = defaultdict(list)
    for summary in summaries:
        summary_groups[(summary["workload"], summary["budget_mm"])].append(summary)
        if summary["actual_k"] > REQUESTED_K:
            fail(f"Summary actual_k exceeds 2: {summary}")
        if summary["total_wire_length_mm"] > summary["budget_mm"] + EPS:
            fail(f"Summary wire budget exceeded: {summary}")
    for row in step_rows:
        key = (row["workload"], row["budget_mm"])
        step_groups[key].append(row)
        if row["cumulative_wire_length_mm"] > row["budget_mm"] + EPS:
            fail(f"Step cumulative wire budget exceeded: {row}")
        if row["max_link_load_after_step"] > row["baseline_max_link_load"] + EPS:
            fail(f"Step baseline load cap exceeded: {row}")
        if tuple(sorted((row["u"], row["v"]))) in physical["mesh_edges"]:
            fail(f"Step selected an existing mesh edge: {row}")

    expected_keys = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(summary_groups) != expected_keys:
        fail("Configuration-summary workload/budget matrix is incomplete")
    if any(len(group) != 1 for group in summary_groups.values()):
        fail("A workload/budget has more than one configuration summary")
    for key in expected_keys:
        summary = summary_groups[key][0]
        rows = sorted(step_groups.get(key, []), key=lambda row: row["step"])
        if len(rows) != summary["actual_k"]:
            fail(f"Step count does not match actual_k for {key}")
        if [row["step"] for row in rows] != list(range(1, len(rows) + 1)):
            fail(f"Step numbering is invalid for {key}")
        pairs = [(row["u"], row["v"]) for row in rows]
        if len(pairs) != len(set(pairs)):
            fail(f"Duplicate selected link in {key}")
        endpoints = [
            endpoint
            for row in rows
            for endpoint in ((row["u"], row["phy_u"]), (row["v"], row["phy_v"]))
        ]
        if len(endpoints) != len(set(endpoints)):
            fail(f"Shortcut PHY endpoint reused in {key}")

    workload_counts = defaultdict(int)
    for summary in summaries:
        workload_counts[summary["workload"]] += 1
    for workload in WORKLOADS:
        if workload_counts[workload] != 4:
            fail(
                f"Expected four summaries for {workload}; "
                f"found {workload_counts[workload]}"
            )


def write_csv(path, fields, rows):
    try:
        with path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as error:
        fail(f"Cannot write output CSV {path}: {error}")


def main():
    physical = load_physical_context()
    print(f"Validated initial non-mesh candidate count: {physical['initial_candidate_count']}")
    all_step_rows = []
    all_summaries = []

    for workload in WORKLOADS:
        print()
        print("=" * 88)
        print(f"K=2 WORKLOAD-AWARE SELECTION — {workload.upper()}")
        print("=" * 88)
        workload_context = make_workload_context(physical, workload)
        baseline, _ = load_baseline(
            workload, workload_context, physical["topology"]
        )
        for budget in BUDGETS:
            selected, final_metrics, total_wire = greedy_configuration(
                workload,
                budget,
                physical,
                workload_context,
                baseline,
            )
            step_rows, summary = make_output_rows(
                workload,
                budget,
                selected,
                final_metrics,
                total_wire,
                baseline,
            )
            all_step_rows.extend(step_rows)
            all_summaries.append(summary)
            print_configuration(workload, budget, baseline, selected, summary)

    validate_outputs(all_step_rows, all_summaries, physical)
    write_csv(SELECTION_OUTPUT, SELECTION_FIELDS, all_step_rows)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, all_summaries)

    print()
    print(f"Selection rows: {len(all_step_rows)}")
    print(f"Configuration-summary rows: {len(all_summaries)}")
    print(f"Saved: {SELECTION_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print(f"Saved: {SUMMARY_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print("K=2 WORKLOAD-AWARE SELECTION COMPLETE")


if __name__ == "__main__":
    main()
