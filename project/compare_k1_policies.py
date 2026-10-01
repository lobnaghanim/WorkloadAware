"""Create the final Mesh vs Fixed K=1 vs Workload-Aware K=1 dataset."""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
POLICIES = ("mesh", "fixed", "workload_aware")
WORKLOAD_SELECTION_FILE = REPO_ROOT / "results/k1_workload_aware_selections.csv"
WORKLOAD_SUMMARY_FILE = REPO_ROOT / "results/k1_all_booksim_summary.csv"
FIXED_SELECTION_FILE = REPO_ROOT / "results/k1_fixed_selections.csv"
BASE_DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
OUTPUT_FILE = REPO_ROOT / "results/k1_policy_comparison.csv"
ARCHITECTURE_KEYS = ("chiplets", "placement", "packaging", "technologies")
EPS = 1e-9
METRIC_FIELDS = (
    "analytical_latency",
    "analytical_throughput",
    "booksim_low_load",
    "booksim_low_latency",
    "booksim_low_hops",
    "booksim_low_accepted_rate",
    "last_stable_load",
    "last_stable_latency",
    "last_stable_accepted_rate",
    "knee_load",
    "knee_latency",
)
CSV_FIELDS = (
    "workload",
    "budget_mm",
    "policy",
    "k",
    "link",
    "u",
    "v",
    "phy_u",
    "phy_v",
    "wire_length_mm",
    "analytical_latency",
    "analytical_throughput",
    "booksim_low_load",
    "booksim_low_latency",
    "booksim_low_hops",
    "booksim_low_accepted_rate",
    "last_stable_load",
    "last_stable_latency",
    "last_stable_accepted_rate",
    "knee_load",
    "knee_latency",
    "latency_gain_vs_mesh_percent",
    "hop_gain_vs_mesh_percent",
    "analytical_latency_gain_vs_mesh_percent",
    "aware_latency_gain_vs_fixed_percent",
    "aware_hop_gain_vs_fixed_percent",
    "source_result_file",
    "reused_result",
)


def fail(message):
    raise RuntimeError(message)


def relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def read_json(path, description):
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(encoding="utf-8") as input_file:
            value = json.load(input_file)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot parse {description} {path}: {error}")
    if not isinstance(value, dict):
        fail(f"Malformed {description} {path}: expected a JSON object")
    return value


def result_metric(point, section, path):
    if not isinstance(point, dict):
        return None
    nested = point.get(section)
    if not isinstance(nested, dict):
        return None
    value = nested.get("avg")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        fail(f"Malformed result {path}: {section}/avg must be finite")
    return value


