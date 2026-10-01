#!/usr/bin/env python3
"""Materialize STAGE workload-aware K=1/K=2/K=4 RapidChiplet designs."""

from __future__ import annotations

import copy
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
from select_fixed_k1 import evaluate, generate_splif_routing, physical_distance


SELECTION_FILE = ROOT / "results" / "stage_workload_aware_selections.csv"
SUMMARY_FILE = ROOT / "results" / "stage_workload_aware_summary.csv"
BASE_DESIGN_FILE = ROOT / "inputs" / "designs" / "design_project_physical_mesh_8phy.json"
REQUESTED_K_VALUES = (1, 2, 4)
BUDGETS = (5, 15, 25, 45)
BASE_LINKS = 24
EPS = 1e-9

STEP_COLUMNS = {
    "workload", "budget_mm", "requested_k", "actual_k", "step", "u", "v",
    "phy_u", "phy_v", "link_length_mm", "cumulative_wire_length_mm",
}
SUMMARY_COLUMNS = {
    "workload", "budget_mm", "requested_k", "actual_k", "total_wire_length_mm",
    "final_avg_latency", "final_max_link_load", "analytical_throughput",
    "total_routed_traffic", "traffic_wire_cost",
    *(f"link_{index}" for index in range(1, 5)),
    *(f"link_{index}_phy_pair" for index in range(1, 5)),
    *(f"link_{index}_length_mm" for index in range(1, 5)),
}


class DesignError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise DesignError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def integer(value: Any, description: str) -> int:
    try:
        number = float(value)
        converted = int(number)
    except (TypeError, ValueError) as exc:
        fail(f"Invalid {description}: {value!r} ({exc})")
    if number != converted:
        fail(f"Invalid {description}: expected integer, found {value!r}")
    return converted


