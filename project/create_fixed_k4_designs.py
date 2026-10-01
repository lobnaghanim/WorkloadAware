"""Generate fixed K=4 workload designs from finalized uniform selections."""

import copy
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

import create_fixed_k2_designs as proven


SELECTION_FILE = REPO_ROOT / "results/k4_fixed_selections.csv"
SUMMARY_FILE = REPO_ROOT / "results/k4_fixed_summary.csv"
BASE_DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
REQUESTED_K = 4
EPS = 1e-9
STEP_COLUMNS = {
    "budget_mm", "requested_k", "actual_k", "step", "u", "v",
    "phy_u", "phy_v", "link_length_mm", "cumulative_wire_length_mm",
}
SUMMARY_COLUMNS = {
    "budget_mm", "requested_k", "actual_k", "total_wire_length_mm",
    "final_reference_avg_latency", "final_reference_max_link_load",
    "analytical_throughput", "total_routed_traffic", "traffic_wire_cost",
    *(f"link_{index}" for index in range(1, REQUESTED_K + 1)),
    *(f"link_{index}_phy_pair" for index in range(1, REQUESTED_K + 1)),
    *(f"link_{index}_length_mm" for index in range(1, REQUESTED_K + 1)),
}


def fail(message):
    raise RuntimeError(message)


def optional_number(value, description):
    return proven.number(value, description, optional=True)


