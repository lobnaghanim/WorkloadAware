#!/usr/bin/env python3
"""Run RapidChiplet + BookSim for unique STAGE fixed-topology designs."""

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
import create_stage_fixed_designs as designs


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


def links_text(selected: list[dict[str, Any]]) -> str:
    if not selected:
        return "none"
    return ", ".join(
        f"{row['u']}<->{row['v']}[PHY {row['phy_u']}<->{row['phy_v']}]"
        for row in selected
    )


def build_experiments() -> list[dict[str, Any]]:
    workloads = designs.discover_workloads()
    steps, summaries = designs.load_fixed_inputs()
    experiments: list[dict[str, Any]] = []
    for workload in workloads:
        source_by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}
        for requested_k in designs.REQUESTED_K_VALUES:
            for budget in designs.BUDGETS:
                key = (requested_k, budget)
                selected = sorted(steps[key], key=lambda row: row["step"])
                signature = designs.physical_signature(selected)
                source = source_by_signature.get(signature)
                if source is None:
                    result = RESULT_DIR / f"stage_fixed_k{requested_k}_{workload}_B{budget}mm.json"
                    source = {
                        "requested_k": requested_k,
                        "budget": budget,
                        "design": designs.design_path(workload, requested_k, budget),
                        "result": result,
                    }
                    source_by_signature[signature] = source
                    duplicate = False
                else:
                    result = source["result"]
                    duplicate = True
                experiments.append(
                    {
                        "workload": workload,
                        "requested_k": requested_k,
                        "budget": budget,
                        "actual_k": int(summaries[key]["actual_k"]),
                        "selected": selected,
                        "signature": signature,
                        "total_wire": float(summaries[key]["total_wire_length_mm"]),
                        "duplicate": duplicate,
                        "source_requested_k": source["requested_k"],
                        "source_budget": source["budget"],
                        "source_design": source["design"],
                        "design": designs.design_path(workload, requested_k, budget),
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
        fail(f"Expected {expected} logical fixed experiments; found {len(experiments)}")
    return experiments


def print_plan(experiments: list[dict[str, Any]]) -> None:
    print("WORKLOAD | K | BUDGET | ACTUAL_K | FIXED_LINKS | WIRE | STATUS")
    print("=" * 136)
    for experiment in experiments:
        reuse = ""
        if experiment["action"] == "REUSE":
            reuse = f" -> K{experiment['source_requested_k']} B{experiment['source_budget']}mm"
        print(
            f"{experiment['workload']} | {experiment['requested_k']} | "
            f"{experiment['budget']} mm | {experiment['actual_k']} | "
            f"{links_text(experiment['selected'])} | {experiment['total_wire']:.6f} mm | "
            f"{experiment['action']}{reuse}"
        )
        print(f"  design: {relative(experiment['design'])}")
        print(f"  result: {relative(experiment['result'])}")


def validate_designs(experiments: list[dict[str, Any]]) -> None:
    workloads = designs.discover_workloads()
    steps, summaries = designs.load_fixed_inputs()
    baseline = designs.fixed_k2.load_baseline()
    expected_count = len(workloads) * len(designs.REQUESTED_K_VALUES) * len(designs.BUDGETS)
    if len(experiments) != expected_count:
        fail("Logical fixed experiment count changed before validation")
    refs_by_fixed_config: dict[tuple[int, int], tuple[str, str]] = {}
    for experiment in experiments:
        workload = experiment["workload"]
        requested_k = experiment["requested_k"]
        budget = experiment["budget"]
        key = (requested_k, budget)
        path = experiment["design"]
        if not path.is_file():
            fail(f"Missing required fixed design: {path}")
        design = hlp.read_json(str(path))
        topology_path, routing_path = designs.configuration_paths(*key)
        expected_unit = f"inputs/traffic_by_unit/traffic_stage_{workload}.json"
        expected_chiplet = f"inputs/traffic_by_chiplet/traffic_stage_{workload}.json"
        expected_refs = (
            relative(topology_path), relative(routing_path), expected_chiplet, expected_unit
        )
        actual_refs = (
            design.get("topology"), design.get("routing_table"),
            design.get("traffic_by_chiplet"), design.get("traffic_by_unit"),
        )
        if actual_refs != expected_refs:
            fail(f"Incorrect topology/routing/traffic references in {path}")
        previous = refs_by_fixed_config.setdefault(key, actual_refs[:2])
        if previous != actual_refs[:2]:
            fail(f"Fixed topology/routing changes across STAGE workloads for K={requested_k} B{budget}")
        if not topology_path.is_file() or not routing_path.is_file():
            fail(f"Missing shared fixed physical payload for K={requested_k} B{budget}")
        topology = hlp.read_json(str(topology_path))
        routing = hlp.read_json(str(routing_path))
        expected_topology, expected_routing = designs.build_payload(
            requested_k, budget, steps, summaries, baseline
        )
        if topology != expected_topology:
            fail(f"Fixed topology differs from stored fixed selections for K={requested_k} B{budget}")
        if routing != expected_routing:
            fail(f"Fixed routing is not regenerated SPLIF for K={requested_k} B{budget}")
        if len(topology) != designs.BASE_LINKS + experiment["actual_k"]:
            fail(f"Fixed topology link count is not 24 + actual_k for K={requested_k} B{budget}")

    seen: dict[tuple[str, tuple[Any, ...]], dict[str, Any]] = {}
    for experiment in experiments:
        identity = (experiment["workload"], experiment["signature"])
        source = seen.setdefault(identity, experiment)
        if experiment["duplicate"]:
            if source is experiment or source["result"] != experiment["result"]:
                fail("Invalid same-workload duplicate-topology reuse mapping")
        elif source is not experiment:
            fail("Same-workload duplicate topology was not marked REUSE")
    if len(refs_by_fixed_config) != len(designs.REQUESTED_K_VALUES) * len(designs.BUDGETS):
        fail("Did not validate all shared K+budget fixed configurations")
    print(f"Validated all {len(experiments)} fixed STAGE designs before simulation.")


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
            "python3", "rapidchiplet.py", "-df", relative(experiment["design"]),
            "-rf", result.stem, "-l", "-t", "-bs",
        ]
        print("Running:", " ".join(cmd), flush=True)
        try:
            subprocess.run(cmd, check=True, cwd=ROOT)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = f"exit code {exc.returncode}" if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            raise SystemExit(
                "FAILED STAGE fixed BookSim run: "
                f"workload={workload}, K={requested_k}, budget={budget}, "
                f"design={relative(experiment['design'])} ({detail})"
            ) from exc
        if not result_is_complete(result):
            raise SystemExit(
                "FAILED STAGE fixed BookSim result completeness: "
                f"workload={workload}, K={requested_k}, budget={budget}, "
                f"design={relative(experiment['design'])}, result={relative(result)}"
            )
        actual_runs += 1
    for experiment in unique:
        if not result_is_complete(experiment["result"]):
            fail(f"Canonical fixed result is incomplete: {experiment['result']}")
    workloads = sorted({experiment["workload"] for experiment in experiments})
    duplicate_count = sum(bool(experiment["duplicate"]) for experiment in experiments)
    print("=" * 116)
    print("ALL STAGE FIXED BOOKSIM RUNS COMPLETE")
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
    except (RunnerError, designs.FixedDesignError, RuntimeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE fixed BookSim runner failed: {exc}") from exc
