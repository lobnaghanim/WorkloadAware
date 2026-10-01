"""Summarize workload-aware K=1 RapidChiplet and BookSim results."""

import csv
import json
import math
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SELECTION_FILE = REPO_ROOT / "results/k1_workload_aware_selections.csv"
OUTPUT_FILE = REPO_ROOT / "results/k1_all_booksim_summary.csv"
EXPECTED_WORKLOADS = {"random_uniform", "transpose", "permutation", "hotspot"}
EXPECTED_BUDGETS = {5, 15, 25, 45}
REQUIRED_SELECTION_COLUMNS = {
    "workload",
    "budget_mm",
    "u",
    "v",
    "phy_u",
    "phy_v",
    "length_mm",
}
CSV_FIELDS = [
    "workload",
    "budget_mm",
    "link",
    "u",
    "v",
    "phy_u",
    "phy_v",
    "wire_length_mm",
    "analytical_latency",
    "analytical_latency_gain_percent",
    "analytical_throughput",
    "booksim_low_load",
    "booksim_low_latency",
    "booksim_latency_gain_percent",
    "booksim_low_hops",
    "booksim_hop_gain_percent",
    "booksim_low_accepted_rate",
    "last_stable_load",
    "last_stable_latency",
    "last_stable_accepted_rate",
    "knee_load",
    "knee_latency",
    "reused_result",
    "source_result_file",
]


def fail(message):
    raise RuntimeError(message)


def finite_number(value, description, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"Malformed result {path}: {description} must be numeric")
    if not math.isfinite(value):
        fail(f"Malformed result {path}: {description} must be finite")
    return value


def nested_metric(data, keys, path):
    value = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            fail(f"Malformed result {path}: missing {'/'.join(keys)}")
        value = value[key]
    return finite_number(value, "/".join(keys), path)


def parse_result(path):
    if not path.is_file():
        fail(f"Required result file does not exist: {path}")

    try:
        with path.open(encoding="utf-8") as result_file:
            data = json.load(result_file)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot parse result file {path}: {error}")

    if not isinstance(data, dict):
        fail(f"Malformed result {path}: top-level JSON value must be an object")
    for section in ("latency", "throughput", "booksim_simulation"):
        if section not in data or not isinstance(data[section], dict):
            fail(f"Malformed result {path}: missing or invalid '{section}' section")

    load_points = []
    for raw_load, point in data["booksim_simulation"].items():
        try:
            load = float(raw_load)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(load):
            fail(f"Malformed result {path}: non-finite offered load {raw_load!r}")
        if not isinstance(point, dict):
            fail(f"Malformed result {path}: offered-load entry {raw_load!r} is not an object")

        load_points.append(
            {
                "load": load,
                "latency": nested_metric(point, ("packet_latency", "avg"), path),
                "hops": nested_metric(point, ("hops", "avg"), path),
                "accepted_rate": nested_metric(
                    point, ("accepted_packet_rate", "avg"), path
                ),
            }
        )

    if not load_points:
        fail(f"Malformed result {path}: no numeric BookSim offered-load entries")
    load_points.sort(key=lambda point: point["load"])

    low = load_points[0]
    knee_index = None
    for index, point in enumerate(load_points):
        if point["latency"] >= 2.0 * low["latency"]:
            knee_index = index
            break

    if knee_index is None:
        stable = load_points[-1]
        knee_load = None
        knee_latency = None
    else:
        # The first point cannot normally be a knee because it defines the
        # threshold. Keeping this explicit makes malformed zero/negative data
        # fail rather than assigning a nonexistent previous stable point.
        if knee_index == 0:
            fail(f"Malformed result {path}: low-load latency cannot define a knee")
        stable = load_points[knee_index - 1]
        knee_load = load_points[knee_index]["load"]
        knee_latency = load_points[knee_index]["latency"]

    return {
        "analytical_latency": nested_metric(data, ("latency", "avg"), path),
        "analytical_throughput": nested_metric(
            data, ("throughput", "aggregate_throughput"), path
        ),
        "booksim_low_load": low["load"],
        "booksim_low_latency": low["latency"],
        "booksim_low_hops": low["hops"],
        "booksim_low_accepted_rate": low["accepted_rate"],
        "last_stable_load": stable["load"],
        "last_stable_latency": stable["latency"],
        "last_stable_accepted_rate": stable["accepted_rate"],
        "knee_load": knee_load,
        "knee_latency": knee_latency,
    }


