"""Run analytical workload-aware optimization across deterministic seeds."""

import copy
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
import select_k2_workload_aware as proven


WORKLOADS = ("permutation", "hotspot")
SEEDS = (42, 43, 44, 45, 46)
BUDGETS = (5.0, 15.0, 25.0, 45.0)
REQUESTED_K_VALUES = (1, 2, 4)
EPS = proven.EPS
SUBSTANTIAL_GAIN_RANGE_PERCENT = 5.0
RESULTS_OUTPUT = REPO_ROOT / "results/multiseed_results.csv"
SUMMARY_OUTPUT = REPO_ROOT / "results/multiseed_summary.csv"
RESULT_FIELDS = (
    "workload",
    "seed",
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
    "final_throughput",
    "baseline_total_routed_traffic",
    "baseline_traffic_wire_cost",
    "total_routed_traffic",
    "traffic_wire_cost",
)
SUMMARY_FIELDS = (
    "workload",
    "budget_mm",
    "requested_k",
    "seed_count",
    "mean_baseline_latency",
    "mean_final_latency",
    "std_final_latency",
    "min_final_latency",
    "max_final_latency",
    "mean_latency_gain_percent",
    "std_latency_gain_percent",
    "min_latency_gain_percent",
    "max_latency_gain_percent",
    "mean_actual_k",
    "min_actual_k",
    "max_actual_k",
    "mean_total_wire_length_mm",
    "std_total_wire_length_mm",
    "unique_selected_topologies",
    "most_common_selected_topology",
    "most_common_topology_seed_count",
    "most_common_topology_seed_fraction",
)


def fail(message):
    raise RuntimeError(message)


def make_context(physical, workload, seed):
    chiplet_path = (
        REPO_ROOT
        / f"inputs/traffic_by_chiplet/traffic_project_{workload}_seed{seed}.json"
    )
    unit_path = (
        REPO_ROOT / f"inputs/traffic_by_unit/traffic_project_{workload}_seed{seed}.json"
    )
    if not chiplet_path.is_file() or not unit_path.is_file():
        fail(f"Missing multi-seed traffic for {workload} seed {seed}")
    design = copy.deepcopy(physical["design"])
    design["design_name"] = f"project_multiseed_{workload}_seed{seed}"
    design["traffic_by_chiplet"] = chiplet_path.relative_to(REPO_ROOT).as_posix()
    design["traffic_by_unit"] = unit_path.relative_to(REPO_ROOT).as_posix()
    return {
        "design": design,
        "chiplets": physical["chiplets"],
        "placement": physical["placement"],
        "topology": physical["topology"],
        "packaging": physical["packaging"],
        "technologies": physical["technologies"],
        "traffic": hlp.read_json(str(chiplet_path)),
    }


def baseline_metrics(physical, context):
    metrics = proven.evaluate(physical["topology"], context)
    values = {
        "avg_latency": metrics["avg_latency"],
        "max_link_load": metrics["max_load"],
        "throughput": metrics["throughput"],
        "total_routed_traffic": metrics["total_routed_traffic"],
        "traffic_wire_cost": metrics["traffic_wire_cost"],
    }
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values.values()
    ):
        fail("Baseline contains a non-finite/non-numeric metric")
    if values["avg_latency"] <= 0:
        fail("Baseline average latency must be positive")
    return values


