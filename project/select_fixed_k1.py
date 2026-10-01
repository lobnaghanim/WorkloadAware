"""Select one workload-independent K=1 shortcut per physical wire budget."""

import copy
import csv
import math
import queue
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp
import rapidchiplet as rc


DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
REFERENCE_TRAFFIC_FILE = (
    REPO_ROOT / "inputs/traffic_by_chiplet/traffic_project_random_uniform.json"
)
WORKLOAD_SELECTION_FILE = REPO_ROOT / "results/k1_workload_aware_selections.csv"
OUTPUT_FILE = REPO_ROOT / "results/k1_fixed_selections.csv"
REFERENCE_TRAFFIC_NAME = "fixed_reference_uniform"
SELECTION_POLICY = "min_latency_subject_to_wire_budget_and_baseline_max_load"
SPARE_PHYS = (0, 2, 4, 6)
BUDGETS = (5.0, 15.0, 25.0, 45.0)
EXPECTED_CHIPLETS = 16
EXPECTED_MESH_LINKS = 24
EXPECTED_CANDIDATES = 96
EPS = 1e-9
UNIFORM_REL_TOL = 1e-9
UNIFORM_ABS_TOL = 1e-12
COMPARISON_WORKLOADS = (
    "random_uniform",
    "transpose",
    "permutation",
    "hotspot",
)
CSV_FIELDS = (
    "budget_mm",
    "u",
    "v",
    "phy_u",
    "phy_v",
    "length_mm",
    "link_latency_cycles",
    "reference_avg_latency",
    "reference_latency_gain_percent",
    "reference_max_link_load",
    "reference_baseline_max_link_load",
    "reference_throughput",
    "reference_baseline_throughput",
    "total_routed_traffic",
    "traffic_wire_cost",
    "reference_traffic",
    "selection_policy",
)


def fail(message):
    raise RuntimeError(message)


def load_uniform_reference(node_count):
    """Use the fixed file only if it is a complete symmetric all-to-all matrix."""
    try:
        loaded = hlp.read_json(str(REFERENCE_TRAFFIC_FILE))
    except (OSError, ValueError, TypeError) as error:
        loaded = None
        reason = f"could not load file: {error}"
    else:
        reason = "traffic matrix is not complete uniform all-to-all"

    expected_pairs = {
        (source, destination)
        for source in range(node_count)
        for destination in range(node_count)
        if source != destination
    }

    valid = isinstance(loaded, dict)
    non_self = {}
    if valid:
        for pair, volume in loaded.items():
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or isinstance(pair[0], bool)
                or isinstance(pair[1], bool)
                or not isinstance(pair[0], int)
                or not isinstance(pair[1], int)
                or pair[0] not in range(node_count)
                or pair[1] not in range(node_count)
                or isinstance(volume, bool)
                or not isinstance(volume, (int, float))
                or not math.isfinite(volume)
            ):
                valid = False
                break
            if pair[0] != pair[1] and volume > 0:
                non_self[pair] = float(volume)

    if valid and set(non_self) == expected_pairs:
        reference_volume = next(iter(non_self.values()))
        valid = reference_volume > 0 and all(
            math.isclose(
                volume,
                reference_volume,
                rel_tol=UNIFORM_REL_TOL,
                abs_tol=UNIFORM_ABS_TOL,
            )
            for volume in non_self.values()
        )

    if valid:
        outgoing = [0.0] * node_count
        incoming = [0.0] * node_count
        for (source, destination), volume in non_self.items():
            outgoing[source] += volume
            incoming[destination] += volume
        valid = all(
            math.isclose(value, outgoing[0], rel_tol=UNIFORM_REL_TOL, abs_tol=UNIFORM_ABS_TOL)
            for value in outgoing + incoming
        )

    if valid:
        print(
            f"Uniform reference verified: {REFERENCE_TRAFFIC_FILE.relative_to(REPO_ROOT)} "
            f"({len(non_self)} equal non-self flows, no self traffic used)"
        )
        return non_self, "verified_file"

    # A unit-volume matrix is sufficient because the optimization compares all
    # candidates and the baseline under the same reference scale.
    fallback = {pair: 1.0 for pair in expected_pairs}
    print(
        "Uniform reference file was not suitable; using an explicit in-memory "
        f"uniform all-to-all matrix ({reason})."
    )
    return fallback, "generated_in_memory"


