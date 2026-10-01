#!/usr/bin/env python3
"""Run RapidChiplet + BookSim for unique STAGE workload-aware designs."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
import create_stage_workload_aware_designs as designs


RESULT_DIR = ROOT / "results"


class RunnerError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise RunnerError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def result_is_complete(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict):
        return False
    if not {"latency", "throughput", "booksim_simulation"}.issubset(result):
        return False
    booksim = result["booksim_simulation"]
    if not isinstance(booksim, dict):
        return False
    for offered_load in booksim:
        try:
            numeric = float(offered_load)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return True
    return False


def canonical_link(row: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    return tuple(
        sorted(
            (
                (int(row["u"]), int(row["phy_u"])),
                (int(row["v"]), int(row["phy_v"])),
            )
        )
    )


def physical_signature(rows: list[dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(sorted(canonical_link(row) for row in rows))


def links_text(selected: list[dict[str, Any]]) -> str:
    if not selected:
        return "none"
    return ", ".join(
        f"{row['u']}<->{row['v']}[PHY {row['phy_u']}<->{row['phy_v']}]"
        for row in selected
    )


def build_experiments() -> list[dict[str, Any]]:
    steps, summaries, workloads = designs.load_inputs()
    experiments: list[dict[str, Any]] = []
    for workload in workloads:
        source_by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}
        # Lower requested K is the canonical "smaller" logical result; budget
        # breaks ties.  Traffic is fixed within this workload.
        for requested_k in designs.REQUESTED_K_VALUES:
            for budget in designs.BUDGETS:
                key = (workload, requested_k, budget)
                selected = sorted(steps.get(key, []), key=lambda row: row["step"])
                signature = physical_signature(selected)
                source = source_by_signature.get(signature)
                if source is None:
                    result = RESULT_DIR / f"stage_aware_k{requested_k}_{workload}_B{budget}mm.json"
                    source = {
                        "requested_k": requested_k,
                        "budget": budget,
                        "design": designs.configuration_paths(*key)[2],
                        "result": result,
                    }
                    source_by_signature[signature] = source
                    duplicate = False
                else:
                    result = source["result"]
                    duplicate = True
                design_path = designs.configuration_paths(*key)[2]
                experiments.append(
                    {
                        "workload": workload,
                        "requested_k": requested_k,
                        "budget": budget,
                        "actual_k": summaries[key]["actual_k"],
                        "selected": selected,
                        "total_wire": summaries[key]["total_wire_length_mm"],
                        "signature": signature,
                        "duplicate": duplicate,
                        "source_requested_k": source["requested_k"],
                        "source_budget": source["budget"],
                        "source_design": source["design"],
                        "design": design_path,
                        "result": result,
                        "action": (
                            "REUSE"
                            if duplicate
                            else "SKIP" if result_is_complete(result) else "RUN"
                        ),
                    }
                )
    expected = len(workloads) * len(designs.REQUESTED_K_VALUES) * len(designs.BUDGETS)
    if len(experiments) != expected:
        fail(f"Expected {expected} logical experiments; found {len(experiments)}")
    return experiments


def print_plan(experiments: list[dict[str, Any]]) -> None:
    print("WORKLOAD | K | BUDGET | ACTUAL_K | LINKS | WIRE | STATUS")
    print("=" * 132)
    for experiment in experiments:
        reuse = ""
        if experiment["action"] == "REUSE":
            reuse = (
                f" -> K{experiment['source_requested_k']} "
                f"B{experiment['source_budget']}mm"
            )
        print(
            f"{experiment['workload']} | {experiment['requested_k']} | "
            f"{experiment['budget']} mm | {experiment['actual_k']} | "
            f"{links_text(experiment['selected'])} | "
            f"{experiment['total_wire']:.6f} mm | {experiment['action']}{reuse}"
        )
        print(f"  design: {relative(experiment['design'])}")
        print(f"  result: {relative(experiment['result'])}")


def validate_designs(experiments: list[dict[str, Any]]) -> None:
    steps, summaries, workloads = designs.load_inputs()
    baseline = designs.load_baseline()
    expected_count = len(workloads) * len(designs.REQUESTED_K_VALUES) * len(designs.BUDGETS)
    if len(experiments) != expected_count:
        fail(f"Experiment count changed before validation: {len(experiments)}")
    for experiment in experiments:
        workload = experiment["workload"]
        requested_k = experiment["requested_k"]
        budget = experiment["budget"]
        key = (workload, requested_k, budget)
        design_path = experiment["design"]
        if not design_path.is_file():
            fail(f"Missing required design: {design_path}")
        try:
            design = hlp.read_json(str(design_path))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            fail(f"Cannot parse design {design_path}: {exc}")
        if not isinstance(design, dict):
            fail(f"Malformed design: {design_path}")
        expected_refs = designs.configuration_paths(*key)[:2]
        expected_unit = f"inputs/traffic_by_unit/traffic_stage_{workload}.json"
        expected_chiplet = f"inputs/traffic_by_chiplet/traffic_stage_{workload}.json"
        if design.get("topology") != relative(expected_refs[0]):
            fail(f"Incorrect topology reference in {design_path}")
        if design.get("routing_table") != relative(expected_refs[1]):
            fail(f"Incorrect routing reference in {design_path}")
        if design.get("traffic_by_unit") != expected_unit:
            fail(f"Incorrect unit traffic reference in {design_path}")
        if design.get("traffic_by_chiplet") != expected_chiplet:
            fail(f"Incorrect chiplet traffic reference in {design_path}")
        if not (ROOT / expected_unit).is_file() or not (ROOT / expected_chiplet).is_file():
            fail(f"Missing STAGE traffic referenced by {design_path}")

        topology_path, routing_path = expected_refs
        if not topology_path.is_file() or not routing_path.is_file():
            fail(f"Missing topology/routing payload for {key}")
        topology = hlp.read_json(str(topology_path))
        routing = hlp.read_json(str(routing_path))
        expected_topology, expected_routing = designs.build_physical_payload(
            key, steps, summaries, baseline
        )
        if topology != expected_topology:
            fail(f"Topology does not exactly match selected links for {key}")
        if routing != expected_routing:
            fail(f"Routing is not regenerated final SPLIF routing for {key}")
        if len(topology) != designs.BASE_LINKS + experiment["actual_k"]:
            fail(f"Topology link count is not 24 + actual_k for {key}")

    # A reuse may only point to an earlier configuration of the same workload
    # with byte-identical selected shortcut endpoint signatures.
    seen: dict[tuple[str, tuple[Any, ...]], dict[str, Any]] = {}
    for experiment in experiments:
        identity = (experiment["workload"], experiment["signature"])
        source = seen.setdefault(identity, experiment)
        if experiment["duplicate"]:
            if source is experiment:
                fail("First topology occurrence cannot be marked REUSE")
            if source["result"] != experiment["result"]:
                fail("REUSE configuration does not point to canonical result")
        elif source is not experiment:
            fail("Duplicate workload/topology was not marked REUSE")
    print(f"Validated all {len(experiments)} required designs before simulation.")


def run_experiments(experiments: list[dict[str, Any]]) -> None:
    unique = [experiment for experiment in experiments if not experiment["duplicate"]]
    actual_runs = 0
    completed_skips = 0
    for index, experiment in enumerate(unique, start=1):
        workload = experiment["workload"]
        requested_k = experiment["requested_k"]
        budget = experiment["budget"]
        result = experiment["result"]
        if result_is_complete(result):
            print(
                f"SKIP {index}/{len(unique)}: {workload} K={requested_k} "
                f"B{budget}mm (complete result)"
            )
            completed_skips += 1
            continue
        print(f"RUN {index}/{len(unique)}: {workload} K={requested_k} B{budget}mm")
        cmd = [
            "python3",
            "rapidchiplet.py",
            "-df",
            relative(experiment["design"]),
            "-rf",
            result.stem,
            "-l",
            "-t",
            "-bs",
        ]
        print("Running:", " ".join(cmd), flush=True)
        try:
            subprocess.run(cmd, check=True, cwd=ROOT)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = (
                f"exit code {exc.returncode}"
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            raise SystemExit(
                "FAILED STAGE BookSim run: "
                f"workload={workload}, K={requested_k}, budget={budget}, "
                f"design={relative(experiment['design'])} ({detail})"
            ) from exc
        if not result_is_complete(result):
            raise SystemExit(
                "FAILED STAGE BookSim run produced incomplete result: "
                f"workload={workload}, K={requested_k}, budget={budget}, "
                f"design={relative(experiment['design'])}, result={relative(result)}"
            )
        actual_runs += 1

    for experiment in unique:
        if not result_is_complete(experiment["result"]):
            fail(f"Canonical BookSim result is incomplete: {experiment['result']}")
    duplicate_count = sum(bool(experiment["duplicate"]) for experiment in experiments)
    workloads = sorted({experiment["workload"] for experiment in experiments})
    print("=" * 116)
    print("ALL STAGE WORKLOAD-AWARE BOOKSIM RUNS COMPLETE")
    print(f"STAGE workloads: {len(workloads)} ({', '.join(workloads)})")
    print(f"Logical configurations: {len(experiments)}")
    print(f"Actual BookSim runs: {actual_runs}")
    print(f"Completed-result skips: {completed_skips}")
    print(f"Duplicate-topology runs avoided: {duplicate_count}")
    print("=" * 116)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    experiments = build_experiments()
    print_plan(experiments)
    validate_designs(experiments)
    run_experiments(experiments)


if __name__ == "__main__":
    try:
        main()
    except (RunnerError, designs.DesignError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE workload-aware BookSim runner failed: {exc}") from exc
