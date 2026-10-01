import sys
import csv
import copy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
import generate_routing as rgen


BASE_DESIGN_FILE = (
    "inputs/designs/"
    "design_project_physical_mesh_8phy.json"
)

SELECTION_FILE = (
    "results/"
    "k1_workload_aware_selections.csv"
)


base_design = hlp.read_json(
    BASE_DESIGN_FILE
)

chiplets = hlp.read_json(
    base_design["chiplets"]
)

placement = hlp.read_json(
    base_design["placement"]
)

base_topology = hlp.read_json(
    base_design["topology"]
)


# ------------------------------------------------------------
# Load selected links
# ------------------------------------------------------------

with open(
    SELECTION_FILE,
    newline="",
) as f:

    selections = list(
        csv.DictReader(f)
    )


# ------------------------------------------------------------
# Keep track of duplicate physical designs
#
# Example:
# permutation B25 and B45 may select the exact same link.
# ------------------------------------------------------------

seen = set()


# ------------------------------------------------------------
# First create one 8-PHY baseline per workload
# ------------------------------------------------------------

workloads = sorted(
    set(
        row["workload"]
        for row in selections
    )
)


for workload in workloads:

    name = (
        f"project_k1_{workload}_baseline"
    )

    design = copy.deepcopy(
        base_design
    )

    design["design_name"] = name

    design["traffic_by_unit"] = (
        "inputs/traffic_by_unit/"
        f"traffic_project_{workload}.json"
    )

    design["traffic_by_chiplet"] = (
        "inputs/traffic_by_chiplet/"
        f"traffic_project_{workload}.json"
    )

    design_file = (
        "inputs/designs/"
        f"design_{name}.json"
    )

    hlp.write_json(
        design_file,
        design,
    )

    print(
        f"Created baseline: {design_file}"
    )


# ------------------------------------------------------------
# Create workload-aware selected designs
# ------------------------------------------------------------

for row in selections:

    workload = row["workload"]

    budget = int(
        float(row["budget_mm"])
    )

    u = int(row["u"])
    v = int(row["v"])

    phy_u = int(row["phy_u"])
    phy_v = int(row["phy_v"])

    # Same workload + same selected link/PHY pair
    # means the larger budget produced no new topology.
    signature = (
        workload,
        u,
        v,
        phy_u,
        phy_v,
    )

    if signature in seen:

        print(
            f"Skipping duplicate: "
            f"{workload} B{budget} "
            f"({u}<->{v})"
        )

        continue

    seen.add(signature)

    name = (
        f"project_k1_{workload}_"
        f"B{budget}mm"
    )

    topology = copy.deepcopy(
        base_topology
    )

    topology.append({
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
    })

    routing = rgen.generate_routing(
        chiplets,
        placement,
        topology,
        "splif",
    )

    topology_file = (
        "inputs/topologies/"
        f"topology_{name}.json"
    )

    routing_file = (
        "inputs/routing_tables/"
        f"routing_table_{name}.json"
    )

    design_file = (
        "inputs/designs/"
        f"design_{name}.json"
    )

    hlp.write_json(
        topology_file,
        topology,
    )

    hlp.write_json(
        routing_file,
        routing,
    )

    design = copy.deepcopy(
        base_design
    )

    design["design_name"] = name

    design["topology"] = (
        topology_file
    )

    design["routing_table"] = (
        routing_file
    )

    # Critical:
    # use the correct fixed workload.
    design["traffic_by_unit"] = (
        "inputs/traffic_by_unit/"
        f"traffic_project_{workload}.json"
    )

    design["traffic_by_chiplet"] = (
        "inputs/traffic_by_chiplet/"
        f"traffic_project_{workload}.json"
    )

    hlp.write_json(
        design_file,
        design,
    )

    print(
        f"Created {workload} "
        f"B{budget}: "
        f"{u}<->{v}"
    )