def phy_position(chiplet_id, phy_id, placement, chiplets):
    chiplet_desc = placement["chiplets"][chiplet_id]
    chiplet = hlp.rotate_chiplet(
        chiplets[chiplet_desc["name"]], chiplet_desc["rotation"]
    )
    phy = chiplet["phys"][phy_id]
    return (
        chiplet_desc["position"]["x"] + phy["x"],
        chiplet_desc["position"]["y"] + phy["y"],
    )


def physical_distance(u, phy_u, v, phy_v, placement, chiplets, packaging):
    x1, y1 = phy_position(u, phy_u, placement, chiplets)
    x2, y2 = phy_position(v, phy_v, placement, chiplets)
    routing = packaging["link_routing"]
    if routing == "manhattan":
        return abs(x1 - x2) + abs(y1 - y2)
    if routing == "euclidean":
        return math.hypot(x1 - x2, y1 - y2)
    fail(f"Unknown packaging link_routing value: {routing!r}")


def best_phy_pair(u, v, placement, chiplets, packaging):
    options = []
    for phy_u in SPARE_PHYS:
        for phy_v in SPARE_PHYS:
            options.append(
                (
                    physical_distance(
                        u, phy_u, v, phy_v, placement, chiplets, packaging
                    ),
                    phy_u,
                    phy_v,
                )
            )
    if not options:
        fail(f"No physically valid spare-PHY pair for chiplets {u} and {v}")
    return min(options)


def route_statistics(topology, routing, traffic, link_lengths):
    routing_type = routing["type"]
    table = routing["table"]
    link_loads = {}
    for link in topology:
        a = (link["ep1"]["type"], link["ep1"]["outer_id"])
        b = (link["ep2"]["type"], link["ep2"]["outer_id"])
        link_loads[(a, b)] = 0.0
        link_loads[(b, a)] = 0.0

    total_routed_traffic = 0.0
    traffic_wire_cost = 0.0
    for (source_id, destination_id), volume in traffic.items():
        source = ("chiplet", source_id)
        destination = ("chiplet", destination_id)
        previous = "-1"
        current = source
        while current != destination:
            if routing_type == "default":
                next_node = tuple(table[current][destination])
            elif routing_type == "extended":
                next_node = tuple(table[current][destination][previous])
            else:
                fail(f"Unknown routing-table type: {routing_type!r}")
            edge = (current, next_node)
            if edge not in link_loads or edge not in link_lengths:
                fail(f"Routing table uses an unknown directed link: {edge}")
            link_loads[edge] += volume
            total_routed_traffic += volume
            traffic_wire_cost += volume * link_lengths[edge]
            previous = current
            current = next_node

    return max(link_loads.values()), total_routed_traffic, traffic_wire_cost


def generate_splif_routing(chiplets, placement, topology):
    """Generate the same deterministic SPLIF table as generate_routing.py."""
    ici_graph = hlp.construct_ici_graph(chiplets, placement, topology)
    nodes = ici_graph["nodes"]
    relay_map = ici_graph["relay_map"]
    adjacency = ici_graph["adj_list"]
    destinations = [node for node in nodes if node[0] == "chiplet"]
    routing_table = {
        node: {destination: None for destination in destinations}
        for node in nodes
    }

    for destination in destinations:
        distances = {node: float("inf") for node in nodes}
        next_hops = {node: None for node in nodes}
        pending = queue.PriorityQueue()
        distances[destination] = 0
        pending.put((0, destination))

        while not pending.empty():
            current_distance, current = pending.get()
            if current_distance > distances[current]:
                continue
            for neighbor in adjacency[current]:
                neighbor_distance = current_distance + 1
                previous_next = next_hops[neighbor]
                better_tie = (
                    neighbor_distance == distances[neighbor]
                    and previous_next is not None
                    and (
                        (previous_next[0] == "chiplet" and current[0] == "irouter")
                        or (
                            previous_next[0] == current[0]
                            and previous_next[1] > current[1]
                        )
                    )
                )
                if neighbor_distance < distances[neighbor] or better_tie:
                    distances[neighbor] = neighbor_distance
                    next_hops[neighbor] = current
                    if relay_map[neighbor]:
                        pending.put((neighbor_distance, neighbor))

        for node in nodes:
            if node != destination:
                if next_hops[node] is None:
                    fail(f"SPLIF found no route from {node} to {destination}")
                routing_table[node][destination] = next_hops[node]

    return {"type": "default", "table": routing_table}


