#!/usr/bin/env python3
"""Inventory STAGE traces and summarize their communication structure.

The current official STAGE CLI emits length-delimited Chakra v0.0.4 protobuf
records.  This inspector decodes the small, documented subset of that schema
needed here using only the Python standard library, so it also works in the
repository's dependency-light ``python3`` environment.  STAGE's JSON backend
shape (``{"nodes": [...]}``) is supported as well.

Collectives remain abstract: their ``comm_size`` is counted once per logical
collective invocation, never expanded into guessed rank-to-rank transfers.
Point-to-point SEND/RECV records are paired and counted once from the SEND.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "stage" / "generated" / "rapidchiplet_representative" / "manifest.json"
INVENTORY_PATH = ROOT / "results" / "stage_trace_inventory.csv"
SUMMARY_PATH = ROOT / "results" / "stage_communication_summary.csv"

NODE_TYPES = {
    4: "COMP_NODE",
    5: "COMM_SEND_NODE",
    6: "COMM_RECV_NODE",
    7: "COMM_COLL_NODE",
}
COLLECTIVE_TYPES = {
    0: "ALL_REDUCE",
    1: "REDUCE",
    2: "ALL_GATHER",
    3: "GATHER",
    4: "SCATTER",
    5: "BROADCAST",
    6: "ALL_TO_ALL",
    7: "REDUCE_SCATTER",
    8: "REDUCE_SCATTER_BLOCK",
    9: "BARRIER",
}
RANK_RE = re.compile(r"(?:^|[._-])(\d+)(?=\.(?:et|json)$)", re.IGNORECASE)


class TraceError(RuntimeError):
    """Raised when a trace cannot be interpreted without guessing."""


@dataclass(frozen=True)
class Node:
    rank: int
    sequence: int
    node_id: int
    name: str
    node_type: str
    start_time_micros: int
    duration_micros: int
    attrs: dict[str, Any]


@dataclass
class Workload:
    name: str
    trace_path: Path
    trace_files: list[Path]
    comm_groups: dict[int, tuple[int, ...]]
    metadata: dict[str, Any]
    trace_format: str = ""
    schema_version: str = ""
    schema_name: str = ""


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(data) and shift < 70:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    raise TraceError("invalid or truncated protobuf varint")


def _protobuf_fields(data: bytes) -> list[tuple[int, int, Any]]:
    fields: list[tuple[int, int, Any]] = []
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise TraceError("invalid protobuf field number 0")
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 1:
            if pos + 8 > len(data):
                raise TraceError("truncated 64-bit protobuf field")
            value, pos = data[pos : pos + 8], pos + 8
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            if pos + length > len(data):
                raise TraceError("truncated length-delimited protobuf field")
            value, pos = data[pos : pos + length], pos + length
        elif wire == 5:
            if pos + 4 > len(data):
                raise TraceError("truncated 32-bit protobuf field")
            value, pos = data[pos : pos + 4], pos + 4
        else:
            raise TraceError(f"unsupported protobuf wire type {wire}")
        fields.append((number, wire, value))
    return fields


def _field_first(fields: Iterable[tuple[int, int, Any]], number: int, default: Any = None) -> Any:
    for field_number, _wire, value in fields:
        if field_number == number:
            return value
    return default


def _decode_text(value: bytes, context: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TraceError(f"non-UTF-8 {context}") from exc


def _decode_attribute(data: bytes) -> tuple[str, Any]:
    fields = _protobuf_fields(data)
    raw_name = _field_first(fields, 1)
    if not isinstance(raw_name, bytes):
        raise TraceError("Chakra attribute has no name")
    name = _decode_text(raw_name, "attribute name")
    # AttributeProto oneof field numbers used by STAGE's v0.0.4 backend.
    scalar_fields = {3: "double", 5: "float", 7: "int32", 9: "int64", 11: "uint32", 13: "uint64", 27: "bool", 29: "string", 31: "bytes"}
    present = [(number, wire, value) for number, wire, value in fields if number in scalar_fields]
    if not present:
        return name, None
    number, _wire, value = present[-1]
    if number == 29:
        return name, _decode_text(value, f"attribute {name}")
    if number == 31:
        return name, value
    if number in (3, 5):
        raise TraceError(f"unexpected floating-point communication attribute {name}")
    return name, int(value)


def _delimited_messages(path: Path) -> list[bytes]:
    data = path.read_bytes()
    messages: list[bytes] = []
    pos = 0
    while pos < len(data):
        length, pos = _read_varint(data, pos)
        if length <= 0 or pos + length > len(data):
            raise TraceError(f"invalid length-delimited record in {path}")
        messages.append(data[pos : pos + length])
        pos += length
    if not messages:
        raise TraceError(f"empty trace: {path}")
    return messages


def _parse_proto_trace(path: Path, rank: int) -> tuple[list[Node], str]:
    messages = _delimited_messages(path)
    metadata_fields = _protobuf_fields(messages[0])
    metadata_attrs = dict(
        _decode_attribute(value)
        for number, wire, value in metadata_fields
        if number == 2 and wire == 2
    )
    schema_name = str(metadata_attrs.get("schema", ""))
    if schema_name != "symbolic_tensor_network":
        raise TraceError(f"unsupported Chakra metadata schema {schema_name!r} in {path}")
    nodes: list[Node] = []
    for sequence, message in enumerate(messages[1:]):
        fields = _protobuf_fields(message)
        node_type_number = int(_field_first(fields, 3, 0))
        node_type = NODE_TYPES.get(node_type_number, f"NODE_TYPE_{node_type_number}")
        raw_name = _field_first(fields, 2, b"")
        attrs = dict(
            _decode_attribute(value)
            for number, wire, value in fields
            if number == 10 and wire == 2
        )
        nodes.append(
            Node(
                rank=rank,
                sequence=sequence,
                node_id=int(_field_first(fields, 1, 0)),
                name=_decode_text(raw_name, "node name") if isinstance(raw_name, bytes) else "",
                node_type=node_type,
                start_time_micros=int(_field_first(fields, 6, 0)),
                duration_micros=int(_field_first(fields, 7, 0)),
                attrs=attrs,
            )
        )
    return nodes, schema_name


def _parse_json_trace(path: Path, rank: int) -> tuple[list[Node], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceError(f"invalid JSON trace {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise TraceError(f"JSON file is not a STAGE JSON-backend trace: {path}")
    nodes: list[Node] = []
    type_map = {
        "comp_node": "COMP_NODE",
        "comm_send_node": "COMM_SEND_NODE",
        "comm_recv_node": "COMM_RECV_NODE",
        "coll_comm_node": "COMM_COLL_NODE",
    }
    common = {"id", "name", "node_type", "start_time_micros", "duration_micros", "data_deps", "ctrl_deps", "inputs", "outputs"}
    for sequence, raw in enumerate(payload["nodes"]):
        if not isinstance(raw, dict) or "node_type" not in raw:
            raise TraceError(f"malformed node {sequence} in {path}")
        nodes.append(
            Node(
                rank=rank,
                sequence=sequence,
                node_id=int(raw.get("id", 0)),
                name=str(raw.get("name", "")),
                node_type=type_map.get(str(raw["node_type"]), str(raw["node_type"]).upper()),
                start_time_micros=int(raw.get("start_time_micros", 0)),
                duration_micros=int(raw.get("duration_micros", 0)),
                attrs={key: value for key, value in raw.items() if key not in common},
            )
        )
    return nodes, str(payload.get("schema", "Json"))


def _rank_from_path(path: Path) -> int:
    matches = list(RANK_RE.finditer(path.name))
    if not matches:
        raise TraceError(f"cannot infer rank from trace filename {path.name!r}")
    return int(matches[-1].group(1))


def _load_comm_groups(directory: Path, trace_files: list[Path]) -> dict[int, tuple[int, ...]]:
    trace_jsons = {path.resolve() for path in trace_files if path.suffix.lower() == ".json"}
    candidates: list[tuple[Path, dict[int, tuple[int, ...]]]] = []
    for path in sorted(directory.glob("*.json")):
        if path.resolve() in trace_jsons or path.name == "manifest.json":
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not raw:
                continue
            groups = {int(key): tuple(int(rank) for rank in value) for key, value in raw.items() if isinstance(value, list)}
            if len(groups) == len(raw):
                candidates.append((path, groups))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if len(candidates) != 1:
        names = ", ".join(str(path) for path, _groups in candidates) or "none"
        raise TraceError(f"expected exactly one communication-group JSON in {directory}; found {names}")
    return candidates[0][1]


def _trace_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.et"))
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
            files.append(path)
    return sorted(files, key=lambda path: (_rank_from_path(path), path.name))


def _load_manifest(path: Path) -> list[Workload]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceError(f"cannot read manifest {path}: {exc}") from exc
    entries = raw.get("workloads") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or not entries:
        raise TraceError(f"manifest has no workloads: {path}")
    workloads: list[Workload] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("workload") or not entry.get("trace_path"):
            raise TraceError(f"invalid workload entry in {path}")
        trace_path = Path(entry["trace_path"])
        if not trace_path.is_absolute():
            trace_path = ROOT / trace_path
        files = _trace_files(trace_path)
        expected = entry.get("expected_ranks")
        if expected is not None and len(files) != int(expected):
            raise TraceError(f"{entry['workload']}: expected {expected} rank traces, found {len(files)}")
        workloads.append(
            Workload(
                name=str(entry["workload"]),
                trace_path=trace_path,
                trace_files=files,
                comm_groups=_load_comm_groups(trace_path, files),
                metadata=entry,
            )
        )
    return workloads


def _discover_sources(sources: list[str]) -> list[Workload]:
    grouped: dict[Path, list[Path]] = defaultdict(list)
    for source_text in sources:
        source = Path(source_text)
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.exists():
            raise TraceError(f"trace source does not exist: {source}")
        if source.is_file():
            # A STAGE ``trace.N.et`` is one rank of a logical trace set.  A
            # single-file argument therefore locates and inspects its sibling
            # ranks as well; otherwise collective membership could not be
            # validated or summarized without undercounting.
            siblings = _trace_files(source.parent)
            if source not in siblings:
                raise TraceError(f"file is not a recognized STAGE rank trace: {source}")
            grouped[source.parent].extend(siblings)
        else:
            directories = {path.parent for path in source.rglob("*.et")}
            for json_path in source.rglob("*.json"):
                try:
                    raw = json.loads(json_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
                    directories.add(json_path.parent)
            for directory in directories:
                grouped[directory].extend(_trace_files(directory))
    workloads: list[Workload] = []
    for directory, files in sorted(grouped.items(), key=lambda item: str(item[0])):
        unique_files = sorted(set(files), key=lambda path: (_rank_from_path(path), path.name))
        workloads.append(
            Workload(
                name=directory.name,
                trace_path=directory,
                trace_files=unique_files,
                comm_groups=_load_comm_groups(directory, unique_files),
                metadata={},
            )
        )
    if not workloads:
        raise TraceError("no STAGE rank traces found")
    return workloads


def _required_int(node: Node, attribute: str) -> int:
    value = node.attrs.get(attribute)
    if not isinstance(value, int) or value < 0:
        raise TraceError(f"rank {node.rank} node {node.node_id}: missing/invalid {attribute}")
    return value


def _group_id(node: Node) -> int:
    value = node.attrs.get("pg_name", node.attrs.get("comm_group"))
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TraceError(f"rank {node.rank} node {node.node_id}: missing/invalid communication group") from exc


def _analyze(workload: Workload) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ranks: list[int] = []
    nodes: list[Node] = []
    schema_names: set[str] = set()
    formats: set[str] = set()
    for path in workload.trace_files:
        rank = _rank_from_path(path)
        ranks.append(rank)
        if path.suffix.lower() == ".et":
            parsed, schema_name = _parse_proto_trace(path, rank)
            formats.add("Chakra length-delimited protobuf")
            workload.schema_version = "v0.0.4"
        elif path.suffix.lower() == ".json":
            parsed, schema_name = _parse_json_trace(path, rank)
            formats.add("STAGE JSON backend")
            workload.schema_version = "Json"
        else:
            raise TraceError(f"unsupported trace extension: {path}")
        nodes.extend(parsed)
        schema_names.add(schema_name)
    if len(ranks) != len(set(ranks)):
        raise TraceError(f"{workload.name}: duplicate rank trace files")
    if sorted(ranks) != list(range(len(ranks))):
        raise TraceError(f"{workload.name}: ranks are not contiguous from 0: {sorted(ranks)}")
    if len(formats) != 1 or len(schema_names) != 1:
        raise TraceError(f"{workload.name}: mixed trace formats/schemas are not supported")
    workload.trace_format = next(iter(formats))
    workload.schema_name = next(iter(schema_names))

    sends: Counter[tuple[int, int, int, int]] = Counter()
    recvs: Counter[tuple[int, int, int, int]] = Counter()
    collectives: dict[tuple[int, str, int, str], list[tuple[int, int]]] = defaultdict(list)
    timestamp_available = any(node.start_time_micros or node.duration_micros for node in nodes)
    for node in nodes:
        if node.node_type == "COMM_SEND_NODE":
            sends[(node.rank, _required_int(node, "comm_dst"), _required_int(node, "comm_tag"), _required_int(node, "comm_size"))] += 1
        elif node.node_type == "COMM_RECV_NODE":
            recvs[(_required_int(node, "comm_src"), node.rank, _required_int(node, "comm_tag"), _required_int(node, "comm_size"))] += 1
        elif node.node_type == "COMM_COLL_NODE":
            comm_type_value = node.attrs.get("comm_type")
            if isinstance(comm_type_value, int):
                if comm_type_value not in COLLECTIVE_TYPES:
                    raise TraceError(f"rank {node.rank} node {node.node_id}: unknown collective enum {comm_type_value}")
                comm_type = COLLECTIVE_TYPES[comm_type_value]
            elif isinstance(comm_type_value, str) and comm_type_value in set(COLLECTIVE_TYPES.values()):
                comm_type = comm_type_value
            else:
                raise TraceError(f"rank {node.rank} node {node.node_id}: invalid collective type")
            group_id = _group_id(node)
            collectives[(node.node_id, node.name, group_id, comm_type)].append((node.rank, _required_int(node, "comm_size")))

    if sends != recvs:
        missing_recvs = sends - recvs
        missing_sends = recvs - sends
        raise TraceError(f"{workload.name}: SEND/RECV mismatch; unmatched sends={dict(missing_recvs)}, unmatched receives={dict(missing_sends)}")

    rows_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for (source, destination, _tag, size), count in sorted(sends.items()):
        key = ("POINT_TO_POINT", source, destination)
        row = rows_by_key.setdefault(
            key,
            {
                "workload": workload.name,
                "communication_type": "SEND_RECV",
                "communication_class": "point_to_point",
                "event_count": 0,
                "total_payload_bytes": 0,
                "participant_count": 2,
                "source_rank": source,
                "destination_rank": destination,
                "representation": "explicit_pairwise",
                "timestamp_available": "yes" if timestamp_available else "no",
            },
        )
        row["event_count"] += count
        row["total_payload_bytes"] += size * count

    collective_totals: dict[tuple[str, int], tuple[int, int]] = defaultdict(lambda: (0, 0))
    for (_node_id, _name, group_id, comm_type), observations in sorted(collectives.items()):
        if group_id not in workload.comm_groups:
            raise TraceError(f"{workload.name}: collective references unknown group {group_id}")
        expected_ranks = tuple(sorted(workload.comm_groups[group_id]))
        observed_ranks = tuple(sorted(rank for rank, _size in observations))
        if observed_ranks != expected_ranks:
            raise TraceError(
                f"{workload.name}: collective group {group_id} participants differ: expected {expected_ranks}, observed {observed_ranks}"
            )
        sizes = {size for _rank, size in observations}
        if len(sizes) != 1:
            raise TraceError(f"{workload.name}: collective group {group_id} has inconsistent comm_size values")
        size = next(iter(sizes))
        count, total = collective_totals[(comm_type, len(expected_ranks))]
        collective_totals[(comm_type, len(expected_ranks))] = (count + 1, total + size)

    for (comm_type, participants), (count, total) in sorted(collective_totals.items()):
        rows_by_key[(comm_type, participants)] = {
            "workload": workload.name,
            "communication_type": comm_type,
            "communication_class": "collective",
            "event_count": count,
            "total_payload_bytes": total,
            "participant_count": participants,
            "source_rank": "",
            "destination_rank": "",
            "representation": "abstract_collective",
            "timestamp_available": "yes" if timestamp_available else "no",
        }

    summary_rows = list(rows_by_key.values())
    for row in summary_rows:
        count = int(row["event_count"])
        row["average_payload_bytes"] = f"{int(row['total_payload_bytes']) / count:.6f}" if count else "0.000000"
    summary_rows.sort(key=lambda row: (row["workload"], row["communication_class"], row["communication_type"], str(row["source_rank"]), str(row["destination_rank"]), int(row["participant_count"])))

    event_count = sum(int(row["event_count"]) for row in summary_rows)
    total_bytes = sum(int(row["total_payload_bytes"]) for row in summary_rows)
    bytes_by_type: Counter[str] = Counter()
    for row in summary_rows:
        bytes_by_type[str(row["communication_type"])] += int(row["total_payload_bytes"])
    dominant = bytes_by_type.most_common(1)[0][0] if bytes_by_type else ""
    metadata = workload.metadata
    inventory = {
        "workload": workload.name,
        "model_type": metadata.get("model_type", "unknown"),
        "parallelism_configuration": metadata.get("parallelism_configuration", "unknown"),
        "trace_path": str(workload.trace_path.resolve()),
        "trace_format": workload.trace_format,
        "schema_version": workload.schema_version,
        "schema_name": workload.schema_name,
        "number_of_ranks": len(ranks),
        "rank_trace_files": len(workload.trace_files),
        "communication_event_count": event_count,
        "total_communication_bytes": total_bytes,
        "dominant_communication_type": dominant,
        "timestamp_available": "yes" if timestamp_available else "no",
        "event_order_available": "yes (per-rank record order)",
        "collective_representation": "abstract",
        "generation_command": metadata.get("generation_command", "not recorded"),
        "notes": metadata.get("notes", "discovered from supplied trace source"),
    }
    return inventory, summary_rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", help="STAGE trace files or directories (searched recursively)")
    parser.add_argument("--manifest", type=Path, help="manifest that records workload names and generation settings")
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    args = parser.parse_args(argv)

    try:
        if args.sources:
            workloads = _discover_sources(args.sources)
        else:
            manifest = args.manifest or DEFAULT_MANIFEST
            workloads = _load_manifest(manifest)
        inventory_rows: list[dict[str, Any]] = []
        summary_rows: list[dict[str, Any]] = []
        for workload in workloads:
            inventory, rows = _analyze(workload)
            inventory_rows.append(inventory)
            summary_rows.extend(rows)
        inventory_rows.sort(key=lambda row: row["workload"])

        inventory_fields = [
            "workload", "model_type", "parallelism_configuration", "trace_path", "trace_format",
            "schema_version", "schema_name", "number_of_ranks", "rank_trace_files",
            "communication_event_count", "total_communication_bytes", "dominant_communication_type",
            "timestamp_available", "event_order_available", "collective_representation",
            "generation_command", "notes",
        ]
        summary_fields = [
            "workload", "communication_type", "communication_class", "event_count",
            "total_payload_bytes", "average_payload_bytes", "participant_count",
            "source_rank", "destination_rank", "representation", "timestamp_available",
        ]
        _write_csv(args.inventory, inventory_fields, inventory_rows)
        _write_csv(args.summary, summary_fields, summary_rows)

        total_events = sum(int(row["event_count"]) for row in summary_rows)
        total_bytes = sum(int(row["total_payload_bytes"]) for row in summary_rows)
        collective_types = sorted({row["communication_type"] for row in summary_rows if row["communication_class"] == "collective"})
        p2p_types = sorted({row["communication_type"] for row in summary_rows if row["communication_class"] == "point_to_point"})
        print(f"Workloads inspected: {len(workloads)}")
        print(f"Logical trace sets: {len(workloads)}")
        print(f"Rank trace files: {sum(len(workload.trace_files) for workload in workloads)}")
        print(f"Total ranks: {sum(int(row['number_of_ranks']) for row in inventory_rows)}")
        print(f"Communication events (logical, no SEND/RECV or collective-rank double count): {total_events}")
        print(f"Communication payload bytes (STAGE comm_size): {total_bytes}")
        print(f"Point-to-point types: {', '.join(p2p_types) if p2p_types else 'none'}")
        print(f"Collective types: {', '.join(collective_types) if collective_types else 'none'}")
        print("Collectives represented: abstract (not expanded into pairwise traffic)")
        print(f"Inventory: {args.inventory.resolve()}")
        print(f"Communication summary: {args.summary.resolve()}")
        print("STAGE TRACE INSPECTION COMPLETE")
        return 0
    except (OSError, TraceError, ValueError) as exc:
        print(f"STAGE trace inspection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
