#!/usr/bin/env python3
"""Run RapidChiplet + BookSim for all STAGE no-shortcut mesh baselines."""

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

import create_stage_mesh_baselines as designs


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


def build_experiments() -> list[dict[str, Any]]:
    experiments = []
    for workload in designs.discover_workloads():
        result = RESULT_DIR / f"stage_mesh_{workload}.json"
        experiments.append(
            {
                "workload": workload,
                "design": designs.design_path(workload),
                "result": result,
                "status": "SKIP" if result_is_complete(result) else "RUN",
            }
        )
    return experiments


def print_plan(experiments: list[dict[str, Any]]) -> None:
    print("WORKLOAD | DESIGN | RESULT | STATUS")
    print("=" * 116)
    for experiment in experiments:
        print(
            f"{experiment['workload']} | {relative(experiment['design'])} | "
            f"{relative(experiment['result'])} | {experiment['status']}"
        )


def validate_designs(experiments: list[dict[str, Any]]) -> None:
    workloads = designs.discover_workloads()
    if len(experiments) != len(workloads):
        fail(f"Expected {len(workloads)} mesh designs; found {len(experiments)}")
    baseline = designs.load_and_validate_baseline()
    physical_references = set()
    for experiment in experiments:
        path = experiment["design"]
        if not path.is_file():
            fail(f"Missing required mesh design: {path}")
        designs.validate_design(path, experiment["workload"], baseline)
        design = designs.hlp.read_json(str(path))
        physical_references.add(
            tuple(
                design[key]
                for key in ("technologies", "chiplets", "placement", "topology", "packaging", "routing_table")
            )
        )
    if len(physical_references) != 1:
        fail("Physical mesh topology differs across STAGE workloads")
    print(f"Validated all {len(experiments)} STAGE mesh designs before simulation.")


def run_experiments(experiments: list[dict[str, Any]]) -> None:
    actual_runs = 0
    completed_skips = 0
    for index, experiment in enumerate(experiments, start=1):
        workload = experiment["workload"]
        result = experiment["result"]
        if result_is_complete(result):
            print(f"SKIP {index}/{len(experiments)}: {workload} (complete result)")
            completed_skips += 1
            continue
        print(f"RUN {index}/{len(experiments)}: {workload}")
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
                f"FAILED STAGE mesh BookSim run: workload={workload}, "
                f"design={relative(experiment['design'])} ({detail})"
            ) from exc
        if not result_is_complete(result):
            raise SystemExit(
                f"FAILED STAGE mesh BookSim result completeness: workload={workload}, "
                f"design={relative(experiment['design'])}, result={relative(result)}"
            )
        actual_runs += 1

    if any(not result_is_complete(experiment["result"]) for experiment in experiments):
        fail("At least one STAGE mesh BookSim result remains incomplete")
    workloads = [experiment["workload"] for experiment in experiments]
    print("=" * 116)
    print("ALL STAGE MESH BOOKSIM RUNS COMPLETE")
    print(f"STAGE workloads: {len(workloads)} ({', '.join(workloads)})")
    print(f"Designs created: {len(experiments)}")
    print(f"Actual BookSim runs: {actual_runs}")
    print(f"Completed-result skips: {completed_skips}")
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
    except (RunnerError, designs.MeshBaselineError, RuntimeError, OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"STAGE mesh BookSim runner failed: {exc}") from exc
