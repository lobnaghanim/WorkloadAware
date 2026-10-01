#!/usr/bin/env python3
"""Generate final publication-quality figures from completed project CSVs."""

from __future__ import annotations

import csv
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


# The shell's MSYS Python may not carry the project's pinned plotting stack,
# while the normal project Python does.  Preserve the documented `python3 ...`
# entry point by relaunching once with an installed local Python when necessary.
os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(os.environ.get("TEMP", ".")) / "rapidchiplet_matplotlib_cache"),
)
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    if exc.name != "matplotlib" or os.environ.get("RAPIDCHIPLET_PLOT_REEXEC") == "1":
        raise
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = sorted((local_app_data / "Programs" / "Python").glob("Python*/python.exe"), reverse=True)
    candidate = next((path for path in candidates if path.is_file()), None)
    if candidate is None:
        raise SystemExit(
            "matplotlib is unavailable. Install the pinned requirements with: pip install -r requirements.txt"
        ) from exc
    environment = dict(os.environ)
    environment["RAPIDCHIPLET_PLOT_REEXEC"] = "1"
    raise SystemExit(
        subprocess.run([str(candidate), str(Path(__file__).resolve())], env=environment, check=False).returncode
    )


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUTPUT_DIR = RESULTS / "figures"
INPUTS = {
    "synthetic_comparison": RESULTS / "all_k_policy_comparison.csv",
    "synthetic_summary": RESULTS / "all_k_summary.csv",
    "multiseed": RESULTS / "multiseed_summary.csv",
    "stage_comparison": RESULTS / "stage_policy_comparison.csv",
    "stage_summary": RESULTS / "stage_policy_summary.csv",
    "physical_cost": RESULTS / "physical_cost_summary.csv",
    "overhead": RESULTS / "physical_cost_overhead.csv",
}
BUDGETS = (5, 15, 25, 45)
K_VALUES = (1, 2, 4)
POLICIES = ("mesh", "fixed", "workload_aware")
POLICY_LABELS = {"mesh": "Mesh", "fixed": "Fixed", "workload_aware": "Workload-Aware"}
K_MARKERS = {1: "o", 2: "s", 4: "^"}
BUDGET_MARKERS = {5: "o", 15: "s", 25: "^", 45: "D"}
EPS = 1e-9


