"""Create workload-specific designs for the fixed K=1 budget selections."""

import copy
import csv
import math
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
from select_fixed_k1 import generate_splif_routing, physical_distance


SELECTION_FILE = REPO_ROOT / "results/k1_fixed_selections.csv"
BASE_DESIGN_FILE = (
    REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
)
TOPOLOGY_DIR = REPO_ROOT / "inputs/topologies"
ROUTING_DIR = REPO_ROOT / "inputs/routing_tables"
DESIGN_DIR = REPO_ROOT / "inputs/designs"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
REQUIRED_COLUMNS = {"budget_mm", "u", "v", "phy_u", "phy_v", "length_mm"}
EXPECTED_BASE_LINKS = 24
EXPECTED_FIXED_LINKS = 25
EPS = 1e-9


def fail(message):
    raise RuntimeError(message)


def relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def load_selections():
    try:
        with SELECTION_FILE.open(newline="", encoding="utf-8") as selection_file:
            reader = csv.DictReader(selection_file)
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            if missing:
                fail(f"Fixed-selection CSV is missing columns: {', '.join(missing)}")
            raw_rows = list(reader)
    except OSError as error:
        fail(f"Cannot read fixed-selection CSV {SELECTION_FILE}: {error}")

    selections = {}
    for row_number, row in enumerate(raw_rows, start=2):
        try:
            raw_budget = float(row["budget_mm"])
            budget = int(raw_budget)
            selection = {
                "budget_mm": budget,
                "u": int(row["u"]),
                "v": int(row["v"]),
                "phy_u": int(row["phy_u"]),
                "phy_v": int(row["phy_v"]),
                "length_mm": float(row["length_mm"]),
            }
        except (TypeError, ValueError) as error:
            fail(f"Invalid fixed-selection CSV row {row_number}: {error}")
        if raw_budget != budget:
            fail(f"Budget on fixed-selection CSV row {row_number} is not integral")
        if budget in selections:
            fail(f"Duplicate fixed selection for budget {budget} mm")
        if not math.isfinite(selection["length_mm"]):
            fail(f"Non-finite length_mm on fixed-selection CSV row {row_number}")
        selections[budget] = selection

    if set(selections) != set(BUDGETS):
        fail(
            f"Fixed-selection budgets must be {list(BUDGETS)}; "
            f"found {sorted(selections)}"
        )
    return selections


def chiplet_links(topology):
    return [
        link
        for link in topology
        if link["ep1"]["type"] == "chiplet"
        and link["ep2"]["type"] == "chiplet"
    ]


def validate_baseline(topology, node_count):
    links = chiplet_links(topology)
    if len(links) != EXPECTED_BASE_LINKS or len(topology) != EXPECTED_BASE_LINKS:
        fail(
            f"Physical baseline must contain exactly {EXPECTED_BASE_LINKS} "
            f"chiplet links; found {len(links)} chiplet links and {len(topology)} total links"
        )

    edges = set()
    used_phys = {chiplet_id: set() for chiplet_id in range(node_count)}
    for link in links:
        ep1, ep2 = link["ep1"], link["ep2"]
        u, v = ep1["outer_id"], ep2["outer_id"]
        if not 0 <= u < node_count or not 0 <= v < node_count or u == v:
            fail(f"Invalid baseline link {u}<->{v}")
        edge = tuple(sorted((u, v)))
        if edge in edges:
            fail(f"Duplicate baseline edge {edge}")
        edges.add(edge)
        used_phys[u].add(ep1["inner_id"])
        used_phys[v].add(ep2["inner_id"])
    return edges, used_phys


def validate_selection(selection, context, mesh_edges, used_phys):
    u, v = selection["u"], selection["v"]
    phy_u, phy_v = selection["phy_u"], selection["phy_v"]
    node_count = len(context["placement"]["chiplets"])
    if not 0 <= u < node_count or not 0 <= v < node_count or u == v:
        fail(f"B{selection['budget_mm']} has invalid endpoints {u}<->{v}")
    if tuple(sorted((u, v))) in mesh_edges:
        fail(f"B{selection['budget_mm']} selects existing mesh edge {u}<->{v}")

    for chiplet_id, phy_id in ((u, phy_u), (v, phy_v)):
        chiplet_desc = context["placement"]["chiplets"][chiplet_id]
        phy_count = len(context["chiplets"][chiplet_desc["name"]]["phys"])
        if not 0 <= phy_id < phy_count:
            fail(
                f"B{selection['budget_mm']} uses invalid PHY {phy_id} "
                f"on chiplet {chiplet_id}"
            )
        if phy_id in used_phys[chiplet_id]:
            fail(
                f"B{selection['budget_mm']} uses occupied PHY {phy_id} "
                f"on chiplet {chiplet_id}"
            )

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
        actual_length, selection["length_mm"], rel_tol=EPS, abs_tol=EPS
    ):
        fail(
            f"B{selection['budget_mm']} CSV length {selection['length_mm']} does not "
            f"match physical length {actual_length}"
        )


