"""Compare Mesh, fixed K=2, and workload-aware K=2 policies."""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
POLICIES = ("mesh", "fixed", "workload_aware")
FIXED_SUMMARY = REPO_ROOT / "results/k2_fixed_summary.csv"
AWARE_SUMMARY = REPO_ROOT / "results/k2_workload_aware_summary.csv"
BASE_DESIGN = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
OUTPUT_FILE = REPO_ROOT / "results/k2_policy_comparison.csv"
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
    "requested_k",
    "actual_k",
    "link_1",
    "link_2",
    "total_wire_length_mm",
    *METRIC_FIELDS,
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


def integer(value, description):
    try:
        raw = float(value)
        converted = int(raw)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if raw != converted:
        fail(f"Invalid {description}: expected an integer, found {value!r}")
    return converted


def number(value, description):
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if not math.isfinite(converted):
        fail(f"Invalid {description}: expected a finite number")
    return converted


def read_json(path, description, expected_type=dict):
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(encoding="utf-8") as input_file:
            value = json.load(input_file)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Cannot parse {description} {path}: {error}")
    if not isinstance(value, expected_type):
        fail(f"Malformed {description} {path}: expected {expected_type.__name__}")
    return value


def read_csv(path, required, description):
    try:
        with path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            missing = sorted(set(required) - set(reader.fieldnames or []))
            if missing:
                fail(f"{description} is missing columns: {', '.join(missing)}")
            return list(reader)
    except OSError as error:
        fail(f"Cannot read {description} {path}: {error}")