class PlotError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise PlotError(message)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def numeric(value: Any, description: str, optional: bool = False) -> float | None:
    if optional and (value is None or str(value).strip() == ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        fail(f"Invalid {description}: {value!r} ({exc})")
    if not math.isfinite(converted):
        fail(f"Non-finite {description}")
    return converted


def integer(value: Any, description: str) -> int:
    converted = numeric(value, description)
    result = int(converted)
    if result != converted:
        fail(f"{description} must be an integer")
    return result


def read_csv(name: str, required: set[str]) -> list[dict[str, str]]:
    path = INPUTS[name]
    if not path.is_file():
        fail(f"Missing required input CSV: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                fail(f"{path} lacks columns: {', '.join(missing)}")
            return list(reader)
    except OSError as exc:
        fail(f"Cannot read {path}: {exc}")


def comparison_rows(name: str, family: str) -> list[dict[str, Any]]:
    required = {
        "workload", "budget_mm", "policy", "requested_k", "actual_k",
        "total_wire_length_mm", "booksim_low_latency", "booksim_low_hops",
        "knee_load", "latency_gain_vs_mesh_percent", "aware_latency_gain_vs_fixed_percent",
    }
    rows = []
    for raw in read_csv(name, required):
        policy = raw["policy"]
        budget = integer(raw["budget_mm"], "comparison budget")
        requested_k = integer(raw["requested_k"], "comparison K")
        if policy not in POLICIES or budget not in BUDGETS or requested_k not in K_VALUES:
            fail(f"Unexpected {family} comparison dimensions")
        rows.append(
            {
                "workload_family": family,
                "workload": raw["workload"],
                "budget_mm": budget,
                "policy": policy,
                "requested_k": requested_k,
                "actual_k": integer(raw["actual_k"], "actual_k"),
                "total_wire_length_mm": numeric(raw["total_wire_length_mm"], "wire length"),
                "booksim_low_latency": numeric(raw["booksim_low_latency"], "BookSim latency"),
                "booksim_low_hops": numeric(raw["booksim_low_hops"], "BookSim hops"),
                "knee_load": numeric(raw["knee_load"], "latency knee", optional=True),
                "latency_gain_vs_mesh_percent": numeric(raw["latency_gain_vs_mesh_percent"], "mesh gain"),
                "aware_latency_gain_vs_fixed_percent": numeric(
                    raw["aware_latency_gain_vs_fixed_percent"], "aware-vs-fixed gain", optional=True
                ),
            }
        )
    workloads = {row["workload"] for row in rows}
    expected = len(workloads) * len(BUDGETS) * len(K_VALUES) * len(POLICIES)
    if len(rows) != expected:
        fail(f"{family} comparison has {len(rows)} rows; expected {expected}")
    return rows


def physical_rows() -> list[dict[str, Any]]:
    required = {
        "workload_family", "workload", "budget_mm", "policy", "requested_k", "actual_k",
        "total_wire_length_mm", "booksim_low_latency", "latency_gain_vs_mesh_percent",
    }
    rows = []
    for raw in read_csv("physical_cost", required):
        rows.append(
            {
                "workload_family": raw["workload_family"],
                "workload": raw["workload"],
                "budget_mm": integer(raw["budget_mm"], "physical budget"),
                "policy": raw["policy"],
                "requested_k": integer(raw["requested_k"], "physical K"),
                "actual_k": integer(raw["actual_k"], "physical actual_k"),
                "total_wire_length_mm": numeric(raw["total_wire_length_mm"], "physical wire"),
                "booksim_low_latency": numeric(raw["booksim_low_latency"], "physical latency"),
                "latency_gain_vs_mesh_percent": numeric(raw["latency_gain_vs_mesh_percent"], "physical gain"),
            }
        )
    if len(rows) != 252 or {row["workload_family"] for row in rows} != {"synthetic", "stage"}:
        fail("Physical-cost input must contain 252 distinguishable synthetic/STAGE rows")
    return rows


def validate_summary_inputs(synthetic: list[dict[str, Any]], stage: list[dict[str, Any]]) -> None:
    specifications = (
        ("synthetic_summary", synthetic, {
            "mesh": "mesh_bs_latency", "fixed": "fixed_bs_latency", "workload_aware": "aware_bs_latency"
        }),
        ("stage_summary", stage, {
            "mesh": "mesh_latency", "fixed": "fixed_latency", "workload_aware": "aware_latency"
        }),
    )
    for input_name, comparisons, names in specifications:
        summary = read_csv(input_name, {"workload", "budget_mm", "requested_k", *names.values()})
        indexed = {
            (row["workload"], integer(row["budget_mm"], "summary budget"), integer(row["requested_k"], "summary K")): row
            for row in summary
        }
        for comparison in comparisons:
            key = (comparison["workload"], comparison["budget_mm"], comparison["requested_k"])
            if key not in indexed:
                fail(f"Missing policy summary group {key}")
            expected = numeric(indexed[key][names[comparison["policy"]]], "summary latency")
            if not math.isclose(expected, comparison["booksim_low_latency"], rel_tol=EPS, abs_tol=EPS):
                fail(f"Summary/comparison mismatch for {key} {comparison['policy']}")


def setup_style() -> dict[str, str]:
    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 8,
            "figure.titlesize": 13,
            "savefig.dpi": 220,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "lines.linewidth": 1.8,
            "lines.markersize": 5,
        }
    )
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    return {policy: colors[index] for index, policy in enumerate(POLICIES)}


def save_figure(fig: Any, basename: str, generated: list[str]) -> None:
    fig.tight_layout()
    for extension in ("png", "pdf"):
        path = OUTPUT_DIR / f"{basename}.{extension}"
        try:
            fig.savefig(path, bbox_inches="tight")
        except OSError as exc:
            fail(f"Cannot save figure {path}: {exc}")
        if not path.is_file() or path.stat().st_size == 0:
            fail(f"Figure was not saved successfully: {path}")
    plt.close(fig)
    generated.append(basename)


def subset(rows: list[dict[str, Any]], **conditions: Any) -> list[dict[str, Any]]:
    return [row for row in rows if all(row[field] == value for field, value in conditions.items())]


