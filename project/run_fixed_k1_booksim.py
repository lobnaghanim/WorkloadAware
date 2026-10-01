"""Run BookSim for every unique fixed K=1 workload/topology combination."""

import csv
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SELECTION_FILE = REPO_ROOT / "results/k1_fixed_selections.csv"
DESIGN_DIR = REPO_ROOT / "inputs/designs"
RESULT_DIR = REPO_ROOT / "results"
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
REQUIRED_COLUMNS = {"budget_mm", "u", "v", "phy_u", "phy_v"}


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
            float(offered_load)
        except (TypeError, ValueError):
            continue
        return True
    return False


def load_selections():
    try:
        with SELECTION_FILE.open(newline="", encoding="utf-8") as selection_file:
            reader = csv.DictReader(selection_file)
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            if missing:
                fail(f"Fixed-selection CSV is missing columns: {', '.join(missing)}")
            rows = list(reader)
    except OSError as error:
        fail(f"Cannot read fixed-selection CSV {SELECTION_FILE}: {error}")

    selections = {}
    for row_number, row in enumerate(rows, start=2):
        try:
            raw_budget = float(row["budget_mm"])
            budget = int(raw_budget)
            selection = {
                "budget_mm": budget,
                "u": int(row["u"]),
                "v": int(row["v"]),
                "phy_u": int(row["phy_u"]),
                "phy_v": int(row["phy_v"]),
            }
        except (TypeError, ValueError) as error:
            fail(f"Invalid fixed-selection CSV row {row_number}: {error}")
        if raw_budget != budget:
            fail(f"Budget on fixed-selection CSV row {row_number} is not integral")
        if budget in selections:
            fail(f"Duplicate fixed selection for budget {budget} mm")
        selections[budget] = selection

    if set(selections) != set(BUDGETS):
        fail(
            f"Fixed-selection budgets must be {list(BUDGETS)}; "
            f"found {sorted(selections)}"
        )
    return selections


def signature(selection):
    return (
        selection["u"],
        selection["v"],
        selection["phy_u"],
        selection["phy_v"],
    )


def build_experiments(selections):
    source_budget = {}
    for budget in BUDGETS:
        source_budget.setdefault(signature(selections[budget]), budget)

    experiments = []
    for workload in WORKLOADS:
        for budget in BUDGETS:
            selection = selections[budget]
            source = source_budget[signature(selection)]
            design = (
                DESIGN_DIR
                / f"design_project_fixed_k1_{workload}_B{budget}mm.json"
            )
            source_result = RESULT_DIR / f"fixed_k1_{workload}_B{source}mm.json"
            duplicate = source != budget
            experiments.append(
                {
                    "workload": workload,
                    "budget": budget,
                    "source_budget": source,
                    "selection": selection,
                    "design": design,
                    "result": source_result,
                    "duplicate": duplicate,
                    "action": (
                        "SKIP"
                        if duplicate or result_is_complete(source_result)
                        else "RUN"
                    ),
                }
            )
    if len(experiments) != 16:
        fail(f"Expected 16 logical experiment points; found {len(experiments)}")
    return experiments


def print_plan(experiments):
    print("=" * 108)
    print("FIXED K=1 BOOKSIM RUN PLAN")
    print("=" * 108)
    for index, experiment in enumerate(experiments, start=1):
        selection = experiment["selection"]
        reuse = (
            f" (REUSE B{experiment['source_budget']}mm)"
            if experiment["duplicate"]
            else ""
        )
        print(
            f"{index:2d}/{len(experiments)}  {experiment['workload']:<16} "
            f"B{experiment['budget']:<2}mm  "
            f"{selection['u']}<->{selection['v']}  "
            f"{experiment['action']}{reuse}"
        )
        print(f"       design: {relative(experiment['design'])}")
        print(f"       result: {relative(experiment['result'])}")


def validate_designs(experiments):
    missing = sorted(
        {experiment["design"] for experiment in experiments if not experiment["design"].is_file()}
    )
    if missing:
        paths = "\n".join(f"  - {path}" for path in missing)
        fail(f"Missing required fixed K=1 design file(s):\n{paths}")


def run_experiments(experiments):
    unique_experiments = [
        experiment for experiment in experiments if not experiment["duplicate"]
    ]
    actual_runs = 0
    skipped_completed = 0

    for run_number, experiment in enumerate(unique_experiments, start=1):
        workload = experiment["workload"]
        budget = experiment["budget"]
        result = experiment["result"]
        if result_is_complete(result):
            print(
                f"SKIP {run_number}/{len(unique_experiments)}: "
                f"{workload} B{budget}mm (complete result)"
            )
            skipped_completed += 1
            continue

        print(
            f"RUN {run_number}/{len(unique_experiments)}: "
            f"{workload} B{budget}mm"
        )
        # rapidchiplet.py supplies results/ and appends '.json' itself.
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
                f"FAILED fixed K=1 BookSim run: {workload} B{budget}mm ({detail})"
            ) from error

        if not result_is_complete(result):
            raise SystemExit(
                f"FAILED fixed K=1 BookSim run: {workload} B{budget}mm did not "
                f"produce a complete result at {result}"
            )
        actual_runs += 1

    duplicate_count = sum(experiment["duplicate"] for experiment in experiments)
    print("=" * 108)
    print("ALL FIXED K=1 BOOKSIM RUNS COMPLETE")
    print(f"Logical experiment points: {len(experiments)}")
    print(f"Actual BookSim runs: {actual_runs}")
    print(f"Skipped completed results: {skipped_completed}")
    print(f"Duplicate-topology runs avoided: {duplicate_count}")
    print("=" * 108)


def main():
    selections = load_selections()
    experiments = build_experiments(selections)
    print_plan(experiments)
    validate_designs(experiments)
    run_experiments(experiments)


if __name__ == "__main__":
    main()