def number(value: Any, description: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        fail(f"Invalid {description}: {value!r} ({exc})")
    if not math.isfinite(converted):
        fail(f"Invalid {description}: expected finite number")
    return converted


def load_csv(path: Path, required: set[str], description: str) -> list[dict[str, str]]:
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                fail(f"{description} missing columns: {', '.join(missing)}")
            return list(reader)
    except OSError as exc:
        fail(f"Cannot read {description} {path}: {exc}")


def load_inputs() -> tuple[
    dict[tuple[str, int, int], list[dict[str, Any]]],
    dict[tuple[str, int, int], dict[str, Any]],
    tuple[str, ...],
]:
    raw_steps = load_csv(SELECTION_FILE, STEP_COLUMNS, "STAGE selection CSV")
    raw_summaries = load_csv(SUMMARY_FILE, SUMMARY_COLUMNS, "STAGE summary CSV")
    steps: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    summaries: dict[tuple[str, int, int], dict[str, Any]] = {}

    for row_number, row in enumerate(raw_steps, start=2):
        workload = row["workload"].strip()
        requested_k = integer(row["requested_k"], f"requested_k on selection row {row_number}")
        budget = integer(row["budget_mm"], f"budget on selection row {row_number}")
        key = (workload, requested_k, budget)
        steps[key].append(
            {
                "workload": workload,
                "requested_k": requested_k,
                "budget_mm": budget,
                "actual_k": integer(row["actual_k"], f"actual_k on selection row {row_number}"),
                "step": integer(row["step"], f"step on selection row {row_number}"),
                "u": integer(row["u"], f"u on selection row {row_number}"),
                "v": integer(row["v"], f"v on selection row {row_number}"),
                "phy_u": integer(row["phy_u"], f"phy_u on selection row {row_number}"),
                "phy_v": integer(row["phy_v"], f"phy_v on selection row {row_number}"),
                "link_length_mm": number(row["link_length_mm"], f"link length on selection row {row_number}"),
                "cumulative_wire_length_mm": number(
                    row["cumulative_wire_length_mm"],
                    f"cumulative wire on selection row {row_number}",
                ),
            }
        )

    for row_number, row in enumerate(raw_summaries, start=2):
        workload = row["workload"].strip()
        requested_k = integer(row["requested_k"], f"requested_k on summary row {row_number}")
        budget = integer(row["budget_mm"], f"budget on summary row {row_number}")
        key = (workload, requested_k, budget)
        if key in summaries:
            fail(f"Duplicate STAGE summary configuration: {key}")
        summary: dict[str, Any] = {
            "workload": workload,
            "requested_k": requested_k,
            "budget_mm": budget,
            "actual_k": integer(row["actual_k"], f"actual_k on summary row {row_number}"),
            "total_wire_length_mm": number(row["total_wire_length_mm"], f"total wire on summary row {row_number}"),
            "final_avg_latency": number(row["final_avg_latency"], f"final latency on summary row {row_number}"),
            "final_max_link_load": number(row["final_max_link_load"], f"final load on summary row {row_number}"),
            "analytical_throughput": number(row["analytical_throughput"], f"throughput on summary row {row_number}"),
            "total_routed_traffic": number(row["total_routed_traffic"], f"routed traffic on summary row {row_number}"),
            "traffic_wire_cost": number(row["traffic_wire_cost"], f"traffic-wire cost on summary row {row_number}"),
        }
        for index in range(1, 5):
            summary[f"link_{index}"] = row[f"link_{index}"].strip()
            summary[f"link_{index}_phy_pair"] = row[f"link_{index}_phy_pair"].strip()
            raw_length = row[f"link_{index}_length_mm"].strip()
            summary[f"link_{index}_length_mm"] = (
                number(raw_length, f"link {index} length on summary row {row_number}")
                if raw_length
                else None
            )
        summaries[key] = summary

    workloads = tuple(sorted({key[0] for key in summaries}))
    if not workloads:
        fail("No STAGE workloads discovered in summary CSV")
    expected = {
        (workload, requested_k, budget)
        for workload in workloads
        for requested_k in REQUESTED_K_VALUES
        for budget in BUDGETS
    }
    if set(summaries) != expected:
        fail(
            f"STAGE summary matrix mismatch: missing={sorted(expected - set(summaries))}, "
            f"extra={sorted(set(summaries) - expected)}"
        )
    if any(key not in expected for key in steps):
        fail(f"Selection CSV contains unexpected configurations: {sorted(set(steps) - expected)}")

    for key in sorted(expected):
        summary = summaries[key]
        group = sorted(steps.get(key, []), key=lambda row: row["step"])
        requested_k, actual_k, budget = key[1], summary["actual_k"], key[2]
        if requested_k not in REQUESTED_K_VALUES or not 0 <= actual_k <= requested_k:
            fail(f"Invalid requested_k/actual_k for {key}")
        if len(group) != actual_k:
            fail(f"Selection count differs from actual_k for {key}")
        if [row["step"] for row in group] != list(range(1, actual_k + 1)):
            fail(f"Non-contiguous greedy steps for {key}")
        if any(row["requested_k"] != requested_k or row["actual_k"] != actual_k for row in group):
            fail(f"Selection requested_k/actual_k differs from summary for {key}")
        if summary["total_wire_length_mm"] > budget + EPS:
            fail(f"Summary exceeds wire budget for {key}")
        wire_sum = sum(row["link_length_mm"] for row in group)
        if not math.isclose(wire_sum, summary["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Selection wire length differs from summary for {key}")
        for index in range(1, 5):
            if index <= actual_k:
                selected = group[index - 1]
                if summary[f"link_{index}"] != f"{selected['u']}<->{selected['v']}":
                    fail(f"Summary link {index} differs from selection CSV for {key}")
                if summary[f"link_{index}_phy_pair"] != f"{selected['phy_u']}<->{selected['phy_v']}":
                    fail(f"Summary PHY pair {index} differs from selection CSV for {key}")
                if not math.isclose(
                    float(summary[f"link_{index}_length_mm"]),
                    selected["link_length_mm"], rel_tol=EPS, abs_tol=EPS,
                ):
                    fail(f"Summary link length {index} differs from selection CSV for {key}")
            elif summary[f"link_{index}"] != "none":
                fail(f"Summary contains unexpected link {index} for {key}")
    return steps, summaries, workloads


def load_baseline() -> dict[str, Any]:
    design = hlp.read_json(str(BASE_DESIGN_FILE))
    baseline = {
        "design": design,
        "chiplets": hlp.read_json(str(ROOT / design["chiplets"])),
        "placement": hlp.read_json(str(ROOT / design["placement"])),
        "topology": hlp.read_json(str(ROOT / design["topology"])),
        "packaging": hlp.read_json(str(ROOT / design["packaging"])),
        "technologies": hlp.read_json(str(ROOT / design["technologies"])),
    }
    if len(baseline["placement"]["chiplets"]) != 16:
        fail("Baseline placement must contain 16 chiplets")
    if len(baseline["topology"]) != BASE_LINKS:
        fail(f"Baseline topology must start with 24 links; found {len(baseline['topology'])}")
    mesh_edges: set[tuple[int, int]] = set()
    occupied = {chiplet: set() for chiplet in range(16)}
    for link in baseline["topology"]:
        ep1, ep2 = link["ep1"], link["ep2"]
        if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
            fail("Baseline topology contains non-chiplet mesh link")
        u, v = int(ep1["outer_id"]), int(ep2["outer_id"])
        edge = tuple(sorted((u, v)))
        if edge in mesh_edges:
            fail(f"Duplicate baseline mesh edge {edge}")
        mesh_edges.add(edge)
        occupied[u].add(int(ep1["inner_id"]))
        occupied[v].add(int(ep2["inner_id"]))
    if len(mesh_edges) != BASE_LINKS:
        fail("Baseline does not contain 24 unique mesh edges")
    baseline["mesh_edges"] = mesh_edges
    baseline["occupied"] = occupied
    return baseline


def add_selected_links(
    topology: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    summary: dict[str, Any],
    baseline: dict[str, Any],
    key: tuple[str, int, int],
) -> None:
    occupied = {chiplet: set(phys) for chiplet, phys in baseline["occupied"].items()}
    pairs: set[tuple[int, int]] = set()
    cumulative = 0.0
    for row in selected:
        u, v, phy_u, phy_v = row["u"], row["v"], row["phy_u"], row["phy_v"]
        if not 0 <= u < 16 or not 0 <= v < 16 or u == v:
            fail(f"Invalid selected shortcut endpoints for {key}: {u}<->{v}")
        pair = tuple(sorted((u, v)))
        if pair in baseline["mesh_edges"] or pair in pairs:
            fail(f"Selected mesh/duplicate edge for {key}: {pair}")
        for chiplet, phy in ((u, phy_u), (v, phy_v)):
            chiplet_name = baseline["placement"]["chiplets"][chiplet]["name"]
            phy_count = len(baseline["chiplets"][chiplet_name]["phys"])
            if not 0 <= phy < phy_count:
                fail(f"Invalid PHY {phy} on chiplet {chiplet} for {key}")
            if phy in occupied[chiplet]:
                fail(f"Occupied/reused PHY endpoint {(chiplet, phy)} for {key}")
        actual_length = physical_distance(
            u, phy_u, v, phy_v,
            baseline["placement"], baseline["chiplets"], baseline["packaging"],
        )
        if not math.isclose(actual_length, row["link_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Physical length differs from selection CSV for {key} step {row['step']}")
        cumulative += actual_length
        if cumulative > key[2] + EPS:
            fail(f"Selected shortcuts exceed total budget for {key}")
        if not math.isclose(cumulative, row["cumulative_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
            fail(f"Cumulative wire differs from selection CSV for {key} step {row['step']}")
        topology.append(
            {
                "ep1": {"type": "chiplet", "outer_id": u, "inner_id": phy_u},
                "ep2": {"type": "chiplet", "outer_id": v, "inner_id": phy_v},
            }
        )
        pairs.add(pair)
        occupied[u].add(phy_u)
        occupied[v].add(phy_v)
    if len(selected) != summary["actual_k"] or len(topology) != BASE_LINKS + summary["actual_k"]:
        fail(f"Final topology does not contain 24 + actual_k links for {key}")
    if not math.isclose(cumulative, summary["total_wire_length_mm"], rel_tol=EPS, abs_tol=EPS):
        fail(f"Final wire length differs from summary for {key}")


def configuration_paths(workload: str, requested_k: int, budget: int) -> tuple[Path, Path, Path]:
    suffix = f"stage_aware_k{requested_k}_{workload}_B{budget}mm"
    return (
        ROOT / "inputs" / "topologies" / f"topology_{suffix}.json",
        ROOT / "inputs" / "routing_tables" / f"routing_table_{suffix}.json",
        ROOT / "inputs" / "designs" / f"design_{suffix}.json",
    )


def build_physical_payload(
    key: tuple[str, int, int],
    steps: dict[tuple[str, int, int], list[dict[str, Any]]],
    summaries: dict[tuple[str, int, int], dict[str, Any]],
    baseline: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = sorted(steps.get(key, []), key=lambda row: row["step"])
    topology = copy.deepcopy(baseline["topology"])
    add_selected_links(topology, selected, summaries[key], baseline, key)
    routing = generate_splif_routing(
        baseline["chiplets"], baseline["placement"], topology
    )
    if routing.get("type") != "default" or not isinstance(routing.get("table"), dict):
        fail(f"Final SPLIF routing regeneration failed for {key}")
    return topology, routing


def metrics_match(metrics: dict[str, Any], summary: dict[str, Any], key: tuple[str, int, int]) -> None:
    for actual_field, summary_field in (
        ("avg_latency", "final_avg_latency"),
        ("max_load", "final_max_link_load"),
        ("throughput", "analytical_throughput"),
        ("total_routed_traffic", "total_routed_traffic"),
        ("traffic_wire_cost", "traffic_wire_cost"),
    ):
        if not math.isclose(
            float(metrics[actual_field]), float(summary[summary_field]),
            rel_tol=EPS, abs_tol=EPS,
        ):
            fail(f"Generated topology metric {actual_field} differs from optimizer summary for {key}")


def create_designs() -> None:
    steps, summaries, workloads = load_inputs()
    baseline = load_baseline()
    created: list[Path] = []
    for workload in workloads:
        traffic_chiplet = f"inputs/traffic_by_chiplet/traffic_stage_{workload}.json"
        traffic_unit = f"inputs/traffic_by_unit/traffic_stage_{workload}.json"
        if not (ROOT / traffic_chiplet).is_file() or not (ROOT / traffic_unit).is_file():
            fail(f"Missing STAGE traffic files for {workload}")
        workload_context = {
            "design": copy.deepcopy(baseline["design"]),
            "chiplets": baseline["chiplets"],
            "placement": baseline["placement"],
            "packaging": baseline["packaging"],
            "technologies": baseline["technologies"],
            "traffic": hlp.read_json(str(ROOT / traffic_chiplet)),
        }
        workload_context["design"]["traffic_by_chiplet"] = traffic_chiplet
        workload_context["design"]["traffic_by_unit"] = traffic_unit
        for requested_k in REQUESTED_K_VALUES:
            for budget in BUDGETS:
                key = (workload, requested_k, budget)
                topology, routing = build_physical_payload(
                    key, steps, summaries, baseline
                )
                metrics_match(evaluate(topology, workload_context), summaries[key], key)
                topology_path, routing_path, design_path = configuration_paths(*key)
                hlp.write_json(str(topology_path), topology)
                hlp.write_json(str(routing_path), routing)
                design = copy.deepcopy(baseline["design"])
                design["design_name"] = f"stage_aware_k{requested_k}_{workload}_B{budget}mm"
                design["topology"] = relative(topology_path)
                design["routing_table"] = relative(routing_path)
                design["traffic_by_chiplet"] = traffic_chiplet
                design["traffic_by_unit"] = traffic_unit
                hlp.write_json(str(design_path), design)
                loaded_design = hlp.read_json(str(design_path))
                if (
                    loaded_design.get("topology") != relative(topology_path)
                    or loaded_design.get("routing_table") != relative(routing_path)
                    or loaded_design.get("traffic_by_chiplet") != traffic_chiplet
                    or loaded_design.get("traffic_by_unit") != traffic_unit
                ):
                    fail(f"Generated design references are incorrect for {key}")
                if hlp.read_json(str(topology_path)) != topology:
                    fail(f"Topology JSON round-trip failed for {key}")
                if hlp.read_json(str(routing_path)) != routing:
                    fail(f"Routing JSON does not equal regenerated SPLIF routing for {key}")
                created.append(design_path)
                print(
                    f"Created {relative(design_path)}: requested_k={requested_k}, "
                    f"actual_k={summaries[key]['actual_k']}, links={len(topology)}, "
                    f"wire={summaries[key]['total_wire_length_mm']:.6f} mm"
                )
    expected = {
        configuration_paths(workload, requested_k, budget)[2]
        for workload in workloads
        for requested_k in REQUESTED_K_VALUES
        for budget in BUDGETS
    }
    actual = set((ROOT / "inputs" / "designs").glob("design_stage_aware_k*_B*mm.json"))
    if len(created) != len(expected) or set(created) != expected or actual != expected:
        fail(
            f"Logical design set mismatch: created={len(created)}, expected={len(expected)}, files={len(actual)}"
        )
    print(f"STAGE workloads: {len(workloads)}")
    print(f"Logical designs created: {len(created)}")
    print("STAGE WORKLOAD-AWARE DESIGNS COMPLETE")


if __name__ == "__main__":
    try:
        create_designs()
    except (DesignError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE workload-aware design generation failed: {exc}") from exc
