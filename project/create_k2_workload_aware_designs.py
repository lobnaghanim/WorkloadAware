"""Generate final workload-aware K=2 designs from the selected CSV rows."""

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
from select_fixed_k1 import evaluate, generate_splif_routing, physical_distance


SELECTION_FILE = REPO_ROOT / "results/k2_workload_aware_selections.csv"
SUMMARY_FILE = REPO_ROOT / "results/k2_workload_aware_summary.csv"
BASE_DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
EXPECTED_BASE_LINKS = 24
EPS = 1e-9
REQUIRED_STEP_COLUMNS = {
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
}
REQUIRED_SUMMARY_COLUMNS = {
    "workload",
    "budget_mm",
    "requested_k",
    "actual_k",
    "link_1",
    "link_2",
    "total_wire_length_mm",
    "final_avg_latency",
    "final_max_link_load",
    "analytical_throughput",
    "total_routed_traffic",
    "traffic_wire_cost",
}


def fail(message):
    raise RuntimeError(message)


def relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def integer(value, description):
    try:
        raw = float(value)
        converted = int(raw)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if raw != converted:
        fail(f"Invalid {description}: expected an integer, found {value!r}")
    return converted


def number(value, description):
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if not math.isfinite(converted):
        fail(f"Invalid {description}: expected a finite number")
    return converted


def load_csv(path, required, description):
    try:
        with path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            missing = sorted(set(required) - set(reader.fieldnames or []))
            if missing:
                fail(f"{description} is missing columns: {', '.join(missing)}")
            return list(reader)
    except OSError as error:
        fail(f"Cannot read {description} {path}: {error}")


