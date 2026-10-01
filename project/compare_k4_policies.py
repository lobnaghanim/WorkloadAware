"""Compare mesh, fixed K=4, and workload-aware K=4 policies."""

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

# Reuse the proven BookSim parsing and numeric/JSON validation from K=2.
import compare_k2_policies as proven


WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
POLICIES = ("mesh", "fixed", "workload_aware")
REQUESTED_K = 4
FIXED_SUMMARY = REPO_ROOT / "results/k4_fixed_summary.csv"
AWARE_SUMMARY = REPO_ROOT / "results/k4_workload_aware_summary.csv"
BASE_DESIGN = REPO_ROOT / "inputs/designs/design_project_physical_mesh_8phy.json"
OUTPUT_FILE = REPO_ROOT / "results/k4_policy_comparison.csv"
ARCHITECTURE_KEYS = proven.ARCHITECTURE_KEYS
EPS = proven.EPS
METRIC_FIELDS = proven.METRIC_FIELDS
CSV_FIELDS = (
    "workload",
    "budget_mm",
    "policy",
    "requested_k",
    "actual_k",
    "link_1",
    "link_2",
    "link_3",
    "link_4",
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


def parse_link(link_name, phy_pair, description):
    try:
        u, v = (proven.integer(value, description) for value in link_name.split("<->"))
        phy_u, phy_v = (
            proven.integer(value, description) for value in phy_pair.split("<->")
        )
    except (AttributeError, TypeError, ValueError) as error:
        fail(f"Invalid link/PHY pair in {description}: {error}")
    if u == v:
        fail(f"Self shortcut in {description}: {link_name}")
    endpoint_a = (u, phy_u)
    endpoint_b = (v, phy_v)
    return tuple(sorted((endpoint_a, endpoint_b)))


def parse_summary_row(row, description):
    requested_k = proven.integer(row["requested_k"], f"requested_k in {description}")
    actual_k = proven.integer(row["actual_k"], f"actual_k in {description}")
    if requested_k != REQUESTED_K or not 0 <= actual_k <= REQUESTED_K:
        fail(f"Invalid requested_k/actual_k in {description}")

    parsed = {
        "requested_k": requested_k,
        "actual_k": actual_k,
        "total_wire_length_mm": proven.number(
            row["total_wire_length_mm"], f"wire length in {description}"
        ),
        "summary_analytical_throughput": proven.number(
            row["analytical_throughput"],
            f"analytical throughput in {description}",
        ),
        "signature": [],
    }
    for index in range(1, REQUESTED_K + 1):
        link = row[f"link_{index}"].strip()
        phy_pair = row[f"link_{index}_phy_pair"].strip()
        raw_length = row[f"link_{index}_length_mm"].strip()
        if index <= actual_k:
            if not link or link == "none" or not phy_pair or not raw_length:
                fail(f"Missing selected link {index} in {description}")
            length = proven.number(raw_length, f"link {index} length in {description}")
            parsed["signature"].append(
                parse_link(link, phy_pair, f"{description} link {index}")
            )
            parsed[f"link_{index}"] = link
            parsed[f"link_{index}_length_mm"] = length
        else:
            if link not in ("", "none") or phy_pair or raw_length:
                fail(f"Unused link {index} is not blank/none in {description}")
            parsed[f"link_{index}"] = "none"
            parsed[f"link_{index}_length_mm"] = None

    parsed["signature"] = tuple(sorted(parsed["signature"]))
    if len(parsed["signature"]) != actual_k:
        fail(f"Duplicate endpoint-identical shortcut in {description}")
    selected_total = sum(
        parsed[f"link_{index}_length_mm"]
        for index in range(1, actual_k + 1)
    )
    if not math.isclose(
        selected_total,
        parsed["total_wire_length_mm"],
        rel_tol=EPS,
        abs_tol=EPS,
    ):
        fail(f"Selected lengths disagree with total wire in {description}")
    return parsed


def load_summaries():
    link_columns = {
        *(f"link_{index}" for index in range(1, REQUESTED_K + 1)),
        *(f"link_{index}_phy_pair" for index in range(1, REQUESTED_K + 1)),
        *(f"link_{index}_length_mm" for index in range(1, REQUESTED_K + 1)),
    }
    required = {
        "budget_mm",
        "requested_k",
        "actual_k",
        "total_wire_length_mm",
        "analytical_throughput",
        *link_columns,
    }
    fixed_rows = proven.read_csv(FIXED_SUMMARY, required, "fixed K=4 summary")
    aware_rows = proven.read_csv(
        AWARE_SUMMARY, required | {"workload"}, "workload-aware K=4 summary"
    )
    fixed = {}
    aware = {}
    for row_number, row in enumerate(fixed_rows, start=2):
        budget = proven.integer(row["budget_mm"], f"fixed budget on row {row_number}")
        if budget in fixed:
            fail(f"Duplicate fixed summary budget B{budget}")
        fixed[budget] = parse_summary_row(row, f"fixed row {row_number}")
    for row_number, row in enumerate(aware_rows, start=2):
        workload = row["workload"].strip()
        budget = proven.integer(row["budget_mm"], f"aware budget on row {row_number}")
        key = (workload, budget)
        if key in aware:
            fail(f"Duplicate workload-aware summary configuration {key}")
        aware[key] = parse_summary_row(row, f"aware row {row_number}")

    expected_aware = {
        (workload, budget) for workload in WORKLOADS for budget in BUDGETS
    }
    if set(fixed) != set(BUDGETS):
        fail(f"Fixed K=4 summary must contain exactly budgets {list(BUDGETS)}")
    if set(aware) != expected_aware:
        fail(
            "Workload-aware K=4 summary matrix mismatch; "
            f"missing={sorted(expected_aware - set(aware))}, "
            f"extra={sorted(set(aware) - expected_aware)}"
        )
    return fixed, aware


def topology_signature(topology, baseline_pairs, description):
    added = []
    occupied_added = set()
    for link in topology:
        try:
            ep1, ep2 = link["ep1"], link["ep2"]
            if ep1["type"] != "chiplet" or ep2["type"] != "chiplet":
                fail(f"{description} contains a non-chiplet link")
            endpoint_a = (
                proven.integer(ep1["outer_id"], "topology chiplet"),
                proven.integer(ep1["inner_id"], "topology PHY"),
            )
            endpoint_b = (
                proven.integer(ep2["outer_id"], "topology chiplet"),
                proven.integer(ep2["inner_id"], "topology PHY"),
            )
        except (KeyError, TypeError) as error:
            fail(f"Malformed topology link in {description}: {error}")
        chiplet_pair = tuple(sorted((endpoint_a[0], endpoint_b[0])))
        if chiplet_pair not in baseline_pairs:
            signature_link = tuple(sorted((endpoint_a, endpoint_b)))
            if signature_link in added:
                fail(f"Duplicate shortcut in {description}: {signature_link}")
            if endpoint_a in occupied_added or endpoint_b in occupied_added:
                fail(f"Added PHY endpoint reuse in {description}")
            added.append(signature_link)
            occupied_added.update((endpoint_a, endpoint_b))
    return tuple(sorted(added))


def validate_designs(fixed, aware):
    baseline_design = proven.read_json(BASE_DESIGN, "8-PHY baseline design")
    baseline_topology = proven.read_json(
        REPO_ROOT / baseline_design["topology"], "baseline topology", list
    )
    if len(baseline_topology) != 24:
        fail(f"8-PHY baseline must contain 24 links; found {len(baseline_topology)}")
    baseline_pairs = {
        tuple(sorted((link["ep1"]["outer_id"], link["ep2"]["outer_id"])))
        for link in baseline_topology
    }
    if len(baseline_pairs) != 24:
        fail("8-PHY baseline must contain 24 unique mesh edges")

    signatures = {"fixed": {}, "workload_aware": {}}
    for policy in ("fixed", "workload_aware"):
        for workload in WORKLOADS:
            for budget in BUDGETS:
                summary = fixed[budget] if policy == "fixed" else aware[(workload, budget)]
                design_name = (
                    f"design_project_fixed_k4_{workload}_B{budget}mm.json"
                    if policy == "fixed"
                    else f"design_project_aware_k4_{workload}_B{budget}mm.json"
                )
                design = proven.read_json(
                    REPO_ROOT / "inputs/designs" / design_name, f"{policy} design"
                )
                for field in ARCHITECTURE_KEYS:
                    if design.get(field) != baseline_design.get(field):
                        fail(f"8-PHY architecture mismatch in {design_name}: {field}")
                expected_unit = f"inputs/traffic_by_unit/traffic_project_{workload}.json"
                expected_chiplet = (
                    f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
                )
                if (
                    design.get("traffic_by_unit") != expected_unit
                    or design.get("traffic_by_chiplet") != expected_chiplet
                ):
                    fail(f"Cross-workload traffic reference in {design_name}")
                topology = proven.read_json(
                    REPO_ROOT / design["topology"], f"topology for {design_name}", list
                )
                if len(topology) != 24 + summary["actual_k"]:
                    fail(f"Topology link count disagrees with actual_k in {design_name}")
                signature = topology_signature(topology, baseline_pairs, design_name)
                if signature != summary["signature"]:
                    fail(
                        f"Topology links/PHY endpoints disagree with summary in {design_name}"
                    )
                signatures[policy][(workload, budget)] = signature

    for budget in BUDGETS:
        fixed_signatures = {
            signatures["fixed"][(workload, budget)] for workload in WORKLOADS
        }
        if len(fixed_signatures) != 1:
            fail(f"Fixed physical topology changes across workloads for B{budget}")
    return signatures


def resolve_results(signatures):
    resolved = {}
    metric_cache = {}
    for policy in ("fixed", "workload_aware"):
        prefix = "fixed_k4" if policy == "fixed" else "aware_k4"
        for workload in WORKLOADS:
            lower_by_signature = {}
            for budget in BUDGETS:
                signature = signatures[policy][(workload, budget)]
                direct = REPO_ROOT / f"results/{prefix}_{workload}_B{budget}mm.json"
                if proven.result_is_complete(direct):
                    source = direct
                    reused = False
                    lower_by_signature.setdefault(signature, direct)
                else:
                    source = lower_by_signature.get(signature)
                    if source is None or not proven.result_is_complete(source):
                        fail(
                            f"Missing or incomplete {policy} result for {workload} "
                            f"B{budget}: {direct}; no complete identical lower-budget "
                            "result is available"
                        )
                    reused = True
                if source not in metric_cache:
                    metric_cache[source] = proven.parse_result(source)
                resolved[(policy, workload, budget)] = {
                    "metrics": metric_cache[source],
                    "source_result_file": proven.relative(source),
                    "reused_result": reused,
                }
    return resolved


def improvement(baseline, candidate, description):
    return proven.improvement(baseline, candidate, description)


def build_rows(fixed, aware, resolved):
    mesh = {}
    for workload in WORKLOADS:
        path = REPO_ROOT / f"results/k1_{workload}_baseline.json"
        mesh[workload] = {
            "metrics": proven.parse_result(path),
            "source": proven.relative(path),
        }

    rows = []
    for workload in WORKLOADS:
        mesh_metrics = mesh[workload]["metrics"]
        for budget in BUDGETS:
            summaries = {
                "fixed": fixed[budget],
                "workload_aware": aware[(workload, budget)],
            }
            for policy in ("fixed", "workload_aware"):
                if summaries[policy]["total_wire_length_mm"] > budget + EPS:
                    fail(f"{policy} wire length exceeds B{budget} for {workload}")

            policy_rows = {}
            for policy in POLICIES:
                if policy == "mesh":
                    summary = {
                        "requested_k": 0,
                        "actual_k": 0,
                        **{f"link_{index}": "none" for index in range(1, 5)},
                        "total_wire_length_mm": 0.0,
                    }
                    metrics = mesh_metrics
                    source = mesh[workload]["source"]
                    reused = False
                else:
                    summary = summaries[policy]
                    result = resolved[(policy, workload, budget)]
                    metrics = result["metrics"]
                    source = result["source_result_file"]
                    reused = result["reused_result"]
                    if policy == "workload_aware" and not math.isclose(
                        metrics["analytical_throughput"],
                        summary["summary_analytical_throughput"],
                        rel_tol=EPS,
                        abs_tol=EPS,
                    ):
                        fail(
                            "Aware summary/result analytical throughput mismatch for "
                            f"{workload} B{budget}"
                        )
                row = {
                    "workload": workload,
                    "budget_mm": budget,
                    "policy": policy,
                    "requested_k": summary["requested_k"],
                    "actual_k": summary["actual_k"],
                    **{f"link_{index}": summary[f"link_{index}"] for index in range(1, 5)},
                    "total_wire_length_mm": summary["total_wire_length_mm"],
                    **metrics,
                    "source_result_file": source,
                    "reused_result": reused,
                }
                if policy == "mesh":
                    row["latency_gain_vs_mesh_percent"] = 0.0
                    row["hop_gain_vs_mesh_percent"] = 0.0
                    row["analytical_latency_gain_vs_mesh_percent"] = 0.0
                else:
                    row["latency_gain_vs_mesh_percent"] = improvement(
                        mesh_metrics["booksim_low_latency"],
                        metrics["booksim_low_latency"],
                        "BookSim latency vs same-workload mesh",
                    )
                    row["hop_gain_vs_mesh_percent"] = improvement(
                        mesh_metrics["booksim_low_hops"],
                        metrics["booksim_low_hops"],
                        "hops vs same-workload mesh",
                    )
                    row["analytical_latency_gain_vs_mesh_percent"] = improvement(
                        mesh_metrics["analytical_latency"],
                        metrics["analytical_latency"],
                        "analytical latency vs same-workload mesh",
                    )
                row["aware_latency_gain_vs_fixed_percent"] = None
                row["aware_hop_gain_vs_fixed_percent"] = None
                policy_rows[policy] = row

            fixed_row = policy_rows["fixed"]
            aware_row = policy_rows["workload_aware"]
            aware_row["aware_latency_gain_vs_fixed_percent"] = improvement(
                fixed_row["booksim_low_latency"],
                aware_row["booksim_low_latency"],
                "aware latency vs same workload/budget fixed",
            )
            aware_row["aware_hop_gain_vs_fixed_percent"] = improvement(
                fixed_row["booksim_low_hops"],
                aware_row["booksim_low_hops"],
                "aware hops vs same workload/budget fixed",
            )
            rows.extend(policy_rows[policy] for policy in POLICIES)
    return rows


def validate_rows(rows):
    if (
        len(WORKLOADS) != 4
        or len(BUDGETS) != 4
        or len(POLICIES) != 3
        or len(rows) != 48
    ):
        fail("Comparison dimensions must be 4 workloads x 4 budgets x 3 policies = 48 rows")

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
        if (
            mesh["requested_k"] != 0
            or mesh["actual_k"] != 0
            or mesh["total_wire_length_mm"] != 0
            or any(mesh[f"link_{index}"] != "none" for index in range(1, 5))
        ):
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
            if row["requested_k"] != 4 or not 0 <= row["actual_k"] <= 4:
                fail(f"Invalid K fields for {policy} {key}")
            if row["total_wire_length_mm"] > key[1] + EPS:
                fail(f"Wire budget exceeded for {policy} {key}")
            checks = (
                (
                    "latency_gain_vs_mesh_percent",
                    improvement(
                        mesh["booksim_low_latency"],
                        row["booksim_low_latency"],
                        "validation latency",
                    ),
                ),
                (
                    "hop_gain_vs_mesh_percent",
                    improvement(
                        mesh["booksim_low_hops"],
                        row["booksim_low_hops"],
                        "validation hops",
                    ),
                ),
                (
                    "analytical_latency_gain_vs_mesh_percent",
                    improvement(
                        mesh["analytical_latency"],
                        row["analytical_latency"],
                        "validation analytical latency",
                    ),
                ),
            )
            for field, expected_value in checks:
                if not math.isclose(row[field], expected_value, rel_tol=EPS, abs_tol=EPS):
                    fail(f"Cross-workload normalization detected in {field} for {policy} {key}")

        fixed_row = indexed["fixed"]
        aware_row = indexed["workload_aware"]
        expected_latency = improvement(
            fixed_row["booksim_low_latency"],
            aware_row["booksim_low_latency"],
            "validation direct latency",
        )
        expected_hops = improvement(
            fixed_row["booksim_low_hops"],
            aware_row["booksim_low_hops"],
            "validation direct hops",
        )
        if not math.isclose(
            aware_row["aware_latency_gain_vs_fixed_percent"],
            expected_latency,
            rel_tol=EPS,
            abs_tol=EPS,
        ):
            fail(f"Incorrect aware-vs-fixed latency comparison for {key}")
        if not math.isclose(
            aware_row["aware_hop_gain_vs_fixed_percent"],
            expected_hops,
            rel_tol=EPS,
            abs_tol=EPS,
        ):
            fail(f"Incorrect aware-vs-fixed hop comparison for {key}")
        for policy in ("mesh", "fixed"):
            if (
                indexed[policy]["aware_latency_gain_vs_fixed_percent"] is not None
                or indexed[policy]["aware_hop_gain_vs_fixed_percent"] is not None
            ):
                fail(f"Aware-vs-fixed fields must be blank on {policy} row {key}")


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
    selected = [
        row[f"link_{index}"]
        for index in range(1, REQUESTED_K + 1)
        if row[f"link_{index}"] != "none"
    ]
    return "none" if not selected else ",".join(selected)


def print_tables(rows):
    indexed = {
        (row["workload"], row["budget_mm"], row["policy"]): row for row in rows
    }
    for workload in WORKLOADS:
        print(f"\nK=4 POLICY COMPARISON - {workload.upper()}")
        print(
            "BUDGET | POLICY | ACTUAL_K | LINKS | WIRE | BS_LAT | "
            "GAIN_vs_MESH | HOPS | KNEE"
        )
        for budget in BUDGETS:
            for policy in POLICIES:
                row = indexed[(workload, budget, policy)]
                print(
                    f"{budget} | {policy} | {row['actual_k']} | {links(row)} | "
                    f"{row['total_wire_length_mm']:.3f} | "
                    f"{row['booksim_low_latency']:.3f} | "
                    f"{row['latency_gain_vs_mesh_percent']:.3f} | "
                    f"{row['booksim_low_hops']:.3f} | {shown(row['knee_load'])}"
                )

    print(
        "\nWORKLOAD | BUDGET | FIXED_K | AWARE_K | FIXED_LAT | AWARE_LAT | "
        "AWARE_GAIN_vs_FIXED | FIXED_LINKS | AWARE_LINKS"
    )
    for workload in WORKLOADS:
        for budget in BUDGETS:
            fixed = indexed[(workload, budget, "fixed")]
            aware = indexed[(workload, budget, "workload_aware")]
            print(
                f"{workload} | {budget} | {fixed['actual_k']} | {aware['actual_k']} | "
                f"{fixed['booksim_low_latency']:.3f} | "
                f"{aware['booksim_low_latency']:.3f} | "
                f"{aware['aware_latency_gain_vs_fixed_percent']:.3f} | "
                f"{links(fixed)} | {links(aware)}"
            )

    print(
        "\nBUDGET | MESH_LAT | FIXED_LAT | AWARE_LAT | FIXED_GAIN_vs_MESH | "
        "AWARE_GAIN_vs_MESH | AWARE_GAIN_vs_FIXED"
    )
    for budget in BUDGETS:
        means = {
            policy: sum(
                indexed[(workload, budget, policy)]["booksim_low_latency"]
                for workload in WORKLOADS
            )
            / len(WORKLOADS)
            for policy in POLICIES
        }
        fixed_gain = sum(
            indexed[(workload, budget, "fixed")]["latency_gain_vs_mesh_percent"]
            for workload in WORKLOADS
        ) / len(WORKLOADS)
        aware_gain = sum(
            indexed[(workload, budget, "workload_aware")][
                "latency_gain_vs_mesh_percent"
            ]
            for workload in WORKLOADS
        ) / len(WORKLOADS)
        direct_gain = sum(
            indexed[(workload, budget, "workload_aware")][
                "aware_latency_gain_vs_fixed_percent"
            ]
            for workload in WORKLOADS
        ) / len(WORKLOADS)
        print(
            f"{budget} | {means['mesh']:.3f} | {means['fixed']:.3f} | "
            f"{means['workload_aware']:.3f} | {fixed_gain:.3f} | "
            f"{aware_gain:.3f} | {direct_gain:.3f}"
        )


def main():
    fixed, aware = load_summaries()
    signatures = validate_designs(fixed, aware)
    resolved = resolve_results(signatures)
    rows = build_rows(fixed, aware, resolved)
    validate_rows(rows)
    write_csv(rows)
    print_tables(rows)
    print("\nK=4 POLICY COMPARISON COMPLETE")
    print(f"Workloads: {len(WORKLOADS)}")
    print(f"Budgets: {len(BUDGETS)}")
    print(f"Policies: {len(POLICIES)}")
    print(f"Rows: {len(rows)}")
    print(f"Output CSV: {proven.relative(OUTPUT_FILE)}")


if __name__ == "__main__":
    main()