def evaluate(topology, context):
    routing = generate_splif_routing(
        context["chiplets"], context["placement"], topology
    )
    inputs = {
        "design": context["design"],
        "chiplets": context["chiplets"],
        "placement": context["placement"],
        "topology": topology,
        "packaging": context["packaging"],
        "technologies": context["technologies"],
        "routing_table": routing,
        "traffic_by_chiplet": context["traffic"],
        "verbose": False,
        "validate": False,
    }
    intermediates = {}
    link_lengths = rc.compute_link_lengths(inputs, intermediates)
    intermediates["link_lengths"] = link_lengths
    link_latencies = rc.compute_link_latencies(inputs, intermediates)
    intermediates["link_latencies"] = link_latencies
    link_bandwidths = rc.compute_link_bandwidths(inputs, intermediates)
    intermediates["link_bandwidths"] = link_bandwidths
    latency = rc.compute_latency(inputs, intermediates)
    throughput = rc.compute_throughput(inputs, intermediates)
    max_load, total_routed, traffic_wire_cost = route_statistics(
        topology, routing, context["traffic"], link_lengths
    )
    return {
        "link_lengths": link_lengths,
        "link_latencies": link_latencies,
        "avg_latency": latency["avg"],
        "throughput": throughput["aggregate_throughput"],
        "max_load": max_load,
        "total_routed_traffic": total_routed,
        "traffic_wire_cost": traffic_wire_cost,
    }


def load_context():
    design = hlp.read_json(str(DESIGN_FILE))
    chiplets = hlp.read_json(design["chiplets"])
    placement = hlp.read_json(design["placement"])
    topology = hlp.read_json(design["topology"])
    packaging = hlp.read_json(design["packaging"])
    technologies = hlp.read_json(design["technologies"])
    node_count = len(placement["chiplets"])
    if node_count != EXPECTED_CHIPLETS:
        fail(f"Expected exactly 16 placed chiplets; found {node_count}")

    mesh_edges = set()
    used_phys = {chiplet_id: set() for chiplet_id in range(node_count)}
    chiplet_link_count = 0
    for link in topology:
        ep1, ep2 = link["ep1"], link["ep2"]
        if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
            continue
        chiplet_link_count += 1
        u, v = ep1["outer_id"], ep2["outer_id"]
        if u == v or not (0 <= u < node_count and 0 <= v < node_count):
            fail(f"Invalid baseline chiplet link: {u}<->{v}")
        edge = tuple(sorted((u, v)))
        if edge in mesh_edges:
            fail(f"Duplicate baseline mesh edge: {edge}")
        mesh_edges.add(edge)
        used_phys[u].add(ep1["inner_id"])
        used_phys[v].add(ep2["inner_id"])

    if chiplet_link_count != EXPECTED_MESH_LINKS:
        fail(
            f"Expected exactly {EXPECTED_MESH_LINKS} chiplet-chiplet mesh links; "
            f"found {chiplet_link_count}"
        )

    for chiplet_id, chiplet_desc in enumerate(placement["chiplets"]):
        phy_count = len(chiplets[chiplet_desc["name"]]["phys"])
        for phy_id in SPARE_PHYS:
            if not 0 <= phy_id < phy_count:
                fail(f"Spare PHY {phy_id} is invalid for chiplet {chiplet_id}")
            if phy_id in used_phys[chiplet_id]:
                fail(f"Spare PHY {phy_id} is already used on chiplet {chiplet_id}")

    traffic, traffic_source = load_uniform_reference(node_count)
    design_for_reference = copy.deepcopy(design)
    design_for_reference["design_name"] = REFERENCE_TRAFFIC_NAME
    return {
        "design": design_for_reference,
        "chiplets": chiplets,
        "placement": placement,
        "topology": topology,
        "packaging": packaging,
        "technologies": technologies,
        "traffic": traffic,
        "traffic_source": traffic_source,
        "node_count": node_count,
        "mesh_edges": mesh_edges,
    }


