"""Generate workload-aware K=4 designs from finalized greedy selections."""

import copy
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

import create_k2_workload_aware_designs as proven


SELECTION_FILE = REPO_ROOT / "results/k4_workload_aware_selections.csv"
SUMMARY_FILE = REPO_ROOT / "results/k4_workload_aware_summary.csv"
BASE_DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
REQUESTED_K = 4
EPS = 1e-9
STEP_COLUMNS = {
    "workload", "budget_mm", "requested_k", "actual_k", "step", "u", "v",
    "phy_u", "phy_v", "link_length_mm", "cumulative_wire_length_mm",
}
SUMMARY_COLUMNS = {
    "workload", "budget_mm", "requested_k", "actual_k", "total_wire_length_mm",
    "final_avg_latency", "final_max_link_load", "analytical_throughput",
    "total_routed_traffic", "traffic_wire_cost",
    *(f"link_{index}" for index in range(1, REQUESTED_K + 1)),
    *(f"link_{index}_phy_pair" for index in range(1, REQUESTED_K + 1)),
    *(f"link_{index}_length_mm" for index in range(1, REQUESTED_K + 1)),
}


def fail(message):
    raise RuntimeError(message)


def optional_number(value, description):
    if value is None or value == "":
        return None
    return proven.number(value, description)


