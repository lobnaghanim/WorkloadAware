"""Run RapidChiplet and BookSim for unique fixed K=4 configurations."""

import copy
import json
import math
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

import create_fixed_k4_designs as designs


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
    if not isinstance(result, dict):
        return False
    if not {"latency", "throughput", "booksim_simulation"}.issubset(result):
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


def build_experiments():
    steps, summaries = designs.load_inputs()
    source_by_signature = {}
    budget_metadata = {}
    for budget in designs.BUDGETS:
        selected = sorted(steps.get(budget, []), key=lambda row: row["step"])
        signature = designs.physical_signature(selected)
        source_budget = source_by_signature.setdefault(signature, budget)
        budget_metadata[budget] = {
            "selected": selected,
            "source_budget": source_budget,
            "duplicate": source_budget != budget,
            "summary": summaries[budget],
        }

    experiments = []
    for workload in designs.WORKLOADS:
        for budget in designs.BUDGETS:
            metadata = budget_metadata[budget]
            source_budget = metadata["source_budget"]
            design_path = (
                REPO_ROOT
                / f"inputs/designs/design_project_fixed_k4_{workload}_B{budget}mm.json"
            )
            result_path = (
                RESULT_DIR / f"fixed_k4_{workload}_B{source_budget}mm.json"
            )
            duplicate = metadata["duplicate"]
            experiments.append(
                {
                    "workload": workload,
                    "budget": budget,
                    "requested_k": designs.REQUESTED_K,
                    "actual_k": metadata["summary"]["actual_k"],
                    "selected": metadata["selected"],
                    "total_wire": metadata["summary"]["total_wire_length_mm"],
                    "source_budget": source_budget,
                    "duplicate": duplicate,
                    "design": design_path,
                    "result": result_path,
                    "action": (
                        "REUSE"
                        if duplicate
                        else "SKIP" if result_is_complete(result_path) else "RUN"
                    ),
                }
            )
    if len(experiments) != 16:
        fail(f"Expected 16 logical fixed K=4 configurations; found {len(experiments)}")
    return experiments


def links_text(selected):
    if not selected:
        return "none"
    return ", ".join(
        f"{row['u']}<->{row['v']}[PHY {row['phy_u']}<->{row['phy_v']}]"
        for row in selected
    )


def print_plan(experiments):
    print("=" * 112)
    print("FIXED K=4 BOOKSIM RUN PLAN")
    print("=" * 112)
    for index, experiment in enumerate(experiments, start=1):
        reuse = (
            f" B{experiment['source_budget']}mm"
            if experiment["action"] == "REUSE"
            else ""
        )
        print(f"Configuration {index}/{len(experiments)}")
        print(f"  workload:       {experiment['workload']}")
        print(f"  budget:         {experiment['budget']} mm")
        print(f"  requested K:    {experiment['requested_k']}")
        print(f"  actual K:       {experiment['actual_k']}")
        print(f"  selected links: {links_text(experiment['selected'])}")
        print(f"  total wire:     {experiment['total_wire']:.3f} mm")
        print(f"  design path:    {relative(experiment['design'])}")
        print(f"  result path:    {relative(experiment['result'])}")
        print(f"  status:         {experiment['action']}{reuse}")


def expected_physical_payloads(experiments):
    steps, summaries = designs.load_inputs()
    baseline = designs.proven.load_baseline()
    payloads = {}
    for budget in designs.BUDGETS:
        selected = sorted(steps.get(budget, []), key=lambda row: row["step"])
        topology = copy.deepcopy(baseline["topology"])
        designs.proven.add_selected_links(
            topology, selected, summaries[budget], baseline, budget
        )
        routing = designs.proven.generate_splif_routing(
            baseline["chiplets"], baseline["placement"], topology
        )
        payloads[budget] = topology, routing
    return payloads


