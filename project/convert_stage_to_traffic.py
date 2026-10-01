#!/usr/bin/env python3
"""Convert inspected STAGE traces into RapidChiplet traffic inputs.

The converter consumes the real Chakra traces through ``inspect_stage_traces``.
Explicit SEND/RECV traffic is counted once from SEND records.  Abstract
collectives are expanded with the ring algorithms configured by the checked-in
STAGE demo system file.  No physical RapidChiplet routing is performed here.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
import inspect_stage_traces as stage_inspector
import validation as rapid_validation


NUM_CHIPLETS = 16
DESIGN_PATH = ROOT / "inputs" / "designs" / "design_project_physical_mesh_8phy.json"
INVENTORY_PATH = ROOT / "results" / "stage_trace_inventory.csv"
COMMUNICATION_SUMMARY_PATH = ROOT / "results" / "stage_communication_summary.csv"
VALIDATION_PATH = ROOT / "results" / "stage_conversion_validation.csv"
STAGE_SYSTEM_PATH = ROOT / "stage" / "demo" / "isca26" / "system.json"
CANONICAL_PROJECT_WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
DEFAULT_COLLECTIVE_POLICY = "stage_demo_ring_v1"
EPS = 1e-10


class ConversionError(RuntimeError):
    """Raised when conversion would require an unsupported assumption."""


def fail(message: str) -> None:
    raise ConversionError(message)


def read_csv(path: Path, description: str) -> list[dict[str, str]]:
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        fail(f"Cannot read {description} {path}: {exc}")
    if not rows:
        fail(f"Empty {description}: {path}")
    return rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def finite_nonnegative(value: Any, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        fail(f"{context} is not numeric: {value!r}")
    if not math.isfinite(number) or number < 0:
        fail(f"{context} must be finite and non-negative: {number}")
    return number


def derive_target_max_flow() -> tuple[float, str]:
    maxima: list[tuple[float, Path]] = []
    for workload in CANONICAL_PROJECT_WORKLOADS:
        path = ROOT / "inputs" / "traffic_by_chiplet" / f"traffic_project_{workload}.json"
        if not path.is_file():
            fail(f"Cannot derive TARGET_MAX_FLOW; missing canonical traffic {path}")
        traffic = hlp.read_json(str(path))
        if not isinstance(traffic, dict) or not traffic:
            fail(f"Canonical traffic is empty or malformed: {path}")
        values = [finite_nonnegative(value, f"traffic value in {path}") for value in traffic.values()]
        maxima.append((max(values), path))
    target = max(value for value, _path in maxima)
    if target <= 0:
        fail("Canonical project workloads have no positive traffic value")
    source = ";".join(path.relative_to(ROOT).as_posix() for _value, path in maxima)
    return target, source


def load_stage_ring_configuration() -> dict[str, str]:
    if not STAGE_SYSTEM_PATH.is_file():
        fail(f"Missing STAGE collective configuration: {STAGE_SYSTEM_PATH}")
    try:
        config = json.loads(STAGE_SYSTEM_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot parse STAGE collective configuration {STAGE_SYSTEM_PATH}: {exc}")
    keys = {
        "ALL_REDUCE": "all-reduce-implementation",
        "ALL_GATHER": "all-gather-implementation",
        "REDUCE_SCATTER": "reduce-scatter-implementation",
        "ALL_TO_ALL": "all-to-all-implementation",
    }
    implementations: dict[str, str] = {}
    for comm_type, key in keys.items():
        value = config.get(key)
        if not isinstance(value, list) or value != ["ring"]:
            fail(f"STAGE setup does not justify ring expansion for {comm_type}: {key}={value!r}")
        implementations[comm_type] = "ring"
    return implementations


def mapping_for_ranks(ranks: list[int]) -> tuple[dict[int, int], str]:
    if ranks != list(range(len(ranks))):
        fail(f"STAGE ranks must be deterministic and contiguous from zero; found {ranks}")
    if len(ranks) > NUM_CHIPLETS:
        fail(
            f"Trace has {len(ranks)} ranks but only {NUM_CHIPLETS} chiplets; "
            "no justified many-ranks-per-chiplet mapping is configured"
        )
    mapping = {rank: rank for rank in ranks}
    if len(ranks) == NUM_CHIPLETS:
        policy = "one_to_one_rank_i_to_chiplet_i"
    else:
        policy = "one_to_one_rank_i_to_chiplet_i_remaining_chiplets_idle"
    return mapping, policy


def load_nodes(workload: stage_inspector.Workload) -> list[stage_inspector.Node]:
    nodes: list[stage_inspector.Node] = []
    for path in workload.trace_files:
        rank = stage_inspector._rank_from_path(path)
        if path.suffix.lower() == ".et":
            parsed, _schema = stage_inspector._parse_proto_trace(path, rank)
        elif path.suffix.lower() == ".json":
            parsed, _schema = stage_inspector._parse_json_trace(path, rank)
        else:
            fail(f"Unsupported STAGE trace file: {path}")
        nodes.extend(parsed)
    return nodes


def logical_communication(
    workload: stage_inspector.Workload,
    nodes: list[stage_inspector.Node],
) -> tuple[list[tuple[int, int, int]], list[tuple[str, tuple[int, ...], int]], int]:
    sends: Counter[tuple[int, int, int, int]] = Counter()
    recvs: Counter[tuple[int, int, int, int]] = Counter()
    collective_observations: dict[tuple[int, str, int, str], list[tuple[int, int]]] = defaultdict(list)
    for node in nodes:
        if node.node_type == "COMM_SEND_NODE":
            sends[(
                node.rank,
                stage_inspector._required_int(node, "comm_dst"),
                stage_inspector._required_int(node, "comm_tag"),
                stage_inspector._required_int(node, "comm_size"),
            )] += 1
        elif node.node_type == "COMM_RECV_NODE":
            recvs[(
                stage_inspector._required_int(node, "comm_src"),
                node.rank,
                stage_inspector._required_int(node, "comm_tag"),
                stage_inspector._required_int(node, "comm_size"),
            )] += 1
        elif node.node_type == "COMM_COLL_NODE":
            value = node.attrs.get("comm_type")
            if isinstance(value, int):
                comm_type = stage_inspector.COLLECTIVE_TYPES.get(value)
            else:
                comm_type = value if isinstance(value, str) else None
            if comm_type not in {"ALL_REDUCE", "ALL_GATHER", "REDUCE_SCATTER", "ALL_TO_ALL"}:
                fail(f"Unsupported collective type {value!r} in {workload.name}")
            group_id = stage_inspector._group_id(node)
            key = (node.node_id, node.name, group_id, str(comm_type))
            collective_observations[key].append(
                (node.rank, stage_inspector._required_int(node, "comm_size"))
            )
    if sends != recvs:
        fail(f"{workload.name}: SEND and RECV records do not pair exactly")

    point_to_point: list[tuple[int, int, int]] = []
    for (source, destination, _tag, size), count in sorted(sends.items()):
        point_to_point.extend((source, destination, size) for _ in range(count))

    collectives: list[tuple[str, tuple[int, ...], int]] = []
    for (_node_id, _name, group_id, comm_type), observations in sorted(collective_observations.items()):
        if group_id not in workload.comm_groups:
            fail(f"{workload.name}: unknown collective group {group_id}")
        participants = workload.comm_groups[group_id]
        if tuple(sorted(rank for rank, _size in observations)) != tuple(sorted(participants)):
            fail(f"{workload.name}: collective group {group_id} participant mismatch")
        sizes = {size for _rank, size in observations}
        if len(sizes) != 1:
            fail(f"{workload.name}: collective group {group_id} has inconsistent comm_size")
        collectives.append((comm_type, participants, next(iter(sizes))))

    original_payload = sum(size for _src, _dst, size in point_to_point) + sum(
        size for _comm_type, _participants, size in collectives
    )
    return point_to_point, collectives, original_payload


def add_flow(matrix: dict[tuple[int, int], Fraction], source: int, destination: int, amount: Fraction) -> None:
    if amount < 0:
        fail("Collective expansion produced negative traffic")
    if source == destination or amount == 0:
        return
    if not (0 <= source < NUM_CHIPLETS and 0 <= destination < NUM_CHIPLETS):
        fail(f"Invalid mapped chiplet pair {source}->{destination}")
    matrix[(source, destination)] += amount


def expand_ring_collective(
    matrix: dict[tuple[int, int], Fraction],
    comm_type: str,
    participants: tuple[int, ...],
    size: int,
    rank_mapping: dict[int, int],
) -> Fraction:
    """Expand one abstract collective using STAGE's configured ring policy.

    STAGE records the pre-collective local tensor in ``comm_size``.  Thus an
    all-gather starts with ``size`` bytes at every rank, while reduce-scatter
    and all-reduce start with a full local tensor of ``size`` bytes.  Ring
    volumes below are standard aggregate endpoint transfers; routing hops are
    intentionally left for RapidChiplet.
    """
    n = len(participants)
    if n <= 1 or size == 0:
        return Fraction(0)
    mapped = tuple(rank_mapping[rank] for rank in participants)
    before = sum(matrix.values(), Fraction(0))
    if comm_type == "ALL_REDUCE":
        # Reduce-scatter plus all-gather: each rank sends 2*(n-1)/n tensors
        # to its next logical-ring neighbor.
        per_edge = Fraction(2 * (n - 1) * size, n)
        for index, source in enumerate(mapped):
            add_flow(matrix, source, mapped[(index + 1) % n], per_edge)
        expected = Fraction(2 * (n - 1) * size)
    elif comm_type == "ALL_GATHER":
        # Each rank's local ``size``-byte shard traverses n-1 ring steps.
        per_edge = Fraction((n - 1) * size)
        for index, source in enumerate(mapped):
            add_flow(matrix, source, mapped[(index + 1) % n], per_edge)
        expected = Fraction(n * (n - 1) * size)
    elif comm_type == "REDUCE_SCATTER":
        per_edge = Fraction((n - 1) * size, n)
        for index, source in enumerate(mapped):
            add_flow(matrix, source, mapped[(index + 1) % n], per_edge)
        expected = Fraction((n - 1) * size)
    elif comm_type == "ALL_TO_ALL":
        # Ring schedule, direct logical endpoints: every rank exchanges one
        # n-th chunk with every other rank.  Self chunks are local.
        per_peer = Fraction(size, n)
        for source in mapped:
            for destination in mapped:
                if destination != source:
                    add_flow(matrix, source, destination, per_peer)
        expected = Fraction((n - 1) * size)
    else:
        fail(f"No ring expansion semantics for collective {comm_type}")
    observed = sum(matrix.values(), Fraction(0)) - before
    if observed != expected:
        fail(f"Internal ring conservation failure for {comm_type}: {observed} != {expected}")
    return expected


def summary_signature(rows: list[dict[str, Any]], workload: str) -> Counter[tuple[Any, ...]]:
    signature: Counter[tuple[Any, ...]] = Counter()
    for row in rows:
        if str(row.get("workload")) != workload:
            continue
        key = (
            str(row.get("communication_type")),
            str(row.get("communication_class")),
            int(row.get("participant_count", 0)),
            str(row.get("source_rank", "")),
            str(row.get("destination_rank", "")),
            int(row.get("event_count", 0)),
            round(float(row.get("total_payload_bytes", 0)), 9),
        )
        signature[key] += 1
    return signature


def validate_inspection_inputs(
    workload: stage_inspector.Workload,
    inventory_rows: list[dict[str, str]],
    stored_summary_rows: list[dict[str, str]],
    recomputed_inventory: dict[str, Any],
    recomputed_summary: list[dict[str, Any]],
) -> None:
    matches = [row for row in inventory_rows if row.get("workload") == workload.name]
    if len(matches) != 1:
        fail(f"Expected one inventory row for {workload.name}; found {len(matches)}")
    stored = matches[0]
    checks = {
        "number_of_ranks": int(recomputed_inventory["number_of_ranks"]),
        "communication_event_count": int(recomputed_inventory["communication_event_count"]),
        "total_communication_bytes": int(recomputed_inventory["total_communication_bytes"]),
    }
    for field, expected in checks.items():
        if int(stored.get(field, -1)) != expected:
            fail(f"{workload.name}: stored inventory {field} does not match trace recomputation")
    if summary_signature(stored_summary_rows, workload.name) != summary_signature(recomputed_summary, workload.name):
        fail(f"{workload.name}: stored communication summary does not match actual traces")


def chiplet_to_unit_traffic(
    chiplet_traffic: dict[tuple[int, int], float],
    chiplets: dict[str, Any],
    placement: dict[str, Any],
) -> dict[tuple[tuple[int, int], tuple[int, int]], float]:
    """Use the project generator's equal per-unit-pair distribution convention."""
    unit_traffic: dict[tuple[tuple[int, int], tuple[int, int]], float] = {}
    for (source, destination), value in chiplet_traffic.items():
        source_desc = chiplets[placement["chiplets"][source]["name"]]
        destination_desc = chiplets[placement["chiplets"][destination]["name"]]
        source_units = int(source_desc["unit_count"])
        destination_units = int(destination_desc["unit_count"])
        if source_units <= 0 or destination_units <= 0:
            fail(f"Chiplet pair {source}->{destination} has no units")
        per_pair = value / (source_units * destination_units)
        for source_unit in range(source_units):
            for destination_unit in range(destination_units):
                unit_traffic[((source, source_unit), (destination, destination_unit))] = per_pair
    return unit_traffic


