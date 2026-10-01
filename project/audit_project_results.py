#!/usr/bin/env python3
"""Audit project artifacts for reproducibility and internal consistency."""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_DIR))

import helpers as hlp
import analyze_physical_costs as physical_inputs


RESULTS = ROOT / "results"
REPORT_PATH = RESULTS / "reproducibility_audit.txt"
MANIFEST_PATH = RESULTS / "reproducibility_manifest.csv"
BUDGETS = (5, 15, 25, 45)
K_VALUES = (1, 2, 4)
POLICIES = ("mesh", "fixed", "workload_aware")
SYNTHETIC_WORKLOADS = ("random_uniform", "transpose", "permutation", "hotspot")
SEEDS = (42, 43, 44, 45, 46)
EPS = 1e-9
MANIFEST_FIELDS = (
    "artifact_type", "path", "exists", "valid", "source", "workload",
    "requested_k", "budget_mm", "notes",
)
FINAL_DATASETS = (
    "k1_policy_comparison.csv", "k2_policy_comparison.csv", "k4_policy_comparison.csv",
    "all_k_policy_comparison.csv", "all_k_summary.csv", "multiseed_results.csv",
    "multiseed_summary.csv", "stage_policy_comparison.csv", "stage_policy_summary.csv",
    "physical_cost_summary.csv", "physical_cost_overhead.csv", "final_result_summary.csv",
    "final_key_findings.txt",
)
SELECTION_FILES = (
    "k1_workload_aware_selections.csv", "k2_workload_aware_selections.csv",
    "k2_workload_aware_summary.csv", "k4_workload_aware_selections.csv",
    "k4_workload_aware_summary.csv", "k1_fixed_selections.csv",
    "k2_fixed_selections.csv", "k2_fixed_summary.csv", "k4_fixed_selections.csv",
    "k4_fixed_summary.csv", "stage_workload_aware_selections.csv",
    "stage_workload_aware_summary.csv",
)


class AuditError(RuntimeError):
    pass


class Audit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []
        self.counters: defaultdict[str, int] = defaultdict(int)

    def failure(self, message: str) -> None:
        self.failures.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def note(self, message: str) -> None:
        self.info.append(message)

    def record(
        self, artifact_type: str, path: Path, valid: bool, source: str = "",
        workload: str = "", requested_k: int | str = "", budget: int | str = "",
        notes: str = "",
    ) -> None:
        try:
            shown_path = path.relative_to(ROOT).as_posix()
        except ValueError:
            shown_path = str(path)
        self.records.append(
            {
                "artifact_type": artifact_type,
                "path": shown_path,
                "exists": path.exists(),
                "valid": bool(valid),
                "source": source,
                "workload": workload,
                "requested_k": requested_k,
                "budget_mm": budget,
                "notes": notes,
            }
        )


