"""Verify and compare union output geometries across benchmark candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


def analyze_geometry(geom: BaseGeometry) -> dict[str, Any]:
    """Compute geometric and topological properties of a polygon/multipolygon."""
    valid = bool(geom.is_valid)
    area = float(geom.area)
    bounds = [float(b) for b in geom.bounds] if geom.bounds else None

    # Count exterior rings, holes, parts
    parts = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom] if isinstance(geom, Polygon) else []
    part_count = len(parts)
    hole_count = sum(len(p.interiors) for p in parts if isinstance(p, Polygon))
    vertex_count = sum(
        len(p.exterior.coords) + sum(len(h.coords) for h in p.interiors) for p in parts if isinstance(p, Polygon)
    )

    return {
        "valid": valid,
        "area": area,
        "bounds": bounds,
        "part_count": part_count,
        "hole_count": hole_count,
        "vertex_count": vertex_count,
    }


def compare_geometries(candidate: BaseGeometry, baseline: BaseGeometry) -> dict[str, Any]:
    """Compare candidate geometry against a baseline."""
    cand_info = analyze_geometry(candidate)
    base_info = analyze_geometry(baseline)

    area_diff = abs(cand_info["area"] - base_info["area"])
    relative_area_diff = area_diff / base_info["area"] if base_info["area"] > 0 else 0.0

    # Bounds difference
    bounds_diff = None
    if cand_info["bounds"] and base_info["bounds"]:
        bounds_diff = [abs(c - b) for c, b in zip(cand_info["bounds"], base_info["bounds"], strict=True)]

    part_count_diff = cand_info["part_count"] - base_info["part_count"]

    equal: bool | None = None
    sym_diff_area: float | None = None
    error: str | None = None
    if not cand_info["valid"] or not base_info["valid"]:
        error = "Invalid geometry; equivalence not established"
    else:
        try:
            equal = bool(shapely.equals(candidate, baseline))
            sym_diff_area = float(shapely.symmetric_difference(candidate, baseline).area) if not equal else 0.0
        except shapely.errors.GEOSException as exc:
            error = str(exc)

    return {
        "candidate": cand_info,
        "baseline": base_info,
        "area_diff": area_diff,
        "relative_area_diff": relative_area_diff,
        "bounds_diff": bounds_diff,
        "part_count_diff": part_count_diff,
        "topologically_equal": equal,
        "agreement_status": "unknown" if equal is None else "equal" if equal else "different",
        "error": error,
        "symmetric_difference_area": sym_diff_area,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Path to baseline WKB file")
    parser.add_argument("--candidates", type=Path, nargs="+", required=True, help="Paths to candidate WKB files")
    parser.add_argument("--output", type=Path, required=True, help="Path to write JSON comparison report")
    args = parser.parse_args()

    print(f"Loading baseline from {args.baseline}...")
    baseline_bytes = args.baseline.read_bytes()
    baseline_geom = shapely.from_wkb(baseline_bytes)
    base_info = analyze_geometry(baseline_geom)
    print(
        f"Baseline loaded: area={base_info['area']:.6f}, parts={base_info['part_count']}, "
        f"vertices={base_info['vertex_count']:,}, valid={base_info['valid']}"
    )

    comparisons = {}
    for cand_path in args.candidates:
        name = cand_path.stem
        print(f"Comparing candidate {name} from {cand_path}...")
        cand_bytes = cand_path.read_bytes()
        cand_geom = shapely.from_wkb(cand_bytes)
        comp = compare_geometries(cand_geom, baseline_geom)
        comparisons[name] = comp
        print(
            f"  {name}: area_diff={comp['area_diff']:.2e} (rel={comp['relative_area_diff']:.2e}), "
            f"parts={comp['candidate']['part_count']} (diff={comp['part_count_diff']}), "
            f"valid={comp['candidate']['valid']}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(comparisons, indent=2) + "\n")
    print(f"Comparison report saved to {args.output}")


if __name__ == "__main__":
    main()