def load_selections():
    try:
        with SELECTION_FILE.open(newline="", encoding="utf-8") as selection_file:
            reader = csv.DictReader(selection_file)
            missing = sorted(REQUIRED_SELECTION_COLUMNS - set(reader.fieldnames or []))
            if missing:
                fail(f"Selection CSV is missing columns: {', '.join(missing)}")
            raw_rows = list(reader)
    except OSError as error:
        fail(f"Cannot read selection CSV {SELECTION_FILE}: {error}")

    selections = []
    seen_keys = set()
    for row_number, row in enumerate(raw_rows, start=2):
        try:
            workload = row["workload"].strip()
            budget_value = float(row["budget_mm"])
            budget = int(budget_value)
            selection = {
                "workload": workload,
                "budget_mm": budget,
                "u": int(row["u"]),
                "v": int(row["v"]),
                "phy_u": int(row["phy_u"]),
                "phy_v": int(row["phy_v"]),
                "wire_length_mm": float(row["length_mm"]),
            }
        except (TypeError, ValueError) as error:
            fail(f"Invalid selection CSV row {row_number}: {error}")

        if budget_value != budget:
            fail(f"Invalid selection CSV row {row_number}: budget must be a whole number")
        if not workload:
            fail(f"Invalid selection CSV row {row_number}: workload is empty")
        if not math.isfinite(selection["wire_length_mm"]):
            fail(f"Invalid selection CSV row {row_number}: length_mm must be finite")

        key = (workload, budget)
        if key in seen_keys:
            fail(f"Duplicate workload+budget selection: {workload} B{budget}")
        seen_keys.add(key)
        selections.append(selection)

    workloads = {row["workload"] for row in selections}
    if workloads != EXPECTED_WORKLOADS:
        missing = sorted(EXPECTED_WORKLOADS - workloads)
        unexpected = sorted(workloads - EXPECTED_WORKLOADS)
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        fail(f"Selection CSV workload validation failed ({'; '.join(details)})")

    for workload in sorted(EXPECTED_WORKLOADS):
        budgets = {row["budget_mm"] for row in selections if row["workload"] == workload}
        if budgets != EXPECTED_BUDGETS:
            fail(
                f"Workload {workload} must contain budgets "
                f"{sorted(EXPECTED_BUDGETS)}; found {sorted(budgets)}"
            )

    return selections


def result_path(workload, budget=None):
    suffix = "baseline" if budget is None else f"B{budget}mm"
    return REPO_ROOT / "results" / f"k1_{workload}_{suffix}.json"


def relative_path(path):
    return path.relative_to(REPO_ROOT).as_posix()


def topology_signature(selection):
    return (
        selection["u"],
        selection["v"],
        selection["phy_u"],
        selection["phy_v"],
    )


def percent_improvement(baseline, candidate, description, workload):
    if baseline == 0:
        fail(f"Cannot calculate {description} improvement for {workload}: baseline is zero")
    return 100.0 * (baseline - candidate) / baseline


