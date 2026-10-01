"""Run all unique workload-aware K=1 designs through RapidChiplet/BookSim."""

import csv
import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SELECTION_FILE = REPO_ROOT / "results/k1_workload_aware_selections.csv"
DESIGN_DIR = REPO_ROOT / "inputs/designs"
RESULT_DIR = REPO_ROOT / "results"
REQUIRED_COLUMNS = {"workload", "budget_mm", "u", "v", "phy_u", "phy_v"}


def result_is_complete(path):
    """Return True only for a valid result containing usable BookSim data."""
    if not path.is_file():
        return False

    try:
        with path.open(encoding="utf-8") as result_file:
            result = json.load(result_file)
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(result, dict):
        return False

    required_sections = {"latency", "throughput", "booksim_simulation"}
    if not required_sections.issubset(result):
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
            columns = set(reader.fieldnames or [])
            missing_columns = sorted(REQUIRED_COLUMNS - columns)
            if missing_columns:
                raise ValueError(
                    f"Selection CSV is missing columns: {', '.join(missing_columns)}"
                )
            selections = list(reader)
    except OSError as error:
        raise SystemExit(f"Cannot read selection CSV {SELECTION_FILE}: {error}") from error

    if not selections:
        raise SystemExit(f"Selection CSV contains no selections: {SELECTION_FILE}")

    return selections


def build_runs(selections):
    workloads = sorted({row["workload"].strip() for row in selections})
    if "" in workloads:
        raise ValueError("Selection CSV contains an empty workload name")

    runs = []
    for workload in workloads:
        runs.append(
            {
                "workload": workload,
                "label": "baseline",
                "design": DESIGN_DIR / f"design_project_k1_{workload}_baseline.json",
                "result": RESULT_DIR / f"k1_{workload}_baseline.json",
            }
        )

    seen = set()
    for row_number, row in enumerate(selections, start=2):
        try:
            workload = row["workload"].strip()
            budget_value = float(row["budget_mm"])
            budget = int(budget_value)
            u = int(row["u"])
            v = int(row["v"])
            phy_u = int(row["phy_u"])
            phy_v = int(row["phy_v"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid selection data on CSV row {row_number}: {error}") from error

        if budget_value != budget:
            raise ValueError(
                f"Budget on CSV row {row_number} is not a whole number: {budget_value}"
            )

        signature = (workload, u, v, phy_u, phy_v)
        if signature in seen:
            continue
        seen.add(signature)

        runs.append(
            {
                "workload": workload,
                "label": f"B{budget}mm",
                "design": (
                    DESIGN_DIR / f"design_project_k1_{workload}_B{budget}mm.json"
                ),
                "result": RESULT_DIR / f"k1_{workload}_B{budget}mm.json",
            }
        )

    return runs


def completed_result(run):
    """Find a complete canonical result or a legacy double-extension result."""
    result = run["result"]
    if result_is_complete(result):
        return result

    # rapidchiplet.py appends '.json'. Older runners that passed an already
    # suffixed name therefore produced valid files ending in '.json.json'.
    legacy_result = result.with_name(result.name + ".json")
    if result_is_complete(legacy_result):
        return legacy_result

    return None


def relative(path):
    return path.relative_to(REPO_ROOT)


def main():
    try:
        runs = build_runs(load_selections())
    except ValueError as error:
        raise SystemExit(f"Invalid selection CSV {SELECTION_FILE}: {error}") from error

    for run in runs:
        run["completed_result"] = completed_result(run)
        run["action"] = "SKIP" if run["completed_result"] else "RUN"

    print("=" * 100)
    print("K=1 BOOKSIM RUN PLAN")
    print("=" * 100)
    for index, run in enumerate(runs, start=1):
        print(
            f"{index:2d}/{len(runs)}  {run['workload']:<16} "
            f"{run['label']:<10} {run['action']}"
        )
        print(f"       design: {relative(run['design'])}")
        print(f"       result: {relative(run['result'])}")
    print(f"Total unique configurations: {len(runs)}")

    missing_designs = [run["design"] for run in runs if not run["design"].is_file()]
    if missing_designs:
        paths = "\n".join(f"  - {path}" for path in missing_designs)
        raise SystemExit(f"Missing required design file(s):\n{paths}")

    run_count = 0
    skip_count = 0

    for index, run in enumerate(runs, start=1):
        completed = run["completed_result"]
        if completed is not None:
            if completed != run["result"]:
                shutil.copy2(completed, run["result"])
                print(
                    f"Normalized completed result {relative(completed)} -> "
                    f"{relative(run['result'])}"
                )
            print(f"SKIP {index}/{len(runs)}: {run['workload']} {run['label']}")
            skip_count += 1
            continue

        print(f"RUN {index}/{len(runs)}: {run['workload']} {run['label']}")
        # rapidchiplet.py adds the results directory and '.json' suffix itself.
        result_stem = run["result"].stem
        cmd = [
            "python3",
            "rapidchiplet.py",
            "-df",
            str(relative(run["design"])),
            "-rf",
            result_stem,
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
                f"FAILED: {run['workload']} {run['label']} "
                f"({detail})"
            ) from error

        if not result_is_complete(run["result"]):
            raise SystemExit(
                f"FAILED: {run['workload']} {run['label']} did not produce a "
                f"complete result at {run['result']}"
            )
        run_count += 1

    print("=" * 100)
    print("ALL K=1 BOOKSIM RUNS COMPLETE")
    print(f"Actually run: {run_count}")
    print(f"Skipped (already complete): {skip_count}")
    print(f"Total configurations: {len(runs)}")
    print("=" * 100)


if __name__ == "__main__":
    main()
