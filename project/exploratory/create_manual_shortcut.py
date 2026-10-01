import sys
import copy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
import generate_routing as rgen
import global_config as cfg


BASE_DESIGN = "inputs/designs/design_project_mesh_4x4.json"
NEW_NAME = "project_manual_3_12_transpose"


# ----------------------------------------------------------
# Load baseline design
# ----------------------------------------------------------

design = hlp.read_json(BASE_DESIGN)

chiplets = hlp.read_json(design["chiplets"])
placement = hlp.read_json(design["placement"])
topology = hlp.read_json(design["topology"])


print("Baseline links:", len(topology))


# ----------------------------------------------------------
# Verify that the PHYs we want are unused
# ----------------------------------------------------------

used_phys = set()

for link in topology:
    for ep in [link["ep1"], link["ep2"]]:
        if ep["type"] == "chiplet":
            used_phys.add(
                (ep["outer_id"], ep["inner_id"])
            )


shortcut_endpoints = [
    (3, 2),
    (12, 0),
]

for endpoint in shortcut_endpoints:
    if endpoint in used_phys:
        raise RuntimeError(
            f"PHY already used: chiplet={endpoint[0]}, "
            f"phy={endpoint[1]}"
        )


# ----------------------------------------------------------
# Add direct long-range link 3 <-> 12
# ----------------------------------------------------------

shortcut = {
    "ep1": {
        "type": "chiplet",
        "outer_id": 3,
        "inner_id": 2,
    },

    "ep2": {
        "type": "chiplet",
        "outer_id": 12,
        "inner_id": 0,
    },

    "color": cfg.colors[0],
}

topology.append(shortcut)


print("Added shortcut: chiplet 3 <-> chiplet 12")
print("New number of links:", len(topology))


# ----------------------------------------------------------
# Save new topology
# ----------------------------------------------------------

topology_file = (
    f"inputs/topologies/topology_{NEW_NAME}.json"
)

hlp.write_json(
    topology_file,
    topology,
)


# ----------------------------------------------------------
# Recompute routing
# ----------------------------------------------------------

routing = rgen.generate_routing(
    chiplets,
    placement,
    topology,
    "splif",
)

routing_file = (
    f"inputs/routing_tables/"
    f"routing_table_{NEW_NAME}.json"
)

hlp.write_json(
    routing_file,
    routing,
)


# ----------------------------------------------------------
# Create new design
# ----------------------------------------------------------

new_design = copy.deepcopy(design)

new_design["design_name"] = NEW_NAME

new_design["topology"] = topology_file

new_design["routing_table"] = routing_file

# Use the FIXED transpose workload created earlier
new_design["traffic_by_unit"] = (
    "inputs/traffic_by_unit/"
    "traffic_project_transpose.json"
)

new_design["traffic_by_chiplet"] = (
    "inputs/traffic_by_chiplet/"
    "traffic_project_transpose.json"
)


design_file = (
    f"inputs/designs/design_{NEW_NAME}.json"
)

hlp.write_json(
    design_file,
    new_design,
)


print()
print("Created:")
print(topology_file)
print(routing_file)
print(design_file)