#!/usr/bin/env python3
"""Apply existing uniform-reference fixed topologies to STAGE workloads."""

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
import create_fixed_k1_designs as fixed_k1
import create_fixed_k2_designs as fixed_k2
import create_fixed_k4_designs as fixed_k4


STAGE_SUMMARY = ROOT / "results" / "stage_workload_aware_summary.csv"
REQUESTED_K_VALUES = (1, 2, 4)
BUDGETS = (5, 15, 25, 45)
BASE_LINKS = 24
EPS = 1e-9
FIXED_SELECTION_FILES = {
    1: ROOT / "results" / "k1_fixed_selections.csv",
    2: ROOT / "results" / "k2_fixed_selections.csv",
    4: ROOT / "results" / "k4_fixed_selections.csv",
}
FIXED_SUMMARY_FILES = {
    2: ROOT / "results" / "k2_fixed_summary.csv",
    4: ROOT / "results" / "k4_fixed_summary.csv",
}


class FixedDesignError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise FixedDesignError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def discover_workloads() -> tuple[str, ...]:
    if not STAGE_SUMMARY.is_file():
        fail(f"Missing STAGE results used for workload discovery: {STAGE_SUMMARY}")
    try:
        with STAGE_SUMMARY.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if "workload" not in set(reader.fieldnames or []):
                fail("STAGE workload-aware summary has no workload column")
            rows = list(reader)
    except OSError as exc:
        fail(f"Cannot read STAGE workload-aware summary: {exc}")
    workloads = tuple(sorted({row["workload"].strip() for row in rows if row["workload"].strip()}))
    if not workloads:
        fail("No STAGE workloads discovered")
    expected_rows = len(workloads) * len(REQUESTED_K_VALUES) * len(BUDGETS)
    if len(rows) != expected_rows:
        fail(f"STAGE summary has {len(rows)} rows; expected {expected_rows}")
    return workloads


def validate_fixed_provenance() -> None:
    """Prove the selected links came from the workload-independent reference."""
    for path in (*FIXED_SELECTION_FILES.values(), *FIXED_SUMMARY_FILES.values()):
        if not path.is_file():
            fail(f"Missing fixed-selection input: {path}")
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            if "reference_traffic" not in fields or "selection_policy" not in fields:
                fail(f"Fixed input lacks provenance fields: {path}")
            rows = list(reader)
        if not rows or any(row["reference_traffic"].strip() != "fixed_reference_uniform" for row in rows):
            fail(f"Fixed input is not uniformly workload-independent: {path}")
        if "workload" in fields:
            fail(f"Fixed input unexpectedly contains workload-specific selection data: {path}")


def load_fixed_inputs() -> tuple[
    dict[tuple[int, int], list[dict[str, Any]]],
    dict[tuple[int, int], dict[str, Any]],
]:
    validate_fixed_provenance()
    steps: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    summaries: dict[tuple[int, int], dict[str, Any]] = {}

    selections_k1 = fixed_k1.load_selections()
    for budget in BUDGETS:
        row = selections_k1[budget]
        standardized = {
            "budget_mm": budget,
            "requested_k": 1,
            "actual_k": 1,
            "step": 1,
            "u": row["u"],
            "v": row["v"],
            "phy_u": row["phy_u"],
            "phy_v": row["phy_v"],
            "link_length_mm": row["length_mm"],
            "cumulative_wire_length_mm": row["length_mm"],
        }
        steps[(1, budget)] = [standardized]
        summaries[(1, budget)] = {
            "budget_mm": budget,
            "requested_k": 1,
            "actual_k": 1,
            "total_wire_length_mm": row["length_mm"],
        }

    for requested_k, loader in ((2, fixed_k2.load_inputs), (4, fixed_k4.load_inputs)):
        loaded_steps, loaded_summaries = loader()
        for budget in BUDGETS:
            steps[(requested_k, budget)] = sorted(
                (copy.deepcopy(row) for row in loaded_steps.get(budget, [])),
                key=lambda row: row["step"],
            )
            summaries[(requested_k, budget)] = copy.deepcopy(loaded_summaries[budget])

    expected = {(requested_k, budget) for requested_k in REQUESTED_K_VALUES for budget in BUDGETS}
    if set(steps) != expected or set(summaries) != expected:
        fail("Fixed K/budget input matrix is incomplete")
    for key in sorted(expected):
        requested_k, budget = key
        selected = steps[key]
        summary = summaries[key]
        actual_k = int(summary["actual_k"])
        if int(summary["requested_k"]) != requested_k or not 0 <= actual_k <= requested_k:
            fail(f"Invalid stored requested_k/actual_k for K={requested_k} B{budget}")
        if len(selected) != actual_k or [int(row["step"]) for row in selected] != list(range(1, actual_k + 1)):
            fail(f"Stored fixed selection steps disagree with actual_k for K={requested_k} B{budget}")
        total = sum(float(row["link_length_mm"]) for row in selected)
        if total > budget + EPS or not math.isclose(
            total, float(summary["total_wire_length_mm"]), rel_tol=EPS, abs_tol=EPS
        ):
            fail(f"Stored fixed wire total is invalid for K={requested_k} B{budget}")
    return steps, summaries


def configuration_paths(requested_k: int, budget: int) -> tuple[Path, Path]:
    return (
        ROOT / "inputs" / "topologies" / f"topology_stage_fixed_k{requested_k}_B{budget}mm.json",
        ROOT / "inputs" / "routing_tables" / f"routing_table_stage_fixed_k{requested_k}_B{budget}mm.json",
    )


def design_path(workload: str, requested_k: int, budget: int) -> Path:
    return ROOT / "inputs" / "designs" / f"design_stage_fixed_k{requested_k}_{workload}_B{budget}mm.json"


