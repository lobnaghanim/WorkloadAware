"""Generate deterministic multi-seed permutation and hotspot workloads."""

import math
import random
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import generate_traffic as trgen
import helpers as hlp


DESIGN_FILE = REPO_ROOT / "inputs/designs/design_project_mesh_4x4.json"
WORKLOADS = {
    "permutation": None,
    "hotspot": (4, 0.5),
}
SEEDS = (42, 43, 44, 45, 46)
REL_TOL = 1e-12
ABS_TOL = 1e-12


def fail(message):
    raise RuntimeError(message)


def numerically_equal(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            numerically_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            numerically_equal(a, b) for a, b in zip(left, right)
        )
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return left == right


def write_or_validate(path, payload):
    if path.is_file():
        existing = hlp.read_json(str(path))
        if not numerically_equal(existing, payload):
            fail(f"Existing seeded workload differs from deterministic generation: {path}")
        print(f"Validated existing {path.relative_to(REPO_ROOT).as_posix()}")
        return False
    hlp.write_json(str(path), payload)
    print(f"Created {path.relative_to(REPO_ROOT).as_posix()}")
    return True


def validate_seed_42(workload, traffic_by_unit, traffic_by_chiplet):
    unseeded_unit = (
        REPO_ROOT / f"inputs/traffic_by_unit/traffic_project_{workload}.json"
    )
    unseeded_chiplet = (
        REPO_ROOT / f"inputs/traffic_by_chiplet/traffic_project_{workload}.json"
    )
    if not unseeded_unit.is_file() or not unseeded_chiplet.is_file():
        fail(f"Missing existing deterministic seed-42 reference for {workload}")
    existing_unit = hlp.read_json(str(unseeded_unit))
    existing_chiplet = hlp.read_json(str(unseeded_chiplet))
    if not numerically_equal(existing_unit, traffic_by_unit):
        fail(f"Generated seed 42 unit traffic does not match existing {workload}")
    if not numerically_equal(existing_chiplet, traffic_by_chiplet):
        fail(f"Generated seed 42 chiplet traffic does not match existing {workload}")
    print(f"Seed 42 numerically matches existing {workload} workload")


def generate_workloads():
    design = hlp.read_json(str(DESIGN_FILE))
    chiplets = hlp.read_json(str(REPO_ROOT / design["chiplets"]))
    placement = hlp.read_json(str(REPO_ROOT / design["placement"]))
    if len(placement["chiplets"]) != 16:
        fail("Multi-seed generation requires exactly 16 placed chiplets")

    created = 0
    validated = 0
    for workload, parameters in WORKLOADS.items():
        for seed in SEEDS:
            # This is the only change from generate_fixed_workloads.py.
            random.seed(seed)
            traffic_by_unit, traffic_by_chiplet = trgen.generate_traffic(
                chiplets, placement, workload, parameters
            )
            if seed == 42:
                validate_seed_42(workload, traffic_by_unit, traffic_by_chiplet)

            unit_path = (
                REPO_ROOT
                / f"inputs/traffic_by_unit/traffic_project_{workload}_seed{seed}.json"
            )
            chiplet_path = (
                REPO_ROOT
                / f"inputs/traffic_by_chiplet/traffic_project_{workload}_seed{seed}.json"
            )
            for path, payload in (
                (unit_path, traffic_by_unit),
                (chiplet_path, traffic_by_chiplet),
            ):
                if write_or_validate(path, payload):
                    created += 1
                else:
                    validated += 1

    expected = len(WORKLOADS) * len(SEEDS) * 2
    paths = [
        REPO_ROOT / f"inputs/traffic_by_{level}/traffic_project_{workload}_seed{seed}.json"
        for level in ("unit", "chiplet")
        for workload in WORKLOADS
        for seed in SEEDS
    ]
    if len(paths) != expected or not all(path.is_file() for path in paths):
        fail("Multi-seed workload file matrix is incomplete")
    print("MULTI-SEED WORKLOAD GENERATION COMPLETE")
    print(f"Workloads: {len(WORKLOADS)}")
    print(f"Seeds: {len(SEEDS)}")
    print(f"Files created: {created}")
    print(f"Existing files validated without overwrite: {validated}")


if __name__ == "__main__":
    generate_workloads()
