import sys
import csv
import copy
import json
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
import generate_routing as rgen
import rapidchiplet as rc


# ============================================================
# Command-line argument
# ============================================================

parser = argparse.ArgumentParser()

parser.add_argument(
    "workload",
    choices=[
        "random_uniform",
        "transpose",
        "permutation",
        "hotspot",
    ],
)

args = parser.parse_args()

WORKLOAD = args.workload


# ============================================================
# Files
# ============================================================

DESIGN_FILE = (
    "inputs/designs/"
    "design_project_physical_mesh_8phy.json"
)

TRAFFIC_CHIPLET_FILE = (
    "inputs/traffic_by_chiplet/"
    f"traffic_project_{WORKLOAD}.json"
)

TRAFFIC_UNIT_FILE = (
    "inputs/traffic_by_unit/"
    f"traffic_project_{WORKLOAD}.json"
)

OUTPUT_FILE = (
    "results/"
    f"physical_candidates_{WORKLOAD}.csv"
)

BASELINE_OUTPUT_FILE = (
    "results/"
    f"physical_baseline_{WORKLOAD}.json"
)

SPARE_PHYS = [0, 2, 4, 6]


# ============================================================
# Load physical baseline
# ============================================================

design = hlp.read_json(DESIGN_FILE)

chiplets = hlp.read_json(
    design["chiplets"]
)

placement = hlp.read_json(
    design["placement"]
)

topology_base = hlp.read_json(
    design["topology"]
)

packaging = hlp.read_json(
    design["packaging"]
)

technologies = hlp.read_json(
    design["technologies"]
)

# IMPORTANT:
# Load the workload requested on the command line,
# rather than whatever workload happens to be stored
# inside the baseline design file.

traffic = hlp.read_json(
    TRAFFIC_CHIPLET_FILE
)

N = len(
    placement["chiplets"]
)


# Create a workload-specific in-memory design
design_for_workload = copy.deepcopy(
    design
)

design_for_workload["traffic_by_chiplet"] = (
    TRAFFIC_CHIPLET_FILE
)

design_for_workload["traffic_by_unit"] = (
    TRAFFIC_UNIT_FILE
)


# ============================================================
# Helper: create RapidChiplet in-memory inputs
# ============================================================

def make_inputs(topology, routing):

    return {
        "design": design_for_workload,
        "chiplets": chiplets,
        "placement": placement,
        "topology": topology,
        "packaging": packaging,
        "technologies": technologies,
        "routing_table": routing,
        "traffic_by_chiplet": traffic,
        "verbose": False,
        "validate": False,
    }


# ============================================================
# PHY physical position
# ============================================================

def phy_position(chiplet_id, phy_id):

    chiplet_desc = (
        placement["chiplets"][chiplet_id]
    )

    chiplet = chiplets[
        chiplet_desc["name"]
    ]

    chiplet = hlp.rotate_chiplet(
        chiplet,
        chiplet_desc["rotation"],
    )

    phy = chiplet["phys"][phy_id]

    x = (
        chiplet_desc["position"]["x"]
        + phy["x"]
    )

    y = (
        chiplet_desc["position"]["y"]
        + phy["y"]
    )

    return x, y


# ============================================================
# Physical distance
# ============================================================

def physical_distance(
    u,
    phy_u,
    v,
    phy_v,
):

    x1, y1 = phy_position(
        u,
        phy_u,
    )

    x2, y2 = phy_position(
        v,
        phy_v,
    )

    if packaging["link_routing"] == "manhattan":

        return (
            abs(x1 - x2)
            + abs(y1 - y2)
        )

    elif packaging["link_routing"] == "euclidean":

        return (
            (x1 - x2) ** 2
            + (y1 - y2) ** 2
        ) ** 0.5

    else:

        raise RuntimeError(
            "Unknown link routing type: "
            + str(
                packaging["link_routing"]
            )
        )


# ============================================================
# Choose shortest spare-PHY combination
# ============================================================

def best_phy_pair(u, v):

    best = None

    for phy_u in SPARE_PHYS:

        for phy_v in SPARE_PHYS:

            length = physical_distance(
                u,
                phy_u,
                v,
                phy_v,
            )

            if (
                best is None
                or length < best[0]
            ):

                best = (
                    length,
                    phy_u,
                    phy_v,
                )

    return best


# ============================================================
# Existing mesh links
# ============================================================

