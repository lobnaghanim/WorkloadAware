# Results Guide

This guide identifies the final result entry points. All paths are relative to the repository root, and all listed files were validated by [the reproducibility audit](../results/reproducibility_audit.txt).

## Reading the metrics

- `requested_k` is the maximum shortcut count requested from the policy: 1, 2, or 4.
- `actual_k` is the number of links actually installed after wire, PHY, edge, and maximum-load constraints. Never infer `actual_k` from the filename alone.
- Mesh rows use the fair 8-PHY, 24-link no-shortcut topology and have `actual_k = 0`.
- Fixed uses one uniform-reference topology per K/budget across workloads; Workload-Aware conditions selection on the workload.
- `booksim_low_*` values come from the smallest complete numeric offered-load point.
- The **derived latency knee** is the first tested offered load where average packet latency is at least twice low-load latency. Blank knees mean no qualifying tested point; they are not zero and are not exact throughput boundaries.
- Wire values are physical Manhattan lengths in millimeters.

## Policy-comparison files

### [`results/k1_policy_comparison.csv`](../results/k1_policy_comparison.csv)

Mesh, Fixed K=1, and Workload-Aware K=1 rows for every synthetic workload and budget. It contains selected link/endpoints, wire, analytical metrics, low-load BookSim latency/hops, accepted rate, last stable point, derived knee, gains, source result, and duplicate-result reuse status. This retains the historical K=1 schema; Mesh rows carry no shortcut.

### [`results/k2_policy_comparison.csv`](../results/k2_policy_comparison.csv)

The equivalent synthetic Mesh/Fixed/Workload-Aware comparison for requested K=2. `actual_k` and `link_1`/`link_2` show whether the second greedy step succeeded.

### [`results/k4_policy_comparison.csv`](../results/k4_policy_comparison.csv)

The equivalent synthetic comparison for requested K=4, with up to four selected-link fields. Empty/`none` link fields beyond `actual_k` are intentional early stopping.

### [`results/all_k_policy_comparison.csv`](../results/all_k_policy_comparison.csv)

The normalized union of the three synthetic policy-comparison datasets. Use this when a single row-level schema across K=1/K=2/K=4 is needed.

### [`results/all_k_summary.csv`](../results/all_k_summary.csv)

One synthetic row per workload, budget, and requested K. It places Mesh, Fixed, and Workload-Aware latency/hops/knee metrics side by side and includes K1→K2 and K2→K4 changes in latency, wire, and installed links. This is the primary file for combined-K analysis.

## Robustness and STAGE files

### [`results/multiseed_summary.csv`](../results/multiseed_summary.csv)

Aggregates the permutation and hotspot experiments over seeds 42–46 for every budget and requested K. It reports mean/std/min/max latency gain, `actual_k` and wire statistics, number of unique selected topologies, and the most-common topology frequency. Use [`multiseed_results.csv`](../results/multiseed_results.csv) for individual seed rows.

### [`results/stage_policy_summary.csv`](../results/stage_policy_summary.csv)

One row per STAGE workload, budget, and requested K comparing Mesh, Fixed, and Workload-Aware. It contains actual link counts, exact link fields, wire, analytical latency/throughput/load, low-load BookSim latency/hops, derived knees, and policy gains. The row-level source is [`stage_policy_comparison.csv`](../results/stage_policy_comparison.csv).

STAGE names, mapping, normalization, collective expansion, and volume validation are recorded in [`stage_conversion_validation.csv`](../results/stage_conversion_validation.csv) and [`stage_trace_inventory.csv`](../results/stage_trace_inventory.csv).

## Physical-cost files

### [`results/physical_cost_summary.csv`](../results/physical_cost_summary.csv)

Per logical Mesh/Fixed/Workload-Aware configuration across synthetic and STAGE families. It combines `requested_k`, `actual_k`, total/average/min/max shortcut wire, shortcut link latency, spare-PHY endpoint use, analytical and BookSim metrics, gain per millimeter, hop gain per millimeter, traffic-wire cost, and incremental change from the previous requested K.

Capability overhead and shortcut usage are separate: Mesh rows have no usage cost, while the eight-PHY architecture still has the capability cost below.

### [`results/physical_cost_overhead.csv`](../results/physical_cost_overhead.csv)

The hardware-capability comparison between the original 4-PHY reference and the shortcut-capable 8-PHY architecture. It records chiplet dimensions, area, power, PHY count, placement footprint, and the measured 4-PHY→8-PHY overhead. The 4-PHY design is not the performance baseline; fair performance uses the 8-PHY no-shortcut Mesh.

## Final analysis and audit

### [`results/final_result_summary.csv`](../results/final_result_summary.csv)

Final arithmetic aggregates by family, workload, K, and budget, plus topology-adaptation, multi-seed, and overhead summary rows. This is the best compact machine-readable source for headline values.

### [`results/final_key_findings.txt`](../results/final_key_findings.txt)

The validated narrative findings: overall win counts and means, workload-specific results, K/budget diminishing returns, derived-knee behavior, seed robustness, STAGE generalization, physical cost, limitations, and conclusion. The stated ±0.1% equality/negligibility threshold is descriptive, not a significance test.

### [`results/reproducibility_audit.txt`](../results/reproducibility_audit.txt)

Human-readable audit with 12 sections covering inputs, selections, wire/PHY constraints, design topology, BookSim completeness and reuse, derived metrics, STAGE conversion, missing artifacts, and reproduction commands. Current status: **PASS**.

### [`results/reproducibility_manifest.csv`](../results/reproducibility_manifest.csv)

Artifact-level traceability with artifact type, path, existence/validity flags, source, workload, K, budget, and notes.

## Figures and tables

### [`results/figures/`](../results/figures/)

Publication-oriented plots in matched PNG and PDF forms. The directory contains 32 unique figures (64 files): synthetic latency versus budget/K, average hops, derived knees, gain versus Fixed, physical-wire tradeoffs, STAGE latency and gain plots, requested-vs-actual K, multi-seed robustness, and 4-PHY/8-PHY capability overhead. The final report embeds a representative subset.

### [`results/tables/`](../results/tables/)

Five condensed CSV tables:

- `synthetic_policy_summary.csv`: compact synthetic policy metrics.
- `stage_policy_summary_final.csv`: compact STAGE policy metrics.
- `selected_links_summary.csv`: all logical selected link sets.
- `physical_overhead_summary.csv`: one-row 4-PHY/8-PHY comparison.
- `multiseed_robustness_final.csv`: compact robustness table.

## Reports

- [`docs/report/report.tex`](report/report.tex): LaTeX source of the full report (methodology, results, discussion), with figures in `docs/report/figures/`.
- [`docs/REPRODUCIBILITY.md`](REPRODUCIBILITY.md): ordered regeneration workflow and expected outputs.