def greedy_configuration(workload, seed, budget, requested_k, physical, context, baseline):
    """Use the proven dynamic evaluator for one to four greedy iterations."""
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

    for step in range(1, requested_k + 1):
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
                f"{workload} seed {seed} B{budget:g} K{requested_k} evaluated "
                f"{considered_pairs} initial pairs, expected 96"
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
        pair = tuple(sorted((best["u"], best["v"])))
        endpoints = (
            (best["u"], best["phy_u"]),
            (best["v"], best["phy_v"]),
        )
        if pair in physical["mesh_edges"] or pair in selected_pairs:
            fail(f"Optimizer attempted a mesh/duplicate shortcut {pair}")
        if any(phy in occupied[chiplet] for chiplet, phy in endpoints):
            fail(f"Optimizer attempted PHY reuse for {pair}")

        current_topology.append(copy.deepcopy(best["link"]))
        selected_pairs.add(pair)
        for chiplet, phy in endpoints:
            occupied[chiplet].add(phy)
        cumulative_wire += best["length_mm"]
        remaining_budget = budget - cumulative_wire
        if cumulative_wire > budget + EPS or remaining_budget < -EPS:
            fail(f"{workload} seed {seed} B{budget:g} exceeded total wire budget")
        if best["max_link_load"] > baseline["max_link_load"] + EPS:
            fail(f"{workload} seed {seed} B{budget:g} exceeded original load cap")

        routing = proven.generate_splif_routing(
            physical["chiplets"], physical["placement"], current_topology
        )
        if not isinstance(routing, dict) or not isinstance(routing.get("table"), dict):
            fail(
                f"Routing regeneration failed for {workload} seed {seed} "
                f"B{budget:g} K{requested_k} step {step}"
            )
        routing_regenerations += 1
        best["step"] = step
        best["endpoints"] = endpoints
        selected.append(best)

    if len(selected) > requested_k:
        fail("Optimizer selected more links than requested K")
    if routing_regenerations != len(selected):
        fail("Routing was not regenerated after every topology change")
    if len(current_topology) != proven.EXPECTED_MESH_LINKS + len(selected):
        fail("Final topology does not contain 24 + actual_k links")

    final = proven.evaluate(current_topology, context)
    if final["max_load"] > baseline["max_link_load"] + EPS:
        fail("Final topology exceeds its workload+seed baseline load cap")
    if selected:
        last = selected[-1]
        for selected_field, final_field in (
            ("avg_latency", "avg_latency"),
            ("max_link_load", "max_load"),
            ("throughput", "throughput"),
            ("total_routed_traffic", "total_routed_traffic"),
            ("traffic_wire_cost", "traffic_wire_cost"),
        ):
            if not proven.metrics_close(last[selected_field], final[final_field]):
                fail(f"Final metric mismatch: {selected_field}")
    endpoints = [endpoint for link in selected for endpoint in link["endpoints"]]
    if len(endpoints) != len(set(endpoints)):
        fail("Selected topology reuses a PHY endpoint")
    return selected, final, cumulative_wire


def gain_percent(baseline_latency, final_latency):
    return 100.0 * (baseline_latency - final_latency) / baseline_latency


def make_result_row(workload, seed, budget, requested_k, selected, wire, baseline, final):
    row = {
        "workload": workload,
        "seed": seed,
        "budget_mm": budget,
        "requested_k": requested_k,
        "actual_k": len(selected),
        "total_wire_length_mm": wire,
        "baseline_avg_latency": baseline["avg_latency"],
        "final_avg_latency": final["avg_latency"],
        "latency_gain_vs_baseline_percent": gain_percent(
            baseline["avg_latency"], final["avg_latency"]
        ),
        "baseline_max_link_load": baseline["max_link_load"],
        "final_max_link_load": final["max_load"],
        "baseline_throughput": baseline["throughput"],
        "final_throughput": final["throughput"],
        "baseline_total_routed_traffic": baseline["total_routed_traffic"],
        "baseline_traffic_wire_cost": baseline["traffic_wire_cost"],
        "total_routed_traffic": final["total_routed_traffic"],
        "traffic_wire_cost": final["traffic_wire_cost"],
        "_endpoints": tuple(endpoint for link in selected for endpoint in link["endpoints"]),
    }
    for index in range(1, 5):
        if index <= len(selected):
            link = selected[index - 1]
            row[f"link_{index}"] = f"{link['u']}<->{link['v']}"
            row[f"link_{index}_phy_pair"] = f"{link['phy_u']}<->{link['phy_v']}"
            row[f"link_{index}_length_mm"] = link["length_mm"]
        else:
            row[f"link_{index}"] = None
            row[f"link_{index}_phy_pair"] = None
            row[f"link_{index}_length_mm"] = None
    return row


def canonical_topology(row):
    links = []
    for index in range(1, row["actual_k"] + 1):
        u, v = (int(value) for value in row[f"link_{index}"].split("<->"))
        links.append(tuple(sorted((u, v))))
    links.sort()
    return ";".join(f"{u}<->{v}" for u, v in links) if links else "none"


