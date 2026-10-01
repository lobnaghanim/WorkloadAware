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

CSV_FILE = (
    "results/"
    "physical_candidates_transpose.csv"
)

BUDGETS = [
    5.0,
    15.0,
    25.0,
    45.0,
]

BASELINE_MAX_LOAD = 24.0


# ------------------------------------------------------------
# Load baseline
# ------------------------------------------------------------

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
# Load candidate results
# ------------------------------------------------------------

with open(CSV_FILE, newline="") as f:
    reader = csv.DictReader(f)
    rows = list(reader)


candidates = []

for row in rows:

    candidates.append({
        "u": int(row["u"]),
        "v": int(row["v"]),
        "phy_u": int(row["phy_u"]),
        "phy_v": int(row["phy_v"]),
        "length": float(row["length_mm"]),
        "avg_latency": float(row["avg_latency"]),
        "latency_gain_percent":
            float(row["latency_gain_percent"]),
        "max_load":
            float(row["max_link_load"]),
    })


# ------------------------------------------------------------
# Select and generate one design per physical budget
# ------------------------------------------------------------

for budget in BUDGETS:

    feasible = [
        c for c in candidates
        if c["length"] <= budget
        and c["max_load"] <= BASELINE_MAX_LOAD
    ]

    if not feasible:
        print(
            f"No feasible candidate for {budget} mm"
        )
        continue

    # Primary objective: lowest average latency.
    #
    # Remaining fields provide deterministic tie-breaking.
    best = min(
        feasible,
        key=lambda x: (
            x["avg_latency"],
            x["max_load"],
            x["length"],
            x["u"],
            x["v"],
        ),
    )

    budget_name = int(budget)

    name = (
        f"project_k1_transpose_"
        f"B{budget_name}mm"
    )

    topology = copy.deepcopy(
        base_topology
    )

    topology.append({
        "ep1": {
            "type": "chiplet",
            "outer_id": best["u"],
            "inner_id": best["phy_u"],
        },
        "ep2": {
            "type": "chiplet",
            "outer_id": best["v"],
            "inner_id": best["phy_v"],
        },
    })

    # Recompute routing for modified topology
    routing = rgen.generate_routing(
        chiplets,
        placement,
        topology,
        "splif",
    )

    topology_file = (
        f"inputs/topologies/"
        f"topology_{name}.json"
    )

    routing_file = (
        f"inputs/routing_tables/"
        f"routing_table_{name}.json"
    )

    design_file = (
        f"inputs/designs/"
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
    design["topology"] = topology_file
    design["routing_table"] = routing_file

    hlp.write_json(
        design_file,
        design,
    )

    print("=" * 70)

    print(
        f"Budget: {budget_name} mm"
    )

    print(
        f"Selected: "
        f"{best['u']} <-> {best['v']}"
    )

    print(
        f"PHYs: "
        f"{best['phy_u']} <-> "
        f"{best['phy_v']}"
    )

    print(
        f"Length: "
        f"{best['length']:.3f} mm"
    )

    print(
        f"Analytical latency gain: "
        f"{best['latency_gain_percent']:.2f}%"
    )

    print(
        "Created:",
        design_file,
    )