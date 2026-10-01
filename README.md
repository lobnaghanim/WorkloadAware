# Workload-Aware Long-Range Links for Chiplet Networks

This repository extends RapidChiplet with a physically constrained study of workload-aware long-range links in a 16-chiplet, 4×4 mesh. The project asks whether traffic-specific shortcuts provide a better communication-performance tradeoff than the same mesh or workload-independent Fixed shortcuts under equal hardware capability.

The final stored-artifact audit status is **PASS**: 732/732 artifact records valid, zero warnings, and zero failures. See [the audit](results/reproducibility_audit.txt).

## Project objective and contribution

The main contribution is an evaluated link-selection workflow that minimizes workload-average analytical latency while enforcing:

- a maximum requested link count K;
- a total physical wire budget;
- no increase above the original workload's maximum directed-link load;
- valid and available PHY endpoints;
- no PHY reuse, duplicate shortcut, or existing mesh edge; and
- regenerated SPLIF routing after topology changes.

K=1 evaluates all 96 non-mesh chiplet pairs under the project candidate model. K=2 and K=4 greedily extend the current topology and reevaluate feasibility after each selected link. Exact selections are then materialized as RapidChiplet designs and validated with BookSim.

## Architecture and terminology

- **4-PHY reference:** the original mesh architecture, used only to quantify the area and power overhead of providing shortcut capability.
- **8-PHY Mesh:** the no-shortcut, 24-link performance baseline used for every Mesh-vs-Fixed-vs-Workload-Aware comparison.
- **`requested_k`:** the maximum number of shortcuts the optimizer is allowed to select: 1, 2, or 4.
- **`actual_k`:** the number it could actually install after wire, PHY, topology, and load constraints. It may be smaller than `requested_k`.
- **Derived latency knee:** the first tested offered load at which average packet latency is at least twice low-load packet latency. It is not an exact throughput boundary.

The physical wire budgets are **5, 15, 25, and 45 mm**.

## Workloads

Synthetic workloads are `random_uniform`, `transpose`, `permutation`, and `hotspot`. Fixed links are chosen once using the uniform reference; Workload-Aware links are chosen separately for the traffic under test.

The STAGE-derived workloads are discovered from the validated traces/results: `gpt_pipeline`, `gpt_fsdp`, and `moe_expert`. Each contains 16 ranks mapped one-to-one to the 16 chiplets. Abstract collectives are expanded using the validated ring policy and normalized to the canonical project traffic scale.

## Repository structure

| Path | Purpose |
| --- | --- |
| `rapidchiplet.py`, `helpers.py`, `booksim_wrapper.py` | RapidChiplet analysis and BookSim integration |
| `booksim2/` | BookSim source and local build products |
| `project/` | Project generation, optimization, simulation, comparison, analysis, and audit scripts |
| `inputs/` | Chiplets, placement, packaging, traffic, topology, routing, and generated designs |
| `stage/` | STAGE source plus representative generated traces |
| `results/` | Stored selections, simulations, comparisons, final analysis, figures, and tables |
| `docs/` | Final report, executive summary, reproducibility and packaging guides |
| `experiments/` | RapidChiplet experiment definitions, including the base project mesh |

## Requirements

- Python 3 with the packages pinned in [requirements.txt](requirements.txt): Matplotlib 3.8.4, NetworkX 3.3, and NumPy 2.1.1.
- A C++ toolchain and GNU Make (or an equivalent Windows build) for BookSim.
- For regenerating STAGE traces rather than using the stored traces, the packages in [stage/requirements.txt](stage/requirements.txt).

Create an isolated environment and install the core requirements:

```bash
python3 -m venv .venv
python3 -m pip install -r requirements.txt
```

Do not commit `.venv/` or Python cache directories.

## RapidChiplet and BookSim setup

From the repository root, build BookSim:

```bash
make -C booksim2/src
```

RapidChiplet expects `booksim2/src/booksim` on Unix-like systems or `booksim2/src/booksim.exe` on Windows. A single design can be evaluated with:

```bash
python3 rapidchiplet.py -df inputs/designs/<design>.json -rf results/<result>.json -l -t -bs
```

The project runners invoke this form with `subprocess.run(..., check=True)`, validate result completeness, skip completed outputs, and document duplicate-topology reuse where applicable.

## Workload-aware and Fixed policies

```text
Fixed:
uniform reference traffic → choose topology once → freeze across workloads

Workload-Aware:
current workload traffic → choose topology for that workload
```

Both policies use the same requested K, wire budgets, spare-PHY architecture, physical-distance model, SPLIF routing, and original-baseline maximum-load constraint. The intentional difference is the traffic used for topology selection.

## Main results

The most useful entry points are:

- [final_result_summary.csv](results/final_result_summary.csv): final aggregate metrics.
- [final_key_findings.txt](results/final_key_findings.txt): evidence-based narrative findings.
- [all_k_summary.csv](results/all_k_summary.csv): combined synthetic K=1/K=2/K=4 comparison.
- [stage_policy_summary.csv](results/stage_policy_summary.csv): STAGE Mesh/Fixed/Workload-Aware results.
- [multiseed_summary.csv](results/multiseed_summary.csv): robustness across seeds 42–46.
- [physical_cost_summary.csv](results/physical_cost_summary.csv): per-configuration wire, PHY, and efficiency metrics.
- [physical_cost_overhead.csv](results/physical_cost_overhead.csv): 4-PHY→8-PHY area/power capability cost.
- [reproducibility_manifest.csv](results/reproducibility_manifest.csv): artifact-level traceability.

See [RESULTS_GUIDE.md](docs/RESULTS_GUIDE.md) for field-level interpretation.

## Final figures, tables, and report

- Publication figures: [`results/figures/`](results/figures/)
- Condensed tables: [`results/tables/`](results/tables/)
- Complete report: [docs/final_project_report.md](docs/final_project_report.md)
- Executive summary: [docs/final_project_summary.md](docs/final_project_summary.md)
- Current pipeline status: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

## Reproduction

The ordered workflow, commands, prerequisites, and expected outputs are documented in [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md). The pipeline is intentionally not wrapped in one automatic command because RapidChiplet/BookSim runs are expensive and each stage validates its existing outputs before proceeding.

For an inexpensive consistency check of already generated artifacts:

```bash
python3 -m py_compile project/audit_project_results.py
python3 project/audit_project_results.py
```

Repository cleanup candidates are documented without deletion in [docs/CLEANUP_RECOMMENDATIONS.md](docs/CLEANUP_RECOMMENDATIONS.md).