def physical_signature(selected: list[dict[str, Any]]) -> tuple[Any, ...]:
    links = []
    for row in selected:
        links.append(
            tuple(sorted(((int(row["u"]), int(row["phy_u"])), (int(row["v"]), int(row["phy_v"])))))
        )
    return tuple(sorted(links))


def build_payload(
    requested_k: int,
    budget: int,
    steps: dict[tuple[int, int], list[dict[str, Any]]],
    summaries: dict[tuple[int, int], dict[str, Any]],
    baseline: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = (requested_k, budget)
    selected = sorted(steps[key], key=lambda row: row["step"])
    topology = copy.deepcopy(baseline["topology"])
    fixed_k2.add_selected_links(topology, selected, summaries[key], baseline, budget)
    routing = fixed_k2.generate_splif_routing(
        baseline["chiplets"], baseline["placement"], topology
    )
    if routing.get("type") != "default" or not isinstance(routing.get("table"), dict):
        fail(f"SPLIF regeneration failed for fixed K={requested_k} B{budget}")
    if len(topology) != BASE_LINKS + int(summaries[key]["actual_k"]):
        fail(f"Final topology is not 24 + actual_k for K={requested_k} B{budget}")
    return topology, routing


def verify_reference_metrics(
    requested_k: int,
    budget: int,
    topology: list[dict[str, Any]],
    summary: dict[str, Any],
    baseline: dict[str, Any],
) -> None:
    if requested_k == 2:
        fixed_k2.verify_reference_metrics(topology, summary, baseline)
    elif requested_k == 4:
        fixed_k4.verify_reference_metrics(topology, summary, baseline)
    # K=1 has no separate summary file; its exact fixed selection, physical
    # length, provenance, and endpoint validity are checked above/below.


def create_designs() -> None:
    workloads = discover_workloads()
    steps, summaries = load_fixed_inputs()
    baseline = fixed_k2.load_baseline()
    if len(baseline["topology"]) != BASE_LINKS:
        fail("Baseline must contain exactly 24 mesh links")
    created: list[Path] = []
    payloads: dict[tuple[int, int], tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    for requested_k in REQUESTED_K_VALUES:
        for budget in BUDGETS:
            key = (requested_k, budget)
            topology, routing = build_payload(
                requested_k, budget, steps, summaries, baseline
            )
            verify_reference_metrics(
                requested_k, budget, topology, summaries[key], baseline
            )
            topology_path, routing_path = configuration_paths(requested_k, budget)
            hlp.write_json(str(topology_path), topology)
            hlp.write_json(str(routing_path), routing)
            if hlp.read_json(str(topology_path)) != topology or hlp.read_json(str(routing_path)) != routing:
                fail(f"Fixed topology/routing JSON round-trip failed for K={requested_k} B{budget}")
            payloads[key] = (topology, routing)

            references = set()
            for workload in workloads:
                traffic_chiplet = f"inputs/traffic_by_chiplet/traffic_stage_{workload}.json"
                traffic_unit = f"inputs/traffic_by_unit/traffic_stage_{workload}.json"
                if not (ROOT / traffic_chiplet).is_file() or not (ROOT / traffic_unit).is_file():
                    fail(f"Missing STAGE traffic for {workload}")
                design = copy.deepcopy(baseline["base_design"])
                design["design_name"] = f"stage_fixed_k{requested_k}_{workload}_B{budget}mm"
                design["topology"] = relative(topology_path)
                design["routing_table"] = relative(routing_path)
                design["traffic_by_chiplet"] = traffic_chiplet
                design["traffic_by_unit"] = traffic_unit
                path = design_path(workload, requested_k, budget)
                hlp.write_json(str(path), design)
                loaded = hlp.read_json(str(path))
                expected_refs = (
                    relative(topology_path), relative(routing_path), traffic_chiplet, traffic_unit
                )
                actual_refs = (
                    loaded.get("topology"), loaded.get("routing_table"),
                    loaded.get("traffic_by_chiplet"), loaded.get("traffic_by_unit"),
                )
                if actual_refs != expected_refs:
                    fail(f"Incorrect fixed design references for {workload} K={requested_k} B{budget}")
                references.add(actual_refs[:2])
                created.append(path)
                print(
                    f"Created {relative(path)}: actual_k={summaries[key]['actual_k']}, "
                    f"links={len(topology)}, wire={summaries[key]['total_wire_length_mm']:.6f} mm"
                )
            if len(references) != 1:
                fail(f"Fixed topology/routing varies by STAGE workload for K={requested_k} B{budget}")

    expected_designs = {
        design_path(workload, requested_k, budget)
        for workload in workloads
        for requested_k in REQUESTED_K_VALUES
        for budget in BUDGETS
    }
    actual_designs = set((ROOT / "inputs" / "designs").glob("design_stage_fixed_k*_B*mm.json"))
    if len(created) != len(expected_designs) or set(created) != expected_designs or actual_designs != expected_designs:
        fail(
            f"STAGE fixed design set mismatch: created={len(created)}, "
            f"expected={len(expected_designs)}, files={len(actual_designs)}"
        )
    print(f"STAGE workloads: {len(workloads)}")
    print(f"Logical designs: {len(created)}")
    print(f"K+budget physical configurations: {len(payloads)}")
    print("Fixed-selection provenance: fixed_reference_uniform (no STAGE influence)")
    print("STAGE FIXED DESIGNS COMPLETE")


if __name__ == "__main__":
    try:
        create_designs()
    except (FixedDesignError, RuntimeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE fixed design generation failed: {exc}") from exc