def result_metric(point, section, path):
    nested = point.get(section) if isinstance(point, dict) else None
    value = nested.get("avg") if isinstance(nested, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        fail(f"Malformed result {path}: {section}/avg must be finite")
    return value


def parse_result(path):
    data = read_json(path, "BookSim result")
    for section in ("latency", "throughput", "booksim_simulation"):
        if not isinstance(data.get(section), dict):
            fail(f"Malformed result {path}: missing or invalid {section!r}")
    analytical_latency = number(data["latency"].get("avg"), f"latency/avg in {path}")
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
            fail(f"Malformed result {path}: non-finite offered load {raw_load!r}")
        latency = result_metric(point, "packet_latency", path)
        hops = result_metric(point, "hops", path)
        accepted = result_metric(point, "accepted_packet_rate", path)
        # Failed BookSim loads may be retained as empty marker objects.
        if latency is None or hops is None or accepted is None:
            continue
        points.append((load, latency, hops, accepted))
    if not points:
        fail(f"Malformed result {path}: no complete numeric offered-load points")
    points.sort(key=lambda point: point[0])
    low = points[0]
    knee_index = next(
        (index for index, point in enumerate(points) if point[1] >= 2.0 * low[1]),
        None,
    )
    if knee_index == 0:
        fail(f"Malformed result {path}: low-load point cannot be its own knee")
    stable = points[-1] if knee_index is None else points[knee_index - 1]
    knee = None if knee_index is None else points[knee_index]
    return {
        "analytical_latency": analytical_latency,
        "analytical_throughput": analytical_throughput,
        "booksim_low_load": low[0],
        "booksim_low_latency": low[1],
        "booksim_low_hops": low[2],
        "booksim_low_accepted_rate": low[3],
        "last_stable_load": stable[0],
        "last_stable_latency": stable[1],
        "last_stable_accepted_rate": stable[3],
        "knee_load": None if knee is None else knee[0],
        "knee_latency": None if knee is None else knee[1],
    }


def result_is_complete(path):
    try:
        parse_result(path)
    except RuntimeError:
        return False
    return True


def load_summaries():
    required = {
        "budget_mm", "requested_k", "actual_k", "link_1", "link_2",
        "total_wire_length_mm", "analytical_throughput",
    }
    fixed_rows = read_csv(FIXED_SUMMARY, required, "fixed K=2 summary")
    aware_rows = read_csv(AWARE_SUMMARY, required | {"workload"}, "workload-aware K=2 summary")
    fixed = {}
    aware = {}
    for row_number, row in enumerate(fixed_rows, start=2):
        budget = integer(row["budget_mm"], f"fixed budget on row {row_number}")
        if budget in fixed:
            fail(f"Duplicate fixed summary budget B{budget}")
        fixed[budget] = parse_summary_row(row, f"fixed row {row_number}")
    for row_number, row in enumerate(aware_rows, start=2):
        workload = row["workload"].strip()
        budget = integer(row["budget_mm"], f"aware budget on row {row_number}")
        key = (workload, budget)
        if key in aware:
            fail(f"Duplicate workload-aware summary configuration {key}")
        aware[key] = parse_summary_row(row, f"aware row {row_number}")
    expected_aware = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(fixed) != set(BUDGETS):
        fail(f"Fixed K=2 summary must contain exactly budgets {list(BUDGETS)}")
    if set(aware) != expected_aware:
        fail(
            "Workload-aware K=2 summary matrix mismatch; "
            f"missing={sorted(expected_aware - set(aware))}, extra={sorted(set(aware) - expected_aware)}"
        )
    return fixed, aware


def parse_summary_row(row, description):
    requested_k = integer(row["requested_k"], f"requested_k in {description}")
    actual_k = integer(row["actual_k"], f"actual_k in {description}")
    link_1, link_2 = row["link_1"].strip(), row["link_2"].strip()
    if requested_k != 2 or not 0 <= actual_k <= 2:
        fail(f"Invalid requested_k/actual_k in {description}")
    links = (link_1, link_2)
    for index, link in enumerate(links):
        expected_none = index >= actual_k
        if (link == "none") != expected_none:
            fail(f"Link fields disagree with actual_k in {description}")
    return {
        "requested_k": requested_k,
        "actual_k": actual_k,
        "link_1": link_1,
        "link_2": link_2,
        "total_wire_length_mm": number(row["total_wire_length_mm"], f"wire length in {description}"),
        "summary_analytical_throughput": number(
            row["analytical_throughput"], f"analytical throughput in {description}"
        ),
    }


def canonical_pair(link_name):
    try:
        left, right = (int(value) for value in link_name.split("<->"))
    except (TypeError, ValueError) as error:
        fail(f"Invalid selected link name {link_name!r}: {error}")
    return tuple(sorted((left, right)))


def physical_signature(topology, baseline_pairs):
    added = []
    for link in topology:
        try:
            ep1, ep2 = link["ep1"], link["ep2"]
            if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
                fail("K=2 topology contains a non-chiplet link")
            endpoint_a = (integer(ep1["outer_id"], "topology chiplet"), integer(ep1["inner_id"], "topology PHY"))
            endpoint_b = (integer(ep2["outer_id"], "topology chiplet"), integer(ep2["inner_id"], "topology PHY"))
        except (KeyError, TypeError) as error:
            fail(f"Malformed topology link: {error}")
        chiplet_pair = tuple(sorted((endpoint_a[0], endpoint_b[0])))
        if chiplet_pair not in baseline_pairs:
            added.append(tuple(sorted((endpoint_a, endpoint_b))))
    return tuple(sorted(added))


def validate_designs(fixed, aware):
    baseline_design = read_json(BASE_DESIGN, "8-PHY baseline design")
    baseline_topology = read_json(REPO_ROOT / baseline_design["topology"], "baseline topology", list)
    if len(baseline_topology) != 24:
        fail(f"8-PHY baseline must contain 24 links; found {len(baseline_topology)}")
    baseline_pairs = {
        tuple(sorted((link["ep1"]["outer_id"], link["ep2"]["outer_id"])))
        for link in baseline_topology
    }
    signatures = {"fixed": {}, "workload_aware": {}}
    for policy in ("fixed", "workload_aware"):
        for workload in WORKLOADS:
            for budget in BUDGETS:
                summary = fixed[budget] if policy == "fixed" else aware[(workload, budget)]
                design_name = (
                    f"design_project_fixed_k2_{workload}_B{budget}mm.json"
                    if policy == "fixed"
                    else f"design_project_aware_k2_{workload}_B{budget}mm.json"
                )
                design = read_json(REPO_ROOT / "inputs/designs" / design_name, f"{policy} design")
                for field in ARCHITECTURE_KEYS:
                    if design.get(field) != baseline_design.get(field):
                        fail(f"8-PHY architecture mismatch in {design_name}: {field}")
                expected_unit = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
                expected_chiplet = f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
                if design.get("traffic_by_unit") != expected_unit or design.get("traffic_by_chiplet") != expected_chiplet:
                    fail(f"Cross-workload traffic reference in {design_name}")
                topology = read_json(REPO_ROOT / design["topology"], f"topology for {design_name}", list)
                if len(topology) != 24 + summary["actual_k"]:
                    fail(f"Topology link count disagrees with actual_k in {design_name}")
                signature = physical_signature(topology, baseline_pairs)
                expected_pairs = {
                    canonical_pair(summary[field])
                    for field in ("link_1", "link_2")
                    if summary[field] != "none"
                }
                if len(signature) != summary["actual_k"] or {tuple(sorted((a[0], b[0]))) for a, b in signature} != expected_pairs:
                    fail(f"Topology selected links disagree with summary in {design_name}")
                signatures[policy][(workload, budget)] = signature
    for budget in BUDGETS:
        fixed_signatures = {signatures["fixed"][(workload, budget)] for workload in WORKLOADS}
        if len(fixed_signatures) != 1:
            fail(f"Fixed physical topology changes across workloads for B{budget}")
    return signatures


def resolve_results(signatures):
    resolved = {}
    cache = {}
    for policy in ("fixed", "workload_aware"):
        prefix = "fixed_k2" if policy == "fixed" else "aware_k2"
        for workload in WORKLOADS:
            lower_by_signature = {}
            for budget in BUDGETS:
                signature = signatures[policy][(workload, budget)]
                direct = REPO_ROOT / f"results/{prefix}_{workload}_B{budget}mm.json"
                if result_is_complete(direct):
                    source = direct
                    reused = False
                    lower_by_signature.setdefault(signature, direct)
                else:
                    source = lower_by_signature.get(signature)
                    if source is None or not result_is_complete(source):
                        fail(
                            f"Missing or incomplete {policy} result for {workload} B{budget}: "
                            f"{direct}; no complete identical lower-budget result is available"
                        )
                    reused = True
                if source not in cache:
                    cache[source] = parse_result(source)
                resolved[(policy, workload, budget)] = {
                    "metrics": cache[source],
                    "source_result_file": relative(source),
                    "reused_result": reused,
                }
    return resolved


def improvement(baseline, candidate, description):
    if baseline == 0:
        fail(f"Cannot normalize {description}: baseline is zero")
    return 100.0 * (baseline - candidate) / baseline


def build_rows(fixed, aware, resolved):
    mesh = {}
    for workload in WORKLOADS:
        path = REPO_ROOT / f"results/k1_{workload}_baseline.json"
        mesh[workload] = {"metrics": parse_result(path), "source": relative(path)}
    rows = []
    for workload in WORKLOADS:
        mesh_metrics = mesh[workload]["metrics"]
        for budget in BUDGETS:
            summaries = {"fixed": fixed[budget], "workload_aware": aware[(workload, budget)]}
            for policy in ("fixed", "workload_aware"):
                if summaries[policy]["total_wire_length_mm"] > budget + EPS:
                    fail(f"{policy} wire length exceeds B{budget} for {workload}")
            policy_rows = {}
            for policy in POLICIES:
                if policy == "mesh":
                    summary = {"requested_k": 0, "actual_k": 0, "link_1": "none", "link_2": "none", "total_wire_length_mm": 0.0}
                    metrics = mesh_metrics
                    source = mesh[workload]["source"]
                    reused = False
                else:
                    summary = summaries[policy]
                    result = resolved[(policy, workload, budget)]
                    metrics, source, reused = result["metrics"], result["source_result_file"], result["reused_result"]
                    if policy == "workload_aware" and not math.isclose(
                        metrics["analytical_throughput"], summary["summary_analytical_throughput"], rel_tol=EPS, abs_tol=EPS
                    ):
                        fail(f"Aware summary/result analytical throughput mismatch for {workload} B{budget}")
                row = {
                    "workload": workload, "budget_mm": budget, "policy": policy,
                    "requested_k": summary["requested_k"], "actual_k": summary["actual_k"],
                    "link_1": summary["link_1"], "link_2": summary["link_2"],
                    "total_wire_length_mm": summary["total_wire_length_mm"],
                    **metrics, "source_result_file": source, "reused_result": reused,
                }
                if policy == "mesh":
                    row["latency_gain_vs_mesh_percent"] = 0.0
                    row["hop_gain_vs_mesh_percent"] = 0.0
                    row["analytical_latency_gain_vs_mesh_percent"] = 0.0
                else:
                    row["latency_gain_vs_mesh_percent"] = improvement(mesh_metrics["booksim_low_latency"], metrics["booksim_low_latency"], "BookSim latency vs same-workload mesh")
                    row["hop_gain_vs_mesh_percent"] = improvement(mesh_metrics["booksim_low_hops"], metrics["booksim_low_hops"], "hops vs same-workload mesh")
                    row["analytical_latency_gain_vs_mesh_percent"] = improvement(mesh_metrics["analytical_latency"], metrics["analytical_latency"], "analytical latency vs same-workload mesh")
                row["aware_latency_gain_vs_fixed_percent"] = None
                row["aware_hop_gain_vs_fixed_percent"] = None
                policy_rows[policy] = row
            fixed_row, aware_row = policy_rows["fixed"], policy_rows["workload_aware"]
            aware_row["aware_latency_gain_vs_fixed_percent"] = improvement(fixed_row["booksim_low_latency"], aware_row["booksim_low_latency"], "aware latency vs same workload/budget fixed")
            aware_row["aware_hop_gain_vs_fixed_percent"] = improvement(fixed_row["booksim_low_hops"], aware_row["booksim_low_hops"], "aware hops vs same workload/budget fixed")
            rows.extend(policy_rows[policy] for policy in POLICIES)
    return rows


def validate_rows(rows):
    if len(WORKLOADS) != 4 or len(BUDGETS) != 4 or len(POLICIES) != 3 or len(rows) != 48:
        fail("Comparison dimensions must be exactly 4 workloads x 4 budgets x 3 policies = 48 rows")
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["workload"], row["budget_mm"])].append(row)
    expected = {(workload, budget) for workload in WORKLOADS for budget in BUDGETS}
    if set(grouped) != expected:
        fail("Comparison workload/budget matrix is incomplete")
    for key, group in grouped.items():
        indexed = {row["policy"]: row for row in group}
        if len(group) != 3 or set(indexed) != set(POLICIES):
            fail(f"Comparison group {key} does not contain exactly one row per policy")
        mesh = indexed["mesh"]
        if mesh["requested_k"] != 0 or mesh["actual_k"] != 0 or mesh["total_wire_length_mm"] != 0:
            fail(f"Invalid mesh physical fields for {key}")
        for field in (
            "latency_gain_vs_mesh_percent",
            "hop_gain_vs_mesh_percent",
            "analytical_latency_gain_vs_mesh_percent",
        ):
            if mesh[field] != 0.0:
                fail(f"Mesh improvement field {field} is not zero for {key}")
        for policy in ("fixed", "workload_aware"):
            row = indexed[policy]
            if row["requested_k"] != 2 or not 0 <= row["actual_k"] <= 2:
                fail(f"Invalid K fields for {policy} {key}")
            checks = (
                (
                    "latency_gain_vs_mesh_percent",
                    improvement(mesh["booksim_low_latency"], row["booksim_low_latency"], "validation latency"),
                ),
                (
                    "hop_gain_vs_mesh_percent",
                    improvement(mesh["booksim_low_hops"], row["booksim_low_hops"], "validation hops"),
                ),
                (
                    "analytical_latency_gain_vs_mesh_percent",
                    improvement(mesh["analytical_latency"], row["analytical_latency"], "validation analytical latency"),
                ),
            )
            for field, expected_value in checks:
                if not math.isclose(row[field], expected_value, rel_tol=EPS, abs_tol=EPS):
                    fail(f"Cross-workload normalization detected in {field} for {policy} {key}")
        expected_direct = improvement(indexed["fixed"]["booksim_low_latency"], indexed["workload_aware"]["booksim_low_latency"], "validation direct latency")
        if not math.isclose(indexed["workload_aware"]["aware_latency_gain_vs_fixed_percent"], expected_direct, rel_tol=EPS, abs_tol=EPS):
            fail(f"Incorrect direct fixed-vs-aware comparison for {key}")
        expected_direct_hops = improvement(indexed["fixed"]["booksim_low_hops"], indexed["workload_aware"]["booksim_low_hops"], "validation direct hops")
        if not math.isclose(indexed["workload_aware"]["aware_hop_gain_vs_fixed_percent"], expected_direct_hops, rel_tol=EPS, abs_tol=EPS):
            fail(f"Incorrect direct fixed-vs-aware hop comparison for {key}")
        for policy in ("mesh", "fixed"):
            if indexed[policy]["aware_latency_gain_vs_fixed_percent"] is not None or indexed[policy]["aware_hop_gain_vs_fixed_percent"] is not None:
                fail(f"Direct aware-vs-fixed fields must be blank on {policy} row {key}")