def load_inputs():
    raw_steps = load_csv(SELECTION_FILE, REQUIRED_STEP_COLUMNS, "K=2 selection CSV")
    raw_summaries = load_csv(SUMMARY_FILE, REQUIRED_SUMMARY_COLUMNS, "K=2 summary CSV")
    steps = defaultdict(list)
    for row_number, row in enumerate(raw_steps, start=2):
        workload = row["workload"].strip()
        budget = integer(row["budget_mm"], f"budget on selection row {row_number}")
        key = (workload, budget)
        steps[key].append(
            {
                "workload": workload,
                "budget_mm": budget,
                "requested_k": integer(
                    row["requested_k"], f"requested_k on selection row {row_number}"
                ),
                "actual_k": integer(
                    row["actual_k"], f"actual_k on selection row {row_number}"
                ),
                "step": integer(row["step"], f"step on selection row {row_number}"),
                "u": integer(row["u"], f"u on selection row {row_number}"),
                "v": integer(row["v"], f"v on selection row {row_number}"),
                "phy_u": integer(
                    row["phy_u"], f"phy_u on selection row {row_number}"
                ),
                "phy_v": integer(
                    row["phy_v"], f"phy_v on selection row {row_number}"
                ),
                "link_length_mm": number(
                    row["link_length_mm"], f"link length on selection row {row_number}"
                ),
                "cumulative_wire_length_mm": number(
                    row["cumulative_wire_length_mm"],
                    f"cumulative wire on selection row {row_number}",
                ),
            }
        )

    summaries = {}
    for row_number, row in enumerate(raw_summaries, start=2):
        workload = row["workload"].strip()
        budget = integer(row["budget_mm"], f"budget on summary row {row_number}")
        key = (workload, budget)
        if key in summaries:
            fail(f"Duplicate K=2 summary configuration: {key}")
        summaries[key] = {
            "workload": workload,
            "budget_mm": budget,
            "requested_k": integer(
                row["requested_k"], f"requested_k on summary row {row_number}"
            ),
            "actual_k": integer(row["actual_k"], f"actual_k on summary row {row_number}"),
            "link_1": row["link_1"].strip(),
            "link_2": row["link_2"].strip(),
            "total_wire_length_mm": number(
                row["total_wire_length_mm"], f"total wire on summary row {row_number}"
            ),
            "final_avg_latency": number(
                row["final_avg_latency"], f"final latency on summary row {row_number}"
            ),
            "final_max_link_load": number(
                row["final_max_link_load"], f"final load on summary row {row_number}"
            ),
            "analytical_throughput": number(
                row["analytical_throughput"], f"throughput on summary row {row_number}"
            ),
            "total_routed_traffic": number(
                row["total_routed_traffic"], f"routed traffic on summary row {row_number}"
            ),
            "traffic_wire_cost": number(
                row["traffic_wire_cost"], f"traffic-wire cost on summary row {row_number}"
            ),
        }

    expected = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(summaries) != expected:
        fail(
            f"K=2 summary matrix mismatch; missing={sorted(expected - set(summaries))}, "
            f"extra={sorted(set(summaries) - expected)}"
        )
    if any(key not in expected for key in steps):
        fail(f"K=2 step CSV contains unexpected configurations: {sorted(set(steps) - expected)}")

    for key in expected:
        summary = summaries[key]
        group = sorted(steps.get(key, []), key=lambda row: row["step"])
        actual_k = summary["actual_k"]
        if summary["requested_k"] != 2 or not 0 <= actual_k <= 2:
            fail(f"Invalid requested_k/actual_k for {key}")
        if len(group) != actual_k:
            fail(f"Selected step count does not match summary actual_k for {key}")
        if [row["step"] for row in group] != list(range(1, actual_k + 1)):
            fail(f"Selected steps are not contiguous for {key}")
        if any(row["actual_k"] != actual_k or row["requested_k"] != 2 for row in group):
            fail(f"Step requested_k/actual_k disagrees with summary for {key}")
        names = [f"{row['u']}<->{row['v']}" for row in group]
        expected_names = [
            summary["link_1"] if actual_k >= 1 else "none",
            summary["link_2"] if actual_k >= 2 else "none",
        ]
        if (names + ["none"] * (2 - len(names))) != expected_names:
            fail(f"Selected links disagree with summary for {key}")
        selected_wire = sum(row["link_length_mm"] for row in group)
        if not math.isclose(
            selected_wire, summary["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS
        ):
            fail(f"Selected wire length disagrees with summary for {key}")
        if group and not math.isclose(
            group[-1]["cumulative_wire_length_mm"],
            summary["total_wire_length_mm"],
            rel_tol=EPS,
            abs_tol=EPS,
        ):
            fail(f"Final cumulative wire disagrees with summary for {key}")
    return steps, summaries


def load_physical_baseline():
    design = hlp.read_json(str(BASE_DESIGN_FILE))
    context = {
        "base_design": design,
        "chiplets": hlp.read_json(str(REPO_ROOT / design["chiplets"])),
        "placement": hlp.read_json(str(REPO_ROOT / design["placement"])),
        "topology": hlp.read_json(str(REPO_ROOT / design["topology"])),
        "packaging": hlp.read_json(str(REPO_ROOT / design["packaging"])),
        "technologies": hlp.read_json(str(REPO_ROOT / design["technologies"])),
    }
    if len(context["placement"]["chiplets"]) != 16:
        fail("Physical baseline must contain exactly 16 chiplets")
    if len(context["topology"]) != EXPECTED_BASE_LINKS:
        fail(
            f"Physical baseline must start with exactly 24 links; "
            f"found {len(context['topology'])}"
        )
    mesh_edges = set()
    occupied = {chiplet_id: set() for chiplet_id in range(16)}
    for link in context["topology"]:
        ep1, ep2 = link["ep1"], link["ep2"]
        if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
            fail("Physical baseline contains a non-chiplet mesh link")
        u, v = ep1["outer_id"], ep2["outer_id"]
        edge = tuple(sorted((u, v)))
        if edge in mesh_edges:
            fail(f"Physical baseline contains duplicate mesh edge {edge}")
        mesh_edges.add(edge)
        occupied[u].add(ep1["inner_id"])
        occupied[v].add(ep2["inner_id"])
    context["mesh_edges"] = mesh_edges
    context["baseline_occupied"] = occupied
    return context


def validate_and_add_links(topology, selected, summary, context, key):
    selected_pairs = set()
    occupied = {
        chiplet_id: set(phys)
        for chiplet_id, phys in context["baseline_occupied"].items()
    }
    cumulative = 0.0
    for row in selected:
        u, v, phy_u, phy_v = row["u"], row["v"], row["phy_u"], row["phy_v"]
        if not 0 <= u < 16 or not 0 <= v < 16 or u == v:
            fail(f"Invalid selected endpoints for {key}: {u}<->{v}")
        pair = tuple(sorted((u, v)))
        if pair in context["mesh_edges"]:
            fail(f"Selected shortcut is an existing mesh edge for {key}: {pair}")
        if pair in selected_pairs:
            fail(f"Duplicate selected shortcut for {key}: {pair}")

        for chiplet_id, phy_id in ((u, phy_u), (v, phy_v)):
            chiplet_desc = context["placement"]["chiplets"][chiplet_id]
            phy_count = len(context["chiplets"][chiplet_desc["name"]]["phys"])
            if not 0 <= phy_id < phy_count:
                fail(f"Invalid selected PHY {phy_id} on chiplet {chiplet_id} for {key}")
            if phy_id in occupied[chiplet_id]:
                fail(f"Selected PHY endpoint is reused/occupied for {key}: {(chiplet_id, phy_id)}")

        actual_length = physical_distance(
            u,
            phy_u,
            v,
            phy_v,
            context["placement"],
            context["chiplets"],
            context["packaging"],
        )
        if not math.isclose(
            actual_length, row["link_length_mm"], rel_tol=EPS, abs_tol=EPS
        ):
            fail(
                f"Selected physical length mismatch for {key} step {row['step']}: "
                f"CSV={row['link_length_mm']}, physical={actual_length}"
            )
        cumulative += actual_length
        if cumulative > key[1] + EPS:
            fail(f"Selected shortcuts exceed the total wire budget for {key}")
        if not math.isclose(
            cumulative,
            row["cumulative_wire_length_mm"],
            rel_tol=EPS,
            abs_tol=EPS,
        ):
            fail(f"Cumulative selected wire mismatch for {key} step {row['step']}")

        topology.append(
            {
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
        )
        selected_pairs.add(pair)
        occupied[u].add(phy_u)
        occupied[v].add(phy_v)

    if len(topology) != EXPECTED_BASE_LINKS + summary["actual_k"]:
        fail(f"Final topology link count is invalid for {key}")
    if len(selected) != summary["actual_k"]:
        fail(f"Number of added links does not equal actual_k for {key}")
    if cumulative > key[1] + EPS:
        fail(f"Final cumulative selected wire exceeds budget for {key}")


def metrics_match(actual, summary, key):
    fields = (
        ("avg_latency", "final_avg_latency"),
        ("max_load", "final_max_link_load"),
        ("throughput", "analytical_throughput"),
        ("total_routed_traffic", "total_routed_traffic"),
        ("traffic_wire_cost", "traffic_wire_cost"),
    )
    for actual_field, summary_field in fields:
        if not math.isclose(
            actual[actual_field], summary[summary_field], rel_tol=EPS, abs_tol=EPS
        ):
            fail(
                f"Generated topology metric {actual_field} disagrees with "
                f"K=2 summary for {key}"
            )


def create_designs():
    steps, summaries = load_inputs()
    context = load_physical_baseline()
    created = []

    for workload in WORKLOADS:
        traffic_unit = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
        traffic_chiplet = f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
        if not (REPO_ROOT / traffic_unit).is_file() or not (REPO_ROOT / traffic_chiplet).is_file():
            fail(f"Missing required fixed traffic files for {workload}")

        workload_context = {
            "design": copy.deepcopy(context["base_design"]),
            "chiplets": context["chiplets"],
            "placement": context["placement"],
            "packaging": context["packaging"],
            "technologies": context["technologies"],
            "traffic": hlp.read_json(str(REPO_ROOT / traffic_chiplet)),
        }
        workload_context["design"]["traffic_by_unit"] = traffic_unit
        workload_context["design"]["traffic_by_chiplet"] = traffic_chiplet

        for budget in BUDGETS:
            key = (workload, budget)
            summary = summaries[key]
            selected = sorted(steps.get(key, []), key=lambda row: row["step"])
            topology = copy.deepcopy(context["topology"])
            validate_and_add_links(topology, selected, summary, context, key)
            routing = generate_splif_routing(
                context["chiplets"], context["placement"], topology
            )
            if routing.get("type") != "default" or not isinstance(routing.get("table"), dict):
                fail(f"Final SPLIF routing regeneration failed for {key}")

            evaluated = evaluate(topology, workload_context)
            metrics_match(evaluated, summary, key)

            name = f"project_aware_k2_{workload}_B{budget}mm"
            topology_path = REPO_ROOT / f"inputs/topologies/topology_{name}.json"
            routing_path = REPO_ROOT / f"inputs/routing_tables/routing_table_{name}.json"
            design_path = REPO_ROOT / f"inputs/designs/design_{name}.json"
            hlp.write_json(str(topology_path), topology)
            hlp.write_json(str(routing_path), routing)

            design = copy.deepcopy(context["base_design"])
            design["design_name"] = name
            design["topology"] = relative(topology_path)
            design["routing_table"] = relative(routing_path)
            design["traffic_by_unit"] = traffic_unit
            design["traffic_by_chiplet"] = traffic_chiplet
            if design["traffic_by_unit"] != traffic_unit or design["traffic_by_chiplet"] != traffic_chiplet:
                fail(f"Generated workload traffic references are incorrect for {key}")
            hlp.write_json(str(design_path), design)
            created.append(design_path)
            print(
                f"Created {relative(design_path)} with actual_k={summary['actual_k']} "
                f"and {len(topology)} links"
            )

    expected_paths = {
        REPO_ROOT
        / f"inputs/designs/design_project_aware_k2_{workload}_B{budget}mm.json"
        for workload in WORKLOADS
        for budget in BUDGETS
    }
    actual_paths = set(
        (REPO_ROOT / "inputs/designs").glob("design_project_aware_k2_*_B*mm.json")
    )
    if len(created) != 16 or set(created) != expected_paths or actual_paths != expected_paths:
        fail(
            f"Expected exactly 16 logical K=2 designs; created={len(created)}, "
            f"files={len(actual_paths)}"
        )

    print("K=2 WORKLOAD-AWARE DESIGNS COMPLETE")
    print(f"Designs created: {len(created)}")


if __name__ == "__main__":
    create_designs()