existing_links = set()

for link in topology_base:

    ep1 = link["ep1"]
    ep2 = link["ep2"]

    if (
        ep1["type"] == "chiplet"
        and
        ep2["type"] == "chiplet"
    ):

        u = ep1["outer_id"]
        v = ep2["outer_id"]

        existing_links.add(
            tuple(
                sorted((u, v))
            )
        )


# ============================================================
# Route statistics
# ============================================================

def route_statistics(
    topology,
    routing,
    link_lengths,
):

    routing_type = routing["type"]
    table = routing["table"]

    link_loads = {}

    for link in topology:

        a = (
            link["ep1"]["type"],
            link["ep1"]["outer_id"],
        )

        b = (
            link["ep2"]["type"],
            link["ep2"]["outer_id"],
        )

        link_loads[(a, b)] = 0.0
        link_loads[(b, a)] = 0.0

    total_routed_traffic = 0.0
    traffic_wire_cost = 0.0

    for (
        sid,
        did,
    ), volume in traffic.items():

        src = (
            "chiplet",
            sid,
        )

        dst = (
            "chiplet",
            did,
        )

        previous = "-1"
        current = src

        while current != dst:

            if routing_type == "default":

                next_node = tuple(
                    table[
                        current
                    ][
                        dst
                    ]
                )

            elif routing_type == "extended":

                next_node = tuple(
                    table[
                        current
                    ][
                        dst
                    ][
                        previous
                    ]
                )

            else:

                raise RuntimeError(
                    "Unknown routing type: "
                    + str(
                        routing_type
                    )
                )

            link_loads[
                (
                    current,
                    next_node,
                )
            ] += volume

            total_routed_traffic += (
                volume
            )

            traffic_wire_cost += (
                volume
                * link_lengths[
                    (
                        current,
                        next_node,
                    )
                ]
            )

            previous = current
            current = next_node

    max_link_load = max(
        link_loads.values()
    )

    return (
        max_link_load,
        total_routed_traffic,
        traffic_wire_cost,
    )


# ============================================================
# Evaluate one topology
# ============================================================

def evaluate(topology):

    routing = rgen.generate_routing(
        chiplets,
        placement,
        topology,
        "splif",
    )

    inputs = make_inputs(
        topology,
        routing,
    )

    intermediates = {}

    link_lengths = (
        rc.compute_link_lengths(
            inputs,
            intermediates,
        )
    )

    intermediates[
        "link_lengths"
    ] = link_lengths

    link_latencies = (
        rc.compute_link_latencies(
            inputs,
            intermediates,
        )
    )

    intermediates[
        "link_latencies"
    ] = link_latencies

    link_bandwidths = (
        rc.compute_link_bandwidths(
            inputs,
            intermediates,
        )
    )

    intermediates[
        "link_bandwidths"
    ] = link_bandwidths

    latency = rc.compute_latency(
        inputs,
        intermediates,
    )

    throughput = (
        rc.compute_throughput(
            inputs,
            intermediates,
        )
    )

    (
        max_load,
        total_routed,
        traffic_wire_cost,
    ) = route_statistics(
        topology,
        routing,
        link_lengths,
    )

    return {
        "routing": routing,
        "link_lengths":
            link_lengths,
        "link_latencies":
            link_latencies,
        "latency":
            latency,
        "throughput":
            throughput,
        "max_load":
            max_load,
        "total_routed":
            total_routed,
        "traffic_wire_cost":
            traffic_wire_cost,
    }


# ============================================================
# Evaluate workload-specific baseline
# ============================================================

print("=" * 80)

print(
    f"PHYSICAL 8-PHY BASELINE — "
    f"{WORKLOAD.upper()}"
)

print("=" * 80)

baseline = evaluate(
    topology_base
)

baseline_latency = (
    baseline["latency"]["avg"]
)

baseline_throughput = (
    baseline["throughput"][
        "aggregate_throughput"
    ]
)

baseline_max_load = (
    baseline["max_load"]
)


print(
    "Average latency:",
    baseline_latency,
)

print(
    "Aggregate throughput:",
    baseline_throughput,
)

print(
    "Maximum link load:",
    baseline_max_load,
)

print(
    "Total routed traffic:",
    baseline["total_routed"],
)

print(
    "Traffic-wire cost:",
    baseline["traffic_wire_cost"],
)