def load_context():
    base_design = hlp.read_json(str(BASE_DESIGN_FILE))
    context = {
        "base_design": base_design,
        "chiplets": hlp.read_json(base_design["chiplets"]),
        "placement": hlp.read_json(base_design["placement"]),
        "topology": hlp.read_json(base_design["topology"]),
        "packaging": hlp.read_json(base_design["packaging"]),
    }
    if len(context["placement"]["chiplets"]) != 16:
        fail(
            "Physical baseline must place exactly 16 chiplets; found "
            f"{len(context['placement']['chiplets'])}"
        )
    return context


def create_designs():
    selections = load_selections()
    context = load_context()
    mesh_edges, used_phys = validate_baseline(
        context["topology"], len(context["placement"]["chiplets"])
    )

    for workload in WORKLOADS:
        for traffic_kind in ("traffic_by_unit", "traffic_by_chiplet"):
            traffic_path = (
                REPO_ROOT / "inputs" / traffic_kind / f"traffic_project_{workload}.json"
            )
            if not traffic_path.is_file():
                fail(f"Missing required workload traffic file: {traffic_path}")

    created_designs = []
    for budget in BUDGETS:
        selection = selections[budget]
        validate_selection(selection, context, mesh_edges, used_phys)
        topology = copy.deepcopy(context["topology"])
        topology.append(
            {
                "ep1": {
                    "type": "chiplet",
                    "outer_id": selection["u"],
                    "inner_id": selection["phy_u"],
                },
                "ep2": {
                    "type": "chiplet",
                    "outer_id": selection["v"],
                    "inner_id": selection["phy_v"],
                },
            }
        )
        if len(topology) != EXPECTED_FIXED_LINKS or len(chiplet_links(topology)) != EXPECTED_FIXED_LINKS:
            fail(f"B{budget} topology must contain exactly 25 chiplet links")

        routing = generate_splif_routing(
            context["chiplets"], context["placement"], topology
        )
        topology_path = TOPOLOGY_DIR / f"topology_project_fixed_k1_B{budget}mm.json"
        routing_path = ROUTING_DIR / f"routing_table_project_fixed_k1_B{budget}mm.json"
        hlp.write_json(str(topology_path), topology)
        hlp.write_json(str(routing_path), routing)

        for workload in WORKLOADS:
            name = f"project_fixed_k1_{workload}_B{budget}mm"
            design = copy.deepcopy(context["base_design"])
            design["design_name"] = name
            design["topology"] = relative(topology_path)
            design["routing_table"] = relative(routing_path)
            design["traffic_by_unit"] = (
                f"inputs/traffic_by_unit/traffic_project_{workload}.json"
            )
            design["traffic_by_chiplet"] = (
                f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
            )
            design_path = DESIGN_DIR / f"design_{name}.json"
            hlp.write_json(str(design_path), design)
            created_designs.append(design_path)
            print(
                f"Created {relative(design_path)} using fixed "
                f"{selection['u']}<->{selection['v']}"
            )

    expected_paths = {
        DESIGN_DIR / f"design_project_fixed_k1_{workload}_B{budget}mm.json"
        for workload in WORKLOADS
        for budget in BUDGETS
    }
    actual_paths = set(DESIGN_DIR.glob("design_project_fixed_k1_*_B*mm.json"))
    if len(created_designs) != 16 or set(created_designs) != expected_paths:
        fail(f"Expected to create exactly 16 designs; created {len(created_designs)}")
    if actual_paths != expected_paths:
        extras = sorted(relative(path) for path in actual_paths - expected_paths)
        missing = sorted(relative(path) for path in expected_paths - actual_paths)
        fail(f"Fixed-design file set mismatch; extra={extras}, missing={missing}")

    print("FIXED K=1 DESIGNS COMPLETE")
    print(f"Designs created: {len(created_designs)}")


if __name__ == "__main__":
    create_designs()
