import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import helpers as hlp

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


PATTERNS = [
    "random_uniform",
    "transpose",
    "permutation",
    "hotspot",
]

for pattern in PATTERNS:

    filename = (
        f"inputs/traffic_by_chiplet/"
        f"traffic_project_{pattern}.json"
    )

    traffic = hlp.read_json(filename)

    print("\n" + "=" * 70)
    print(pattern.upper())
    print("=" * 70)

    # Sort source-destination pairs by traffic volume
    sorted_flows = sorted(
        traffic.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    print("Top 15 communication flows:")
    print()

    for (src, dst), value in sorted_flows[:15]:
        print(
            f"chiplet {src:2d} -> chiplet {dst:2d}"
            f"    traffic = {value:.4f}"
        )