def parse_policy_result(path):
    """Parse valid tested loads, ignoring BookSim's empty failed-load marker."""
    data = read_json(path, "BookSim result")
    for section in ("latency", "throughput", "booksim_simulation"):
        if section not in data or not isinstance(data[section], dict):
            fail(f"Malformed result {path}: missing or invalid {section!r} section")

    analytical_latency = number(
        data["latency"].get("avg"), f"latency/avg in {path}"
    )
    analytical_throughput = number(
        data["throughput"].get("aggregate_throughput"),
        f"throughput/aggregate_throughput in {path}",
    )
    points = []
    for raw_load, point in data["booksim_simulation"].items():
        try:
            load = float(raw_load)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(load):
            fail(f"Malformed result {path}: offered load {raw_load!r} is not finite")
        latency = result_metric(point, "packet_latency", path)
        hops = result_metric(point, "hops", path)
        accepted_rate = result_metric(point, "accepted_packet_rate", path)
        # BookSim records an empty object for a tested load that failed before
        # producing metrics. Such a marker is not an observed latency point.
        if latency is None or hops is None or accepted_rate is None:
            continue
        points.append(
            {
                "load": load,
                "latency": latency,
                "hops": hops,
                "accepted_rate": accepted_rate,
            }
        )

    if not points:
        fail(f"Malformed result {path}: no complete numeric BookSim load points")
    points.sort(key=lambda point: point["load"])
    low = points[0]
    knee_index = next(
        (
            index
            for index, point in enumerate(points)
            if point["latency"] >= 2.0 * low["latency"]
        ),
        None,
    )
    if knee_index is None:
        stable = points[-1]
        knee_load = None
        knee_latency = None
    else:
        if knee_index == 0:
            fail(f"Malformed result {path}: low-load point cannot define its own knee")
        stable = points[knee_index - 1]
        knee_load = points[knee_index]["load"]
        knee_latency = points[knee_index]["latency"]

    return {
        "analytical_latency": analytical_latency,
        "analytical_throughput": analytical_throughput,
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


def load_csv(path, required_columns, description):
    try:
        with path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            missing = sorted(set(required_columns) - set(reader.fieldnames or []))
            if missing:
                fail(f"{description} is missing columns: {', '.join(missing)}")
            return list(reader)
    except OSError as error:
        fail(f"Cannot read {description} {path}: {error}")


def integer(value, description):
    try:
        raw = float(value)
        converted = int(raw)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if raw != converted:
        fail(f"Invalid {description}: expected a whole number, found {value!r}")
    return converted


def number(value, description, optional=False):
    if optional and (value is None or value == ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if not math.isfinite(converted):
        fail(f"Invalid {description}: value must be finite")
    return converted


def boolean(value, description):
    if value is True or value == "True":
        return True
    if value is False or value == "False":
        return False
    fail(f"Invalid {description}: expected True or False, found {value!r}")


def signature(selection):
    return (
        selection["u"],
        selection["v"],
        selection["phy_u"],
        selection["phy_v"],
    )


def improvement(baseline, candidate, description, workload):
    if baseline == 0:
        fail(f"Cannot normalize {description} for {workload}: baseline is zero")
    return 100.0 * (baseline - candidate) / baseline


def metrics_match(left, right):
    for field in METRIC_FIELDS:
        a, b = left[field], right[field]
        if a is None or b is None:
            if a is not None or b is not None:
                return False
        elif not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
            return False
    return True


def load_selection_rows(path, workload_aware):
    required = {"budget_mm", "u", "v", "phy_u", "phy_v", "length_mm"}
    if workload_aware:
        required.add("workload")
    raw_rows = load_csv(path, required, "selection CSV")
    selections = {}
    for row_number, row in enumerate(raw_rows, start=2):
        workload = row["workload"].strip() if workload_aware else None
        budget = integer(row["budget_mm"], f"budget on {path} row {row_number}")
        key = (workload, budget) if workload_aware else budget
        if key in selections:
            fail(f"Duplicate selection entry in {path}: {key}")
        selections[key] = {
            "workload": workload,
            "budget_mm": budget,
            "u": integer(row["u"], f"u on {path} row {row_number}"),
            "v": integer(row["v"], f"v on {path} row {row_number}"),
            "phy_u": integer(row["phy_u"], f"phy_u on {path} row {row_number}"),
            "phy_v": integer(row["phy_v"], f"phy_v on {path} row {row_number}"),
            "wire_length_mm": number(
                row["length_mm"], f"length_mm on {path} row {row_number}"
            ),
        }

    expected = (
        {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
        if workload_aware
        else set(BUDGETS)
    )
    if set(selections) != expected:
        missing = sorted(expected - set(selections), key=str)
        extra = sorted(set(selections) - expected, key=str)
        fail(f"Selection matrix mismatch in {path}; missing={missing}, extra={extra}")
    return selections


def validate_architecture(design_path, baseline_design, label):
    design = read_json(design_path, f"{label} design")
    for key in ARCHITECTURE_KEYS:
        if design.get(key) != baseline_design.get(key):
            fail(
                f"Physical architecture mismatch for {label}: design field {key!r} "
                f"is {design.get(key)!r}, expected {baseline_design.get(key)!r}"
            )
    return design


def validate_selected_link(design, selection, label):
    topology_path = REPO_ROOT / design["topology"]
    topology = read_json_list(topology_path, f"{label} topology")
    expected = {
        (selection["u"], selection["phy_u"]),
        (selection["v"], selection["phy_v"]),
    }
    matching = []
    for link in topology:
        ep1, ep2 = link["ep1"], link["ep2"]
        if ep1["type"] == "chiplet" and ep2["type"] == "chiplet":
            endpoints = {
                (ep1["outer_id"], ep1["inner_id"]),
                (ep2["outer_id"], ep2["inner_id"]),
            }
            if endpoints == expected:
                matching.append(link)
    if len(matching) != 1:
        fail(f"{label} topology must contain its selected link exactly once")


def read_json_list(path, description):
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(encoding="utf-8") as input_file:
            value = json.load(input_file)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot parse {description} {path}: {error}")
    if not isinstance(value, list):
        fail(f"Malformed {description} {path}: expected a JSON array")
    return value


def validate_physical_designs(fixed_selections, aware_selections):
    baseline = read_json(BASE_DESIGN_FILE, "8-PHY baseline design")

    for budget in BUDGETS:
        topology_refs = set()
        routing_refs = set()
        topology_contents = []
        for workload in WORKLOADS:
            path = (
                REPO_ROOT
                / f"inputs/designs/design_project_fixed_k1_{workload}_B{budget}mm.json"
            )
            design = validate_architecture(path, baseline, f"fixed {workload} B{budget}")
            validate_selected_link(
                design, fixed_selections[budget], f"fixed {workload} B{budget}"
            )
            topology_refs.add(design.get("topology"))
            routing_refs.add(design.get("routing_table"))
            topology_contents.append(
                read_json_list(REPO_ROOT / design["topology"], "fixed topology")
            )
        if len(topology_refs) != 1 or len(routing_refs) != 1:
            fail(f"Fixed topology or routing changes across workloads for B{budget}")
        if any(topology != topology_contents[0] for topology in topology_contents[1:]):
            fail(f"Fixed topology content changes across workloads for B{budget}")

    for workload in WORKLOADS:
        seen_signatures = {}
        for budget in BUDGETS:
            selection = aware_selections[(workload, budget)]
            selected_signature = signature(selection)
            source_budget = seen_signatures.setdefault(selected_signature, budget)
            design_path = (
                REPO_ROOT
                / f"inputs/designs/design_project_k1_{workload}_B{source_budget}mm.json"
            )
            design = validate_architecture(
                design_path, baseline, f"workload-aware {workload} B{budget}"
            )
            validate_selected_link(
                design, selection, f"workload-aware {workload} B{budget}"
            )


def parse_summary_metrics(row, row_number):
    metrics = {}
    for field in METRIC_FIELDS:
        metrics[field] = number(
            row[field],
            f"{field} on workload-aware summary row {row_number}",
            optional=field in {"knee_load", "knee_latency"},
        )
    return metrics


def load_aware_summary(aware_selections):
    required = {
        "workload",
        "budget_mm",
        "u",
        "v",
        "phy_u",
        "phy_v",
        "wire_length_mm",
        "source_result_file",
        "reused_result",
        *METRIC_FIELDS,
    }
    raw_rows = load_csv(WORKLOAD_SUMMARY_FILE, required, "workload-aware summary")
    expected_keys = {
        (workload, budget)
        for workload in WORKLOADS
        for budget in (0, *BUDGETS)
    }
    parsed = {}
    for row_number, row in enumerate(raw_rows, start=2):
        workload = row["workload"].strip()
        budget = integer(row["budget_mm"], f"summary budget on row {row_number}")
        key = (workload, budget)
        if key in parsed:
            fail(f"Duplicate workload-aware summary row: {key}")
        metrics = parse_summary_metrics(row, row_number)
        source_path = REPO_ROOT / row["source_result_file"]
        raw_metrics = parse_policy_result(source_path)
        if not metrics_match(metrics, raw_metrics):
            fail(
                f"Workload-aware summary metrics do not match source result "
                f"{source_path} for {workload} B{budget}"
            )
        parsed[key] = {
            "metrics": metrics,
            "source_result_file": relative(source_path),
            "reused_result": boolean(
                row["reused_result"], f"reused_result on summary row {row_number}"
            ),
        }

        if budget != 0:
            selection = aware_selections.get(key)
            if selection is None:
                fail(f"Workload-aware summary has no matching selection: {key}")
            for field in ("u", "v", "phy_u", "phy_v"):
                if integer(row[field], f"{field} on summary row {row_number}") != selection[field]:
                    fail(f"Workload-aware summary/selection mismatch for {key}: {field}")
            summary_length = number(
                row["wire_length_mm"], f"wire_length_mm on summary row {row_number}"
            )
            if not math.isclose(
                summary_length,
                selection["wire_length_mm"],
                rel_tol=EPS,
                abs_tol=EPS,
            ):
                fail(f"Workload-aware summary/selection length mismatch for {key}")

    if set(parsed) != expected_keys:
        missing = sorted(expected_keys - set(parsed))
        extra = sorted(set(parsed) - expected_keys)
        fail(f"Workload-aware summary matrix mismatch; missing={missing}, extra={extra}")

    for workload in WORKLOADS:
        source_by_signature = {}
        for budget in BUDGETS:
            key = (workload, budget)
            selected_signature = signature(aware_selections[key])
            expected_source_budget = source_by_signature.setdefault(selected_signature, budget)
            expected_reuse = expected_source_budget != budget
            expected_source = f"results/k1_{workload}_B{expected_source_budget}mm.json"
            if parsed[key]["reused_result"] != expected_reuse:
                fail(f"Incorrect workload-aware reuse flag for {workload} B{budget}")
            if parsed[key]["source_result_file"] != expected_source:
                fail(
                    f"Incorrect workload-aware source result for {workload} B{budget}: "
                    f"{parsed[key]['source_result_file']}, expected {expected_source}"
                )
    return parsed


def resolve_fixed_metrics(fixed_selections):
    resolved = {}
    source_by_signature = {}
    metric_cache = {}
    for budget in BUDGETS:
        selected_signature = signature(fixed_selections[budget])
        source_budget = source_by_signature.setdefault(selected_signature, budget)
        resolved[budget] = {
            "source_budget": source_budget,
            "reused_result": source_budget != budget,
        }

    for workload in WORKLOADS:
        for budget in BUDGETS:
            source_budget = resolved[budget]["source_budget"]
            path = REPO_ROOT / f"results/fixed_k1_{workload}_B{source_budget}mm.json"
            cache_key = (workload, source_budget)
            if cache_key not in metric_cache:
                metric_cache[cache_key] = parse_policy_result(path)
            resolved[(workload, budget)] = {
                "metrics": metric_cache[cache_key],
                "source_result_file": relative(path),
                "reused_result": source_budget != budget,
            }
    return resolved


def base_row(workload, budget, policy, k, link, selection, metrics, source, reused):
    return {
        "workload": workload,
        "budget_mm": budget,
        "policy": policy,
        "k": k,
        "link": link,
        "u": "" if selection is None else selection["u"],
        "v": "" if selection is None else selection["v"],
        "phy_u": "" if selection is None else selection["phy_u"],
        "phy_v": "" if selection is None else selection["phy_v"],
        "wire_length_mm": 0.0 if selection is None else selection["wire_length_mm"],
        **metrics,
        "source_result_file": source,
        "reused_result": reused,
    }


def build_rows(fixed_selections, aware_selections, aware_summary, fixed_metrics):
    baselines = {}
    for workload in WORKLOADS:
        baseline_path = REPO_ROOT / f"results/k1_{workload}_baseline.json"
        baselines[workload] = {
            "metrics": parse_policy_result(baseline_path),
            "source_result_file": relative(baseline_path),
        }
        if not metrics_match(
            baselines[workload]["metrics"], aware_summary[(workload, 0)]["metrics"]
        ):
            fail(f"Mesh baseline and workload-aware summary baseline differ for {workload}")

    rows = []
    for workload in WORKLOADS:
        mesh = baselines[workload]["metrics"]
        for budget in BUDGETS:
            fixed_selection = fixed_selections[budget]
            aware_selection = aware_selections[(workload, budget)]
            for label, selection in (("fixed", fixed_selection), ("workload-aware", aware_selection)):
                if selection["wire_length_mm"] > budget + EPS:
                    fail(
                        f"{label} {workload} B{budget} wire length "
                        f"{selection['wire_length_mm']} exceeds budget"
                    )

            fixed = fixed_metrics[(workload, budget)]["metrics"]
            aware = aware_summary[(workload, budget)]["metrics"]
            mesh_row = base_row(
                workload,
                budget,
                "mesh",
                0,
                "none",
                None,
                mesh,
                baselines[workload]["source_result_file"],
                False,
            )
            fixed_row = base_row(
                workload,
                budget,
                "fixed",
                1,
                f"{fixed_selection['u']}<->{fixed_selection['v']}",
                fixed_selection,
                fixed,
                fixed_metrics[(workload, budget)]["source_result_file"],
                fixed_metrics[(workload, budget)]["reused_result"],
            )
            aware_row = base_row(
                workload,
                budget,
                "workload_aware",
                1,
                f"{aware_selection['u']}<->{aware_selection['v']}",
                aware_selection,
                aware,
                aware_summary[(workload, budget)]["source_result_file"],
                aware_summary[(workload, budget)]["reused_result"],
            )

            for row in (mesh_row, fixed_row, aware_row):
                if row["policy"] == "mesh":
                    row["latency_gain_vs_mesh_percent"] = 0.0
                    row["hop_gain_vs_mesh_percent"] = 0.0
                    row["analytical_latency_gain_vs_mesh_percent"] = 0.0
                else:
                    row["latency_gain_vs_mesh_percent"] = improvement(
                        mesh["booksim_low_latency"],
                        row["booksim_low_latency"],
                        "BookSim latency",
                        workload,
                    )
                    row["hop_gain_vs_mesh_percent"] = improvement(
                        mesh["booksim_low_hops"],
                        row["booksim_low_hops"],
                        "BookSim hops",
                        workload,
                    )
                    row["analytical_latency_gain_vs_mesh_percent"] = improvement(
                        mesh["analytical_latency"],
                        row["analytical_latency"],
                        "analytical latency",
                        workload,
                    )
                row["aware_latency_gain_vs_fixed_percent"] = None
                row["aware_hop_gain_vs_fixed_percent"] = None

            aware_row["aware_latency_gain_vs_fixed_percent"] = improvement(
                fixed["booksim_low_latency"],
                aware["booksim_low_latency"],
                "workload-aware latency vs fixed",
                workload,
            )
            aware_row["aware_hop_gain_vs_fixed_percent"] = improvement(
                fixed["booksim_low_hops"],
                aware["booksim_low_hops"],
                "workload-aware hops vs fixed",
                workload,
            )
            rows.extend((mesh_row, fixed_row, aware_row))

    return rows


def validate_rows(rows, fixed_selections):
    if len(WORKLOADS) != 4 or len(BUDGETS) != 4 or len(POLICIES) != 3:
        fail("Internal comparison dimensions must be 4 workloads x 4 budgets x 3 policies")
    if len(rows) != 48:
        fail(f"Policy comparison must contain exactly 48 rows; found {len(rows)}")

    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["workload"], row["budget_mm"])].append(row)
        if row["policy"] in {"fixed", "workload_aware"} and row["k"] != 1:
            fail(f"K validation failed for {row['workload']} B{row['budget_mm']}")
        if row["policy"] == "mesh" and (
            row["k"] != 0 or row["wire_length_mm"] != 0
        ):
            fail(f"Mesh physical fields are invalid for {row['workload']} B{row['budget_mm']}")

    expected_groups = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(grouped) != expected_groups:
        fail("Comparison workload/budget groups are incomplete")
    for key, group in grouped.items():
        if len(group) != 3 or {row["policy"] for row in group} != set(POLICIES):
            fail(f"Comparison group {key} does not contain exactly one row per policy")

    for budget in BUDGETS:
        signatures = {
            (
                row["u"],
                row["v"],
                row["phy_u"],
                row["phy_v"],
                row["wire_length_mm"],
            )
            for row in rows
            if row["policy"] == "fixed" and row["budget_mm"] == budget
        }
        expected = fixed_selections[budget]
        expected_signature = {
            (
                expected["u"],
                expected["v"],
                expected["phy_u"],
                expected["phy_v"],
                expected["wire_length_mm"],
            )
        }
        if signatures != expected_signature:
            fail(f"Fixed topology changes across workloads for B{budget}")


def write_csv(rows):
    try:
        with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows({field: row[field] for field in CSV_FIELDS} for row in rows)
    except OSError as error:
        fail(f"Cannot write policy comparison CSV {OUTPUT_FILE}: {error}")


def shown(value, decimals=3):
    return "-" if value is None else f"{value:.{decimals}f}"


def print_workload_tables(rows):
    for workload in WORKLOADS:
        print()
        print(f"K=1 POLICY COMPARISON — {workload.upper()}")
        print("-" * 88)
        print(
            f"{'BUDGET':<8}{'POLICY':<17}{'LINK':<9}{'WIRE':>8}"
            f"{'BS_LAT':>10}{'GAIN_vs_MESH':>15}{'HOPS':>9}{'KNEE':>9}"
        )
        for row in rows:
            if row["workload"] != workload:
                continue
            print(
                f"{str(row['budget_mm']) + 'mm':<8}{row['policy']:<17}{row['link']:<9}"
                f"{row['wire_length_mm']:>8.3f}{row['booksim_low_latency']:>10.2f}"
                f"{row['latency_gain_vs_mesh_percent']:>15.2f}"
                f"{row['booksim_low_hops']:>9.3f}{shown(row['knee_load']):>9}"
            )


def print_direct_table(rows):
    indexed = {
        (row["workload"], row["budget_mm"], row["policy"]): row for row in rows
    }
    print()
    print("WORKLOAD-AWARE ADVANTAGE OVER FIXED K=1")
    print("-" * 109)
    print(
        f"{'WORKLOAD':<16}{'BUDGET':<8}{'FIXED_LAT':>11}{'AWARE_LAT':>11}"
        f"{'AWARE_GAIN_vs_FIXED':>21}{'FIXED_LINK':>13}{'AWARE_LINK':>13}"
    )
    for workload in WORKLOADS:
        for budget in BUDGETS:
            fixed = indexed[(workload, budget, "fixed")]
            aware = indexed[(workload, budget, "workload_aware")]
            print(
                f"{workload:<16}{str(budget) + 'mm':<8}"
                f"{fixed['booksim_low_latency']:>11.2f}"
                f"{aware['booksim_low_latency']:>11.2f}"
                f"{aware['aware_latency_gain_vs_fixed_percent']:>21.2f}"
                f"{fixed['link']:>13}{aware['link']:>13}"
            )


def print_aggregate_table(rows):
    indexed = defaultdict(list)
    for row in rows:
        indexed[(row["budget_mm"], row["policy"])].append(row["booksim_low_latency"])

    print()
    print("AVERAGE LOW-LOAD LATENCY ACROSS FOUR SYNTHETIC WORKLOADS")
    print("-" * 101)
    print(
        f"{'BUDGET':<8}{'MESH_LAT':>11}{'FIXED_LAT':>12}{'AWARE_LAT':>12}"
        f"{'FIXED_GAIN%':>14}{'AWARE_GAIN%':>14}{'AWARE_vs_FIXED%':>18}"
    )
    for budget in BUDGETS:
        means = {
            policy: sum(indexed[(budget, policy)]) / len(indexed[(budget, policy)])
            for policy in POLICIES
        }
        print(
            f"{str(budget) + 'mm':<8}{means['mesh']:>11.2f}"
            f"{means['fixed']:>12.2f}{means['workload_aware']:>12.2f}"
            f"{improvement(means['mesh'], means['fixed'], 'aggregate fixed latency', 'all workloads'):>14.2f}"
            f"{improvement(means['mesh'], means['workload_aware'], 'aggregate aware latency', 'all workloads'):>14.2f}"
            f"{improvement(means['fixed'], means['workload_aware'], 'aggregate aware vs fixed latency', 'all workloads'):>18.2f}"
        )


def main():
    fixed_selections = load_selection_rows(FIXED_SELECTION_FILE, workload_aware=False)
    aware_selections = load_selection_rows(WORKLOAD_SELECTION_FILE, workload_aware=True)
    validate_physical_designs(fixed_selections, aware_selections)
    aware_summary = load_aware_summary(aware_selections)
    fixed_metrics = resolve_fixed_metrics(fixed_selections)
    rows = build_rows(
        fixed_selections, aware_selections, aware_summary, fixed_metrics
    )
    validate_rows(rows, fixed_selections)
    write_csv(rows)
    print_workload_tables(rows)
    print_direct_table(rows)
    print_aggregate_table(rows)

    print()
    print("K=1 POLICY COMPARISON COMPLETE")
    print(f"Workloads: {len(WORKLOADS)}")
    print(f"Budgets: {len(BUDGETS)}")
    print(f"Policies: {len(POLICIES)}")
    print(f"Rows: {len(rows)}")
    print(f"Output CSV: {relative(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()
