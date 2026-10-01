#!/usr/bin/env python3
"""Create one no-shortcut 8-PHY mesh baseline per STAGE workload."""

from __future__ import annotations

import copy
import csv
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
import create_fixed_k2_designs as physical


BASE_DESIGN = ROOT / "inputs" / "designs" / "design_project_physical_mesh_8phy.json"
STAGE_SUMMARY = ROOT / "results" / "stage_workload_aware_summary.csv"
DESIGN_DIR = ROOT / "inputs" / "designs"
CHIPLET_COUNT = 16
MESH_LINK_COUNT = 24
PHY_COUNT = 8


class MeshBaselineError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise MeshBaselineError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def discover_workloads() -> tuple[str, ...]:
    if not STAGE_SUMMARY.is_file():
        fail(f"Missing STAGE summary used for workload discovery: {STAGE_SUMMARY}")
    try:
        with STAGE_SUMMARY.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if "workload" not in set(reader.fieldnames or []):
                fail("STAGE workload-aware summary has no workload column")
            workloads = tuple(
                sorted({row["workload"].strip() for row in reader if row["workload"].strip()})
            )
    except OSError as exc:
        fail(f"Cannot read STAGE workload summary: {exc}")
    if not workloads:
        fail("No STAGE workloads discovered")
    return workloads


def design_path(workload: str) -> Path:
    return DESIGN_DIR / f"design_stage_mesh_{workload}.json"


def traffic_references(workload: str) -> tuple[str, str]:
    return (
        f"inputs/traffic_by_chiplet/traffic_stage_{workload}.json",
        f"inputs/traffic_by_unit/traffic_stage_{workload}.json",
    )


def validate_traffic(workload: str) -> None:
    chiplet_ref, unit_ref = traffic_references(workload)
    try:
        chiplet_traffic = hlp.read_json(str(ROOT / chiplet_ref))
        unit_traffic = hlp.read_json(str(ROOT / unit_ref))
    except (OSError, ValueError, TypeError) as exc:
        fail(f"Cannot load STAGE traffic for {workload}: {exc}")
    if not isinstance(chiplet_traffic, dict) or not chiplet_traffic:
        fail(f"Chiplet traffic for {workload} must be a nonempty mapping")
    if not isinstance(unit_traffic, dict) or not unit_traffic:
        fail(f"Unit traffic for {workload} must be a nonempty mapping")
    for pair, load in chiplet_traffic.items():
        if (
            not isinstance(pair, tuple) or len(pair) != 2
            or not all(isinstance(chiplet, int) and 0 <= chiplet < CHIPLET_COUNT for chiplet in pair)
            or pair[0] == pair[1]
            or not isinstance(load, (int, float)) or not math.isfinite(load) or load < 0
        ):
            fail(f"Invalid chiplet traffic entry for {workload}: {pair!r} -> {load!r}")
    for pair, load in unit_traffic.items():
        if (
            not isinstance(pair, tuple) or len(pair) != 2
            or not all(
                isinstance(endpoint, tuple) and len(endpoint) == 2
                and isinstance(endpoint[0], int) and 0 <= endpoint[0] < CHIPLET_COUNT
                and isinstance(endpoint[1], int) and 0 <= endpoint[1] < PHY_COUNT
                for endpoint in pair
            )
            or pair[0][0] == pair[1][0]
            or not isinstance(load, (int, float)) or not math.isfinite(load) or load < 0
        ):
            fail(f"Invalid unit traffic entry for {workload}: {pair!r} -> {load!r}")


