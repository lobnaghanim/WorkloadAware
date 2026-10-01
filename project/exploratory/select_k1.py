import csv


INPUT_FILE = "results/physical_candidates_transpose.csv"

BUDGETS = [
    5.0,
    15.0,
    25.0,
    45.0,
]

BASELINE_MAX_LOAD = 24.0


# ------------------------------------------------------------
# Load candidate results
# ------------------------------------------------------------

with open(INPUT_FILE, newline="") as f:
    reader = csv.DictReader(f)
    rows = list(reader)


candidates = []

for row in rows:

    candidates.append({
        "u": int(row["u"]),
        "v": int(row["v"]),
        "phy_u": int(row["phy_u"]),
        "phy_v": int(row["phy_v"]),

        "length": float(
            row["length_mm"]
        ),

        "link_latency": float(
            row["link_latency_cycles"]
        ),

        "avg_latency": float(
            row["avg_latency"]
        ),

        "latency_gain_percent": float(
            row["latency_gain_percent"]
        ),

        "max_load": float(
            row["max_link_load"]
        ),

        "throughput": float(
            row["throughput"]
        ),
    })


# ------------------------------------------------------------
# Select best candidate under each budget
# ------------------------------------------------------------

print("=" * 90)
print("K=1 WORKLOAD-AWARE SELECTION — TRANSPOSE")
print("=" * 90)

for budget in BUDGETS:

    feasible = []

    for candidate in candidates:

        if candidate["length"] > budget:
            continue

        if candidate["max_load"] > BASELINE_MAX_LOAD:
            continue

        feasible.append(candidate)

    if not feasible:

        print(
            f"\nBudget {budget:.0f} mm:"
            " no feasible shortcut"
        )
        continue

    # Primary objective:
    # lowest average latency
    best = min(
        feasible,
        key=lambda x: x["avg_latency"],
    )

    print()
    print(f"Wire budget: {budget:.0f} mm")

    print(
        f"Selected link: "
        f"{best['u']} <-> {best['v']}"
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
        f"Link latency: "
        f"{best['link_latency']:.1f} cycles"
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
        f"{best['max_load']:.2f}"
    )

    print(
        f"Analytical throughput: "
        f"{best['throughput']:.2f}"
    )