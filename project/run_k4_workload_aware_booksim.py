"""Run BookSim for unique workload-aware K=4 physical configurations."""

import json
import math
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from create_k4_workload_aware_designs import BUDGETS, WORKLOADS, load_inputs


RESULT_DIR = REPO_ROOT / "results"


def fail(message):
    raise RuntimeError(message)


def relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def result_is_complete(path):
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as result_file:
            result = json.load(result_file)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(result, dict) or not {"latency", "throughput", "booksim_simulation"}.issubset(result):
        return False
    booksim = result["booksim_simulation"]
    if not isinstance(booksim, dict):
        return False
    for offered_load in booksim:
        try:
            numeric_load = float(offered_load)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric_load):
            return True
    return False


def canonical_link(row):
    endpoint_u = (row["u"], row["phy_u"])
    endpoint_v = (row["v"], row["phy_v"])
    return tuple(sorted((endpoint_u, endpoint_v)))


def physical_signature(rows):
    return tuple(sorted(canonical_link(row) for row in rows))


def build_experiments():
    steps, summaries = load_inputs()
    experiments = []
    for workload in WORKLOADS:
        source_by_signature = {}
        for budget in BUDGETS:
            key = (workload, budget)
            selected = sorted(steps.get(key, []), key=lambda row: row["step"])
            signature = physical_signature(selected)
            source_budget = source_by_signature.setdefault(signature, budget)
            duplicate = source_budget != budget
            result = RESULT_DIR / f"aware_k4_{workload}_B{source_budget}mm.json"
            experiments.append(
                {
                    "workload": workload,
                    "budget": budget,
                    "requested_k": summaries[key]["requested_k"],
                    "actual_k": summaries[key]["actual_k"],
                    "selected": selected,
                    "total_wire": summaries[key]["total_wire_length_mm"],
                    "source_budget": source_budget,
                    "duplicate": duplicate,
                    "design": REPO_ROOT / f"inputs/designs/design_project_aware_k4_{workload}_B{budget}mm.json",
                    "result": result,
                    "action": "REUSE" if duplicate else "SKIP" if result_is_complete(result) else "RUN",
                }
            )
    if len(experiments) != 16:
        fail(f"Expected 16 logical K=4 configurations; found {len(experiments)}")
    return experiments


def link_description(selected):
    if not selected:
        return "none"
    return ", ".join(
        f"{row['u']}<->{row['v']}[PHY {row['phy_u']}<->{row['phy_v']}]"
        for row in selected
    )


def print_plan(experiments):
    print("=" * 120)
    print("WORKLOAD-AWARE K=4 BOOKSIM RUN PLAN")
    print("=" * 120)
    for index, experiment in enumerate(experiments, start=1):
        reuse = f" B{experiment['source_budget']}mm" if experiment["action"] == "REUSE" else ""
        print(
            f"{index:2d}/{len(experiments)}  {experiment['workload']:<16} B{experiment['budget']:<2}mm "
            f"requested_k={experiment['requested_k']} actual_k={experiment['actual_k']} "
            f"{experiment['action']}{reuse}"
        )
        print(f"       links:  {link_description(experiment['selected'])}")
        print(f"       wire:   {experiment['total_wire']:.3f} mm")
        print(f"       design: {relative(experiment['design'])}")
        print(f"       result: {relative(experiment['result'])}")


def validate_designs(experiments):
    missing = sorted({item["design"] for item in experiments if not item["design"].is_file()})
    if missing:
        fail("Missing required K=4 designs:\n" + "\n".join(f"  - {path}" for path in missing))
    for experiment in experiments:
        try:
            with experiment["design"].open(encoding="utf-8") as design_file:
                design = json.load(design_file)
        except (OSError, json.JSONDecodeError) as error:
            fail(f"Cannot parse required design {experiment['design']}: {error}")
        workload, budget = experiment["workload"], experiment["budget"]
        if design.get("traffic_by_unit") != f"inputs/traffic_by_unit/traffic_project_{workload}.json":
            fail(f"Incorrect unit traffic in {experiment['design']}")
        if design.get("traffic_by_chiplet") != f"inputs/traffic_by_chiplet/traffic_project_{workload}.json":
            fail(f"Incorrect chiplet traffic in {experiment['design']}")
        expected_refs = (
            f"inputs/topologies/topology_project_aware_k4_{workload}_B{budget}mm.json",
            f"inputs/routing_tables/routing_table_project_aware_k4_{workload}_B{budget}mm.json",
        )
        if (design.get("topology"), design.get("routing_table")) != expected_refs:
            fail(f"Unexpected topology/routing references in {experiment['design']}")
        topology_path, routing_path = (REPO_ROOT / ref for ref in expected_refs)
        for description, path in (("topology", topology_path), ("routing table", routing_path)):
            if not path.is_file():
                fail(f"Missing referenced {description}: {path}")
            try:
                with path.open(encoding="utf-8") as input_file:
                    payload = json.load(input_file)
            except (OSError, json.JSONDecodeError) as error:
                fail(f"Cannot parse referenced {description} {path}: {error}")
            if description == "topology" and len(payload) != 24 + experiment["actual_k"]:
                fail(f"Topology link count is not 24 + actual_k for {workload} B{budget}")


def run_experiments(experiments):
    unique = [item for item in experiments if not item["duplicate"]]
    actual_runs = 0
    completed_skips = 0
    for run_number, experiment in enumerate(unique, start=1):
        workload, budget, result = experiment["workload"], experiment["budget"], experiment["result"]
        if result_is_complete(result):
            print(f"SKIP {run_number}/{len(unique)}: {workload} B{budget}mm (complete result)")
            completed_skips += 1
            continue
        print(f"RUN {run_number}/{len(unique)}: {workload} B{budget}mm")
        cmd = [
            "python3", "rapidchiplet.py", "-df", relative(experiment["design"]),
            "-rf", result.stem, "-l", "-t", "-bs",
        ]
        print("Running:", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, cwd=REPO_ROOT)
        except (OSError, subprocess.CalledProcessError) as error:
            detail = f"exit code {error.returncode}" if isinstance(error, subprocess.CalledProcessError) else str(error)
            raise SystemExit(f"FAILED workload-aware K=4 BookSim run: {workload} B{budget}mm ({detail})") from error
        if not result_is_complete(result):
            raise SystemExit(
                f"FAILED workload-aware K=4 BookSim run: {workload} B{budget}mm did not "
                f"produce a complete result at {result}"
            )
        actual_runs += 1
    duplicate_count = sum(item["duplicate"] for item in experiments)
    print("=" * 120)
    print("ALL WORKLOAD-AWARE K=4 BOOKSIM RUNS COMPLETE")
    print(f"Logical configurations: {len(experiments)}")
    print(f"Actual BookSim runs: {actual_runs}")
    print(f"Completed-result skips: {completed_skips}")
    print(f"Duplicate-topology runs avoided: {duplicate_count}")
    print("=" * 120)


def main():
    experiments = build_experiments()
    print_plan(experiments)
    validate_designs(experiments)
    run_experiments(experiments)


if __name__ == "__main__":
    main()
