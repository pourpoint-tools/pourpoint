"""Compare saved workflow geometries separately from performance measurements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import shapely


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    baseline = shapely.from_wkb(args.baseline.read_bytes())
    candidate = shapely.from_wkb(args.candidate.read_bytes())
    receipt = {
        "baseline_valid": bool(shapely.is_valid(baseline)),
        "candidate_valid": bool(shapely.is_valid(candidate)),
        "topologically_equal": bool(shapely.equals(baseline, candidate)),
        "baseline": args.baseline.name,
        "candidate": args.candidate.name,
        "limitation": "Direct GEOS is a comparator, not an independent correctness oracle.",
    }
    print(json.dumps(receipt, indent=2))
    assert receipt["baseline_valid"] and receipt["candidate_valid"] and receipt["topologically_equal"]


if __name__ == "__main__":
    main()
