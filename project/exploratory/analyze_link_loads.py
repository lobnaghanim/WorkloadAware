import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp


DESIGNS = [
    (
        "BASELINE",
        "inputs/designs/design_project_baseline_transpose.json",
    ),
    (
        "SHORTCUT 3<->12",
        "inputs/designs/design_project_manual_3_12_transpose.json",
    ),
]


def analyze_design(label, design_file):

    design = hlp.read_json(design_file)

    topology = hlp.read_json(design["topology"])
    routing_table_data = hlp.read_json(design["routing_table"])
    traffic = hlp.read_json(design["traffic_by_chiplet"])

    routing_type = routing_table_data["type"]
    routing_table = routing_table_data["table"]

    # Initialize directed link loads
    link_loads = {}

    for link in topology:

        ep1 = (
            link["ep1"]["type"],
            link["ep1"]["outer_id"],
        )

        ep2 = (
            link["ep2"]["type"],
            link["ep2"]["outer_id"],
        )

        link_loads[(ep1, ep2)] = 0.0
        link_loads[(ep2, ep1)] = 0.0

    # Route every communication flow and accumulate traffic
    for (sid, did), flow_traffic in traffic.items():

        src = ("chiplet", sid)
        dst = ("chiplet", did)

        previous = "-1"
        current = src

        while current != dst:

            if routing_type == "default":

                next_node = tuple(
                    routing_table[current][dst]
                )

            elif routing_type == "extended":

                next_node = tuple(
                    routing_table[current][dst][previous]
                )

            else:
                raise RuntimeError(
                    f"Unknown routing table type: {routing_type}"
                )

            link_loads[(current, next_node)] += flow_traffic

            previous = current
            current = next_node

    sorted_links = sorted(
        link_loads.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    print()
    print("=" * 80)
    print(label)
    print("=" * 80)

    print("Top 15 directed links by traffic load:")
    print()

    for (src, dst), load in sorted_links[:15]:

        print(
            f"{src[0]} {src[1]:2d}"
            f" -> "
            f"{dst[0]} {dst[1]:2d}"
            f"    load = {load:.4f}"
        )

    max_load = sorted_links[0][1]

    total_load = sum(link_loads.values())

    print()
    print(f"Maximum directed-link load: {max_load:.4f}")
    print(f"Total routed link traffic:  {total_load:.4f}")


for label, design_file in DESIGNS:
    analyze_design(label, design_file)