def plot_policy_vs_budget(
    rows: list[dict[str, Any]], workload: str, metric: str, ylabel: str,
    title: str, basename: str, colors: dict[str, str], generated: list[str],
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12.0, 3.7), sharey=True)
    for axis, requested_k in zip(axes, K_VALUES):
        for policy in POLICIES:
            group = sorted(subset(rows, workload=workload, requested_k=requested_k, policy=policy), key=lambda r: r["budget_mm"])
            values = [math.nan if row[metric] is None else row[metric] for row in group]
            axis.plot(BUDGETS, values, marker="o", color=colors[policy], label=POLICY_LABELS[policy])
        axis.set_title(f"K={requested_k}")
        axis.set_xlabel("Wire budget (mm)")
        axis.set_xticks(BUDGETS)
    axes[0].set_ylabel(ylabel)
    axes[-1].legend(loc="best")
    fig.suptitle(title)
    save_figure(fig, basename, generated)


def plot_aware_advantage(
    rows: list[dict[str, Any]], workload: str, family_label: str,
    colors: dict[str, str], generated: list[str], basename: str,
) -> None:
    fig, axis = plt.subplots(figsize=(6.5, 4.3))
    for requested_k in K_VALUES:
        group = sorted(subset(rows, workload=workload, requested_k=requested_k, policy="workload_aware"), key=lambda r: r["budget_mm"])
        axis.plot(
            BUDGETS, [row["aware_latency_gain_vs_fixed_percent"] for row in group],
            marker=K_MARKERS[requested_k], label=f"K={requested_k}",
        )
    axis.axhline(0.0, linewidth=0.8, color="black")
    axis.set_xticks(BUDGETS)
    axis.set_xlabel("Wire budget (mm)")
    axis.set_ylabel("Workload-Aware gain vs Fixed (%)")
    axis.set_title(f"{family_label}: {workload}")
    axis.legend()
    save_figure(fig, basename, generated)


def plot_latency_vs_k(
    rows: list[dict[str, Any]], workload: str, colors: dict[str, str], generated: list[str],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.5), sharex=True, sharey=True)
    for axis, budget in zip(axes.flat, BUDGETS):
        for policy in ("fixed", "workload_aware"):
            group = sorted(subset(rows, workload=workload, budget_mm=budget, policy=policy), key=lambda r: r["requested_k"])
            axis.plot(K_VALUES, [row["booksim_low_latency"] for row in group], marker="o", color=colors[policy], label=POLICY_LABELS[policy])
        axis.set_title(f"Budget = {budget} mm")
        axis.set_xticks(K_VALUES, [f"K={value}" for value in K_VALUES])
    axes[1, 0].set_xlabel("Requested K")
    axes[1, 1].set_xlabel("Requested K")
    axes[0, 0].set_ylabel("BookSim low-load latency (cycles)")
    axes[1, 0].set_ylabel("BookSim low-load latency (cycles)")
    axes[0, 1].legend()
    fig.suptitle(f"Latency vs requested K: {workload}")
    save_figure(fig, f"synthetic_latency_vs_k_{workload}", generated)


def plot_scatters(
    physical: list[dict[str, Any]], colors: dict[str, str], generated: list[str],
) -> None:
    synthetic = [row for row in physical if row["workload_family"] == "synthetic" and row["policy"] != "mesh"]
    specifications = (
        ("booksim_low_latency", "BookSim low-load latency (cycles)", "synthetic_latency_vs_physical_wire"),
        ("latency_gain_vs_mesh_percent", "Latency gain vs Mesh (%)", "synthetic_latency_gain_vs_physical_wire"),
    )
    for metric, ylabel, basename in specifications:
        fig, axis = plt.subplots(figsize=(7.4, 5.0))
        for policy in ("fixed", "workload_aware"):
            for requested_k in K_VALUES:
                group = subset(synthetic, policy=policy, requested_k=requested_k)
                axis.scatter(
                    [row["total_wire_length_mm"] for row in group],
                    [row[metric] for row in group],
                    color=colors[policy], marker=K_MARKERS[requested_k], alpha=0.75,
                    label=f"{POLICY_LABELS[policy]}, K={requested_k}",
                )
        axis.set_xlabel("Used shortcut wire length (mm)")
        axis.set_ylabel(ylabel)
        axis.set_title("Synthetic workloads: measured physical tradeoff")
        axis.legend(ncol=2)
        save_figure(fig, basename, generated)


