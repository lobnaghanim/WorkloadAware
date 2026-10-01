import sys
import copy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp


BASE_DESIGN = "inputs/designs/design_project_mesh_4x4.json"
NEW_NAME = "project_baseline_transpose"


design = hlp.read_json(BASE_DESIGN)

new_design = copy.deepcopy(design)

new_design["design_name"] = NEW_NAME

# Use exactly the same fixed workload as the shortcut design
new_design["traffic_by_unit"] = (
    "inputs/traffic_by_unit/"
    "traffic_project_transpose.json"
)

new_design["traffic_by_chiplet"] = (
    "inputs/traffic_by_chiplet/"
    "traffic_project_transpose.json"
)

design_file = f"inputs/designs/design_{NEW_NAME}.json"

hlp.write_json(
    design_file,
    new_design,
)

print("Created:", design_file)
print("Using fixed transpose workload.")