def write_csv(rows):
    try:
        with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows({field: row[field] for field in CSV_FIELDS} for row in rows)
    except OSError as error:
        fail(f"Cannot write comparison CSV {OUTPUT_FILE}: {error}")


def shown(value, decimals=3):
    return "-" if value is None else f"{value:.{decimals}f}"


def links(row):
    return row["link_1"] if row["link_2"] == "none" else f"{row['link_1']},{row['link_2']}"


def print_tables(rows):
    indexed = {(row["workload"], row["budget_mm"], row["policy"]): row for row in rows}
    for workload in WORKLOADS:
        print(f"\nK=2 POLICY COMPARISON - {workload.upper()}")
        print("BUDGET | POLICY | ACTUAL_K | LINKS | WIRE | BS_LAT | GAIN_vs_MESH | HOPS | KNEE")
        for budget in BUDGETS:
            for policy in POLICIES:
                row = indexed[(workload, budget, policy)]
                print(f"{budget} | {policy} | {row['actual_k']} | {links(row)} | {row['total_wire_length_mm']:.3f} | {row['booksim_low_latency']:.3f} | {row['latency_gain_vs_mesh_percent']:.3f} | {row['booksim_low_hops']:.3f} | {shown(row['knee_load'])}")
    print("\nWORKLOAD | BUDGET | FIXED_K | AWARE_K | FIXED_LAT | AWARE_LAT | AWARE_GAIN_vs_FIXED | FIXED_LINKS | AWARE_LINKS")
    for workload in WORKLOADS:
        for budget in BUDGETS:
            fixed = indexed[(workload, budget, "fixed")]
            aware = indexed[(workload, budget, "workload_aware")]
            print(f"{workload} | {budget} | {fixed['actual_k']} | {aware['actual_k']} | {fixed['booksim_low_latency']:.3f} | {aware['booksim_low_latency']:.3f} | {aware['aware_latency_gain_vs_fixed_percent']:.3f} | {links(fixed)} | {links(aware)}")
    print("\nBUDGET | MESH_LAT | FIXED_LAT | AWARE_LAT | FIXED_GAIN_vs_MESH | AWARE_GAIN_vs_MESH | AWARE_GAIN_vs_FIXED")
    for budget in BUDGETS:
        means = {
            policy: sum(indexed[(workload, budget, policy)]["booksim_low_latency"] for workload in WORKLOADS) / len(WORKLOADS)
            for policy in POLICIES
        }
        print(f"{budget} | {means['mesh']:.3f} | {means['fixed']:.3f} | {means['workload_aware']:.3f} | {improvement(means['mesh'], means['fixed'], 'aggregate fixed'):.3f} | {improvement(means['mesh'], means['workload_aware'], 'aggregate aware'):.3f} | {improvement(means['fixed'], means['workload_aware'], 'aggregate direct'):.3f}")


def main():
    fixed, aware = load_summaries()
    signatures = validate_designs(fixed, aware)
    resolved = resolve_results(signatures)
    rows = build_rows(fixed, aware, resolved)
    validate_rows(rows)
    write_csv(rows)
    print_tables(rows)
    print("\nK=2 POLICY COMPARISON COMPLETE")
    print(f"Workloads: {len(WORKLOADS)}")
    print(f"Budgets: {len(BUDGETS)}")
    print(f"Policies: {len(POLICIES)}")
    print(f"Rows: {len(rows)}")
    print(f"Output CSV: {relative(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()
