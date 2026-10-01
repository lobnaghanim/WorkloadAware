import sys
import random
from pathlib import Path

# Path to the RapidChiplet repository root
REPO_ROOT = Path(__file__).resolve().parents[1]

# Make RapidChiplet's root modules importable
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
import generate_traffic as trgen
DESIGN_FILE = "inputs/designs/design_project_mesh_4x4.json"
SEED = 42


# Load our verified 4x4 design
design = hlp.read_json(DESIGN_FILE)
chiplets = hlp.read_json(design["chiplets"])
placement = hlp.read_json(design["placement"])


patterns = {
    "random_uniform": (["compute"], ["compute"]),
    "transpose": None,
    "permutation": None,
    "hotspot": (4, 0.5),
}


for pattern, params in patterns.items():

    # Important: make randomized workloads reproducible
    random.seed(SEED)

    traffic_by_unit, traffic_by_chiplet = trgen.generate_traffic(
        chiplets,
        placement,
        pattern,
        params,
    )

    filename = f"traffic_project_{pattern}"

    hlp.write_json(
        f"inputs/traffic_by_unit/{filename}.json",
        traffic_by_unit,
    )

    hlp.write_json(
        f"inputs/traffic_by_chiplet/{filename}.json",
        traffic_by_chiplet,
    )

    print("=" * 70)
    print(f"Traffic pattern: {pattern}")
    print("=" * 70)

    # Print a 16x16 chiplet-level traffic matrix
    print("      " + " ".join(f"{d:>6}" for d in range(16)))

    for src in range(16):
        row = []

        for dst in range(16):
            value = traffic_by_chiplet.get((src, dst), 0.0)
            row.append(f"{value:6.2f}")

        print(f"{src:>3}:  " + " ".join(row))

    print()

print("Saved all four fixed workloads.")