def validate_generated_traffic(
    chiplet_path: Path,
    unit_path: Path,
    expected_chiplet: dict[tuple[int, int], float],
    chiplets: dict[str, Any],
    placement: dict[str, Any],
    design: dict[str, Any],
) -> None:
    loaded_chiplet = hlp.read_json(str(chiplet_path))
    loaded_unit = hlp.read_json(str(unit_path))
    if not isinstance(loaded_chiplet, dict) or not isinstance(loaded_unit, dict):
        fail("RapidChiplet helper did not load generated traffic dictionaries")
    if set(loaded_chiplet) != set(expected_chiplet):
        fail(f"Generated chiplet traffic key mismatch in {chiplet_path}")
    for pair, expected in expected_chiplet.items():
        actual = finite_nonnegative(loaded_chiplet[pair], f"generated flow {pair}")
        if pair[0] == pair[1]:
            fail(f"Generated traffic contains unintended self flow {pair}")
        if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
            fail(f"Generated chiplet flow changed during JSON round-trip: {pair}")
    aggregated = hlp.convert_by_unit_traffic_to_by_chiplet_traffic(loaded_unit)
    if set(aggregated) != set(expected_chiplet):
        fail(f"Unit traffic does not aggregate to chiplet traffic in {unit_path}")
    for pair, expected in expected_chiplet.items():
        if not math.isclose(float(aggregated[pair]), expected, rel_tol=1e-11, abs_tol=1e-11):
            fail(f"Unit traffic aggregation mismatch for {pair}")

    common = {
        "design": design,
        "chiplets": chiplets,
        "placement": placement,
        "validate": True,
        "verbose": False,
    }
    rapid_validation.validate_traffic_by_chiplet({**common, "traffic_by_chiplet": loaded_chiplet})
    rapid_validation.validate_traffic_by_unit({**common, "traffic_by_unit": loaded_unit})