def plot_actual_k(
    physical: list[dict[str, Any]], family: str, colors: dict[str, str], generated: list[str],
) -> None:
    rows = [row for row in physical if row["workload_family"] == family and row["policy"] != "mesh"]
    workloads = sorted({row["workload"] for row in rows})
    columns = 2
    row_count = math.ceil(len(workloads) / columns)
    fig, axes = plt.subplots(row_count, columns, figsize=(9.0, 3.4 * row_count), sharex=True, sharey=True)
    axes_flat = list(axes.flat) if hasattr(axes, "flat") else [axes]
    for axis, workload in zip(axes_flat, workloads):
        for policy in ("fixed", "workload_aware"):
            for budget in BUDGETS:
                group = sorted(subset(rows, workload=workload, policy=policy, budget_mm=budget), key=lambda r: r["requested_k"])
                axis.plot(
                    K_VALUES, [row["actual_k"] for row in group],
                    color=colors[policy], marker=BUDGET_MARKERS[budget], alpha=0.8,
                    label=f"{POLICY_LABELS[policy]}, {budget} mm",
                )
        axis.plot(K_VALUES, K_VALUES, linestyle="--", linewidth=1.0, color="black", label="actual = requested")
        axis.set_title(workload)
        axis.set_xticks(K_VALUES, [f"K={value}" for value in K_VALUES])
        axis.set_yticks(range(0, 5))
        axis.set_xlabel("Requested K")
        axis.set_ylabel("Actual selected links")
    for axis in axes_flat[len(workloads):]:
        axis.set_visible(False)
    handles, labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.02))
    fig.suptitle(f"Actual K vs requested K — {family}", y=1.06)
    save_figure(fig, f"actual_k_vs_requested_k_{family}", generated)


def plot_multiseed(generated: list[str]) -> None:
    required = {
        "workload", "budget_mm", "requested_k", "mean_latency_gain_percent",
        "std_latency_gain_percent",
    }
    raw_rows = read_csv("multiseed", required)
    rows = [
        {
            "workload": row["workload"],
            "budget_mm": integer(row["budget_mm"], "multiseed budget"),
            "requested_k": integer(row["requested_k"], "multiseed K"),
            "mean": numeric(row["mean_latency_gain_percent"], "multiseed mean"),
            "std": numeric(row["std_latency_gain_percent"], "multiseed std"),
        }
        for row in raw_rows
    ]
    workloads = ("permutation", "hotspot")
    if len(rows) != len(workloads) * len(BUDGETS) * len(K_VALUES):
        fail("Multiseed summary matrix is incomplete")
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.2), sharey=True)
    for axis, workload in zip(axes, workloads):
        for requested_k in K_VALUES:
            group = sorted(
                [row for row in rows if row["workload"] == workload and row["requested_k"] == requested_k],
                key=lambda row: row["budget_mm"],
            )
            axis.errorbar(
                BUDGETS, [row["mean"] for row in group], yerr=[row["std"] for row in group],
                marker=K_MARKERS[requested_k], capsize=3, label=f"K={requested_k}",
            )
        axis.set_title(workload)
        axis.set_xlabel("Wire budget (mm)")
        axis.set_xticks(BUDGETS)
        axis.legend()
    axes[0].set_ylabel("Mean analytical latency gain (%)\n(error bars: seed standard deviation)")
    fig.suptitle("Multi-seed robustness")
    save_figure(fig, "multiseed_latency_gain_robustness", generated)