def read_csv(path: Path, description: str, required: set[str] | None = None) -> list[dict[str, str]]:
    if not path.is_file():
        raise AuditError(f"Missing {description}: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = sorted((required or set()) - fields)
            if missing:
                raise AuditError(f"{description} lacks columns: {', '.join(missing)}")
            rows = list(reader)
    except OSError as exc:
        raise AuditError(f"Cannot read {description} {path}: {exc}")
    if not rows:
        raise AuditError(f"Empty {description}: {path}")
    return rows


def number(value: Any, description: str, optional: bool = False) -> float | None:
    if optional and (value is None or str(value).strip() == ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise AuditError(f"Invalid {description}: {value!r} ({exc})")
    if not math.isfinite(converted):
        raise AuditError(f"Non-finite {description}")
    return converted


def integer(value: Any, description: str) -> int:
    converted = number(value, description)
    result = int(converted)
    if result != converted:
        raise AuditError(f"{description} must be an integer")
    return result


def boolean(value: Any, description: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false"}:
        raise AuditError(f"Invalid {description}: {value!r}")
    return normalized == "true"


def mean(values: list[float]) -> float:
    if not values:
        raise AuditError("Cannot average an empty set")
    return sum(values) / len(values)


def validate_required_datasets(audit: Audit) -> None:
    for filename in FINAL_DATASETS:
        path = RESULTS / filename
        try:
            if path.suffix == ".csv":
                read_csv(path, filename)
            else:
                if not path.is_file() or not path.read_text(encoding="utf-8").strip():
                    raise AuditError(f"Missing or empty {filename}")
            valid = True
        except (AuditError, OSError, UnicodeDecodeError) as exc:
            valid = False
            audit.failure(str(exc))
        audit.record("final_dataset", path, valid, source="completed analysis pipeline")

    tables = sorted((RESULTS / "tables").glob("*.csv"))
    expected_tables = {
        "synthetic_policy_summary.csv", "stage_policy_summary_final.csv",
        "selected_links_summary.csv", "physical_overhead_summary.csv",
        "multiseed_robustness_final.csv",
    }
    if {path.name for path in tables} != expected_tables:
        audit.failure("Final table set does not match the required five tables")
    for path in tables:
        try:
            read_csv(path, f"final table {path.name}")
            valid = True
        except AuditError as exc:
            valid = False
            audit.failure(str(exc))
        audit.record("final_table", path, valid, source="project/generate_final_tables.py")

    figures_dir = RESULTS / "figures"
    pngs = sorted(figures_dir.glob("*.png"))
    pdfs = sorted(figures_dir.glob("*.pdf"))
    if len(pngs) != 32 or len(pdfs) != 32 or {p.stem for p in pngs} != {p.stem for p in pdfs}:
        audit.failure(f"Final figure set mismatch: PNG={len(pngs)}, PDF={len(pdfs)}")
    for path in (*pngs, *pdfs):
        valid = path.is_file() and path.stat().st_size > 0
        if valid:
            try:
                signature = path.read_bytes()[:8]
                valid = signature.startswith(b"\x89PNG") if path.suffix == ".png" else signature.startswith(b"%PDF")
            except OSError:
                valid = False
        if not valid:
            audit.failure(f"Invalid final figure: {path}")
        audit.record("final_figure", path, valid, source="project/generate_final_plots.py")
    audit.counters["final_tables"] = len(tables)
    audit.counters["final_figures"] = len(pngs)


def load_baseline(audit: Audit) -> dict[str, Any] | None:
    path = ROOT / "inputs" / "designs" / "design_project_physical_mesh_8phy.json"
    try:
        design = hlp.read_json(str(path))
        chiplets = hlp.read_json(str(ROOT / design["chiplets"]))
        placement = hlp.read_json(str(ROOT / design["placement"]))
        topology = hlp.read_json(str(ROOT / design["topology"]))
        valid = len(placement["chiplets"]) == 16 and len(topology) == 24
        if not valid:
            raise AuditError("8-PHY baseline must contain 16 chiplets and 24 links")
        mesh_pairs = set()
        occupied = set()
        for link in topology:
            ep1, ep2 = link["ep1"], link["ep2"]
            pair = tuple(sorted((int(ep1["outer_id"]), int(ep2["outer_id"]))))
            if pair in mesh_pairs:
                raise AuditError(f"Duplicate mesh edge {pair}")
            mesh_pairs.add(pair)
            occupied.update(
                ((int(ep1["outer_id"]), int(ep1["inner_id"])),
                 (int(ep2["outer_id"]), int(ep2["inner_id"])))
            )
        if len(mesh_pairs) != 24:
            raise AuditError("Baseline does not have 24 unique mesh edges")
        for chiplet_id, descriptor in enumerate(placement["chiplets"]):
            if len(chiplets[descriptor["name"]]["phys"]) != 8:
                raise AuditError(f"Baseline chiplet {chiplet_id} is not 8-PHY")
        context = {
            "design": design, "chiplets": chiplets, "placement": placement,
            "topology": topology, "mesh_pairs": mesh_pairs, "occupied": occupied,
        }
    except (OSError, KeyError, ValueError, AuditError) as exc:
        audit.failure(f"Baseline validation failed: {exc}")
        valid = False
        context = None
    audit.record("baseline_design", path, valid, source="project/create_physical_baseline.py")
    return context


def validate_selection_files(audit: Audit, baseline: dict[str, Any] | None) -> tuple[dict[Any, Any], dict[Any, Any]]:
    for filename in SELECTION_FILES:
        path = RESULTS / filename
        try:
            read_csv(path, filename)
            valid = True
        except AuditError as exc:
            valid = False
            audit.failure(str(exc))
        audit.record("optimizer_selection", path, valid, source="stored optimizer output")
    if baseline is None:
        return {}, {}
    try:
        selections, optimizer_metrics = physical_inputs.selection_inputs()
    except Exception as exc:
        audit.failure(f"Cannot load normalized stored selections: {exc}")
        return {}, {}

    expected_keys = 168
    if len(selections) != expected_keys:
        audit.failure(f"Expected {expected_keys} normalized selection configurations; found {len(selections)}")
    for key, raw_steps in selections.items():
        family, workload, requested_k, budget, policy = key
        steps = sorted(raw_steps, key=lambda row: row["step"])
        endpoints = set()
        pairs = set()
        total = 0.0
        issues = []
        if len(steps) > requested_k:
            issues.append(f"actual_k={len(steps)} exceeds requested_k={requested_k}")
        for step in steps:
            u, v = int(step["u"]), int(step["v"])
            phy_u, phy_v = int(step["phy_u"]), int(step["phy_v"])
            pair = tuple(sorted((u, v)))
            endpoint_pair = (
                (u, phy_u),
                (v, phy_v),
            )
            for chiplet_id, phy_id in endpoint_pair:
                if not 0 <= chiplet_id < len(baseline["placement"]["chiplets"]):
                    issues.append(f"invalid chiplet ID {chiplet_id}")
                    continue
                chiplet_name = baseline["placement"]["chiplets"][chiplet_id]["name"]
                if not 0 <= phy_id < len(baseline["chiplets"][chiplet_name]["phys"]):
                    issues.append(f"invalid PHY ID {chiplet_id}:{phy_id}")
            if pair in baseline["mesh_pairs"]:
                issues.append(f"shortcut {pair} is an original mesh edge")
            if pair in pairs:
                issues.append(f"duplicate shortcut {pair}")
            if any(endpoint in endpoints or endpoint in baseline["occupied"] for endpoint in endpoint_pair):
                issues.append(f"PHY endpoint reuse/mesh collision {endpoint_pair}")
            pairs.add(pair)
            endpoints.update(endpoint_pair)
            total += float(step["length"])
        if total > budget + EPS:
            issues.append(f"wire={total} exceeds budget={budget}")
        valid = not issues
        if issues:
            audit.failure(f"Selection {key}: {'; '.join(issues)}")
        audit.record(
            "logical_selection", RESULTS / "physical_cost_summary.csv", valid,
            source="normalized exact selection CSV rows", workload=f"{family}:{workload}",
            requested_k=requested_k, budget=budget,
            notes=f"policy={policy}; actual_k={len(steps)}; wire={total:.12f} mm",
        )
    audit.counters["selection_configs"] = len(selections)

    # Check every stored step-level maximum-load constraint for which both
    # post-step and baseline values were recorded.
    constraint_specs = (
        ("k1_workload_aware_selections.csv", "max_link_load", "baseline_max_load"),
        ("k2_workload_aware_selections.csv", "max_link_load_after_step", "baseline_max_link_load"),
        ("k4_workload_aware_selections.csv", "max_link_load_after_step", "baseline_max_link_load"),
        ("stage_workload_aware_selections.csv", "max_link_load_after_step", "baseline_max_link_load"),
        ("k1_fixed_selections.csv", "reference_max_link_load", "reference_baseline_max_link_load"),
        ("k2_fixed_selections.csv", "reference_max_link_load_after_step", "reference_baseline_max_link_load"),
        ("k4_fixed_selections.csv", "reference_max_link_load_after_step", "reference_baseline_max_link_load"),
    )
    checked = 0
    for filename, final_field, baseline_field in constraint_specs:
        rows = read_csv(RESULTS / filename, filename, {final_field, baseline_field})
        for row_number, row in enumerate(rows, start=2):
            checked += 1
            if number(row[final_field], final_field) > number(row[baseline_field], baseline_field) + EPS:
                audit.failure(f"Max-load constraint violated in {filename} row {row_number}")
    audit.counters["max_load_checks"] = checked

    # Greedy-prefix consistency: exact chiplet and PHY endpoint trajectory.
    prefix_mismatches = []
    for family in ("synthetic", "stage"):
        workloads = sorted({key[1] for key in selections if key[0] == family})
        for workload in workloads:
            for budget in BUDGETS:
                for policy in ("fixed", "workload_aware"):
                    def signature(k: int) -> list[tuple[int, int, int, int]]:
                        return [
                            (int(row["u"]), int(row["v"]), int(row["phy_u"]), int(row["phy_v"]))
                            for row in sorted(selections[(family, workload, k, budget, policy)], key=lambda item: item["step"])
                        ]
                    k1, k2, k4 = signature(1), signature(2), signature(4)
                    if k2[:len(k1)] != k1 or k4[:len(k2)] != k2:
                        prefix_mismatches.append((family, workload, budget, policy, k1, k2, k4))
    for mismatch in prefix_mismatches:
        audit.failure(f"Greedy-prefix mismatch: {mismatch}")
    audit.counters["prefix_checks"] = 2 * (4 + 3) * len(BUDGETS) * 2
    audit.counters["prefix_mismatches"] = len(prefix_mismatches)
    return selections, optimizer_metrics


def parse_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuditError(f"Missing BookSim result: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"Cannot parse BookSim result {path}: {exc}")
    for section in ("latency", "throughput", "booksim_simulation"):
        if not isinstance(data.get(section), dict):
            raise AuditError(f"Result {path} lacks valid {section}")
    analytical_latency = number(data["latency"].get("avg"), f"latency/avg in {path}")
    analytical_throughput = number(
        data["throughput"].get("aggregate_throughput"), f"throughput in {path}"
    )
    points = []
    for raw_load, point in data["booksim_simulation"].items():
        try:
            load = float(raw_load)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(load):
            raise AuditError(f"Non-finite offered load in {path}")
        if not isinstance(point, dict):
            continue
        try:
            latency = number(point["packet_latency"]["avg"], f"packet latency in {path}")
            hops = number(point["hops"]["avg"], f"hops in {path}")
            accepted = number(point["accepted_packet_rate"]["avg"], f"accepted rate in {path}")
        except (KeyError, TypeError, AuditError):
            continue
        points.append((load, latency, hops, accepted))
    if not points:
        raise AuditError(f"No complete numeric offered-load point in {path}")
    points.sort(key=lambda item: item[0])
    low = points[0]
    knee_index = next((index for index, point in enumerate(points) if point[1] >= 2.0 * low[1]), None)
    if knee_index == 0:
        raise AuditError(f"Low-load point is its own knee in {path}")
    stable = points[-1] if knee_index is None else points[knee_index - 1]
    knee = None if knee_index is None else points[knee_index]
    return {
        "analytical_latency": analytical_latency,
        "analytical_throughput": analytical_throughput,
        "booksim_low_load": low[0], "booksim_low_latency": low[1],
        "booksim_low_hops": low[2], "booksim_low_accepted_rate": low[3],
        "last_stable_load": stable[0], "last_stable_latency": stable[1],
        "last_stable_accepted_rate": stable[3],
        "knee_load": None if knee is None else knee[0],
        "knee_latency": None if knee is None else knee[1],
    }


def topology_signature(topology: list[dict[str, Any]], mesh_pairs: set[tuple[int, int]]) -> tuple[Any, ...]:
    added = []
    for link in topology:
        ep1, ep2 = link["ep1"], link["ep2"]
        chiplet_pair = tuple(sorted((int(ep1["outer_id"]), int(ep2["outer_id"]))))
        if chiplet_pair not in mesh_pairs:
            added.append(
                tuple(sorted(((int(ep1["outer_id"]), int(ep1["inner_id"])),
                              (int(ep2["outer_id"]), int(ep2["inner_id"])))))
            )
    return tuple(sorted(added))


def expected_signature(steps: list[dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        sorted(
            tuple(sorted(((int(row["u"]), int(row["phy_u"])),
                          (int(row["v"]), int(row["phy_v"])))))
            for row in steps
        )
    )


def direct_design_path(family: str, workload: str, k: int, budget: int, policy: str) -> Path:
    directory = ROOT / "inputs" / "designs"
    if family == "stage":
        return directory / f"design_stage_{'fixed' if policy == 'fixed' else 'aware'}_k{k}_{workload}_B{budget}mm.json"
    if policy == "fixed":
        return directory / f"design_project_fixed_k{k}_{workload}_B{budget}mm.json"
    if k == 1:
        return directory / f"design_project_k1_{workload}_B{budget}mm.json"
    return directory / f"design_project_aware_k{k}_{workload}_B{budget}mm.json"


def resolve_design(
    family: str, workload: str, k: int, budget: int, policy: str,
    selections: dict[Any, list[dict[str, Any]]],
) -> tuple[Path, bool]:
    direct = direct_design_path(family, workload, k, budget, policy)
    if direct.is_file():
        return direct, False
    signature = expected_signature(selections[(family, workload, k, budget, policy)])
    for candidate_budget in BUDGETS:
        if candidate_budget >= budget:
            continue
        candidate = direct_design_path(family, workload, k, candidate_budget, policy)
        if candidate.is_file() and expected_signature(
            selections[(family, workload, k, candidate_budget, policy)]
        ) == signature:
            return candidate, True
    return direct, False


def validate_designs(
    audit: Audit, baseline: dict[str, Any] | None,
    selections: dict[Any, list[dict[str, Any]]],
) -> None:
    if baseline is None or not selections:
        audit.failure("Cannot validate generated designs without baseline/selections")
        return
    topology_by_fixed_key: defaultdict[tuple[str, int, int], set[tuple[Any, ...]]] = defaultdict(set)
    resolved_reuses = 0
    for family in ("synthetic", "stage"):
        workloads = SYNTHETIC_WORKLOADS if family == "synthetic" else tuple(
            sorted({key[1] for key in selections if key[0] == "stage"})
        )
        for workload in workloads:
            for k in K_VALUES:
                for budget in BUDGETS:
                    for policy in ("fixed", "workload_aware"):
                        key = (family, workload, k, budget, policy)
                        path, reused = resolve_design(family, workload, k, budget, policy, selections)
                        issues = []
                        try:
                            if not path.is_file():
                                raise AuditError(f"Missing design and no identical lower-budget reuse: {path}")
                            design = hlp.read_json(str(path))
                            topology_path = ROOT / design["topology"]
                            routing_path = ROOT / design["routing_table"]
                            topology = hlp.read_json(str(topology_path))
                            routing = hlp.read_json(str(routing_path))
                            expected = expected_signature(selections[key])
                            actual = topology_signature(topology, baseline["mesh_pairs"])
                            if len(topology) != 24 + len(selections[key]):
                                issues.append(f"topology links={len(topology)}, expected={24 + len(selections[key])}")
                            if actual != expected:
                                issues.append(f"topology endpoint signature differs from selection: actual={actual}, expected={expected}")
                            if not routing_path.is_file() or not isinstance(routing, dict) or not isinstance(routing.get("table"), dict):
                                issues.append("routing table missing/malformed")
                            traffic_prefix = "traffic_stage" if family == "stage" else "traffic_project"
                            expected_chiplet = f"inputs/traffic_by_chiplet/{traffic_prefix}_{workload}.json"
                            expected_unit = f"inputs/traffic_by_unit/{traffic_prefix}_{workload}.json"
                            if design.get("traffic_by_chiplet") != expected_chiplet or design.get("traffic_by_unit") != expected_unit:
                                issues.append("incorrect workload traffic reference")
                            if not (ROOT / expected_chiplet).is_file() or not (ROOT / expected_unit).is_file():
                                issues.append("referenced traffic file missing")
                            if policy == "fixed":
                                topology_by_fixed_key[(family, k, budget)].add(actual)
                        except (OSError, KeyError, ValueError, AuditError) as exc:
                            issues.append(str(exc))
                        valid = not issues
                        if issues:
                            audit.failure(f"Design {key}: {'; '.join(issues)}")
                        if reused:
                            resolved_reuses += 1
                        audit.record(
                            "logical_design", path, valid,
                            source="selection CSV; duplicate-topology design reuse" if reused else "selection CSV",
                            workload=f"{family}:{workload}", requested_k=k, budget=budget,
                            notes=f"policy={policy}; reused_lower_budget_design={reused}",
                        )

    for key, signatures in topology_by_fixed_key.items():
        if len(signatures) != 1:
            audit.failure(f"Fixed topology is workload-dependent for {key}: {signatures}")
    audit.counters["logical_designs"] = 2 * (4 + 3) * len(K_VALUES) * len(BUDGETS)
    audit.counters["design_reuses"] = resolved_reuses

    # Mesh baseline designs: physical topology remains exactly 24 links.
    mesh_designs = [
        *(ROOT / "inputs" / "designs" / f"design_project_k1_{workload}_baseline.json" for workload in SYNTHETIC_WORKLOADS),
        *(ROOT / "inputs" / "designs" / f"design_stage_mesh_{workload}.json" for workload in sorted({key[1] for key in selections if key[0] == "stage"})),
    ]
    for path in mesh_designs:
        issues = []
        try:
            design = hlp.read_json(str(path))
            topology = hlp.read_json(str(ROOT / design["topology"]))
            if len(topology) != 24 or topology_signature(topology, baseline["mesh_pairs"]):
                issues.append("mesh design is not the unchanged 24-link baseline")
            if not (ROOT / design["routing_table"]).is_file():
                issues.append("mesh routing table missing")
        except (OSError, KeyError, ValueError) as exc:
            issues.append(str(exc))
        if issues:
            audit.failure(f"Mesh design {path}: {'; '.join(issues)}")
        audit.record("mesh_design", path, not issues, source="8-PHY physical mesh baseline")


def compare_optional(actual: float | None, expected: float | None) -> bool:
    return actual is None and expected is None or (
        actual is not None and expected is not None
        and math.isclose(actual, expected, rel_tol=EPS, abs_tol=EPS)
    )


def validate_results_and_metrics(audit: Audit) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    required = {
        "workload", "budget_mm", "policy", "requested_k", "actual_k",
        "analytical_latency", "analytical_throughput", "booksim_low_load",
        "booksim_low_latency", "booksim_low_hops", "booksim_low_accepted_rate",
        "last_stable_load", "last_stable_latency", "last_stable_accepted_rate",
        "knee_load", "knee_latency", "latency_gain_vs_mesh_percent",
        "hop_gain_vs_mesh_percent", "analytical_latency_gain_vs_mesh_percent",
        "aware_latency_gain_vs_fixed_percent", "aware_hop_gain_vs_fixed_percent",
        "source_result_file", "reused_result",
    }
    synthetic = read_csv(RESULTS / "all_k_policy_comparison.csv", "all-K comparison", required)
    stage = read_csv(RESULTS / "stage_policy_comparison.csv", "STAGE comparison", required)
    result_cache: dict[Path, dict[str, Any]] = {}
    derived_mismatches = 0
    aggregate_configs: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    groups: defaultdict[tuple[str, str, int, int], dict[str, dict[str, str]]] = defaultdict(dict)
    for family, rows in (("synthetic", synthetic), ("stage", stage)):
        for row_number, row in enumerate(rows, start=2):
            workload = row["workload"]
            budget = integer(row["budget_mm"], "comparison budget")
            k = integer(row["requested_k"], "comparison K")
            policy = row["policy"]
            result_path = ROOT / row["source_result_file"]
            issues = []
            try:
                if result_path not in result_cache:
                    result_cache[result_path] = parse_result(result_path)
                parsed = result_cache[result_path]
                for field in (
                    "analytical_latency", "analytical_throughput", "booksim_low_load",
                    "booksim_low_latency", "booksim_low_hops", "booksim_low_accepted_rate",
                    "last_stable_load", "last_stable_latency", "last_stable_accepted_rate",
                ):
                    if not math.isclose(number(row[field], field), parsed[field], rel_tol=EPS, abs_tol=EPS):
                        issues.append(f"{field} CSV={row[field]} JSON={parsed[field]}")
                for field in ("knee_load", "knee_latency"):
                    if not compare_optional(number(row[field], field, optional=True), parsed[field]):
                        issues.append(f"{field} CSV={row[field]} JSON={parsed[field]}")
            except AuditError as exc:
                issues.append(str(exc))
            if issues:
                derived_mismatches += len(issues)
                audit.failure(f"Result metric mismatch {family} row {row_number}: {'; '.join(issues)}")
            reused = boolean(row["reused_result"], "reused_result")
            audit.record(
                "booksim_result", result_path, not issues,
                source=f"{family} policy comparison row {row_number}", workload=f"{family}:{workload}",
                requested_k=k, budget=budget,
                notes=f"policy={policy}; documented_duplicate_topology_reuse={reused}",
            )
            groups[(family, workload, budget, k)][policy] = row

    for key, policies in groups.items():
        if set(policies) != set(POLICIES):
            audit.failure(f"Comparison group lacks one row per policy: {key}")
            continue
        mesh, fixed, aware = (policies[policy] for policy in POLICIES)
        mesh_latency = number(mesh["booksim_low_latency"], "mesh latency")
        mesh_hops = number(mesh["booksim_low_hops"], "mesh hops")
        mesh_analytical = number(mesh["analytical_latency"], "mesh analytical")
        fixed_latency = number(fixed["booksim_low_latency"], "fixed latency")
        aware_latency = number(aware["booksim_low_latency"], "aware latency")
        fixed_hops = number(fixed["booksim_low_hops"], "fixed hops")
        aware_hops = number(aware["booksim_low_hops"], "aware hops")
        calculations = (
            (fixed, "latency_gain_vs_mesh_percent", 100.0 * (mesh_latency - fixed_latency) / mesh_latency),
            (aware, "latency_gain_vs_mesh_percent", 100.0 * (mesh_latency - aware_latency) / mesh_latency),
            (fixed, "hop_gain_vs_mesh_percent", 100.0 * (mesh_hops - fixed_hops) / mesh_hops),
            (aware, "hop_gain_vs_mesh_percent", 100.0 * (mesh_hops - aware_hops) / mesh_hops),
            (fixed, "analytical_latency_gain_vs_mesh_percent", 100.0 * (mesh_analytical - number(fixed["analytical_latency"], "fixed analytical")) / mesh_analytical),
            (aware, "analytical_latency_gain_vs_mesh_percent", 100.0 * (mesh_analytical - number(aware["analytical_latency"], "aware analytical")) / mesh_analytical),
            (aware, "aware_latency_gain_vs_fixed_percent", 100.0 * (fixed_latency - aware_latency) / fixed_latency),
            (aware, "aware_hop_gain_vs_fixed_percent", 100.0 * (fixed_hops - aware_hops) / fixed_hops),
        )
        for target, field, expected in calculations:
            if not math.isclose(number(target[field], field), expected, rel_tol=EPS, abs_tol=EPS):
                derived_mismatches += 1
                audit.failure(f"Derived gain mismatch for {key} {target['policy']} {field}")
        if integer(mesh["actual_k"], "mesh actual_k") != 0:
            audit.failure(f"Mesh actual_k is not zero for {key}")
        aggregate_configs[key[0]].append(
            {
                "workload": key[1], "budget_mm": key[2], "requested_k": key[3],
                "mesh_latency": mesh_latency, "fixed_latency": fixed_latency,
                "aware_latency": aware_latency,
                "fixed_gain_vs_mesh_percent": number(fixed["latency_gain_vs_mesh_percent"], "fixed gain"),
                "aware_gain_vs_mesh_percent": number(aware["latency_gain_vs_mesh_percent"], "aware gain"),
                "aware_gain_vs_fixed_percent": number(aware["aware_latency_gain_vs_fixed_percent"], "direct gain"),
                "fixed_actual_k": number(fixed["actual_k"], "fixed actual_k"),
                "aware_actual_k": number(aware["actual_k"], "aware actual_k"),
                "fixed_wire_mm": number(fixed["total_wire_length_mm"], "fixed wire"),
                "aware_wire_mm": number(aware["total_wire_length_mm"], "aware wire"),
                "fixed_knee": number(fixed["knee_load"], "fixed knee", optional=True),
                "aware_knee": number(aware["knee_load"], "aware knee", optional=True),
            }
        )
    audit.counters["logical_results"] = len(synthetic) + len(stage)
    audit.counters["unique_result_jsons"] = len(result_cache)
    audit.counters["derived_mismatches"] = derived_mismatches

    # Check arithmetic means in the two aggregate policy summaries.
    summary_specs = (
        ("synthetic", synthetic, RESULTS / "all_k_summary.csv", "mesh_bs_latency", "fixed_bs_latency", "aware_bs_latency"),
        ("stage", stage, RESULTS / "stage_policy_summary.csv", "mesh_latency", "fixed_latency", "aware_latency"),
    )
    aggregate_checks = 0
    for family, comparison, summary_path, mesh_field, fixed_field, aware_field in summary_specs:
        by_policy = {
            (row["workload"], integer(row["budget_mm"], "budget"), integer(row["requested_k"], "K"), row["policy"]): row
            for row in comparison
        }
        summary_rows = read_csv(summary_path, f"{family} summary")
        for row in summary_rows:
            key = (row["workload"], integer(row["budget_mm"], "summary budget"), integer(row["requested_k"], "summary K"))
            values = (
                (mesh_field, "mesh"), (fixed_field, "fixed"), (aware_field, "workload_aware")
            )
            for field, policy in values:
                aggregate_checks += 1
                expected = number(by_policy[(*key, policy)]["booksim_low_latency"], "comparison latency")
                if not math.isclose(number(row[field], field), expected, rel_tol=EPS, abs_tol=EPS):
                    audit.failure(f"Summary/comparison mismatch for {family} {key} {field}")
    audit.counters["summary_metric_checks"] = aggregate_checks

    # The separate K=1/K=2/K=4 comparison files must be exact projections of
    # the combined all-K comparison (allowing their historical column names).
    combined_index = {
        (row["workload"], integer(row["budget_mm"], "all-K budget"), row["policy"], integer(row["requested_k"], "all-K K")): row
        for row in synthetic
    }
    projection_fields = (
        "analytical_latency", "analytical_throughput", "booksim_low_load",
        "booksim_low_latency", "booksim_low_hops", "booksim_low_accepted_rate",
        "last_stable_load", "last_stable_latency", "last_stable_accepted_rate",
        "knee_load", "knee_latency", "latency_gain_vs_mesh_percent",
        "hop_gain_vs_mesh_percent", "analytical_latency_gain_vs_mesh_percent",
    )
    projection_checks = 0
    for requested_k in K_VALUES:
        rows = read_csv(RESULTS / f"k{requested_k}_policy_comparison.csv", f"K={requested_k} comparison")
        if len(rows) != len(SYNTHETIC_WORKLOADS) * len(BUDGETS) * len(POLICIES):
            audit.failure(f"K={requested_k} comparison has {len(rows)} rows; expected 48")
        for row in rows:
            stored_k = integer(row.get("requested_k", row.get("k", "")), f"K={requested_k} requested K")
            policy = row["policy"]
            # K=1's historical mesh rows store k=0, while the file itself is
            # the requested-K=1 experiment. All non-mesh rows must store 1.
            if policy != "mesh" and stored_k != requested_k:
                audit.failure(f"K={requested_k} projection has stored K={stored_k} for {row['workload']} {policy}")
            target = combined_index[(row["workload"], integer(row["budget_mm"], "K budget"), policy, requested_k)]
            for field in projection_fields:
                projection_checks += 1
                left = number(row[field], field, optional=field.startswith("knee_"))
                right = number(target[field], field, optional=field.startswith("knee_"))
                if not compare_optional(left, right):
                    audit.failure(f"K={requested_k}/all-K mismatch for {row['workload']} B{row['budget_mm']} {policy} {field}")
            if row["source_result_file"] != target["source_result_file"] or boolean(row["reused_result"], "reuse") != boolean(target["reused_result"], "reuse"):
                audit.failure(f"K={requested_k}/all-K provenance mismatch for {row['workload']} B{row['budget_mm']} {policy}")
    audit.counters["k_projection_checks"] = projection_checks

    # Recompute the arithmetic means published in final_result_summary.csv.
    final_rows = read_csv(RESULTS / "final_result_summary.csv", "final result summary")
    aggregate_fields = (
        "mesh_latency", "fixed_latency", "aware_latency",
        "fixed_gain_vs_mesh_percent", "aware_gain_vs_mesh_percent",
        "aware_gain_vs_fixed_percent", "fixed_actual_k", "aware_actual_k",
        "fixed_wire_mm", "aware_wire_mm", "fixed_knee", "aware_knee",
    )

    def arithmetic_mean(values: list[float | None]) -> float | None:
        present = [value for value in values if value is not None]
        return None if not present else sum(present) / len(present)

    final_mean_checks = 0
    for row in final_rows:
        category = row["category"]
        family = "synthetic" if category.startswith("synthetic_") else "stage" if category.startswith("stage_") else ""
        if not family:
            continue
        subset = aggregate_configs[family]
        if category.endswith("_workload"):
            subset = [item for item in subset if item["workload"] == row["workload"]]
        elif category.endswith("_k") or category == "synthetic_topology_adaptation":
            subset = [item for item in subset if item["requested_k"] == integer(row["requested_k"], "final-summary K")]
        if category.endswith("_budget") or category == "synthetic_topology_adaptation":
            subset = [item for item in subset if item["budget_mm"] == integer(row["budget_mm"], "final-summary budget")]
        if not subset:
            audit.failure(f"No source rows for final aggregate {category}")
            continue
        for field in aggregate_fields:
            final_mean_checks += 1
            expected = arithmetic_mean([item[field] for item in subset])
            actual = number(row[field], field, optional=True)
            if not compare_optional(actual, expected):
                audit.failure(f"Final arithmetic-mean mismatch for {category} {row['workload']} K={row['requested_k']} B={row['budget_mm']} {field}")
    audit.counters["final_mean_checks"] = final_mean_checks
    return synthetic, stage


def validate_traffic(audit: Audit) -> None:
    # Canonical synthetic traffic inputs.
    for workload in SYNTHETIC_WORKLOADS:
        for level in ("chiplet", "unit"):
            path = ROOT / "inputs" / f"traffic_by_{level}" / f"traffic_project_{workload}.json"
            try:
                payload = hlp.read_json(str(path))
                valid = isinstance(payload, dict) and bool(payload)
            except (OSError, ValueError):
                valid = False
            if not valid:
                audit.failure(f"Invalid synthetic traffic input: {path}")
            audit.record("synthetic_traffic", path, valid, source="project/generate_fixed_workloads.py", workload=workload)

    # Deterministic randomized seed files.
    for workload in ("permutation", "hotspot"):
        for seed in SEEDS:
            for level in ("chiplet", "unit"):
                path = ROOT / "inputs" / f"traffic_by_{level}" / f"traffic_project_{workload}_seed{seed}.json"
                try:
                    payload = hlp.read_json(str(path))
                    valid = isinstance(payload, dict) and bool(payload)
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    audit.failure(f"Missing/invalid seeded traffic: {path}")
                audit.record(
                    "seeded_traffic", path, valid, source="project/generate_multiseed_workloads.py",
                    workload=workload, notes=f"random_seed={seed}",
                )
    seed_rows = read_csv(RESULTS / "multiseed_results.csv", "multiseed results", {"workload", "seed"})
    observed = {integer(row["seed"], "multiseed seed") for row in seed_rows}
    if observed != set(SEEDS):
        audit.failure(f"Multiseed results contain seeds {sorted(observed)}, expected {list(SEEDS)}")
    audit.counters["seed_values"] = len(observed)

    conversion_rows = read_csv(
        RESULTS / "stage_conversion_validation.csv", "STAGE conversion validation",
        {"workload", "num_stage_ranks", "mapping_policy", "normalization_factor",
         "conversion_valid", "traffic_by_chiplet_path", "traffic_by_unit_path", "mapping_path",
         "collective_expansion_policy", "target_max_flow", "target_max_flow_source",
         "unit_distribution_policy"},
    )
    inventory_rows = read_csv(
        RESULTS / "stage_trace_inventory.csv", "STAGE trace inventory",
        {"workload", "trace_path", "number_of_ranks", "generation_command"},
    )
    inventory = {row["workload"]: row for row in inventory_rows}
    stage_notes = []
    for row in conversion_rows:
        workload = row["workload"]
        issues = []
        if row["conversion_valid"].strip().lower() != "true":
            issues.append("conversion_valid is not True")
        if workload not in inventory:
            issues.append("missing trace inventory row")
        else:
            if integer(row["num_stage_ranks"], "converted ranks") != integer(inventory[workload]["number_of_ranks"], "inventory ranks"):
                issues.append("rank count differs between conversion and inventory")
        referenced = [ROOT / row[field] for field in ("traffic_by_chiplet_path", "traffic_by_unit_path", "mapping_path")]
        for path in referenced:
            if not path.is_file():
                issues.append(f"missing converted artifact {path}")
            audit.record("stage_conversion_output", path, path.is_file(), source="project/convert_stage_to_traffic.py", workload=workload)
        trace_dir = ROOT / "stage" / "generated" / "rapidchiplet_representative" / workload
        if not trace_dir.is_dir():
            issues.append(f"trace directory missing: {trace_dir}")
        audit.record("stage_trace", trace_dir, trace_dir.is_dir(), source=inventory.get(workload, {}).get("generation_command", ""), workload=workload)
        if issues:
            audit.failure(f"STAGE conversion {workload}: {'; '.join(issues)}")
        stage_notes.append(
            f"{workload}: trace={inventory.get(workload, {}).get('trace_path', 'missing')}; "
            f"ranks={row['num_stage_ranks']}; mapping={row['mapping_policy']}; "
            f"normalization=scale largest converted flow to {row['target_max_flow']} using "
            f"factor {row['normalization_factor']} (reference={row['target_max_flow_source']}); "
            f"collectives={row['collective_expansion_policy']}; units={row['unit_distribution_policy']}."
        )
    audit.info.extend(stage_notes)
    audit.counters["stage_workloads"] = len(conversion_rows)


def reproduction_commands() -> list[str]:
    return [
        "python3 project/generate_fixed_workloads.py",
        "python3 project/generate_multiseed_workloads.py",
        "python3 project/select_k1_all.py",
        "python3 project/create_k1_all_designs.py",
        "python3 project/run_k1_booksim.py",
        "python3 project/summarize_k1_all.py",
        "python3 project/select_fixed_k1.py",
        "python3 project/create_fixed_k1_designs.py",
        "python3 project/run_fixed_k1_booksim.py",
        "python3 project/select_k2_workload_aware.py",
        "python3 project/select_fixed_k2.py",
        "python3 project/create_k2_workload_aware_designs.py",
        "python3 project/create_fixed_k2_designs.py",
        "python3 project/run_k2_workload_aware_booksim.py",
        "python3 project/run_fixed_k2_booksim.py",
        "python3 project/select_k4_workload_aware.py",
        "python3 project/select_fixed_k4.py",
        "python3 project/create_k4_workload_aware_designs.py",
        "python3 project/create_fixed_k4_designs.py",
        "python3 project/run_k4_workload_aware_booksim.py",
        "python3 project/run_fixed_k4_booksim.py",
        "python3 project/compare_k1_policies.py",
        "python3 project/compare_k2_policies.py",
        "python3 project/compare_k4_policies.py",
        "python3 project/compare_all_k.py",
        "python3 project/run_multiseed_analysis.py",
        "python3 project/inspect_stage_traces.py",
        "python3 project/convert_stage_to_traffic.py",
        "python3 project/run_stage_workload_aware_optimization.py",
        "python3 project/create_stage_workload_aware_designs.py",
        "python3 project/run_stage_workload_aware_booksim.py",
        "python3 project/create_stage_fixed_designs.py",
        "python3 project/run_stage_fixed_booksim.py",
        "python3 project/create_stage_mesh_baselines.py",
        "python3 project/run_stage_mesh_booksim.py",
        "python3 project/compare_stage_policies.py",
        "python3 project/analyze_physical_costs.py",
        "python3 project/generate_final_tables.py",
        "python3 project/generate_final_plots.py",
        "python3 project/analyze_final_results.py",
        "python3 project/audit_project_results.py",
    ]


def write_manifest(audit: Audit) -> None:
    try:
        with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows({field: row[field] for field in MANIFEST_FIELDS} for row in audit.records)
    except OSError as exc:
        raise AuditError(f"Cannot write manifest: {exc}")


def report_text(audit: Audit, status: str) -> str:
    sections = []
    sections.extend(
        [
            "PROJECT REPRODUCIBILITY AND CONSISTENCY AUDIT",
            "=============================================",
            "",
            "1. Overall status",
            "-----------------",
            f"Status: {status}",
            f"Artifacts checked: {len(audit.records)}",
            f"Valid artifact records: {sum(bool(row['valid']) for row in audit.records)}",
            f"Warnings: {len(audit.warnings)}",
            f"Failures: {len(audit.failures)}",
            "Large RapidChiplet/BookSim simulations were not rerun; stored artifacts were parsed and cross-checked.",
            "",
            "2. Input/workload validation",
            "----------------------------",
            f"Canonical synthetic traffic files checked: {len(SYNTHETIC_WORKLOADS) * 2}.",
            f"Seeded traffic files checked: {2 * len(SEEDS) * 2}; expected seeds={list(SEEDS)}.",
            f"STAGE workloads checked: {audit.counters['stage_workloads']}.",
            *[f"- {note}" for note in audit.info],
            "",
            "3. Optimizer-selection validation",
            "---------------------------------",
            f"Logical Fixed/Workload-Aware selections checked: {audit.counters['selection_configs']}.",
            f"Recorded max-load constraints checked: {audit.counters['max_load_checks']}.",
            f"Greedy-prefix comparisons checked: {audit.counters['prefix_checks']}; mismatches={audit.counters['prefix_mismatches']}.",
            "K1/K2/K4 requested_k values and actual_k<=requested_k were checked from exact stored selections.",
            "",
            "4. Physical-budget validation",
            "-----------------------------",
            "Every normalized selection was checked for cumulative selected wire <= its 5/15/25/45 mm budget.",
            "Mesh logical rows were checked for actual_k=0 through comparison validation.",
            "",
            "5. PHY validation",
            "-----------------",
            "All shortcut endpoints were checked against original mesh occupancy, duplicate endpoint reuse, duplicate chiplet pairs, and original mesh edges.",
            "",
            "6. Design/topology validation",
            "-----------------------------",
            f"Logical shortcut designs checked: {audit.counters['logical_designs']}; identical lower-budget design reuses resolved: {audit.counters['design_reuses']}.",
            "Each checked design was required to contain 24+actual_k links, exact chiplet/PHY selection endpoints, a routing table, and matching workload traffic.",
            "Fixed topology signatures were required to be identical across workloads for each family/K/budget.",
            "",
            "7. BookSim-result validation",
            "---------------------------",
            f"Logical policy result rows checked: {audit.counters['logical_results']}; unique source JSONs parsed: {audit.counters['unique_result_jsons']}.",
            "Each source JSON was checked for latency, throughput, booksim_simulation, and a complete numeric offered-load point.",
            "Low load was recomputed as the smallest complete numeric offered load. The derived latency knee was recomputed as the first tested load with packet latency >= 2x low-load latency.",
            "Documented duplicate-topology rows were resolved through source_result_file; no duplicate JSON was fabricated.",
            "",
            "8. Derived-metric validation",
            "----------------------------",
            f"JSON/CSV or derived-gain mismatches: {audit.counters['derived_mismatches']}.",
            f"Aggregate summary latency checks: {audit.counters['summary_metric_checks']}.",
            f"Separate-K/all-K projection checks: {audit.counters['k_projection_checks']}.",
            f"Final arithmetic-mean checks: {audit.counters['final_mean_checks']}.",
            "Latency gain vs Mesh/Fixed, hop gain, analytical gain, low-load metrics, last-stable metrics, knees, and summary values were recomputed with tolerance 1e-9.",
            "",
            "9. STAGE-conversion validation",
            "-----------------------------",
            "STAGE conversion_valid flags, rank counts, traffic/mapping paths, trace directories, mapping policy, collective expansion, unit distribution, and max-flow normalization metadata were checked against inventory records.",
            "",
            "10. Missing/inconsistent artifacts",
            "----------------------------------",
        ]
    )
    if audit.failures:
        sections.extend(f"FAILURE: {message}" for message in audit.failures)
    else:
        sections.append("No critical inconsistencies found.")
    if audit.warnings:
        sections.extend(f"WARNING: {message}" for message in audit.warnings)
    else:
        sections.append("No audit warnings.")
    sections.extend(
        [
            "",
            "11. Reproduction commands",
            "-------------------------",
            "Ordered commands (documented only; not executed by this audit):",
            *[f"{index}. {command}" for index, command in enumerate(reproduction_commands(), start=1)],
            "",
            "12. Final audit verdict",
            "-----------------------",
            status,
            "The verdict reflects stored-artifact traceability and inexpensive consistency checks, not a fresh rerun of large simulations.",
            "",
        ]
    )
    return "\n".join(sections)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    audit = Audit()
    validate_required_datasets(audit)
    baseline = load_baseline(audit)
    selections, _optimizer_metrics = validate_selection_files(audit, baseline)
    validate_designs(audit, baseline, selections)
    validate_results_and_metrics(audit)
    validate_traffic(audit)
    status = "FAIL" if audit.failures else "PASS WITH WARNINGS" if audit.warnings else "PASS"

    # Include the two audit deliverables in their own traceability manifest.
    audit.record("audit_output", REPORT_PATH, True, source="project/audit_project_results.py", notes="generated after all checks")
    audit.record("audit_output", MANIFEST_PATH, True, source="project/audit_project_results.py", notes="self-describing manifest")
    write_manifest(audit)
    try:
        REPORT_PATH.write_text(report_text(audit, status), encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Cannot write audit report: {exc}") from exc
    # Rewrite manifest so output existence flags reflect completed writes.
    for row in audit.records:
        if row["artifact_type"] == "audit_output":
            row["exists"] = True
    write_manifest(audit)

    valid_artifacts = sum(bool(row["valid"]) for row in audit.records)
    print("FINAL REPRODUCIBILITY AUDIT COMPLETE")
    print(f"Total artifacts checked: {len(audit.records)}")
    print(f"Valid artifacts: {valid_artifacts}")
    print(f"Warnings: {len(audit.warnings)}")
    print(f"Failures: {len(audit.failures)}")
    print(f"Final audit status: {status}")
    print(f"Audit report: {REPORT_PATH.relative_to(ROOT).as_posix()}")
    print(f"Manifest: {MANIFEST_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
