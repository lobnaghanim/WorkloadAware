# Reproducibility Workflow

This is the ordered project workflow reconstructed from [the validated audit](../results/reproducibility_audit.txt), script dependencies, and stored outputs. Run commands from the repository root unless a section says otherwise. Large BookSim stages can take substantial time; the current packaging step did **not** rerun them.

The 4-PHY design is the hardware-overhead reference. The generated 8-PHY, 24-link, no-shortcut mesh is the fair performance baseline. `requested_k` is an upper bound; `actual_k` is the number of feasible links installed. The reported congestion threshold is the **derived latency knee**, not an exact throughput boundary.

## A. Environment validation

```bash
python3 --version
```

Expected output: a working Python 3 interpreter; no project file is created.

```bash
python3 -m pip install -r requirements.txt
```

Expected output: the core Python environment with the versions in [`requirements.txt`](../requirements.txt).

```bash
make -C booksim2/src
```

Expected output: `booksim2/src/booksim` on Unix-like systems. On Windows, provide/build `booksim2/src/booksim.exe`; `booksim_wrapper.py` selects the platform-specific name.

For fresh STAGE trace generation, also run:

```bash
python3 -m pip install -r stage/requirements.txt
```

Expected output: the STAGE generation dependencies; stored project results are unchanged.

## B. Base 4×4 physical mesh

The committed starting point is `inputs/designs/design_project_mesh_4x4.json`, containing the original 4-PHY reference mesh.

```bash
python3 project/create_physical_baseline.py
```

Expected important outputs:

- `inputs/designs/design_project_physical_mesh_8phy.json`
- `inputs/chiplets/chiplets_project_physical_mesh_8phy.json`
- `inputs/placements/placement_project_physical_mesh_8phy.json`
- `inputs/topologies/topology_project_physical_mesh_8phy.json`
- `inputs/routing_tables/routing_table_project_physical_mesh_8phy.json`

The generated topology must contain 16 chiplets, 24 mesh links, cardinal PHYs 1/3/5/7, and spare PHYs 0/2/4/6.

## C. Synthetic workload generation

```bash
python3 project/generate_fixed_workloads.py
```

Expected important outputs: `inputs/traffic_by_chiplet/traffic_project_<workload>.json` and `inputs/traffic_by_unit/traffic_project_<workload>.json` for `random_uniform`, `transpose`, `permutation`, and `hotspot`.

```bash
python3 project/generate_multiseed_workloads.py
```

Expected important outputs: chiplet- and unit-level `traffic_project_permutation_seed<seed>.json` and `traffic_project_hotspot_seed<seed>.json` for seeds 42, 43, 44, 45, and 46.

## D. K=1 workload-aware optimization

The K=1 selector consumes workload-specific physical candidate caches. Regenerate each cache first:

```bash
python3 project/evaluate_physical_candidates.py random_uniform
```

Expected outputs: `results/physical_candidates_random_uniform.csv` and `results/physical_baseline_random_uniform.json`.

```bash
python3 project/evaluate_physical_candidates.py transpose
```

Expected outputs: `results/physical_candidates_transpose.csv` and `results/physical_baseline_transpose.json`.

```bash
python3 project/evaluate_physical_candidates.py permutation
```

Expected outputs: `results/physical_candidates_permutation.csv` and `results/physical_baseline_permutation.json`.

```bash
python3 project/evaluate_physical_candidates.py hotspot
```

Expected outputs: `results/physical_candidates_hotspot.csv` and `results/physical_baseline_hotspot.json`.

```bash
python3 project/select_k1_all.py
```

Expected output: `results/k1_workload_aware_selections.csv`, containing the exact K=1 workload-aware link and PHY endpoints for all four budgets.

```bash
python3 project/create_k1_all_designs.py
```

Expected outputs: `inputs/designs/design_project_k1_<workload>_baseline.json` plus unique `design_project_k1_<workload>_B<budget>mm.json` designs and their topology/routing files.

```bash
python3 project/run_k1_booksim.py
```

Expected outputs: complete `results/k1_<workload>_baseline.json` and unique `results/k1_<workload>_B<budget>mm.json` RapidChiplet/BookSim results. Duplicate physical topologies are documented as reuse rather than fabricated results.

```bash
python3 project/summarize_k1_all.py
```

