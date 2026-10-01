"""Generate fixed K=2 workload designs from the analytical selections."""

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

import helpers as hlp
from select_fixed_k1 import (
    evaluate,
    generate_splif_routing,
    load_uniform_reference,
    physical_distance,
)


SELECTION_FILE = REPO_ROOT / "results/k2_fixed_selections.csv"
SUMMARY_FILE = REPO_ROOT / "results/k2_fixed_summary.csv"
BASE_DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
EPS = 1e-9
STEP_COLUMNS = {
    "budget_mm", "requested_k", "actual_k", "step", "u", "v",
    "phy_u", "phy_v", "link_length_mm", "cumulative_wire_length_mm",
}
SUMMARY_COLUMNS = {
    "budget_mm", "requested_k", "actual_k", "link_1", "link_1_phy_pair",
    "link_1_length_mm", "link_2", "link_2_phy_pair", "link_2_length_mm",
    "total_wire_length_mm", "final_reference_avg_latency",
    "final_reference_max_link_load", "analytical_throughput",
    "total_routed_traffic", "traffic_wire_cost",
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


def number(value, description, optional=False):
    if optional and (value is None or value == ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if not math.isfinite(converted):
        fail(f"Invalid {description}: expected a finite number")
    return converted


def read_csv(path, required, description):
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
    raw_steps = read_csv(SELECTION_FILE, STEP_COLUMNS, "fixed K=2 selection CSV")
    raw_summaries = read_csv(SUMMARY_FILE, SUMMARY_COLUMNS, "fixed K=2 summary CSV")
    steps = defaultdict(list)
    for row_number, row in enumerate(raw_steps, start=2):
        budget = integer(row["budget_mm"], f"selection budget on row {row_number}")
        steps[budget].append(
            {
                "budget_mm": budget,
                "requested_k": integer(row["requested_k"], f"requested_k on row {row_number}"),
                "actual_k": integer(row["actual_k"], f"actual_k on row {row_number}"),
                "step": integer(row["step"], f"step on row {row_number}"),
                "u": integer(row["u"], f"u on row {row_number}"),
                "v": integer(row["v"], f"v on row {row_number}"),
                "phy_u": integer(row["phy_u"], f"phy_u on row {row_number}"),
                "phy_v": integer(row["phy_v"], f"phy_v on row {row_number}"),
                "link_length_mm": number(row["link_length_mm"], f"link length on row {row_number}"),
                "cumulative_wire_length_mm": number(
                    row["cumulative_wire_length_mm"], f"cumulative wire on row {row_number}"
                ),
            }
        )

    summaries = {}
    for row_number, row in enumerate(raw_summaries, start=2):
        budget = integer(row["budget_mm"], f"summary budget on row {row_number}")
        if budget in summaries:
            fail(f"Duplicate fixed K=2 summary for B{budget}")
        summaries[budget] = {
            "budget_mm": budget,
            "requested_k": integer(row["requested_k"], f"summary requested_k on row {row_number}"),
            "actual_k": integer(row["actual_k"], f"summary actual_k on row {row_number}"),
            "link_1": row["link_1"].strip(),
            "link_1_phy_pair": row["link_1_phy_pair"].strip(),
            "link_1_length_mm": number(row["link_1_length_mm"], "link_1_length_mm", optional=True),
            "link_2": row["link_2"].strip(),
            "link_2_phy_pair": row["link_2_phy_pair"].strip(),
            "link_2_length_mm": number(row["link_2_length_mm"], "link_2_length_mm", optional=True),
            "total_wire_length_mm": number(row["total_wire_length_mm"], "total_wire_length_mm"),
            "final_reference_avg_latency": number(row["final_reference_avg_latency"], "final_reference_avg_latency"),
            "final_reference_max_link_load": number(row["final_reference_max_link_load"], "final_reference_max_link_load"),
            "analytical_throughput": number(row["analytical_throughput"], "analytical_throughput"),
            "total_routed_traffic": number(row["total_routed_traffic"], "total_routed_traffic"),
            "traffic_wire_cost": number(row["traffic_wire_cost"], "traffic_wire_cost"),
        }

    if set(summaries) != set(BUDGETS) or any(budget not in BUDGETS for budget in steps):
        fail(f"Fixed K=2 inputs must contain exactly budgets {list(BUDGETS)}")
    for budget in BUDGETS:
        group = sorted(steps.get(budget, []), key=lambda row: row["step"])
        summary = summaries[budget]
        actual_k = summary["actual_k"]
        if summary["requested_k"] != 2 or not 0 <= actual_k <= 2:
            fail(f"Invalid requested_k/actual_k for B{budget}")
        if len(group) != actual_k or [row["step"] for row in group] != list(range(1, actual_k + 1)):
            fail(f"Selection steps disagree with actual_k for B{budget}")
        if any(row["requested_k"] != 2 or row["actual_k"] != actual_k for row in group):
            fail(f"Selection K values disagree with summary for B{budget}")
        names = [f"{row['u']}<->{row['v']}" for row in group]
        phys = [f"{row['phy_u']}<->{row['phy_v']}" for row in group]
        expected_names = [summary["link_1"], summary["link_2"]]
        expected_phys = [summary["link_1_phy_pair"], summary["link_2_phy_pair"]]
        if names + ["none"] * (2 - actual_k) != expected_names:
            fail(f"Selection links disagree with summary for B{budget}")
        if phys + [""] * (2 - actual_k) != expected_phys:
            fail(f"Selection PHY pairs disagree with summary for B{budget}")
        total = sum(row["link_length_mm"] for row in group)
        if not math.isclose(total, summary["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Selection wire length disagrees with summary for B{budget}")
        if total > budget + EPS:
            fail(f"Fixed K=2 selection exceeds total wire budget B{budget}")
    return steps, summaries


def load_baseline():
    design = hlp.read_json(str(BASE_DESIGN_FILE))
    context = {
        "base_design": design,
        "chiplets": hlp.read_json(str(REPO_ROOT / design["chiplets"])),
        "placement": hlp.read_json(str(REPO_ROOT / design["placement"])),
        "topology": hlp.read_json(str(REPO_ROOT / design["topology"])),
        "packaging": hlp.read_json(str(REPO_ROOT / design["packaging"])),
        "technologies": hlp.read_json(str(REPO_ROOT / design["technologies"])),
    }
    if len(context["placement"]["chiplets"]) != 16 or len(context["topology"]) != 24:
        fail("Physical baseline must contain 16 chiplets and exactly 24 links")
    mesh_edges = set()
    occupied = {chiplet_id: set() for chiplet_id in range(16)}
    for link in context["topology"]:
        ep1, ep2 = link["ep1"], link["ep2"]
        if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
            fail("Physical baseline contains a non-chiplet link")
        edge = tuple(sorted((ep1["outer_id"], ep2["outer_id"])))
        if edge in mesh_edges:
            fail(f"Duplicate baseline mesh edge {edge}")
        mesh_edges.add(edge)
        occupied[ep1["outer_id"]].add(ep1["inner_id"])
        occupied[ep2["outer_id"]].add(ep2["inner_id"])
    context["mesh_edges"] = mesh_edges
    context["baseline_occupied"] = occupied
    return context


def add_selected_links(topology, selected, summary, baseline, budget):
    occupied = {chiplet: set(phys) for chiplet, phys in baseline["baseline_occupied"].items()}
    pairs = set()
    cumulative = 0.0
    for row in selected:
        u, v, phy_u, phy_v = row["u"], row["v"], row["phy_u"], row["phy_v"]
        if not 0 <= u < 16 or not 0 <= v < 16 or u == v:
            fail(f"Invalid fixed shortcut endpoints for B{budget}: {u}<->{v}")
        pair = tuple(sorted((u, v)))
        if pair in baseline["mesh_edges"] or pair in pairs:
            fail(f"Fixed shortcut is a mesh/duplicate edge for B{budget}: {pair}")
        for chiplet_id, phy_id in ((u, phy_u), (v, phy_v)):
            desc = baseline["placement"]["chiplets"][chiplet_id]
            phy_count = len(baseline["chiplets"][desc["name"]]["phys"])
            if not 0 <= phy_id < phy_count:
                fail(f"Invalid PHY {phy_id} on chiplet {chiplet_id} for B{budget}")
            if phy_id in occupied[chiplet_id]:
                fail(f"PHY endpoint reused/occupied for B{budget}: {(chiplet_id, phy_id)}")
        length = physical_distance(
            u, phy_u, v, phy_v, baseline["placement"], baseline["chiplets"], baseline["packaging"]
        )
        if not math.isclose(length, row["link_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Physical length disagrees with selection CSV for B{budget} step {row['step']}")
        cumulative += length
        if cumulative > budget + EPS or not math.isclose(
            cumulative, row["cumulative_wire_length_mm"], rel_tol=EPS, abs_tol=EPS
        ):
            fail(f"Cumulative wire is invalid for B{budget} step {row['step']}")
        topology.append({
            "ep1": {"type": "chiplet", "outer_id": u, "inner_id": phy_u},
            "ep2": {"type": "chiplet", "outer_id": v, "inner_id": phy_v},
        })
        pairs.add(pair)
        occupied[u].add(phy_u)
        occupied[v].add(phy_v)
    if len(selected) != summary["actual_k"] or len(topology) != 24 + summary["actual_k"]:
        fail(f"Final topology does not contain 24 + actual_k links for B{budget}")


def verify_reference_metrics(topology, summary, baseline):
    traffic, _ = load_uniform_reference(16)
    context = {
        "design": baseline["base_design"], "chiplets": baseline["chiplets"],
        "placement": baseline["placement"], "packaging": baseline["packaging"],
        "technologies": baseline["technologies"], "traffic": traffic,
    }
    actual = evaluate(topology, context)
    checks = (
        (actual["avg_latency"], summary["final_reference_avg_latency"]),
        (actual["max_load"], summary["final_reference_max_link_load"]),
        (actual["throughput"], summary["analytical_throughput"]),
        (actual["total_routed_traffic"], summary["total_routed_traffic"]),
        (actual["traffic_wire_cost"], summary["traffic_wire_cost"]),
    )
    if any(not math.isclose(a, b, rel_tol=EPS, abs_tol=EPS) for a, b in checks):
        fail(f"Generated topology metrics disagree with fixed K=2 summary for B{summary['budget_mm']}")


def create_designs():
    steps, summaries = load_inputs()
    baseline = load_baseline()
    created = []
    expected_designs = set()
    for budget in BUDGETS:
        summary = summaries[budget]
        selected = sorted(steps.get(budget, []), key=lambda row: row["step"])
        topology = copy.deepcopy(baseline["topology"])
        add_selected_links(topology, selected, summary, baseline, budget)
        routing = generate_splif_routing(baseline["chiplets"], baseline["placement"], topology)
        if routing.get("type") != "default" or not isinstance(routing.get("table"), dict):
            fail(f"SPLIF routing regeneration failed for B{budget}")
        verify_reference_metrics(topology, summary, baseline)

        topology_path = REPO_ROOT / f"inputs/topologies/topology_project_fixed_k2_B{budget}mm.json"
        routing_path = REPO_ROOT / f"inputs/routing_tables/routing_table_project_fixed_k2_B{budget}mm.json"
        hlp.write_json(str(topology_path), topology)
        hlp.write_json(str(routing_path), routing)

        budget_refs = set()
        for workload in WORKLOADS:
            unit_traffic = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
            chiplet_traffic = f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
            if not (REPO_ROOT / unit_traffic).is_file() or not (REPO_ROOT / chiplet_traffic).is_file():
                fail(f"Missing fixed workload traffic for {workload}")
            name = f"project_fixed_k2_{workload}_B{budget}mm"
            design_path = REPO_ROOT / f"inputs/designs/design_{name}.json"
            design = copy.deepcopy(baseline["base_design"])
            design["design_name"] = name
            design["topology"] = relative(topology_path)
            design["routing_table"] = relative(routing_path)
            design["traffic_by_unit"] = unit_traffic
            design["traffic_by_chiplet"] = chiplet_traffic
            hlp.write_json(str(design_path), design)
            budget_refs.add((design["topology"], design["routing_table"]))
            created.append(design_path)
            expected_designs.add(design_path)
            print(f"Created {relative(design_path)} with actual_k={summary['actual_k']}")
        if len(budget_refs) != 1:
            fail(f"Fixed topology/routing changes across workloads for B{budget}")

    actual_designs = set(
        (REPO_ROOT / "inputs/designs").glob("design_project_fixed_k2_*_B*mm.json")
    )
    if len(created) != 16 or actual_designs != expected_designs:
        fail(f"Expected exactly 16 fixed K=2 designs; created={len(created)}, files={len(actual_designs)}")
    print("FIXED K=2 DESIGNS COMPLETE")
    print(f"Logical designs created: {len(created)}")


if __name__ == "__main__":
    create_designs()