def build_summary(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["workload"], row["budget_mm"], row["requested_k"])].append(row)
    summaries = []
    for workload in WORKLOADS:
        for budget in BUDGETS:
            for requested_k in REQUESTED_K_VALUES:
                group = grouped[(workload, budget, requested_k)]
                if len(group) != len(SEEDS):
                    fail(f"Robustness group does not contain five seeds")
                final_latencies = [row["final_avg_latency"] for row in group]
                gains = [row["latency_gain_vs_baseline_percent"] for row in group]
                actual_ks = [row["actual_k"] for row in group]
                wires = [row["total_wire_length_mm"] for row in group]
                topologies = [canonical_topology(row) for row in group]
                counts = Counter(topologies)
                most_common, common_count = sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )[0]
                summaries.append(
                    {
                        "workload": workload,
                        "budget_mm": budget,
                        "requested_k": requested_k,
                        "seed_count": len(group),
                        "mean_baseline_latency": statistics.mean(
                            row["baseline_avg_latency"] for row in group
                        ),
                        "mean_final_latency": statistics.mean(final_latencies),
                        # These five prescribed seeds are the complete analyzed set.
                        "std_final_latency": statistics.pstdev(final_latencies),
                        "min_final_latency": min(final_latencies),
                        "max_final_latency": max(final_latencies),
                        "mean_latency_gain_percent": statistics.mean(gains),
                        "std_latency_gain_percent": statistics.pstdev(gains),
                        "min_latency_gain_percent": min(gains),
                        "max_latency_gain_percent": max(gains),
                        "mean_actual_k": statistics.mean(actual_ks),
                        "min_actual_k": min(actual_ks),
                        "max_actual_k": max(actual_ks),
                        "mean_total_wire_length_mm": statistics.mean(wires),
                        "std_total_wire_length_mm": statistics.pstdev(wires),
                        "unique_selected_topologies": len(counts),
                        "most_common_selected_topology": most_common,
                        "most_common_topology_seed_count": common_count,
                        "most_common_topology_seed_fraction": common_count / len(group),
                    }
                )
    return summaries


def validate_results(rows, summaries, physical):
    if len(WORKLOADS) != 2 or len(SEEDS) != 5 or len(BUDGETS) != 4:
        fail("Internal workload/seed/budget dimensions are invalid")
    if len(REQUESTED_K_VALUES) != 3 or len(rows) != 120:
        fail(f"Detailed results must contain exactly 120 rows; found {len(rows)}")
    if len(summaries) != 24:
        fail(f"Robustness summary must contain exactly 24 rows; found {len(summaries)}")

    keys = set()
    baselines = {}
    for row in rows:
        key = (row["workload"], row["seed"], row["budget_mm"], row["requested_k"])
        if key in keys:
            fail(f"Duplicate detailed result {key}")
        keys.add(key)
        if row["actual_k"] > row["requested_k"]:
            fail(f"actual_k exceeds requested_k for {key}")
        if row["total_wire_length_mm"] > row["budget_mm"] + EPS:
            fail(f"Wire budget exceeded for {key}")
        if row["final_max_link_load"] > row["baseline_max_link_load"] + EPS:
            fail(f"Baseline load cap exceeded for {key}")
        if len(row["_endpoints"]) != len(set(row["_endpoints"])):
            fail(f"PHY endpoint reuse for {key}")
        baseline_key = (row["workload"], row["seed"])
        signature = (
            row["baseline_avg_latency"],
            row["baseline_max_link_load"],
            row["baseline_throughput"],
            row["baseline_total_routed_traffic"],
            row["baseline_traffic_wire_cost"],
        )
        previous = baselines.setdefault(baseline_key, signature)
        if previous != signature:
            fail(f"Cross-configuration baseline mismatch for {baseline_key}")
    if len(keys) != 120 or len(baselines) != len(WORKLOADS) * len(SEEDS):
        fail("Detailed result matrix or workload+seed baseline matrix is incomplete")

    summary_keys = {
        (row["workload"], row["budget_mm"], row["requested_k"])
        for row in summaries
    }
    expected_summary = {
        (workload, budget, requested_k)
        for workload in WORKLOADS
        for budget in BUDGETS
        for requested_k in REQUESTED_K_VALUES
    }
    if summary_keys != expected_summary or len(summary_keys) != len(summaries):
        fail("Robustness summary matrix is incomplete or duplicated")
    if physical["node_count"] != 16 or len(physical["topology"]) != 24:
        fail("Analysis did not use the original 16-chiplet, 24-link baseline")