def build_summary(selections):
    rows = []

    for workload in sorted(EXPECTED_WORKLOADS):
        baseline_path = result_path(workload)
        baseline_metrics = parse_result(baseline_path)
        baseline_row = {
            "workload": workload,
            "budget_mm": 0,
            "link": "none",
            "u": "",
            "v": "",
            "phy_u": "",
            "phy_v": "",
            "wire_length_mm": 0,
            **baseline_metrics,
            "analytical_latency_gain_percent": 0.0,
            "booksim_latency_gain_percent": 0.0,
            "booksim_hop_gain_percent": 0.0,
            "reused_result": False,
            "source_result_file": relative_path(baseline_path),
        }
        rows.append(baseline_row)

        workload_selections = sorted(
            (row for row in selections if row["workload"] == workload),
            key=lambda row: row["budget_mm"],
        )
        signature_sources = {}
        for selection in workload_selections:
            signature = topology_signature(selection)
            if signature not in signature_sources:
                matching = [
                    row
                    for row in workload_selections
                    if topology_signature(row) == signature
                ]
                existing = [
                    row
                    for row in matching
                    if result_path(workload, row["budget_mm"]).is_file()
                ]
                if not existing:
                    budgets = ", ".join(f"B{row['budget_mm']}" for row in matching)
                    fail(
                        f"No BookSim result exists for topology {signature} "
                        f"used by {workload} {budgets}"
                    )
                source_selection = min(existing, key=lambda row: row["budget_mm"])
                signature_sources[signature] = source_selection

            source_selection = signature_sources[signature]
            source_path = result_path(workload, source_selection["budget_mm"])
            metrics = parse_result(source_path)
            rows.append(
                {
                    "workload": workload,
                    "budget_mm": selection["budget_mm"],
                    "link": f"{selection['u']}<->{selection['v']}",
                    "u": selection["u"],
                    "v": selection["v"],
                    "phy_u": selection["phy_u"],
                    "phy_v": selection["phy_v"],
                    "wire_length_mm": selection["wire_length_mm"],
                    **metrics,
                    "analytical_latency_gain_percent": percent_improvement(
                        baseline_metrics["analytical_latency"],
                        metrics["analytical_latency"],
                        "analytical latency",
                        workload,
                    ),
                    "booksim_latency_gain_percent": percent_improvement(
                        baseline_metrics["booksim_low_latency"],
                        metrics["booksim_low_latency"],
                        "BookSim latency",
                        workload,
                    ),
                    "booksim_hop_gain_percent": percent_improvement(
                        baseline_metrics["booksim_low_hops"],
                        metrics["booksim_low_hops"],
                        "BookSim hop count",
                        workload,
                    ),
                    "reused_result": (
                        source_selection["budget_mm"] != selection["budget_mm"]
                    ),
                    "source_result_file": relative_path(source_path),
                }
            )

    keys = [(row["workload"], row["budget_mm"]) for row in rows]
    if len(keys) != len(set(keys)):
        fail("Summary contains duplicate workload+budget rows")
    if len(rows) != 20:
        fail(f"Summary must contain exactly 20 rows; found {len(rows)}")
    return rows


def format_number(value, decimals=3):
    return "-" if value is None else f"{value:.{decimals}f}"


def print_table(rows):
    print("=" * 133)
    print("K=1 ALL-WORKLOAD BOOKSIM SUMMARY")
    print("=" * 133)
    print(
        f"{'WORKLOAD':<15}{'BUDGET':>8}{'LINK':>8}{'WIRE_MM':>9}"
        f"{'AN_LAT':>9}{'AN_GAIN%':>9}{'BS_LAT':>9}{'BS_GAIN%':>9}"
        f"{'HOPS':>8}{'HOP_GAIN%':>10}{'LAST_STABLE':>12}{'KNEE':>9}"
        f"{'ACCEPT_RATE':>12}{'REUSED':>8}"
    )
    print("-" * 133)
    for row in rows:
        budget = "baseline" if row["budget_mm"] == 0 else f"{row['budget_mm']}mm"
        print(
            f"{row['workload']:<15}{budget:>8}{row['link']:>8}"
            f"{row['wire_length_mm']:>9.3f}"
            f"{row['analytical_latency']:>9.2f}"
            f"{row['analytical_latency_gain_percent']:>9.2f}"
            f"{row['booksim_low_latency']:>9.2f}"
            f"{row['booksim_latency_gain_percent']:>9.2f}"
            f"{row['booksim_low_hops']:>8.3f}"
            f"{row['booksim_hop_gain_percent']:>10.2f}"
            f"{format_number(row['last_stable_load']):>12}"
            f"{format_number(row['knee_load']):>9}"
            f"{row['last_stable_accepted_rate']:>12.5f}"
            f"{str(row['reused_result']):>8}"
        )


def write_csv(rows):
    try:
        with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows({field: row[field] for field in CSV_FIELDS} for row in rows)
    except OSError as error:
        fail(f"Cannot write summary CSV {OUTPUT_FILE}: {error}")


def main():
    selections = load_selections()
    rows = build_summary(selections)
    write_csv(rows)
    print_table(rows)

    reused_count = sum(row["reused_result"] for row in rows)
    print("=" * 133)
    print("K=1 ALL-WORKLOAD BOOKSIM SUMMARY COMPLETE")
    print(f"Workloads: {len({row['workload'] for row in rows})}")
    print(f"Summary rows: {len(rows)}")
    print(f"Reused topology results: {reused_count}")
    print(f"Output CSV: {relative_path(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()