def load_inputs():
    raw_steps = proven.read_csv(
        SELECTION_FILE, STEP_COLUMNS, "fixed K=4 selection CSV"
    )
    raw_summaries = proven.read_csv(
        SUMMARY_FILE, SUMMARY_COLUMNS, "fixed K=4 summary CSV"
    )
    steps = defaultdict(list)
    for row_number, row in enumerate(raw_steps, start=2):
        budget = proven.integer(
            row["budget_mm"], f"selection budget on row {row_number}"
        )
        steps[budget].append(
            {
                "budget_mm": budget,
                "requested_k": proven.integer(
                    row["requested_k"], f"requested_k on row {row_number}"
                ),
                "actual_k": proven.integer(
                    row["actual_k"], f"actual_k on row {row_number}"
                ),
                "step": proven.integer(row["step"], f"step on row {row_number}"),
                "u": proven.integer(row["u"], f"u on row {row_number}"),
                "v": proven.integer(row["v"], f"v on row {row_number}"),
                "phy_u": proven.integer(
                    row["phy_u"], f"phy_u on row {row_number}"
                ),
                "phy_v": proven.integer(
                    row["phy_v"], f"phy_v on row {row_number}"
                ),
                "link_length_mm": proven.number(
                    row["link_length_mm"], f"link length on row {row_number}"
                ),
                "cumulative_wire_length_mm": proven.number(
                    row["cumulative_wire_length_mm"],
                    f"cumulative wire on row {row_number}",
                ),
            }
        )

    summaries = {}
    for row_number, row in enumerate(raw_summaries, start=2):
        budget = proven.integer(
            row["budget_mm"], f"summary budget on row {row_number}"
        )
        if budget in summaries:
            fail(f"Duplicate fixed K=4 summary for B{budget}")
        summary = {
            "budget_mm": budget,
            "requested_k": proven.integer(
                row["requested_k"], f"summary requested_k on row {row_number}"
            ),
            "actual_k": proven.integer(
                row["actual_k"], f"summary actual_k on row {row_number}"
            ),
            "total_wire_length_mm": proven.number(
                row["total_wire_length_mm"], f"summary wire on row {row_number}"
            ),
            "final_reference_avg_latency": proven.number(
                row["final_reference_avg_latency"],
                f"summary latency on row {row_number}",
            ),
            "final_reference_max_link_load": proven.number(
                row["final_reference_max_link_load"],
                f"summary load on row {row_number}",
            ),
            "analytical_throughput": proven.number(
                row["analytical_throughput"],
                f"summary throughput on row {row_number}",
            ),
            "total_routed_traffic": proven.number(
                row["total_routed_traffic"],
                f"summary traffic on row {row_number}",
            ),
            "traffic_wire_cost": proven.number(
                row["traffic_wire_cost"], f"summary cost on row {row_number}"
            ),
        }
        for index in range(1, REQUESTED_K + 1):
            summary[f"link_{index}"] = row[f"link_{index}"].strip()
            summary[f"link_{index}_phy_pair"] = row[
                f"link_{index}_phy_pair"
            ].strip()
            summary[f"link_{index}_length_mm"] = optional_number(
                row[f"link_{index}_length_mm"],
                f"link_{index}_length_mm on row {row_number}",
            )
        summaries[budget] = summary

    if set(summaries) != set(BUDGETS) or any(
        budget not in BUDGETS for budget in steps
    ):
        fail(f"Fixed K=4 inputs must contain exactly budgets {list(BUDGETS)}")

    for budget in BUDGETS:
        summary = summaries[budget]
        selected = sorted(steps.get(budget, []), key=lambda row: row["step"])
        actual_k = summary["actual_k"]
        if summary["requested_k"] != REQUESTED_K or not 0 <= actual_k <= REQUESTED_K:
            fail(f"Invalid requested_k/actual_k for B{budget}")
        if len(selected) != actual_k or [row["step"] for row in selected] != list(
            range(1, actual_k + 1)
        ):
            fail(f"Selection steps disagree with actual_k for B{budget}")
        if any(
            row["requested_k"] != REQUESTED_K or row["actual_k"] != actual_k
            for row in selected
        ):
            fail(f"Selection K values disagree with summary for B{budget}")

        total = 0.0
        for index in range(1, REQUESTED_K + 1):
            if index <= actual_k:
                row = selected[index - 1]
                if summary[f"link_{index}"] != f"{row['u']}<->{row['v']}":
                    fail(f"Selected link {index} disagrees with summary for B{budget}")
                if summary[f"link_{index}_phy_pair"] != (
                    f"{row['phy_u']}<->{row['phy_v']}"
                ):
                    fail(
                        f"Selected PHY pair {index} disagrees with summary for B{budget}"
                    )
                if not math.isclose(
                    summary[f"link_{index}_length_mm"],
                    row["link_length_mm"],
                    rel_tol=EPS,
                    abs_tol=EPS,
                ):
                    fail(
                        f"Selected length {index} disagrees with summary for B{budget}"
                    )
                total += row["link_length_mm"]
            elif (
                summary[f"link_{index}"] != ""
                or summary[f"link_{index}_phy_pair"] != ""
                or summary[f"link_{index}_length_mm"] is not None
            ):
                fail(f"Unused summary link {index} is not blank for B{budget}")

        if not math.isclose(
            total, summary["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS
        ):
            fail(f"Selection wire length disagrees with summary for B{budget}")
        if total > budget + EPS:
            fail(f"Fixed K=4 selection exceeds total wire budget B{budget}")
        if selected and not math.isclose(
            selected[-1]["cumulative_wire_length_mm"],
            total,
            rel_tol=EPS,
            abs_tol=EPS,
        ):
            fail(f"Final cumulative wire disagrees with summary for B{budget}")
    return steps, summaries


def verify_reference_metrics(topology, summary, baseline):
    traffic, _ = proven.load_uniform_reference(16)
    context = {
        "design": baseline["base_design"],
        "chiplets": baseline["chiplets"],
        "placement": baseline["placement"],
        "packaging": baseline["packaging"],
        "technologies": baseline["technologies"],
        "traffic": traffic,
    }
    actual = proven.evaluate(topology, context)
    for actual_field, summary_field in (
        ("avg_latency", "final_reference_avg_latency"),
        ("max_load", "final_reference_max_link_load"),
        ("throughput", "analytical_throughput"),
        ("total_routed_traffic", "total_routed_traffic"),
        ("traffic_wire_cost", "traffic_wire_cost"),
    ):
        if not math.isclose(
            actual[actual_field], summary[summary_field], rel_tol=EPS, abs_tol=EPS
        ):
            fail(
                f"Generated {actual_field} disagrees with fixed K=4 summary "
                f"for B{summary['budget_mm']}"
            )


def canonical_link(row):
    if row["u"] <= row["v"]:
        return row["u"], row["v"], row["phy_u"], row["phy_v"]
    return row["v"], row["u"], row["phy_v"], row["phy_u"]


def physical_signature(selected):
    return tuple(sorted(canonical_link(row) for row in selected))


def create_designs():
    steps, summaries = load_inputs()
    baseline = proven.load_baseline()
    if len(baseline["topology"]) != 24:
        fail("Physical baseline must contain exactly 24 links")

    created = []
    expected_designs = set()
    topology_signatures = set()
    for budget in BUDGETS:
        summary = summaries[budget]
        selected = sorted(steps.get(budget, []), key=lambda row: row["step"])
        topology = copy.deepcopy(baseline["topology"])
        proven.add_selected_links(topology, selected, summary, baseline, budget)
        routing = proven.generate_splif_routing(
            baseline["chiplets"], baseline["placement"], topology
        )
        if routing.get("type") != "default" or not isinstance(
            routing.get("table"), dict
        ):
            fail(f"Final SPLIF routing regeneration failed for B{budget}")
        verify_reference_metrics(topology, summary, baseline)

        topology_path = (
            REPO_ROOT
            / f"inputs/topologies/topology_project_fixed_k4_B{budget}mm.json"
        )
        routing_path = (
            REPO_ROOT
            / f"inputs/routing_tables/routing_table_project_fixed_k4_B{budget}mm.json"
        )
        proven.hlp.write_json(str(topology_path), topology)
        proven.hlp.write_json(str(routing_path), routing)
        topology_signatures.add(physical_signature(selected))

        budget_refs = set()
        for workload in WORKLOADS:
            unit_traffic = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
            chiplet_traffic = (
                f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
            )
            if not (REPO_ROOT / unit_traffic).is_file() or not (
                REPO_ROOT / chiplet_traffic
            ).is_file():
                fail(f"Missing fixed workload traffic for {workload}")

            name = f"project_fixed_k4_{workload}_B{budget}mm"
            design_path = REPO_ROOT / f"inputs/designs/design_{name}.json"
            design = copy.deepcopy(baseline["base_design"])
            design["design_name"] = name
            design["topology"] = proven.relative(topology_path)
            design["routing_table"] = proven.relative(routing_path)
            design["traffic_by_unit"] = unit_traffic
            design["traffic_by_chiplet"] = chiplet_traffic
            proven.hlp.write_json(str(design_path), design)
            budget_refs.add((design["topology"], design["routing_table"]))
            created.append(design_path)
            expected_designs.add(design_path)
            print(
                f"Created {proven.relative(design_path)} with "
                f"actual_k={summary['actual_k']} and {len(topology)} links"
            )
        if len(budget_refs) != 1:
            fail(f"Fixed topology/routing changes across workloads for B{budget}")

    actual_designs = set(
        (REPO_ROOT / "inputs/designs").glob(
            "design_project_fixed_k4_*_B*mm.json"
        )
    )
    if len(created) != 16 or actual_designs != expected_designs:
        fail(
            "Expected exactly 16 fixed K=4 designs; "
            f"created={len(created)}, files={len(actual_designs)}"
        )
    print("FIXED K=4 DESIGNS COMPLETE")
    print(f"Logical designs created: {len(created)}")
    print(f"Unique physical topologies: {len(topology_signatures)}")


if __name__ == "__main__":
    create_designs()