def plot_overhead(generated: list[str]) -> None:
    rows = read_csv(
        "overhead", {"record_type", "architecture", "phy_count", "chiplet_area_mm2",
                     "chiplet_power_w", "area_overhead_percent", "power_overhead_percent"}
    )
    architectures = {row["architecture"]: row for row in rows if row["record_type"] == "architecture"}
    overhead = next((row for row in rows if row["record_type"] == "overhead"), None)
    if set(architectures) != {"4PHY", "8PHY"} or overhead is None:
        fail("Physical overhead input lacks required architecture records")
    metrics = (
        ("phy_count", "PHY count", None),
        ("chiplet_area_mm2", "Chiplet area (mm²)", "area_overhead_percent"),
        ("chiplet_power_w", "Chiplet power (W)", "power_overhead_percent"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.8))
    for axis, (field, ylabel, overhead_field) in zip(axes, metrics):
        values = [numeric(architectures[label][field], field, optional=True) for label in ("4PHY", "8PHY")]
        if any(value is None for value in values):
            axis.text(0.5, 0.5, "Metric unavailable", ha="center", va="center")
            axis.set_xticks([])
        else:
            axis.bar(("4PHY", "8PHY"), values)
        axis.set_ylabel(ylabel)
        if overhead_field:
            value = numeric(overhead[overhead_field], overhead_field, optional=True)
            axis.set_title("Unavailable" if value is None else f"Overhead: {value:.2f}%")
    fig.suptitle("Physical capability overhead (project chiplet data)")
    save_figure(fig, "physical_capability_overhead_4phy_vs_8phy", generated)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for path in INPUTS.values():
        if not path.is_file():
            fail(f"Missing required CSV input: {path}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    colors = setup_style()
    synthetic = comparison_rows("synthetic_comparison", "synthetic")
    stage = comparison_rows("stage_comparison", "stage")
    physical = physical_rows()
    validate_summary_inputs(synthetic, stage)
    generated: list[str] = []

    synthetic_workloads = sorted({row["workload"] for row in synthetic})
    for workload in synthetic_workloads:
        plot_policy_vs_budget(
            synthetic, workload, "booksim_low_latency", "BookSim low-load latency (cycles)",
            f"Synthetic latency vs wire budget: {workload}",
            f"synthetic_latency_vs_budget_{workload}", colors, generated,
        )
        plot_aware_advantage(
            synthetic, workload, "Synthetic", colors, generated,
            f"synthetic_aware_gain_vs_fixed_{workload}",
        )
        plot_latency_vs_k(synthetic, workload, colors, generated)
        plot_policy_vs_budget(
            synthetic, workload, "booksim_low_hops", "BookSim average hops",
            f"Synthetic average hops vs budget and K: {workload}",
            f"synthetic_average_hops_{workload}", colors, generated,
        )
        plot_policy_vs_budget(
            synthetic, workload, "knee_load", "Derived latency knee (offered load)",
            f"Synthetic derived latency knee: {workload}",
            f"synthetic_derived_latency_knee_{workload}", colors, generated,
        )

    plot_scatters(physical, colors, generated)
    plot_actual_k(physical, "synthetic", colors, generated)
    plot_actual_k(physical, "stage", colors, generated)
    plot_multiseed(generated)

    stage_workloads = sorted({row["workload"] for row in stage})
    for workload in stage_workloads:
        plot_policy_vs_budget(
            stage, workload, "booksim_low_latency", "BookSim low-load latency (cycles)",
            f"STAGE latency comparison: {workload}",
            f"stage_latency_comparison_{workload}", colors, generated,
        )
        plot_aware_advantage(
            stage, workload, "STAGE", colors, generated,
            f"stage_aware_gain_vs_fixed_{workload}",
        )
    plot_overhead(generated)

    expected_count = 5 * len(synthetic_workloads) + 2 + 2 + 1 + 2 * len(stage_workloads) + 1
    if len(generated) != expected_count or len(set(generated)) != expected_count:
        fail(f"Expected {expected_count} unique figures; generated {len(generated)}")
    files = list(OUTPUT_DIR.glob("*.png")) + list(OUTPUT_DIR.glob("*.pdf"))
    expected_files = {OUTPUT_DIR / f"{name}.{extension}" for name in generated for extension in ("png", "pdf")}
    if set(files) != expected_files:
        fail(f"Figure output set mismatch: files={len(files)}, expected={len(expected_files)}")
    expected_tables = {
        RESULTS / "tables" / name
        for name in (
            "synthetic_policy_summary.csv",
            "stage_policy_summary_final.csv",
            "selected_links_summary.csv",
            "physical_overhead_summary.csv",
            "multiseed_robustness_final.csv",
        )
    }
    if any(not path.is_file() or path.stat().st_size == 0 for path in expected_tables):
        fail("Final publication tables are missing; run generate_final_tables.py first")
    print("FINAL PROJECT PLOTS COMPLETE")
    print(f"Figures generated: {len(generated)} ({len(expected_files)} PNG/PDF files)")
    print(f"Output directory: {relative(OUTPUT_DIR)}")
    print("FINAL PROJECT PLOTS AND TABLES COMPLETE")
    print(f"Tables generated: {len(expected_tables)}")
    print(f"Tables directory: {relative(RESULTS / 'tables')}")


if __name__ == "__main__":
    try:
        main()
    except (PlotError, OSError, KeyError, ValueError, ZeroDivisionError) as exc:
        raise SystemExit(f"Final plot generation failed: {exc}") from exc