def load_inputs():
    raw_steps = proven.load_csv(SELECTION_FILE, STEP_COLUMNS, "K=4 selection CSV")
    raw_summaries = proven.load_csv(SUMMARY_FILE, SUMMARY_COLUMNS, "K=4 summary CSV")
    steps = defaultdict(list)
    summaries = {}
    for row_number, row in enumerate(raw_steps, start=2):
        workload = row["workload"].strip()
        budget = proven.integer(row["budget_mm"], f"selection budget on row {row_number}")
        steps[(workload, budget)].append(
            {
                "workload": workload,
                "budget_mm": budget,
                "requested_k": proven.integer(row["requested_k"], f"requested_k on row {row_number}"),
                "actual_k": proven.integer(row["actual_k"], f"actual_k on row {row_number}"),
                "step": proven.integer(row["step"], f"step on row {row_number}"),
                "u": proven.integer(row["u"], f"u on row {row_number}"),
                "v": proven.integer(row["v"], f"v on row {row_number}"),
                "phy_u": proven.integer(row["phy_u"], f"phy_u on row {row_number}"),
                "phy_v": proven.integer(row["phy_v"], f"phy_v on row {row_number}"),
                "link_length_mm": proven.number(row["link_length_mm"], f"link length on row {row_number}"),
                "cumulative_wire_length_mm": proven.number(
                    row["cumulative_wire_length_mm"], f"cumulative wire on row {row_number}"
                ),
            }
        )

    for row_number, row in enumerate(raw_summaries, start=2):
        workload = row["workload"].strip()
        budget = proven.integer(row["budget_mm"], f"summary budget on row {row_number}")
        key = (workload, budget)
        if key in summaries:
            fail(f"Duplicate K=4 summary configuration: {key}")
        summary = {
            "workload": workload,
            "budget_mm": budget,
            "requested_k": proven.integer(row["requested_k"], f"summary requested_k on row {row_number}"),
            "actual_k": proven.integer(row["actual_k"], f"summary actual_k on row {row_number}"),
            "total_wire_length_mm": proven.number(row["total_wire_length_mm"], f"summary wire on row {row_number}"),
            "final_avg_latency": proven.number(row["final_avg_latency"], f"summary latency on row {row_number}"),
            "final_max_link_load": proven.number(row["final_max_link_load"], f"summary load on row {row_number}"),
            "analytical_throughput": proven.number(row["analytical_throughput"], f"summary throughput on row {row_number}"),
            "total_routed_traffic": proven.number(row["total_routed_traffic"], f"summary traffic on row {row_number}"),
            "traffic_wire_cost": proven.number(row["traffic_wire_cost"], f"summary cost on row {row_number}"),
        }
        for index in range(1, REQUESTED_K + 1):
            summary[f"link_{index}"] = row[f"link_{index}"].strip()
            summary[f"link_{index}_phy_pair"] = row[f"link_{index}_phy_pair"].strip()
            summary[f"link_{index}_length_mm"] = optional_number(
                row[f"link_{index}_length_mm"], f"link_{index}_length_mm on row {row_number}"
            )
        summaries[key] = summary

    expected = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(summaries) != expected or any(key not in expected for key in steps):
        fail("K=4 input workload/budget matrix must contain exactly 16 configurations")
    for key in expected:
        summary = summaries[key]
        selected = sorted(steps.get(key, []), key=lambda row: row["step"])
        actual_k = summary["actual_k"]
        if summary["requested_k"] != REQUESTED_K or not 0 <= actual_k <= REQUESTED_K:
            fail(f"Invalid requested_k/actual_k for {key}")
        if len(selected) != actual_k or [row["step"] for row in selected] != list(range(1, actual_k + 1)):
            fail(f"Selection steps disagree with actual_k for {key}")
        if any(row["requested_k"] != REQUESTED_K or row["actual_k"] != actual_k for row in selected):
            fail(f"Selection K values disagree with summary for {key}")
        total = 0.0
        for index in range(1, REQUESTED_K + 1):
            if index <= actual_k:
                row = selected[index - 1]
                if summary[f"link_{index}"] != f"{row['u']}<->{row['v']}":
                    fail(f"Selected link {index} disagrees with summary for {key}")
                if summary[f"link_{index}_phy_pair"] != f"{row['phy_u']}<->{row['phy_v']}":
                    fail(f"Selected PHY pair {index} disagrees with summary for {key}")
                if not math.isclose(summary[f"link_{index}_length_mm"], row["link_length_mm"], rel_tol=EPS, abs_tol=EPS):
                    fail(f"Selected length {index} disagrees with summary for {key}")
                total += row["link_length_mm"]
            elif summary[f"link_{index}"] != "none" or summary[f"link_{index}_phy_pair"] != "" or summary[f"link_{index}_length_mm"] is not None:
                fail(f"Unused link fields are not empty for {key} link {index}")
        if not math.isclose(total, summary["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Selected wire total disagrees with summary for {key}")
        if total > key[1] + EPS:
            fail(f"Selected K=4 links exceed wire budget for {key}")
        if selected and not math.isclose(selected[-1]["cumulative_wire_length_mm"], total, rel_tol=EPS, abs_tol=EPS):
            fail(f"Final cumulative wire disagrees with summary for {key}")
    return steps, summaries


def metrics_match(actual, summary, key):
    for actual_field, summary_field in (
        ("avg_latency", "final_avg_latency"),
        ("max_load", "final_max_link_load"),
        ("throughput", "analytical_throughput"),
        ("total_routed_traffic", "total_routed_traffic"),
        ("traffic_wire_cost", "traffic_wire_cost"),
    ):
        if not math.isclose(actual[actual_field], summary[summary_field], rel_tol=EPS, abs_tol=EPS):
            fail(f"Generated {actual_field} disagrees with K=4 summary for {key}")


def create_designs():
    steps, summaries = load_inputs()
    context = proven.load_physical_baseline()
    if len(context["topology"]) != 24:
        fail("Physical baseline must contain exactly 24 links")
    created = []
    expected_paths = set()
    for workload in WORKLOADS:
        traffic_unit = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
        traffic_chiplet = f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
        if not (REPO_ROOT / traffic_unit).is_file() or not (REPO_ROOT / traffic_chiplet).is_file():
            fail(f"Missing traffic input for workload {workload}")
        workload_context = {
            "design": copy.deepcopy(context["base_design"]),
            "chiplets": context["chiplets"],
            "placement": context["placement"],
            "packaging": context["packaging"],
            "technologies": context["technologies"],
            "traffic": proven.hlp.read_json(str(REPO_ROOT / traffic_chiplet)),
        }
        workload_context["design"]["traffic_by_unit"] = traffic_unit
        workload_context["design"]["traffic_by_chiplet"] = traffic_chiplet
        for budget in BUDGETS:
            key = (workload, budget)
            summary = summaries[key]
            selected = sorted(steps.get(key, []), key=lambda row: row["step"])
            topology = copy.deepcopy(context["topology"])
            proven.validate_and_add_links(topology, selected, summary, context, key)
            routing = proven.generate_splif_routing(context["chiplets"], context["placement"], topology)
            if routing.get("type") != "default" or not isinstance(routing.get("table"), dict):
                fail(f"Final SPLIF routing regeneration failed for {key}")
            metrics_match(proven.evaluate(topology, workload_context), summary, key)

            name = f"project_aware_k4_{workload}_B{budget}mm"
            topology_path = REPO_ROOT / f"inputs/topologies/topology_{name}.json"
            routing_path = REPO_ROOT / f"inputs/routing_tables/routing_table_{name}.json"
            design_path = REPO_ROOT / f"inputs/designs/design_{name}.json"
            proven.hlp.write_json(str(topology_path), topology)
            proven.hlp.write_json(str(routing_path), routing)
            design = copy.deepcopy(context["base_design"])
            design["design_name"] = name
            design["topology"] = proven.relative(topology_path)
            design["routing_table"] = proven.relative(routing_path)
            design["traffic_by_unit"] = traffic_unit
            design["traffic_by_chiplet"] = traffic_chiplet
            proven.hlp.write_json(str(design_path), design)
            created.append(design_path)
            expected_paths.add(design_path)
            print(f"Created {proven.relative(design_path)} with actual_k={summary['actual_k']} and {len(topology)} links")

    actual_paths = set((REPO_ROOT / "inputs/designs").glob("design_project_aware_k4_*_B*mm.json"))
    if len(created) != 16 or actual_paths != expected_paths:
        fail(f"Expected exactly 16 workload-aware K=4 designs; created={len(created)}, files={len(actual_paths)}")
    print("K=4 WORKLOAD-AWARE DESIGNS COMPLETE")
    print(f"Designs created: {len(created)}")


if __name__ == "__main__":
    create_designs()