def evaluate_candidates(context, baseline):
    candidates = []
    for u in range(context["node_count"]):
        for v in range(u + 1, context["node_count"]):
            if (u, v) in context["mesh_edges"]:
                continue
            estimated_length, phy_u, phy_v = best_phy_pair(
                u,
                v,
                context["placement"],
                context["chiplets"],
                context["packaging"],
            )
            topology = copy.deepcopy(context["topology"])
            topology.append(
                {
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
            )
            evaluated = evaluate(topology, context)
            node_u, node_v = ("chiplet", u), ("chiplet", v)
            actual_length = evaluated["link_lengths"][(node_u, node_v)]
            if not math.isclose(
                estimated_length, actual_length, rel_tol=EPS, abs_tol=EPS
            ):
                fail(
                    f"Physical length mismatch for {u}<->{v}: "
                    f"PHY estimate {estimated_length}, RapidChiplet {actual_length}"
                )
            candidates.append(
                {
                    "u": u,
                    "v": v,
                    "phy_u": phy_u,
                    "phy_v": phy_v,
                    "length_mm": actual_length,
                    "link_latency_cycles": evaluated["link_latencies"][(node_u, node_v)],
                    "reference_avg_latency": evaluated["avg_latency"],
                    "reference_latency_gain_percent": (
                        100.0
                        * (baseline["avg_latency"] - evaluated["avg_latency"])
                        / baseline["avg_latency"]
                    ),
                    "reference_max_link_load": evaluated["max_load"],
                    "reference_throughput": evaluated["throughput"],
                    "total_routed_traffic": evaluated["total_routed_traffic"],
                    "traffic_wire_cost": evaluated["traffic_wire_cost"],
                }
            )

    if len(candidates) != EXPECTED_CANDIDATES:
        fail(
            f"Expected exactly {EXPECTED_CANDIDATES} non-mesh candidates; "
            f"evaluated {len(candidates)}"
        )
    return candidates


def select_by_budget(candidates, baseline, mesh_edges):
    selections = []
    for budget in BUDGETS:
        feasible = [
            candidate
            for candidate in candidates
            if candidate["length_mm"] <= budget + EPS
            and candidate["reference_max_link_load"] <= baseline["max_load"] + EPS
        ]
        if not feasible:
            fail(f"No feasible fixed K=1 candidate for wire budget {budget:.0f} mm")
        best = min(
            feasible,
            key=lambda candidate: (
                candidate["reference_avg_latency"],
                candidate["reference_max_link_load"],
                candidate["length_mm"],
                candidate["u"],
                candidate["v"],
                candidate["phy_u"],
                candidate["phy_v"],
            ),
        )
        if best["length_mm"] > budget + EPS:
            fail(f"Selected B{budget:.0f} link exceeds its wire budget")
        if best["reference_max_link_load"] > baseline["max_load"] + EPS:
            fail(f"Selected B{budget:.0f} link exceeds the baseline max-link load")
        if (best["u"], best["v"]) in mesh_edges:
            fail(f"Selected B{budget:.0f} link is an existing mesh edge")
        if best["phy_u"] not in SPARE_PHYS or best["phy_v"] not in SPARE_PHYS:
            fail(f"Selected B{budget:.0f} link uses an invalid spare PHY")

        selections.append(
            {
                "budget_mm": budget,
                **best,
                "reference_baseline_max_link_load": baseline["max_load"],
                "reference_baseline_throughput": baseline["throughput"],
                "reference_traffic": REFERENCE_TRAFFIC_NAME,
                "selection_policy": SELECTION_POLICY,
            }
        )
    if len(selections) != len(BUDGETS):
        fail(f"Expected four fixed selections; found {len(selections)}")
    return selections


def write_selections(selections):
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(
            {field: selection[field] for field in CSV_FIELDS}
            for selection in selections
        )


def print_selections(selections):
    for selection in selections:
        print()
        print(f"Wire budget: {selection['budget_mm']:.0f} mm")
        print(f"Selected fixed link: {selection['u']} <-> {selection['v']}")
        print(f"PHY pair: {selection['phy_u']} <-> {selection['phy_v']}")
        print(f"Physical length: {selection['length_mm']:.3f} mm")
        print(f"Link latency: {selection['link_latency_cycles']} cycles")
        print(f"Average reference latency: {selection['reference_avg_latency']:.3f}")
        print(
            "Reference latency improvement: "
            f"{selection['reference_latency_gain_percent']:.2f}%"
        )
        print(f"Maximum link load: {selection['reference_max_link_load']:.3f}")
        print(f"Analytical throughput: {selection['reference_throughput']:.3f}")


def load_workload_aware_comparison():
    """Load comparison data only after fixed selections have been frozen."""
    try:
        with WORKLOAD_SELECTION_FILE.open(newline="", encoding="utf-8") as input_file:
            rows = list(csv.DictReader(input_file))
    except OSError as error:
        fail(f"Cannot load informational comparison CSV: {error}")

    comparison = {}
    for row_number, row in enumerate(rows, start=2):
        try:
            key = (row["workload"], int(float(row["budget_mm"])))
            link = f"{int(row['u'])}<->{int(row['v'])}"
        except (KeyError, TypeError, ValueError) as error:
            fail(f"Malformed workload-aware comparison row {row_number}: {error}")
        if key in comparison:
            fail(f"Duplicate workload-aware comparison entry: {key}")
        comparison[key] = link
    return comparison


def print_comparison(selections):
    comparison = load_workload_aware_comparison()
    fixed_by_budget = {
        int(selection["budget_mm"]): f"{selection['u']}<->{selection['v']}"
        for selection in selections
    }
    print()
    print("FIXED VS WORKLOAD-AWARE K=1 (INFORMATION ONLY)")
    print("-" * 85)
    print(
        f"{'BUDGET':<8}{'FIXED':<10}{'RANDOM':<12}{'TRANSPOSE':<12}"
        f"{'PERMUTATION':<14}{'HOTSPOT':<10}"
    )
    for budget in map(int, BUDGETS):
        links = []
        for workload in COMPARISON_WORKLOADS:
            key = (workload, budget)
            if key not in comparison:
                fail(f"Missing workload-aware comparison entry: {workload} B{budget}")
            links.append(comparison[key])
        print(
            f"{str(budget) + 'mm':<8}{fixed_by_budget[budget]:<10}"
            f"{links[0]:<12}{links[1]:<12}{links[2]:<14}{links[3]:<10}"
        )


def main():
    print("=" * 90)
    print("FIXED K=1 SELECTION — UNIFORM REFERENCE")
    print("=" * 90)
    context = load_context()
    baseline = evaluate(context["topology"], context)
    if baseline["avg_latency"] <= 0:
        fail("Uniform-reference baseline average latency must be positive")

    print(f"Reference source: {context['traffic_source']}")
    print(f"Baseline average latency: {baseline['avg_latency']:.3f}")
    print(f"Baseline max link load: {baseline['max_load']:.3f}")
    print(f"Baseline analytical throughput: {baseline['throughput']:.3f}")
    print()
    print(f"Evaluating {EXPECTED_CANDIDATES} non-mesh K=1 candidates...")
    candidates = evaluate_candidates(context, baseline)
    print(f"Evaluated candidates: {len(candidates)}")

    # The fixed decisions are complete before the workload-aware CSV is read.
    selections = select_by_budget(candidates, baseline, context["mesh_edges"])
    write_selections(selections)
    print_selections(selections)
    print_comparison(selections)

    print()
    print(f"Saved: {OUTPUT_FILE.relative_to(REPO_ROOT).as_posix()}")
    print("FIXED K=1 SELECTION COMPLETE")


if __name__ == "__main__":
    main()