Expected output: `results/k1_all_booksim_summary.csv`.

## E. Fixed K=1

```bash
python3 project/select_fixed_k1.py
```

Expected output: `results/k1_fixed_selections.csv`, selected only from the verified uniform reference.

```bash
python3 project/create_fixed_k1_designs.py
```

Expected outputs: workload-specific `inputs/designs/design_project_fixed_k1_<workload>_B<budget>mm.json` designs that share one physical topology/routing per budget.

```bash
python3 project/run_fixed_k1_booksim.py
```

Expected outputs: unique `results/fixed_k1_<workload>_B<budget>mm.json` BookSim results, with duplicate topology points resolved by documented reuse.

## F. K=2 workload-aware and fixed

```bash
python3 project/select_k2_workload_aware.py
```

Expected outputs: `results/k2_workload_aware_selections.csv` and `results/k2_workload_aware_summary.csv`.

```bash
python3 project/select_fixed_k2.py
```

Expected outputs: `results/k2_fixed_selections.csv` and `results/k2_fixed_summary.csv`.

```bash
python3 project/create_k2_workload_aware_designs.py
```

Expected outputs: `inputs/designs/design_project_aware_k2_<workload>_B<budget>mm.json` and corresponding topology/routing files.

```bash
python3 project/create_fixed_k2_designs.py
```

Expected outputs: `inputs/designs/design_project_fixed_k2_<workload>_B<budget>mm.json` and one shared physical topology/routing pair per budget.

```bash
python3 project/run_k2_workload_aware_booksim.py
```

Expected outputs: unique `results/aware_k2_<workload>_B<budget>mm.json` files, with reuse documented for identical workload/topology points.

```bash
python3 project/run_fixed_k2_booksim.py
```

Expected outputs: unique `results/fixed_k2_<workload>_B<budget>mm.json` files.

## G. K=4 workload-aware and fixed

```bash
python3 project/select_k4_workload_aware.py
```

Expected outputs: `results/k4_workload_aware_selections.csv` and `results/k4_workload_aware_summary.csv`.

```bash
python3 project/select_fixed_k4.py
```

Expected outputs: `results/k4_fixed_selections.csv` and `results/k4_fixed_summary.csv`.

```bash
python3 project/create_k4_workload_aware_designs.py
```

Expected outputs: `inputs/designs/design_project_aware_k4_<workload>_B<budget>mm.json` plus exact topology/routing files.

```bash
python3 project/create_fixed_k4_designs.py
```

Expected outputs: `inputs/designs/design_project_fixed_k4_<workload>_B<budget>mm.json` and one physical topology/routing pair per budget.

```bash
python3 project/run_k4_workload_aware_booksim.py
```

Expected outputs: unique `results/aware_k4_<workload>_B<budget>mm.json` files.

```bash
python3 project/run_fixed_k4_booksim.py
```

Expected outputs: unique `results/fixed_k4_<workload>_B<budget>mm.json` files.

## H. Policy comparisons

```bash
python3 project/compare_k1_policies.py
```

Expected output: `results/k1_policy_comparison.csv`.

```bash
python3 project/compare_k2_policies.py
```

Expected output: `results/k2_policy_comparison.csv`.

```bash
python3 project/compare_k4_policies.py
```

Expected output: `results/k4_policy_comparison.csv`.

```bash
python3 project/compare_all_k.py
```

Expected outputs: `results/all_k_policy_comparison.csv` and `results/all_k_summary.csv`.

## I. Multi-seed robustness

```bash
python3 project/run_multiseed_analysis.py
```

Expected outputs: `results/multiseed_results.csv` and `results/multiseed_summary.csv`. The expected seed set is exactly 42–46.

## J. STAGE trace inspection

To regenerate the representative traces, run the following from `stage/`. These commands are copied from `results/stage_trace_inventory.csv`:

```bash
python main.py --output_dir generated/rapidchiplet_representative/gpt_pipeline --output_name trace.%d.et --model_type gpt --dp 2 --tp 2 --pp 4 --sp 1 --ep 1 --weight_sharded false --dmodel 128 --dff 256 --batch 8 --micro_batch 4 --seq 32 --head 8 --kvhead 4 --num_stacks 4
```

