"""Tests for candidate giant-watershed union implementations on independent fixtures."""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import shapely
from shapely.geometry import Polygon

from planning.experiments.giant_watershed_union.bench_python import decode_geometries, read_wkb_bin, run_candidate
from planning.experiments.giant_watershed_union.verify_outputs import analyze_geometry, compare_geometries


def _rect(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])


def _grid_fixture() -> list[Polygon]:
    """Adjacent 4x4 grid of squares sharing edges."""
    return [_rect(x, y, x + 1.0, y + 1.0) for y in range(4) for x in range(4)]


def _overlapping_fixture() -> list[Polygon]:
    """Chain of overlapping squares."""
    return [_rect(i * 0.75, 0.0, i * 0.75 + 1.0, 1.0) for i in range(8)]


def test_wkb_bin_roundtrip_and_bench_runner() -> None:
    polygons = _grid_fixture()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        bin_path = tmp_path / "inputs.wkb_bin"
        with open(bin_path, "wb") as f:
            for idx, poly in enumerate(polygons):
                wkb = poly.wkb
                f.write(struct.pack("<QI", idx + 1, len(wkb)))
                f.write(wkb)

        ids, wkb_list, read_time = read_wkb_bin(bin_path)
        assert len(ids) == 16
        assert len(wkb_list) == 16
        assert read_time >= 0

        geoms, decode_time = decode_geometries(wkb_list)
        assert len(geoms) == 16
        assert decode_time >= 0

        out_dir = tmp_path / "out"
        rec1 = run_candidate("shapely_direct_union", geoms, out_dir)
        rec2 = run_candidate("shapely_batched_union", geoms, out_dir, batch_size=4)
        rec3 = run_candidate("pourpoint_merge", [], out_dir, ids=ids, wkb_list=wkb_list)

        assert rec1["is_valid"] and rec2["is_valid"] and rec3["is_valid"]
        assert rec1["area"] == 16.0
        assert rec2["area"] == 16.0
        assert rec3["area"] == 16.0

        # Test geometry comparison
        g1 = shapely.from_wkb((out_dir / "shapely_direct_union.wkb").read_bytes())
        g2 = shapely.from_wkb((out_dir / "shapely_batched_union.wkb").read_bytes())
        comp = compare_geometries(g2, g1)
        assert comp["topologically_equal"] is True
        assert comp["area_diff"] == 0.0

        info = analyze_geometry(g1)
        assert info["valid"] is True
        assert info["area"] == 16.0


def test_equal_area_is_not_topological_agreement() -> None:
    comparison = compare_geometries(_rect(0, 0, 1, 1), _rect(2, 0, 3, 1))
    assert comparison["area_diff"] == 0
    assert comparison["topologically_equal"] is False
    assert comparison["symmetric_difference_area"] == 2


def test_invalid_geometry_agreement_is_unknown() -> None:
    bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    comparison = compare_geometries(bowtie, bowtie)
    assert comparison["candidate"]["valid"] is False
    assert comparison["topologically_equal"] is None
    assert comparison["agreement_status"] == "unknown"


def test_merge_requires_original_records(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="source IDs/WKB"):
        run_candidate("pourpoint_merge", _grid_fixture(), tmp_path)


def test_truncated_input_is_rejected(tmp_path: Path) -> None:
    import pytest

    path = tmp_path / "truncated.bin"
    path.write_bytes(struct.pack("<QI", 5, 100) + b"short")
    with pytest.raises(ValueError, match="Truncated WKB"):
        read_wkb_bin(path)