def convert_workload(
    workload: stage_inspector.Workload,
    inventory_rows: list[dict[str, str]],
    stored_summary_rows: list[dict[str, str]],
    target_max_flow: float,
    target_source: str,
    collective_policy: str,
    chiplets: dict[str, Any],
    placement: dict[str, Any],
    design: dict[str, Any],
) -> dict[str, Any]:
    recomputed_inventory, recomputed_summary = stage_inspector._analyze(workload)
    validate_inspection_inputs(
        workload, inventory_rows, stored_summary_rows, recomputed_inventory, recomputed_summary
    )
    nodes = load_nodes(workload)
    ranks = sorted({node.rank for node in nodes})
    rank_mapping, mapping_policy = mapping_for_ranks(ranks)
    point_to_point, collectives, original_payload = logical_communication(workload, nodes)
    if original_payload != int(recomputed_inventory["total_communication_bytes"]):
        fail(f"{workload.name}: logical payload differs from inspected inventory")

    matrix = {(source, destination): Fraction(0) for source in range(NUM_CHIPLETS) for destination in range(NUM_CHIPLETS)}
    explicit_network = Fraction(0)
    self_bytes = Fraction(0)
    for source_rank, destination_rank, size in point_to_point:
        source = rank_mapping[source_rank]
        destination = rank_mapping[destination_rank]
        if source == destination:
            self_bytes += size
        else:
            add_flow(matrix, source, destination, Fraction(size))
            explicit_network += size

    expanded_collective_network = Fraction(0)
    original_collective_network_payload = 0
    if collectives and collective_policy == "reject_abstract":
        fail(f"{workload.name}: abstract collectives present and policy is reject_abstract")
    for comm_type, participants, size in collectives:
        if len(participants) <= 1:
            self_bytes += size
            continue
        original_collective_network_payload += size
        expanded_collective_network += expand_ring_collective(
            matrix, comm_type, participants, size, rank_mapping
        )

    raw_network = sum(matrix.values(), Fraction(0))
    expected_network = explicit_network + expanded_collective_network
    if raw_network != expected_network:
        fail(f"{workload.name}: mapped network bytes {raw_network} != policy-expanded bytes {expected_network}")
    if any(value < 0 for value in matrix.values()):
        fail(f"{workload.name}: negative raw traffic")
    if any(matrix[(chiplet, chiplet)] for chiplet in range(NUM_CHIPLETS)):
        fail(f"{workload.name}: raw matrix contains self-network traffic")
    positive = {pair: value for pair, value in matrix.items() if value > 0}
    if not positive:
        fail(f"{workload.name}: conversion produced no network traffic")

    maximum_raw = max(positive.values())
    normalization_factor = target_max_flow / float(maximum_raw)
    normalized = {pair: float(value) * normalization_factor for pair, value in positive.items()}
    for pair, raw_value in positive.items():
        if not math.isclose(normalized[pair] / float(raw_value), normalization_factor, rel_tol=1e-12, abs_tol=1e-12):
            fail(f"{workload.name}: normalization changed relative flow ratios")
    largest_pair = min(
        (pair for pair, value in positive.items() if value == maximum_raw),
        key=lambda pair: (pair[0], pair[1]),
    )
    largest_normalized = normalized[largest_pair]
    if not math.isclose(largest_normalized, target_max_flow, rel_tol=1e-12, abs_tol=1e-12):
        fail(f"{workload.name}: normalization ceiling is not TARGET_MAX_FLOW")

    chiplet_path = ROOT / "inputs" / "traffic_by_chiplet" / f"traffic_stage_{workload.name}.json"
    unit_path = ROOT / "inputs" / "traffic_by_unit" / f"traffic_stage_{workload.name}.json"
    mapping_path = ROOT / "results" / f"stage_mapping_{workload.name}.csv"
    unit_traffic = chiplet_to_unit_traffic(normalized, chiplets, placement)
    hlp.write_json(str(chiplet_path), normalized)
    hlp.write_json(str(unit_path), unit_traffic)
    write_csv(
        mapping_path,
        ["workload", "rank", "chiplet", "mapping_policy"],
        [
            {
                "workload": workload.name,
                "rank": rank,
                "chiplet": rank_mapping[rank],
                "mapping_policy": mapping_policy,
            }
            for rank in ranks
        ],
    )
    validate_generated_traffic(
        chiplet_path, unit_path, normalized, chiplets, placement, design
    )

    original_network_payload = explicit_network + original_collective_network_payload
    raw_total = raw_network + self_bytes
    row = {
        "workload": workload.name,
        "num_stage_ranks": len(ranks),
        "num_mapped_chiplets": len(set(rank_mapping.values())),
        "raw_total_communication_bytes": float(raw_total),
        "raw_self_communication_bytes": float(self_bytes),
        "raw_network_communication_bytes": float(raw_network),
        "converted_total_before_normalization": float(sum(positive.values(), Fraction(0))),
        "normalization_factor": normalization_factor,
        "normalized_total_traffic": sum(normalized.values()),
        "num_nonzero_chiplet_pairs": len(positive),
        "largest_raw_flow_src": largest_pair[0],
        "largest_raw_flow_dst": largest_pair[1],
        "largest_raw_flow_bytes": float(maximum_raw),
        "largest_normalized_flow": largest_normalized,
        "collective_expansion_policy": collective_policy,
        "mapping_policy": mapping_policy,
        "conversion_valid": True,
        "original_stage_payload_bytes": original_payload,
        "original_stage_network_payload_bytes": float(original_network_payload),
        "explicit_point_to_point_network_bytes": float(explicit_network),
        "expanded_collective_network_bytes": float(expanded_collective_network),
        "target_max_flow": target_max_flow,
        "target_max_flow_source": target_source,
        "unit_distribution_policy": "equal_across_all_source_and_destination_unit_pairs",
        "traffic_by_chiplet_path": chiplet_path.relative_to(ROOT).as_posix(),
        "traffic_by_unit_path": unit_path.relative_to(ROOT).as_posix(),
        "mapping_path": mapping_path.relative_to(ROOT).as_posix(),
    }

    print(f"STAGE -> RAPIDCHIPLET CONVERSION — {workload.name.upper()}")
    print(f"Ranks: {len(ranks)}")
    print(f"Mapping policy: {mapping_policy}")
    print(f"Original STAGE communication: {original_payload} bytes")
    print(f"Policy-expanded raw communication: {float(raw_total):.6f} bytes")
    print(f"Network communication: {float(raw_network):.6f} bytes")
    print(f"Nonzero chiplet pairs: {len(positive)}")
    print(f"Normalization factor: {normalization_factor:.12g}")
    print(f"Largest flow: chiplet {largest_pair[0]} -> {largest_pair[1]}")
    print(f"Output chiplet traffic: {chiplet_path.relative_to(ROOT).as_posix()}")
    print(f"Output unit traffic: {unit_path.relative_to(ROOT).as_posix()}")
    print("Validation: PASS")
    print()
    return row


