import sys
import copy
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
import generate_chiplet as cgen
import generate_placement as pgen
import generate_routing as rgen


EXPERIMENT_FILE = "experiments/project_mesh_4x4.json"
OLD_DESIGN_FILE = "inputs/designs/design_project_mesh_4x4.json"

NEW_NAME = "project_physical_mesh_8phy"


# ------------------------------------------------------------
# Load experiment parameters
# ------------------------------------------------------------

with open(EXPERIMENT_FILE) as f:
    exp = json.load(f)


params = {}

for key, value in exp.items():

    if key in ("exp_name", "metrics"):
        continue

    if isinstance(value, list):

        if len(value) == 1:
            params[key] = value[0]

        elif len(value) == 0:
            continue

        else:
            raise ValueError(
                f"{key} has multiple values. "
                "Expected one value for baseline generation."
            )

    else:
        params[key] = value


# Convert 4x4 -> rows=4, cols=4
rows, cols = params["grid_scale"].split("x")

params["rows"] = int(rows)
params["cols"] = int(cols)

params["chiplet_type"] = "compute"


# ------------------------------------------------------------
# Generate an 8-PHY chiplet
# ------------------------------------------------------------

chiplet = cgen.generate_chiplet(
    params,
    "8PHY_OM",
)

chiplets = {
    NEW_NAME: chiplet
}


print("Generated chiplet:")
print("  PHY count:", len(chiplet["phys"]))
print(
    "  dimensions:",
    chiplet["dimensions"]["x"],
    "x",
    chiplet["dimensions"]["y"],
)
print("  power:", chiplet["power"])


# ------------------------------------------------------------
# Generate a new 4x4 placement
#
# This is important because the 8-PHY chiplet is slightly
# larger than the original 4-PHY chiplet.
# ------------------------------------------------------------

placement = pgen.generate_grid_placement(
    params,
    chiplet,
    NEW_NAME,
    False,
)


# ------------------------------------------------------------
# Start from original mesh topology
# ------------------------------------------------------------

old_design = hlp.read_json(
    OLD_DESIGN_FILE
)

old_topology = hlp.read_json(
    old_design["topology"]
)

topology = copy.deepcopy(
    old_topology
)


# ------------------------------------------------------------
# Remap the old 4PHY_Edge PHY IDs to 8PHY_OM cardinal PHYs
#
# Old mesh:
#   0 = West
#   1 = North
#   2 = East
#   3 = South
#
# New 8PHY_OM:
#   1 = West
#   3 = North
#   5 = East
#   7 = South
#
# Spare:
#   0, 2, 4, 6
# ------------------------------------------------------------

phy_map = {
    0: 1,
    1: 3,
    2: 5,
    3: 7,
}

for link in topology:

    for endpoint_name in ["ep1", "ep2"]:

        endpoint = link[endpoint_name]

        if endpoint["type"] == "chiplet":

            old_phy = endpoint["inner_id"]

            endpoint["inner_id"] = (
                phy_map[old_phy]
            )


# ------------------------------------------------------------
# Generate routing
# ------------------------------------------------------------

routing = rgen.generate_routing(
    chiplets,
    placement,
    topology,
    "splif",
)


# ------------------------------------------------------------
# Save physical-model files
# ------------------------------------------------------------

chiplet_file = (
    f"inputs/chiplets/"
    f"chiplets_{NEW_NAME}.json"
)

placement_file = (
    f"inputs/placements/"
    f"placement_{NEW_NAME}.json"
)

topology_file = (
    f"inputs/topologies/"
    f"topology_{NEW_NAME}.json"
)

routing_file = (
    f"inputs/routing_tables/"
    f"routing_table_{NEW_NAME}.json"
)


hlp.write_json(
    chiplet_file,
    chiplets,
)

hlp.write_json(
    placement_file,
    placement,
)

hlp.write_json(
    topology_file,
    topology,
)

hlp.write_json(
    routing_file,
    routing,
)


# ------------------------------------------------------------
# Create design
# ------------------------------------------------------------

new_design = copy.deepcopy(
    old_design
)

new_design["design_name"] = NEW_NAME
new_design["chiplets"] = chiplet_file
new_design["placement"] = placement_file
new_design["topology"] = topology_file
new_design["routing_table"] = routing_file


# Use the fixed transpose workload for initial testing
new_design["traffic_by_unit"] = (
    "inputs/traffic_by_unit/"
    "traffic_project_transpose.json"
)

new_design["traffic_by_chiplet"] = (
    "inputs/traffic_by_chiplet/"
    "traffic_project_transpose.json"
)


design_file = (
    f"inputs/designs/"
    f"design_{NEW_NAME}.json"
)

hlp.write_json(
    design_file,
    new_design,
)


print()
print("Created physical-aware baseline:")
print(" ", design_file)

print()
print("Topology links:", len(topology))

print()
print(
    "Mesh PHYs: 1(W), 3(N), 5(E), 7(S)"
)

print(
    "Reserved long-range PHYs: 0, 2, 4, 6"
)