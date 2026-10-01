"""Combine and analyze completed K=1, K=2, and K=4 policy results."""

import csv
import math
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
BUDGETS = (5, 15, 25, 45)
K_FAMILIES = (("K1", 1), ("K2", 2), ("K4", 4))
POLICIES = ("mesh", "fixed", "workload_aware")
INPUTS = {
    "K1": REPO_ROOT / "results/k1_policy_comparison.csv",
    "K2": REPO_ROOT / "results/k2_policy_comparison.csv",
    "K4": REPO_ROOT / "results/k4_policy_comparison.csv",
}
COMBINED_OUTPUT = REPO_ROOT / "results/all_k_policy_comparison.csv"
SUMMARY_OUTPUT = REPO_ROOT / "results/all_k_summary.csv"
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
    "latency_gain_vs_mesh_percent",
    "hop_gain_vs_mesh_percent",
    "analytical_latency_gain_vs_mesh_percent",
    "aware_latency_gain_vs_fixed_percent",
    "aware_hop_gain_vs_fixed_percent",
)
OPTIONAL_METRICS = {
    "knee_load",
    "knee_latency",
    "aware_latency_gain_vs_fixed_percent",
    "aware_hop_gain_vs_fixed_percent",
}
COMBINED_FIELDS = (
    "k_family",
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
    "source_result_file",
    "reused_result",
)
SUMMARY_FIELDS = (
    "workload",
    "budget_mm",
    "k_family",
    "requested_k",
    "mesh_bs_latency",
    "mesh_hops",
    "mesh_knee_load",
    "fixed_actual_k",
    "fixed_total_wire_mm",
    "fixed_bs_latency",
    "fixed_hops",
    "fixed_knee_load",
    "fixed_latency_gain_vs_mesh_percent",
    "aware_actual_k",
    "aware_total_wire_mm",
    "aware_bs_latency",
    "aware_hops",
    "aware_knee_load",
    "aware_latency_gain_vs_mesh_percent",
    "aware_latency_gain_vs_fixed_percent",
    "aware_hop_gain_vs_fixed_percent",
    "fixed_k1_to_k2_latency_gain_percent",
    "fixed_k2_to_k4_latency_gain_percent",
    "fixed_k1_to_k4_latency_gain_percent",
    "aware_k1_to_k2_latency_gain_percent",
    "aware_k2_to_k4_latency_gain_percent",
    "aware_k1_to_k4_latency_gain_percent",
    "fixed_added_wire_k1_to_k2_mm",
    "fixed_added_wire_k2_to_k4_mm",
    "aware_added_wire_k1_to_k2_mm",
    "aware_added_wire_k2_to_k4_mm",
    "fixed_added_actual_links_k1_to_k2",
    "fixed_added_actual_links_k2_to_k4",
    "aware_added_actual_links_k1_to_k2",
    "aware_added_actual_links_k2_to_k4",
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


def number(value, description, optional=False):
    if optional and (value is None or value == ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError) as error:
        fail(f"Invalid {description}: {value!r} ({error})")
    if not math.isfinite(converted):
        fail(f"Invalid {description}: expected a finite number")
    return converted


def boolean(value, description):
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    fail(f"Invalid {description}: expected True/False, found {value!r}")


def read_csv(path, required, description):
    if not path.is_file():
        fail(f"Missing {description}: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            missing = sorted(set(required) - set(reader.fieldnames or []))
            if missing:
                fail(f"{description} is missing columns: {', '.join(missing)}")
            return list(reader)
    except OSError as error:
        fail(f"Cannot read {description} {path}: {error}")


def metric_values(row, description):
    return {
        field: number(row[field], f"{field} in {description}", field in OPTIONAL_METRICS)
        for field in METRIC_FIELDS
    }


def normalize_links(raw_links, actual_k, description):
    links = []
    for index, value in enumerate(raw_links, start=1):
        normalized = str(value or "").strip()
        if index <= actual_k:
            if not normalized or normalized == "none":
                fail(f"Missing link {index} in {description}")
            links.append(normalized)
        else:
            if normalized not in ("", "none"):
                fail(f"Unexpected link {index} beyond actual_k in {description}")
            links.append("none")
    if len(set(link for link in links if link != "none")) != actual_k:
        fail(f"Duplicate selected link in {description}")
    return links


def normalize_family(k_family, family_k):
    path = INPUTS[k_family]
    common_required = {
        "workload",
        "budget_mm",
        "policy",
        *METRIC_FIELDS,
        "source_result_file",
        "reused_result",
    }
    if k_family == "K1":
        required = common_required | {"k", "link", "wire_length_mm"}
    else:
        required = common_required | {
            "requested_k",
            "actual_k",
            "link_1",
            "link_2",
            "total_wire_length_mm",
        }
        if family_k == 4:
            required |= {"link_3", "link_4"}
    raw_rows = read_csv(path, required, f"{k_family} policy comparison")
    rows = []
    for row_number, raw in enumerate(raw_rows, start=2):
        description = f"{k_family} row {row_number}"
        workload = raw["workload"].strip()
        budget = integer(raw["budget_mm"], f"budget in {description}")
        policy = raw["policy"].strip()
        if workload not in WORKLOADS or budget not in BUDGETS or policy not in POLICIES:
            fail(f"Unexpected workload/budget/policy in {description}")

        if k_family == "K1":
            source_k = integer(raw["k"], f"k in {description}")
            expected_source_k = 0 if policy == "mesh" else 1
            if source_k != expected_source_k:
                fail(f"K1 row has incorrect source k in {description}")
            actual_k = source_k
            links = normalize_links(
                [raw["link"], "none", "none", "none"], actual_k, description
            )
            wire = number(raw["wire_length_mm"], f"wire length in {description}")
        else:
            source_requested = integer(
                raw["requested_k"], f"requested_k in {description}"
            )
            expected_requested = 0 if policy == "mesh" else family_k
            if source_requested != expected_requested:
                fail(f"{k_family} row has incorrect source requested_k in {description}")
            actual_k = integer(raw["actual_k"], f"actual_k in {description}")
            raw_links = [raw["link_1"], raw["link_2"]]
            raw_links.extend(
                [raw.get("link_3", "none"), raw.get("link_4", "none")]
            )
            links = normalize_links(raw_links, actual_k, description)
            wire = number(
                raw["total_wire_length_mm"], f"total wire length in {description}"
            )

        if not 0 <= actual_k <= family_k:
            fail(f"actual_k exceeds requested family K in {description}")
        if policy == "mesh":
            if actual_k != 0 or abs(wire) > EPS or any(link != "none" for link in links):
                fail(f"Mesh row has shortcuts or added wire in {description}")
        elif wire > budget + EPS:
            fail(f"Total wire exceeds B{budget} in {description}")

        # requested_k denotes the logical comparison family. Mesh still has
        # actual_k=0 and zero added wire, so it is never treated as a shortcut topology.
        rows.append(
            {
                "k_family": k_family,
                "workload": workload,
                "budget_mm": budget,
                "policy": policy,
                "requested_k": family_k,
                "actual_k": actual_k,
                **{f"link_{index}": links[index - 1] for index in range(1, 5)},
                "total_wire_length_mm": wire,
                **metric_values(raw, description),
                "source_result_file": raw["source_result_file"].strip(),
                "reused_result": boolean(
                    raw["reused_result"], f"reused_result in {description}"
                ),
            }
        )
    if len(rows) != 48:
        fail(f"{k_family} comparison must contain 48 rows; found {len(rows)}")
    return rows


def load_combined_rows():
    rows = []
    for k_family, family_k in K_FAMILIES:
        rows.extend(normalize_family(k_family, family_k))
    validate_combined(rows)
    return rows


def validate_combined(rows):
    if len(rows) != 144:
        fail(f"Combined comparison must contain exactly 144 rows; found {len(rows)}")
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["workload"], row["budget_mm"], row["k_family"])].append(row)
        family_k = dict(K_FAMILIES)[row["k_family"]]
        if row["requested_k"] != family_k or row["actual_k"] > row["requested_k"]:
            fail(f"Requested/actual K mismatch in combined row {row}")
        if row["policy"] in ("fixed", "workload_aware") and (
            row["total_wire_length_mm"] > row["budget_mm"] + EPS
        ):
            fail(f"Wire budget exceeded in combined row {row}")
        if not (REPO_ROOT / row["source_result_file"]).is_file():
            fail(f"Missing source result referenced by combined row: {row['source_result_file']}")
    expected = {
        (workload, budget, k_family)
        for workload in WORKLOADS
        for budget in BUDGETS
        for k_family, _ in K_FAMILIES
    }
    if set(grouped) != expected:
        fail("Combined workload/budget/K matrix is incomplete")
    for key, group in grouped.items():
        if len(group) != 3 or {row["policy"] for row in group} != set(POLICIES):
            fail(f"Combined group {key} does not contain one row per policy")