def main(argv: list[str] | None = None) -> int:
    # MSYS Python on Windows may otherwise replace the requested Unicode arrow
    # and em dash even though the source and generated files are UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=stage_inspector.DEFAULT_MANIFEST)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--communication-summary", type=Path, default=COMMUNICATION_SUMMARY_PATH)
    parser.add_argument(
        "--collective-policy",
        choices=(DEFAULT_COLLECTIVE_POLICY, "reject_abstract"),
        default=DEFAULT_COLLECTIVE_POLICY,
        help="Explicit policy for abstract STAGE collectives",
    )
    parser.add_argument("--target-max-flow", type=float, help="Override the derived project traffic ceiling")
    args = parser.parse_args(argv)

    try:
        inventory_rows = read_csv(args.inventory, "STAGE trace inventory")
        stored_summary_rows = read_csv(args.communication_summary, "STAGE communication summary")
        workloads = stage_inspector._load_manifest(args.manifest)
        load_stage_ring_configuration()
        derived_target, derived_source = derive_target_max_flow()
        if args.target_max_flow is None:
            target_max_flow, target_source = derived_target, derived_source
        else:
            target_max_flow = finite_nonnegative(args.target_max_flow, "--target-max-flow")
            if target_max_flow <= 0:
                fail("--target-max-flow must be positive")
            target_source = "command_line_override"

        design = hlp.read_json(str(DESIGN_PATH))
        chiplets = hlp.read_json(str(ROOT / design["chiplets"]))
        placement = hlp.read_json(str(ROOT / design["placement"]))
        if len(placement.get("chiplets", [])) != NUM_CHIPLETS:
            fail(f"RapidChiplet placement has {len(placement.get('chiplets', []))} chiplets, expected {NUM_CHIPLETS}")

        validation_rows = [
            convert_workload(
                workload,
                inventory_rows,
                stored_summary_rows,
                target_max_flow,
                target_source,
                args.collective_policy,
                chiplets,
                placement,
                design,
            )
            for workload in workloads
        ]
        fields = [
            "workload", "num_stage_ranks", "num_mapped_chiplets",
            "raw_total_communication_bytes", "raw_self_communication_bytes",
            "raw_network_communication_bytes", "converted_total_before_normalization",
            "normalization_factor", "normalized_total_traffic", "num_nonzero_chiplet_pairs",
            "largest_raw_flow_src", "largest_raw_flow_dst", "largest_raw_flow_bytes",
            "largest_normalized_flow", "collective_expansion_policy", "mapping_policy",
            "conversion_valid", "original_stage_payload_bytes",
            "original_stage_network_payload_bytes", "explicit_point_to_point_network_bytes",
            "expanded_collective_network_bytes", "target_max_flow", "target_max_flow_source",
            "unit_distribution_policy", "traffic_by_chiplet_path", "traffic_by_unit_path",
            "mapping_path",
        ]
        write_csv(VALIDATION_PATH, fields, validation_rows)
        print(f"Validation summary: {VALIDATION_PATH.relative_to(ROOT).as_posix()}")
        print("STAGE TO RAPIDCHIPLET TRAFFIC CONVERSION COMPLETE")
        return 0
    except (ConversionError, stage_inspector.TraceError, OSError, KeyError, ValueError) as exc:
        print(f"STAGE to RapidChiplet conversion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
