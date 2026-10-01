import csv
import json


WORKLOADS = [
    "random_uniform",
    "transpose",
    "permutation",
    "hotspot",
]

BUDGETS = [
    5.0,
    15.0,
    25.0,
    45.0,
]

EPS = 1e-9


def load_baseline(workload):

    filename = (
        "results/"
        f"physical_baseline_{workload}.json"
    )

    with open(filename) as f:
        return json.load(f)


def load_candidates(workload):

    filename = (
        "results/"
        f"physical_candidates_{workload}.csv"
    )

    with open(filename, newline="") as f:
        rows = list(csv.DictReader(f))

    candidates = []

    for row in rows:

        candidates.append({
            "u": int(row["u"]),
            "v": int(row["v"]),
            "phy_u": int(row["phy_u"]),
            "phy_v": int(row["phy_v"]),

            "length":
                float(row["length_mm"]),

            "link_latency":
                float(row["link_latency_cycles"]),

            "avg_latency":
                float(row["avg_latency"]),

            "latency_gain_percent":
                float(row["latency_gain_percent"]),

            "max_load":
                float(row["max_link_load"]),

            "throughput":
                float(row["throughput"]),
        })

    return candidates


all_results = []


for workload in WORKLOADS:

    baseline = load_baseline(
        workload
    )

    candidates = load_candidates(
        workload
    )

    baseline_max_load = (
        baseline["max_link_load"]
    )

    print()
    print("=" * 100)
    print(
        f"K=1 WORKLOAD-AWARE SELECTION — "
        f"{workload.upper()}"
    )
    print("=" * 100)

    print(
        f"Baseline average latency: "
        f"{baseline['avg_latency']:.3f}"
    )

    print(
        f"Baseline maximum link load: "
        f"{baseline_max_load:.3f}"
    )

    for budget in BUDGETS:

        feasible = [
            c
            for c in candidates

            if (
                c["length"]
                <= budget + EPS
            )

            and (
                c["max_load"]
                <= baseline_max_load + EPS
            )
        ]

        if not feasible:

            print()
            print(
                f"Budget {budget:.0f} mm: "
                "NO FEASIBLE SHORTCUT"
            )

            continue

        # Primary objective:
        # lowest average latency.
        #
        # Remaining values are deterministic
        # tie-breakers only.
        best = min(
            feasible,
            key=lambda x: (
                x["avg_latency"],
                x["max_load"],
                x["length"],
                x["u"],
                x["v"],
            ),
        )

        result = {
            "workload":
                workload,

            "budget_mm":
                budget,

            "u":
                best["u"],

            "v":
                best["v"],

            "phy_u":
                best["phy_u"],

            "phy_v":
                best["phy_v"],

            "length_mm":
                best["length"],

            "link_latency_cycles":
                best["link_latency"],

            "avg_latency":
                best["avg_latency"],

            "latency_gain_percent":
                best["latency_gain_percent"],

            "max_link_load":
                best["max_load"],

            "baseline_max_load":
                baseline_max_load,

            "throughput":
                best["throughput"],
        }

        all_results.append(
            result
        )

        print()
        print(
            f"Wire budget: "
            f"{budget:.0f} mm"
        )

        print(
            f"Selected link: "
            f"{best['u']} <-> "
            f"{best['v']}"
        )

        print(
            f"PHYs: "
            f"{best['phy_u']} <-> "
            f"{best['phy_v']}"
        )

        print(
            f"Physical length: "
            f"{best['length']:.3f} mm"
        )

        print(
            f"Average latency: "
            f"{best['avg_latency']:.3f}"
        )

        print(
            f"Latency improvement: "
            f"{best['latency_gain_percent']:.2f}%"
        )

        print(
            f"Maximum link load: "
            f"{best['max_load']:.3f}"
        )

        print(
            f"Analytical throughput: "
            f"{best['throughput']:.2f}"
        )


# ------------------------------------------------------------
# Save one summary CSV for later experiments
# ------------------------------------------------------------

output_file = (
    "results/"
    "k1_workload_aware_selections.csv"
)

if all_results:

    with open(
        output_file,
        "w",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=
                all_results[0].keys(),
        )

        writer.writeheader()
        writer.writerows(
            all_results
        )


print()
print("=" * 100)
print(
    "Saved:",
    output_file,
)