def improvement(baseline, candidate, description):
    if baseline == 0:
        fail(f"Cannot normalize {description}: baseline is zero")
    return 100.0 * (baseline - candidate) / baseline


def build_summary(rows):
    indexed = {
        (row["workload"], row["budget_mm"], row["k_family"], row["policy"]): row
        for row in rows
    }
    summaries = []
    for workload in WORKLOADS:
        for budget in BUDGETS:
            cross_k = {
                policy: {
                    family: indexed[(workload, budget, family, policy)]
                    for family, _ in K_FAMILIES
                }
                for policy in ("fixed", "workload_aware")
            }
            cross = {}
            for policy in ("fixed", "workload_aware"):
                k1, k2, k4 = (
                    cross_k[policy][family] for family in ("K1", "K2", "K4")
                )
                cross[policy] = {
                    "k1_to_k2_gain": improvement(
                        k1["booksim_low_latency"],
                        k2["booksim_low_latency"],
                        f"{policy} K1-to-K2 {workload} B{budget}",
                    ),
                    "k2_to_k4_gain": improvement(
                        k2["booksim_low_latency"],
                        k4["booksim_low_latency"],
                        f"{policy} K2-to-K4 {workload} B{budget}",
                    ),
                    "k1_to_k4_gain": improvement(
                        k1["booksim_low_latency"],
                        k4["booksim_low_latency"],
                        f"{policy} K1-to-K4 {workload} B{budget}",
                    ),
                    "wire_k1_to_k2": k2["total_wire_length_mm"]
                    - k1["total_wire_length_mm"],
                    "wire_k2_to_k4": k4["total_wire_length_mm"]
                    - k2["total_wire_length_mm"],
                    "links_k1_to_k2": k2["actual_k"] - k1["actual_k"],
                    "links_k2_to_k4": k4["actual_k"] - k2["actual_k"],
                }

            for k_family, requested_k in K_FAMILIES:
                mesh = indexed[(workload, budget, k_family, "mesh")]
                fixed = indexed[(workload, budget, k_family, "fixed")]
                aware = indexed[(workload, budget, k_family, "workload_aware")]
                is_k2 = k_family == "K2"
                is_k4 = k_family == "K4"
                summaries.append(
                    {
                        "workload": workload,
                        "budget_mm": budget,
                        "k_family": k_family,
                        "requested_k": requested_k,
                        "mesh_bs_latency": mesh["booksim_low_latency"],
                        "mesh_hops": mesh["booksim_low_hops"],
                        "mesh_knee_load": mesh["knee_load"],
                        "fixed_actual_k": fixed["actual_k"],
                        "fixed_total_wire_mm": fixed["total_wire_length_mm"],
                        "fixed_bs_latency": fixed["booksim_low_latency"],
                        "fixed_hops": fixed["booksim_low_hops"],
                        "fixed_knee_load": fixed["knee_load"],
                        "fixed_latency_gain_vs_mesh_percent": fixed[
                            "latency_gain_vs_mesh_percent"
                        ],
                        "aware_actual_k": aware["actual_k"],
                        "aware_total_wire_mm": aware["total_wire_length_mm"],
                        "aware_bs_latency": aware["booksim_low_latency"],
                        "aware_hops": aware["booksim_low_hops"],
                        "aware_knee_load": aware["knee_load"],
                        "aware_latency_gain_vs_mesh_percent": aware[
                            "latency_gain_vs_mesh_percent"
                        ],
                        "aware_latency_gain_vs_fixed_percent": aware[
                            "aware_latency_gain_vs_fixed_percent"
                        ],
                        "aware_hop_gain_vs_fixed_percent": aware[
                            "aware_hop_gain_vs_fixed_percent"
                        ],
                        "fixed_k1_to_k2_latency_gain_percent": cross["fixed"][
                            "k1_to_k2_gain"
                        ]
                        if is_k2
                        else None,
                        "fixed_k2_to_k4_latency_gain_percent": cross["fixed"][
                            "k2_to_k4_gain"
                        ]
                        if is_k4
                        else None,
                        "fixed_k1_to_k4_latency_gain_percent": cross["fixed"][
                            "k1_to_k4_gain"
                        ]
                        if is_k4
                        else None,
                        "aware_k1_to_k2_latency_gain_percent": cross[
                            "workload_aware"
                        ]["k1_to_k2_gain"]
                        if is_k2
                        else None,
                        "aware_k2_to_k4_latency_gain_percent": cross[
                            "workload_aware"
                        ]["k2_to_k4_gain"]
                        if is_k4
                        else None,
                        "aware_k1_to_k4_latency_gain_percent": cross[
                            "workload_aware"
                        ]["k1_to_k4_gain"]
                        if is_k4
                        else None,
                        "fixed_added_wire_k1_to_k2_mm": cross["fixed"][
                            "wire_k1_to_k2"
                        ]
                        if is_k2
                        else None,
                        "fixed_added_wire_k2_to_k4_mm": cross["fixed"][
                            "wire_k2_to_k4"
                        ]
                        if is_k4
                        else None,
                        "aware_added_wire_k1_to_k2_mm": cross[
                            "workload_aware"
                        ]["wire_k1_to_k2"]
                        if is_k2
                        else None,
                        "aware_added_wire_k2_to_k4_mm": cross[
                            "workload_aware"
                        ]["wire_k2_to_k4"]
                        if is_k4
                        else None,
                        "fixed_added_actual_links_k1_to_k2": cross["fixed"][
                            "links_k1_to_k2"
                        ]
                        if is_k2
                        else None,
                        "fixed_added_actual_links_k2_to_k4": cross["fixed"][
                            "links_k2_to_k4"
                        ]
                        if is_k4
                        else None,
                        "aware_added_actual_links_k1_to_k2": cross[
                            "workload_aware"
                        ]["links_k1_to_k2"]
                        if is_k2
                        else None,
                        "aware_added_actual_links_k2_to_k4": cross[
                            "workload_aware"
                        ]["links_k2_to_k4"]
                        if is_k4
                        else None,
                    }
                )
    validate_summary(summaries, indexed)
    return summaries