def validate_designs(experiments):
    missing = sorted(
        {
            experiment["design"]
            for experiment in experiments
            if not experiment["design"].is_file()
        }
    )
    if missing:
        fail(
            "Missing required fixed K=4 designs:\n"
            + "\n".join(f"  - {path}" for path in missing)
        )

    expected_payloads = expected_physical_payloads(experiments)
    refs_by_budget = {}
    validated_budgets = set()
    for experiment in experiments:
        try:
            design = designs.proven.hlp.read_json(str(experiment["design"]))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            fail(f"Cannot parse fixed K=4 design {experiment['design']}: {error}")
        if not isinstance(design, dict):
            fail(f"Malformed fixed K=4 design {experiment['design']}")

        workload = experiment["workload"]
        expected_unit = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
        expected_chiplet = (
            f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
        )
        if design.get("traffic_by_unit") != expected_unit:
            fail(f"Incorrect unit traffic in {experiment['design']}")
        if design.get("traffic_by_chiplet") != expected_chiplet:
            fail(f"Incorrect chiplet traffic in {experiment['design']}")
        if not (REPO_ROOT / expected_unit).is_file() or not (
            REPO_ROOT / expected_chiplet
        ).is_file():
            fail(f"Missing traffic input referenced by {experiment['design']}")

        refs = (design.get("topology"), design.get("routing_table"))
        previous = refs_by_budget.setdefault(experiment["budget"], refs)
        if refs != previous:
            fail(
                f"Fixed topology/routing changes across workloads for "
                f"B{experiment['budget']}"
            )
        expected_refs = (
            f"inputs/topologies/topology_project_fixed_k4_B{experiment['budget']}mm.json",
            f"inputs/routing_tables/routing_table_project_fixed_k4_B{experiment['budget']}mm.json",
        )
        if refs != expected_refs:
            fail(f"Unexpected topology/routing references in {experiment['design']}")

        budget = experiment["budget"]
        if budget in validated_budgets:
            continue
        validated_budgets.add(budget)
        topology_path, routing_path = (REPO_ROOT / reference for reference in refs)
        for description, path in (
            ("topology", topology_path),
            ("routing table", routing_path),
        ):
            if not path.is_file():
                fail(f"Missing referenced {description} for B{budget}: {path}")
        try:
            topology = designs.proven.hlp.read_json(str(topology_path))
            routing = designs.proven.hlp.read_json(str(routing_path))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            fail(f"Cannot parse physical inputs for B{budget}: {error}")
        expected_topology, expected_routing = expected_payloads[budget]
        if topology != expected_topology:
            fail(f"Topology payload does not exactly match K=4 selections for B{budget}")
        if routing != expected_routing:
            fail(f"Routing payload is not regenerated SPLIF routing for B{budget}")
        if len(topology) != 24 + experiment["actual_k"]:
            fail(f"Topology does not contain 24 + actual_k links for B{budget}")

    if len(refs_by_budget) != 4 or len(validated_budgets) != 4:
        fail("Did not validate exactly four fixed physical configurations")
    print("Validated all 16 logical designs and 4 physical configurations.")


def run_experiments(experiments):
    unique = [experiment for experiment in experiments if not experiment["duplicate"]]
    actual_runs = 0
    completed_skips = 0
    for run_number, experiment in enumerate(unique, start=1):
        workload, budget = experiment["workload"], experiment["budget"]
        result = experiment["result"]
        if result_is_complete(result):
            print(
                f"SKIP {run_number}/{len(unique)}: {workload} B{budget}mm "
                "(complete result)"
            )
            completed_skips += 1
            continue

        print(f"RUN {run_number}/{len(unique)}: {workload} B{budget}mm")
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
        print("Running:", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, cwd=REPO_ROOT)
        except (OSError, subprocess.CalledProcessError) as error:
            detail = (
                f"exit code {error.returncode}"
                if isinstance(error, subprocess.CalledProcessError)
                else str(error)
            )
            raise SystemExit(
                f"FAILED fixed K=4 BookSim run: {workload} B{budget}mm ({detail})"
            ) from error
        if not result_is_complete(result):
            raise SystemExit(
                f"FAILED fixed K=4 BookSim run: {workload} B{budget}mm did not "
                f"produce a complete result at {result}"
            )
        actual_runs += 1

    duplicate_count = sum(experiment["duplicate"] for experiment in experiments)
    print("=" * 112)
    print("ALL FIXED K=4 BOOKSIM RUNS COMPLETE")
    print(f"Logical configurations: {len(experiments)}")
    print(f"Actual BookSim runs: {actual_runs}")
    print(f"Completed-result skips: {completed_skips}")
    print(f"Duplicate-topology runs avoided: {duplicate_count}")
    print("=" * 112)


def main():
    experiments = build_experiments()
    print_plan(experiments)
    validate_designs(experiments)
    run_experiments(experiments)


if __name__ == "__main__":
    main()