# Save baseline values.
#
# This is important because max-link-load differs
# between workloads. We must NOT hard-code 24 later.

baseline_summary = {
    "workload": WORKLOAD,
    "avg_latency":
        baseline_latency,
    "aggregate_throughput":
        baseline_throughput,
    "max_link_load":
        baseline_max_load,
    "total_routed_traffic":
        baseline["total_routed"],
    "traffic_wire_cost":
        baseline["traffic_wire_cost"],
}

with open(
    BASELINE_OUTPUT_FILE,
    "w",
) as f:

    json.dump(
        baseline_summary,
        f,
        indent=2,
    )


# ============================================================
# Enumerate all legal K=1 shortcuts
# ============================================================

results = []

for u in range(N):

    for v in range(
        u + 1,
        N,
    ):

        pair = (
            u,
            v,
        )

        # Existing mesh edge:
        # not a shortcut candidate.
        if pair in existing_links:
            continue

        (
            estimated_length,
            phy_u,
            phy_v,
        ) = best_phy_pair(
            u,
            v,
        )

        topology = copy.deepcopy(
            topology_base
        )

        new_link = {
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

        topology.append(
            new_link
        )

        candidate = evaluate(
            topology
        )

        node_u = (
            "chiplet",
            u,
        )

        node_v = (
            "chiplet",
            v,
        )

        actual_length = (
            candidate[
                "link_lengths"
            ][
                (
                    node_u,
                    node_v,
                )
            ]
        )

        link_latency = (
            candidate[
                "link_latencies"
            ][
                (
                    node_u,
                    node_v,
                )
            ]
        )

        avg_latency = (
            candidate[
                "latency"
            ][
                "avg"
            ]
        )

        latency_gain = (
            baseline_latency
            - avg_latency
        )

        latency_gain_percent = (
            100.0
            * latency_gain
            / baseline_latency
            if baseline_latency > 0
            else 0.0
        )

        gain_per_mm = (
            latency_gain
            / actual_length
            if actual_length > 0
            else 0.0
        )

        throughput = (
            candidate[
                "throughput"
            ][
                "aggregate_throughput"
            ]
        )

        results.append({
            "workload":
                WORKLOAD,

            "u":
                u,

            "v":
                v,

            "phy_u":
                phy_u,

            "phy_v":
                phy_v,

            "length_mm":
                actual_length,

            "link_latency_cycles":
                link_latency,

            "avg_latency":
                avg_latency,

            "latency_gain":
                latency_gain,

            "latency_gain_percent":
                latency_gain_percent,

            "gain_per_mm":
                gain_per_mm,

            "throughput":
                throughput,

            "throughput_change":
                throughput
                - baseline_throughput,

            "max_link_load":
                candidate[
                    "max_load"
                ],

            "total_routed_traffic":
                candidate[
                    "total_routed"
                ],

            "traffic_wire_cost":
                candidate[
                    "traffic_wire_cost"
                ],
        })


# ============================================================
# Sort only for readable output
# ============================================================

results.sort(
    key=lambda x:
        x["gain_per_mm"],
    reverse=True,
)


# ============================================================
# Save all 96 candidates
# ============================================================

with open(
    OUTPUT_FILE,
    "w",
    newline="",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=results[0].keys(),
    )

    writer.writeheader()

    writer.writerows(
        results
    )


# ============================================================
# Display top 15
# ============================================================

print()

print("=" * 80)

print(
    f"TOP 15 CANDIDATES — "
    f"{WORKLOAD.upper()}"
)

print("=" * 80)

for rank, r in enumerate(
    results[:15],
    start=1,
):

    print(
        f"{rank:2d}. "
        f"{r['u']:2d}<->{r['v']:2d}"
        f"  PHY "
        f"{r['phy_u']}<->{r['phy_v']}"
        f"  len="
        f"{r['length_mm']:.3f} mm"
        f"  link_lat="
        f"{r['link_latency_cycles']} cyc"
        f"  avg_lat="
        f"{r['avg_latency']:.3f}"
        f"  gain="
        f"{r['latency_gain']:.3f}"
        f" "
        f"({r['latency_gain_percent']:.2f}%)"
        f"  max_load="
        f"{r['max_link_load']:.2f}"
    )


print()

print(
    "Saved candidates:",
    OUTPUT_FILE,
)

print(
    "Saved baseline:",
    BASELINE_OUTPUT_FILE,
)