def validate_summary(summaries, combined_index):
    if len(summaries) != 48:
        fail(f"All-K summary must contain exactly 48 rows; found {len(summaries)}")
    keys = {
        (row["workload"], row["budget_mm"], row["k_family"])
        for row in summaries
    }
    expected = {
        (workload, budget, family)
        for workload in WORKLOADS
        for budget in BUDGETS
        for family, _ in K_FAMILIES
    }
    if keys != expected or len(keys) != len(summaries):
        fail("All-K summary matrix is incomplete or contains duplicates")

    for row in summaries:
        key = (row["workload"], row["budget_mm"], row["k_family"])
        fixed = combined_index[(*key, "fixed")]
        aware = combined_index[(*key, "workload_aware")]
        if row["fixed_actual_k"] != fixed["actual_k"]:
            fail(f"Fixed actual_k summary mismatch for {key}")
        if row["aware_actual_k"] != aware["actual_k"]:
            fail(f"Aware actual_k summary mismatch for {key}")


def write_csv(path, fields, rows):
    try:
        with path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in rows)
    except OSError as error:
        fail(f"Cannot write output CSV {path}: {error}")


def shown(value, decimals=3):
    return "-" if value is None else f"{value:.{decimals}f}"


def print_tables(rows):
    indexed = {
        (row["workload"], row["budget_mm"], row["k_family"], row["policy"]): row
        for row in rows
    }
    for workload in WORKLOADS:
        print(f"\nALL-K POLICY COMPARISON - {workload.upper()}")
        print("BUDGET | K | POLICY | ACTUAL_K | WIRE | BS_LAT | GAIN_vs_MESH | HOPS | KNEE")
        for budget in BUDGETS:
            for k_family, _ in K_FAMILIES:
                for policy in POLICIES:
                    row = indexed[(workload, budget, k_family, policy)]
                    print(
                        f"{budget} | {k_family} | {policy} | {row['actual_k']} | "
                        f"{row['total_wire_length_mm']:.3f} | "
                        f"{row['booksim_low_latency']:.3f} | "
                        f"{row['latency_gain_vs_mesh_percent']:.3f} | "
                        f"{row['booksim_low_hops']:.3f} | {shown(row['knee_load'])}"
                    )

    print(
        "\nWORKLOAD | BUDGET | POLICY | K1_LAT | K2_LAT | K4_LAT | "
        "K1_ACTUAL | K2_ACTUAL | K4_ACTUAL | K1_WIRE | K2_WIRE | K4_WIRE"
    )
    for workload in WORKLOADS:
        for budget in BUDGETS:
            for policy in ("fixed", "workload_aware"):
                k1, k2, k4 = (
                    indexed[(workload, budget, family, policy)]
                    for family in ("K1", "K2", "K4")
                )
                print(
                    f"{workload} | {budget} | {policy} | "
                    f"{k1['booksim_low_latency']:.3f} | {k2['booksim_low_latency']:.3f} | "
                    f"{k4['booksim_low_latency']:.3f} | {k1['actual_k']} | "
                    f"{k2['actual_k']} | {k4['actual_k']} | "
                    f"{k1['total_wire_length_mm']:.3f} | "
                    f"{k2['total_wire_length_mm']:.3f} | {k4['total_wire_length_mm']:.3f}"
                )

    print(
        "\nWORKLOAD | BUDGET | K | FIXED_LAT | AWARE_LAT | "
        "AWARE_GAIN_vs_FIXED | FIXED_ACTUAL_K | AWARE_ACTUAL_K"
    )
    for workload in WORKLOADS:
        for budget in BUDGETS:
            for family, _ in K_FAMILIES:
                fixed = indexed[(workload, budget, family, "fixed")]
                aware = indexed[(workload, budget, family, "workload_aware")]
                print(
                    f"{workload} | {budget} | {family} | "
                    f"{fixed['booksim_low_latency']:.3f} | "
                    f"{aware['booksim_low_latency']:.3f} | "
                    f"{aware['aware_latency_gain_vs_fixed_percent']:.3f} | "
                    f"{fixed['actual_k']} | {aware['actual_k']}"
                )

    print(
        "\nBUDGET | K | MESH_LAT | FIXED_LAT | AWARE_LAT | "
        "FIXED_GAIN_vs_MESH | AWARE_GAIN_vs_MESH | AWARE_GAIN_vs_FIXED"
    )
    for budget in BUDGETS:
        for family, _ in K_FAMILIES:
            mean = lambda policy, field: sum(
                indexed[(workload, budget, family, policy)][field]
                for workload in WORKLOADS
            ) / len(WORKLOADS)
            print(
                f"{budget} | {family} | {mean('mesh', 'booksim_low_latency'):.3f} | "
                f"{mean('fixed', 'booksim_low_latency'):.3f} | "
                f"{mean('workload_aware', 'booksim_low_latency'):.3f} | "
                f"{mean('fixed', 'latency_gain_vs_mesh_percent'):.3f} | "
                f"{mean('workload_aware', 'latency_gain_vs_mesh_percent'):.3f} | "
                f"{mean('workload_aware', 'aware_latency_gain_vs_fixed_percent'):.3f}"
            )

    print(
        "\nBUDGET | AWARE_K1_LAT | AWARE_K2_LAT | AWARE_K4_LAT | "
        "K1_TO_K2_GAIN | K2_TO_K4_GAIN"
    )
    for budget in BUDGETS:
        means = {
            family: sum(
                indexed[(workload, budget, family, "workload_aware")][
                    "booksim_low_latency"
                ]
                for workload in WORKLOADS
            )
            / len(WORKLOADS)
            for family in ("K1", "K2", "K4")
        }
        print(
            f"{budget} | {means['K1']:.3f} | {means['K2']:.3f} | "
            f"{means['K4']:.3f} | "
            f"{improvement(means['K1'], means['K2'], 'aggregate K1-to-K2'):.3f} | "
            f"{improvement(means['K2'], means['K4'], 'aggregate K2-to-K4'):.3f}"
        )


def main():
    rows = load_combined_rows()
    summary = build_summary(rows)
    write_csv(COMBINED_OUTPUT, COMBINED_FIELDS, rows)
    write_csv(SUMMARY_OUTPUT, SUMMARY_FIELDS, summary)
    print_tables(rows)
    print("\nALL-K SYNTHETIC POLICY COMPARISON COMPLETE")
    print(f"Workloads: {len(WORKLOADS)}")
    print(f"Budgets: {len(BUDGETS)}")
    print(f"Requested K values: {len(K_FAMILIES)}")
    print(f"Policies: {len(POLICIES)}")
    print(f"Combined rows: {len(rows)}")
    print(f"Summary rows: {len(summary)}")
    print(f"Combined CSV: {relative(COMBINED_OUTPUT)}")
    print(f"Summary CSV: {relative(SUMMARY_OUTPUT)}")


if __name__ == "__main__":
    main()
