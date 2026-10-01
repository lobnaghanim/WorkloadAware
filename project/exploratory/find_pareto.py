import csv


INPUT_FILE = "results/physical_candidates_transpose.csv"


# ------------------------------------------------------------
# Load candidates
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
        "length": float(row["length_mm"]),
        "link_latency": float(row["link_latency_cycles"]),
        "avg_latency": float(row["avg_latency"]),
        "latency_gain": float(row["latency_gain"]),
        "latency_gain_percent":
            float(row["latency_gain_percent"]),
        "max_load": float(row["max_link_load"]),
        "throughput": float(row["throughput"]),
        "traffic_wire_cost":
            float(row["traffic_wire_cost"]),
    })


# ------------------------------------------------------------
# Pareto dominance
#
# We minimize:
#   physical length
#   average workload latency
#   maximum directed-link load
# ------------------------------------------------------------

EPS_LENGTH = 1e-3
EPS_LATENCY = 1e-6
EPS_LOAD = 1e-6


def dominates(a, b):

    no_worse = (
        a["length"] <= b["length"] + EPS_LENGTH
        and
        a["avg_latency"] <= b["avg_latency"] + EPS_LATENCY
        and
        a["max_load"] <= b["max_load"] + EPS_LOAD
    )

    strictly_better = (
        a["length"] < b["length"] - EPS_LENGTH
        or
        a["avg_latency"] < b["avg_latency"] - EPS_LATENCY
        or
        a["max_load"] < b["max_load"] - EPS_LOAD
    )

    return no_worse and strictly_better

pareto = []

for candidate in candidates:

    is_dominated = False

    for other in candidates:

        if other is candidate:
            continue

        if dominates(other, candidate):
            is_dominated = True
            break

    if not is_dominated:
        pareto.append(candidate)


# Sort for readable output
pareto.sort(
    key=lambda x: (
        x["length"],
        x["avg_latency"],
        x["max_load"],
    )
)


# ------------------------------------------------------------
# Print
# ------------------------------------------------------------

print("=" * 100)
print("PARETO-OPTIMAL SHORTCUTS — TRANSPOSE")
print("=" * 100)

print(
    f"{'LINK':>8}"
    f"{'LENGTH':>12}"
    f"{'LINK LAT':>12}"
    f"{'AVG LAT':>12}"
    f"{'GAIN %':>10}"
    f"{'MAX LOAD':>12}"
    f"{'THROUGHPUT':>14}"
)

print("-" * 100)

for c in pareto:

    link = f"{c['u']}<->{c['v']}"

    print(
        f"{link:>8}"
        f"{c['length']:>12.3f}"
        f"{c['link_latency']:>12.1f}"
        f"{c['avg_latency']:>12.3f}"
        f"{c['latency_gain_percent']:>10.2f}"
        f"{c['max_load']:>12.2f}"
        f"{c['throughput']:>14.2f}"
    )


print()
print(
    "Number of candidate shortcuts:",
    len(candidates),
)

print(
    "Number of Pareto-optimal shortcuts:",
    len(pareto),
)