def deterministic_probe(rows, physical):
    expected = next(
        row
        for row in rows
        if row["workload"] == "permutation"
        and row["seed"] == 42
        and row["budget_mm"] == 5.0
        and row["requested_k"] == 4
    )
    context = make_context(physical, "permutation", 42)
    baseline = baseline_metrics(physical, context)
    selected, final, wire = greedy_configuration(
        "permutation", 42, 5.0, 4, physical, context, baseline
    )
    repeated = make_result_row(
        "permutation", 42, 5.0, 4, selected, wire, baseline, final
    )
    for field in RESULT_FIELDS:
        left, right = expected[field], repeated[field]
        if isinstance(left, float):
            if not math.isclose(left, right, rel_tol=EPS, abs_tol=EPS):
                fail(f"Determinism probe mismatch in {field}")
        elif left != right:
            fail(f"Determinism probe mismatch in {field}")
    print("Determinism probe passed: permutation seed42 B5 K4")


def write_csv(path, fields, rows):
    try:
        with path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as error:
        fail(f"Cannot write multi-seed output {path}: {error}")


def print_summary(summaries):
    print(
        "WORKLOAD | BUDGET | K | MEAN_GAIN% | STD_GAIN% | MIN_GAIN% | "
        "MAX_GAIN% | MEAN_ACTUAL_K | UNIQUE_TOPOLOGIES | MOST_COMMON_TOPOLOGY"
    )
    for row in summaries:
        print(
            f"{row['workload']} | {row['budget_mm']:.0f} | {row['requested_k']} | "
            f"{row['mean_latency_gain_percent']:.3f} | "
            f"{row['std_latency_gain_percent']:.3f} | "
            f"{row['min_latency_gain_percent']:.3f} | "
            f"{row['max_latency_gain_percent']:.3f} | "
            f"{row['mean_actual_k']:.3f} | "
            f"{row['unique_selected_topologies']} | "
            f"{row['most_common_selected_topology']}"
        )

    print("\nREPORTED SEED VARIATION")
    reported = 0
    for row in summaries:
        reasons = []
        gain_range = row["max_latency_gain_percent"] - row["min_latency_gain_percent"]
        if gain_range >= SUBSTANTIAL_GAIN_RANGE_PERCENT:
            reasons.append(
                f"gain range {gain_range:.3f} percentage points "
                f"(threshold {SUBSTANTIAL_GAIN_RANGE_PERCENT:.1f})"
            )
        if row["min_actual_k"] != row["max_actual_k"]:
            reasons.append(
                f"actual_k range {row['min_actual_k']}..{row['max_actual_k']}"
            )
        if row["unique_selected_topologies"] == len(SEEDS):
            reasons.append("selected topology differs for every seed")
        if reasons:
            reported += 1
            print(
                f"{row['workload']} B{row['budget_mm']:.0f} K{row['requested_k']}: "
                + "; ".join(reasons)
            )
    if reported == 0:
        print("No configuration met the reporting conditions.")


def main():
    physical = proven.load_physical_context()
    rows = []
    for workload in WORKLOADS:
        for seed in SEEDS:
            context = make_context(physical, workload, seed)
            baseline = baseline_metrics(physical, context)
            print(
                f"Optimizing {workload} seed {seed}: "
                f"baseline latency={baseline['avg_latency']:.3f}, "
                f"max load={baseline['max_link_load']:.3f}"
            )
            for budget in BUDGETS:
                for requested_k in REQUESTED_K_VALUES:
                    selected, final, wire = greedy_configuration(
                        workload,
                        seed,
                        budget,
                        requested_k,
                        physical,
                        context,
                        baseline,
                    )
                    rows.append(
                        make_result_row(
                            workload,
                            seed,
                            budget,
                            requested_k,
                            selected,
                            wire,
                            baseline,
                            final,
                        )
                    )
    summaries = build_summary(rows)
    validate_results(rows, summaries, physical)
    deterministic_probe(rows, physical)
    write_csv(RESULTS_OUTPUT, RESULT_FIELDS, rows)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summaries)
    print_summary(summaries)
    print("\nMULTI-SEED ROBUSTNESS ANALYSIS COMPLETE")
    print(f"Detailed rows: {len(rows)}")
    print(f"Summary rows: {len(summaries)}")
    print(f"Detailed CSV: {RESULTS_OUTPUT.relative_to(REPO_ROOT).as_posix()}")
    print(f"Summary CSV: {SUMMARY_OUTPUT.relative_to(REPO_ROOT).as_posix()}")


if __name__ == "__main__":
    main()
