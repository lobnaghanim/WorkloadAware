import json


CONFIGS = [
    {
        "name": "Baseline",
        "budget": 0,
        "link": "none",
        "length": 0.0,
        "file": "results/k1_transpose_baseline.json",
    },
    {
        "name": "B5",
        "budget": 5,
        "link": "1<->4",
        "length": 4.794,
        "file": "results/k1_transpose_B5mm.json",
    },
    {
        "name": "B15",
        "budget": 15,
        "link": "6<->13",
        "length": 13.933,
        "file": "results/k1_transpose_B15mm.json",
    },
    {
        "name": "B25",
        "budget": 25,
        "link": "7<->13",
        "length": 23.072,
        "file": "results/k1_transpose_B25mm.json",
    },
    {
        "name": "B45",
        "budget": 45,
        "link": "3<->12",
        "length": 41.350,
        "file": "results/k1_transpose_B45mm.json",
    },
]


def get_load_points(booksim):

    points = []

    for key, value in booksim.items():

        try:
            load = float(key)
        except ValueError:
            # Ignore things like n_nodes and time_taken
            continue

        points.append(
            (load, value)
        )

    points.sort(
        key=lambda x: x[0]
    )

    return points


def analyze_file(filename):

    with open(filename) as f:
        data = json.load(f)

    analytical_latency = data["latency"]["avg"]

    analytical_throughput = (
        data["throughput"]["aggregate_throughput"]
    )

    booksim = data["booksim_simulation"]

    points = get_load_points(
        booksim
    )

    if not points:
        raise RuntimeError(
            f"No BookSim load points in {filename}"
        )

    # ------------------------------------------------------
    # Low-load behavior
    # ------------------------------------------------------

    low_load, low_result = points[0]

    low_latency = (
        low_result["packet_latency"]["avg"]
    )

    low_hops = (
        low_result["hops"]["avg"]
    )

    # ------------------------------------------------------
    # Find latency knee
    #
    # We define the knee as the first tested load where
    # average packet latency reaches >= 2x low-load latency.
    #
    # This is OUR derived comparison metric.
    # ------------------------------------------------------

    knee_load = None
    knee_latency = None

    last_stable_load = None
    last_stable_latency = None
    last_stable_accepted_rate = None

    for i, (load, result) in enumerate(points):

        latency = (
            result["packet_latency"]["avg"]
        )

        if latency >= 2.0 * low_latency:

            knee_load = load
            knee_latency = latency

            # Previous point = last tested point
            # before the sharp latency increase
            if i > 0:

                stable_load, stable_result = (
                    points[i - 1]
                )

                last_stable_load = (
                    stable_load
                )

                last_stable_latency = (
                    stable_result[
                        "packet_latency"
                    ]["avg"]
                )

                last_stable_accepted_rate = (
                    stable_result[
                        "accepted_packet_rate"
                    ]["avg"]
                )

            break

    # If latency never doubled during the sweep,
    # use the highest tested point as last stable.
    if knee_load is None:

        stable_load, stable_result = (
            points[-1]
        )

        last_stable_load = stable_load

        last_stable_latency = (
            stable_result[
                "packet_latency"
            ]["avg"]
        )

        last_stable_accepted_rate = (
            stable_result[
                "accepted_packet_rate"
            ]["avg"]
        )

    return {
        "analytical_latency":
            analytical_latency,

        "analytical_throughput":
            analytical_throughput,

        "low_load":
            low_load,

        "booksim_low_latency":
            low_latency,

        "booksim_low_hops":
            low_hops,

        "last_stable_load":
            last_stable_load,

        "last_stable_latency":
            last_stable_latency,

        "last_stable_accepted_rate":
            last_stable_accepted_rate,

        "knee_load":
            knee_load,

        "knee_latency":
            knee_latency,
    }


# ==========================================================
# Analyze all configurations
# ==========================================================

results = []

for config in CONFIGS:

    result = analyze_file(
        config["file"]
    )

    result.update(
        config
    )

    results.append(
        result
    )


# ==========================================================
# Calculate improvement relative to baseline
# ==========================================================

baseline = results[0]

baseline_analytical = (
    baseline["analytical_latency"]
)

baseline_booksim = (
    baseline["booksim_low_latency"]
)


for result in results:

    result["analytical_gain_percent"] = (
        100.0
        * (
            baseline_analytical
            - result["analytical_latency"]
        )
        / baseline_analytical
    )

    result["booksim_gain_percent"] = (
        100.0
        * (
            baseline_booksim
            - result["booksim_low_latency"]
        )
        / baseline_booksim
    )


# ==========================================================
# Print comparison
# ==========================================================

print("=" * 135)

print(
    "K=1 TRANSPOSE — "
    "PERFORMANCE VS PHYSICAL WIRE BUDGET"
)

print("=" * 135)

header = (
    f"{'CONFIG':<10}"
    f"{'LINK':<10}"
    f"{'WIRE(mm)':>10}"
    f"{'AN LAT':>10}"
    f"{'AN GAIN%':>11}"
    f"{'BS LAT':>10}"
    f"{'BS GAIN%':>11}"
    f"{'HOPS':>9}"
    f"{'LAST STABLE':>14}"
    f"{'KNEE':>10}"
    f"{'ACCEPT RATE':>14}"
)

print(header)

print("-" * 135)


for r in results:

    knee = (
        f"{r['knee_load']:.3f}"
        if r["knee_load"] is not None
        else "N/A"
    )

    stable = (
        f"{r['last_stable_load']:.3f}"
        if r["last_stable_load"] is not None
        else "N/A"
    )

    accepted = (
        f"{r['last_stable_accepted_rate']:.5f}"
        if r["last_stable_accepted_rate"]
        is not None
        else "N/A"
    )

    print(
        f"{r['name']:<10}"
        f"{r['link']:<10}"
        f"{r['length']:>10.3f}"
        f"{r['analytical_latency']:>10.2f}"
        f"{r['analytical_gain_percent']:>11.2f}"
        f"{r['booksim_low_latency']:>10.2f}"
        f"{r['booksim_gain_percent']:>11.2f}"
        f"{r['booksim_low_hops']:>9.3f}"
        f"{stable:>14}"
        f"{knee:>10}"
        f"{accepted:>14}"
    )