def load_and_validate_baseline() -> dict[str, Any]:
    if not BASE_DESIGN.is_file():
        fail(f"Missing physical mesh baseline: {BASE_DESIGN}")
    baseline = physical.load_baseline()
    design = baseline["base_design"]
    placement = baseline["placement"]
    topology = baseline["topology"]
    chiplets = baseline["chiplets"]
    if len(placement.get("chiplets", [])) != CHIPLET_COUNT:
        fail("Physical mesh baseline must place exactly 16 chiplets")
    if len(topology) != MESH_LINK_COUNT:
        fail("Physical mesh baseline must contain exactly 24 links")

    expected_edges = {
        tuple(sorted((node, node + 1)))
        for node in range(CHIPLET_COUNT)
        if node % 4 != 3
    } | {
        tuple(sorted((node, node + 4)))
        for node in range(CHIPLET_COUNT - 4)
    }
    if baseline["mesh_edges"] != expected_edges:
        fail("Physical baseline is not the exact 24-link 4x4 mesh (shortcut detected)")
    for chiplet_id, descriptor in enumerate(placement["chiplets"]):
        name = descriptor.get("name")
        if name not in chiplets or len(chiplets[name].get("phys", [])) != PHY_COUNT:
            fail(f"Chiplet {chiplet_id} does not use the required 8-PHY architecture")

    regenerated = physical.generate_splif_routing(chiplets, placement, topology)
    if regenerated.get("type") != "default" or not isinstance(regenerated.get("table"), dict):
        fail("Could not regenerate default SPLIF routing for the mesh")
    try:
        stored_routing = hlp.read_json(str(ROOT / design["routing_table"]))
    except (OSError, ValueError, TypeError) as exc:
        fail(f"Cannot load baseline routing table: {exc}")
    if stored_routing != regenerated:
        fail("Stored physical-mesh routing is not the regenerated SPLIF routing")
    if len(regenerated["table"]) != CHIPLET_COUNT:
        fail("Mesh routing table does not contain all 16 sources")
    for source in (("chiplet", index) for index in range(CHIPLET_COUNT)):
        destinations = regenerated["table"].get(source)
        if not isinstance(destinations, dict) or len(destinations) != CHIPLET_COUNT:
            fail(f"Mesh routing table is incomplete for source {source}")
    return baseline


def validate_design(path: Path, workload: str, baseline: dict[str, Any]) -> None:
    try:
        design = hlp.read_json(str(path))
    except (OSError, ValueError, TypeError) as exc:
        fail(f"Cannot load generated design {path}: {exc}")
    base_design = baseline["base_design"]
    chiplet_ref, unit_ref = traffic_references(workload)
    physical_keys = ("technologies", "chiplets", "placement", "topology", "packaging", "routing_table")
    if any(design.get(key) != base_design.get(key) for key in physical_keys):
        fail(f"Physical mesh configuration changed in {path}")
    if design.get("traffic_by_chiplet") != chiplet_ref or design.get("traffic_by_unit") != unit_ref:
        fail(f"Incorrect STAGE traffic references in {path}")
    if design.get("design_name") != f"stage_mesh_{workload}":
        fail(f"Incorrect design name in {path}")
    validate_traffic(workload)


def create_designs() -> None:
    workloads = discover_workloads()
    baseline = load_and_validate_baseline()
    created: list[Path] = []
    for workload in workloads:
        validate_traffic(workload)
        design = copy.deepcopy(baseline["base_design"])
        chiplet_ref, unit_ref = traffic_references(workload)
        design["design_name"] = f"stage_mesh_{workload}"
        design["traffic_by_chiplet"] = chiplet_ref
        design["traffic_by_unit"] = unit_ref
        path = design_path(workload)
        hlp.write_json(str(path), design)
        validate_design(path, workload, baseline)
        created.append(path)
        print(f"Created {relative(path)}: chiplets=16, PHYs/chiplet=8, mesh_links=24")

    expected = {design_path(workload) for workload in workloads}
    actual = set(DESIGN_DIR.glob("design_stage_mesh_*.json"))
    if set(created) != expected or actual != expected:
        fail(
            f"STAGE mesh design set mismatch: created={len(created)}, "
            f"expected={len(expected)}, files={len(actual)}"
        )
    print(f"STAGE workloads: {len(workloads)} ({', '.join(workloads)})")
    print(f"Mesh baseline designs: {len(created)}")
    print("STAGE MESH BASELINE DESIGNS COMPLETE")


if __name__ == "__main__":
    try:
        create_designs()
    except (MeshBaselineError, RuntimeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE mesh baseline generation failed: {exc}") from exc