Expected output: 16 rank traces under `stage/generated/rapidchiplet_representative/gpt_pipeline/`.

```bash
python main.py --output_dir generated/rapidchiplet_representative/gpt_fsdp --output_name trace.%d.et --model_type gpt --dp 16 --tp 1 --pp 1 --sp 1 --ep 1 --weight_sharded true --dmodel 128 --dff 256 --batch 16 --micro_batch 8 --seq 32 --head 8 --kvhead 4 --num_stacks 2
```

Expected output: 16 rank traces under `stage/generated/rapidchiplet_representative/gpt_fsdp/`.

```bash
python main.py --output_dir generated/rapidchiplet_representative/moe_expert --output_name trace.%d.et --model_type moe --dp 2 --tp 2 --pp 1 --sp 1 --ep 4 --weight_sharded false --dmodel 128 --dff 256 --batch 8 --micro_batch 4 --seq 32 --head 8 --kvhead 4 --num_stacks 2 --experts 8 --kexperts 2
```

Expected output: 16 rank traces under `stage/generated/rapidchiplet_representative/moe_expert/`.

Return to the repository root, then run:

```bash
python3 project/inspect_stage_traces.py
```

Expected outputs: `results/stage_trace_inventory.csv` and `results/stage_communication_summary.csv`.

## K. STAGE → RapidChiplet conversion

```bash
python3 project/convert_stage_to_traffic.py
```

Expected outputs:

- `inputs/traffic_by_chiplet/traffic_stage_<workload>.json`
- `inputs/traffic_by_unit/traffic_stage_<workload>.json`
- `results/stage_mapping_<workload>.csv`
- `results/stage_conversion_validation.csv`

All `conversion_valid` values must be true before continuing.

## L. STAGE optimization and BookSim

```bash
python3 project/run_stage_workload_aware_optimization.py
```

Expected outputs: `results/stage_workload_aware_selections.csv` and `results/stage_workload_aware_summary.csv`.

```bash
python3 project/create_stage_workload_aware_designs.py
```

Expected outputs: `inputs/designs/design_stage_aware_k<k>_<workload>_B<budget>mm.json` and matching topology/routing files.

```bash
python3 project/run_stage_workload_aware_booksim.py
```

Expected outputs: unique `results/stage_aware_k<k>_<workload>_B<budget>mm.json` results.

```bash
python3 project/create_stage_fixed_designs.py
```

Expected outputs: `inputs/designs/design_stage_fixed_k<k>_<workload>_B<budget>mm.json`, with one physical topology/routing pair per K/budget.

```bash
python3 project/run_stage_fixed_booksim.py
```

Expected outputs: unique `results/stage_fixed_k<k>_<workload>_B<budget>mm.json` results.

```bash
python3 project/create_stage_mesh_baselines.py
```

Expected outputs: `inputs/designs/design_stage_mesh_<workload>.json`, all referencing the unchanged 8-PHY 24-link mesh.

```bash
python3 project/run_stage_mesh_booksim.py
```

Expected outputs: `results/stage_mesh_<workload>.json`.

```bash
python3 project/compare_stage_policies.py
```

Expected outputs: `results/stage_policy_comparison.csv` and `results/stage_policy_summary.csv`.

## M. Physical-cost analysis

```bash
python3 project/analyze_physical_costs.py
```

Expected outputs: `results/physical_cost_summary.csv` and `results/physical_cost_overhead.csv`.

## N. Final tables and plots

```bash
python3 project/generate_final_tables.py
```

Expected outputs: the five CSV tables under `results/tables/`.

```bash
python3 project/generate_final_plots.py
```

Expected outputs: 32 matched PNG/PDF figure pairs under `results/figures/`.

## O. Final result analysis

```bash
python3 project/analyze_final_results.py
```

Expected outputs: `results/final_result_summary.csv` and `results/final_key_findings.txt`.

## P. Reproducibility audit

```bash
python3 -m py_compile project/audit_project_results.py
```

Expected output: successful compilation with no terminal error; any generated `__pycache__/` is disposable.

```bash
python3 project/audit_project_results.py
```

Expected outputs: `results/reproducibility_audit.txt` and `results/reproducibility_manifest.csv`. The validated project currently reports `PASS`, 732/732 valid artifact records, zero warnings, and zero failures.
