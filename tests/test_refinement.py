import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from conftest import add_pair
from rasterio.transform import from_origin
from shapely.geometry import Point, box
from spatial_fixture import make_spatial_bundle

from pourpoint.fabric import HydroFabric
from pourpoint.features import Subbasin
from pourpoint.refinement import merge, refine
from pourpoint.results import OperationError


def test_whole_mask_trace_excludes_downstream_branch_and_preserves_attributes(tmp_path: Path) -> None:
    hfx = make_spatial_bundle(tmp_path / "fabric")
    path = hfx / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    # All cells flow east. The third unit joins below the requested outlet.
    add_pair(
        hfx,
        direction=np.array([[8, 8, 8, 0]], dtype="uint8"),
        accumulation=np.array([[-999, 2, 3, 4]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    with HydroFabric(hfx) as fabric:
        table = pa.table(
            {
                "id": [10, 20, 30, 40],
                "geometry": [box(i, 0, i + 1, 1).wkb for i in range(4)],
                "source_id": ["ten", "twenty", "thirty", "forty"],
            }
        )
        units = [Subbasin(table, row) for row in range(4)]
        result = refine(fabric=fabric, subbasins=units, target=Point(1.5, 0.5), window_size=1)
        merged = refine(fabric=fabric, footprint=merge(subbasins=units), target=Point(1.5, 0.5), window_size=1)
    assert merge(subbasins=result.subbasins).equals(box(0, 0, 2, 1))
    assert result.geometry.equals(merge(subbasins=result.subbasins))
    assert merge(subbasins=result.subbasins).equals(merged.geometry)
    assert [p.id for p in result.subbasins] == [10, 20]
    assert result.subbasins[0].attributes["source_id"] == "ten"
    assert (result.outlet_cell.row, result.outlet_cell.col) == (0, 1)
    assert result.outlet_cell.center.equals(Point(1.5, 0.5))
    assert result.outlet_cell.geometry.equals(box(1, 0, 2, 1))
    assert result.rasters.direction.mask.tolist() == [[True] * 4]
    assert result.rasters.direction.window.raw_direction.tolist() == [[8, 8, 8, 0]]
    assert result.rasters.direction.window.direction_nodata == 255
    assert result.rasters.accumulation.window.accumulation_nodata == -999
    assert result.coverage.status == "not_assessed"
    assert result.coverage.reason is not None and "subbasins" in result.coverage.reason
    assert merged.coverage.status == "not_assessed"
    assert merged.coverage.reason is not None and "footprint" in merged.coverage.reason


@pytest.mark.parametrize("terminal", [-8, 0, -128])
def test_terminal_or_invalid_cell_does_not_flow_to_neighbor(tmp_path: Path, terminal: int) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[8, terminal, 0]], dtype="int8"),
        accumulation=np.array([[1, 2, 3]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    with HydroFabric(root) as fabric:
        result = refine(fabric=fabric, footprint=box(0, 0, 3, 1), target=Point(2.5, 0.5), window_size=1)
    assert result.geometry.equals(box(2, 0, 3, 1))


def test_mask_hole_breaks_flow_and_local_ties_are_row_major(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[8, 8, 8, 0]], dtype="uint8"),
        accumulation=np.array([[9, 9, 9, 9]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    mask = box(0, 0, 1, 1).union(box(2, 0, 4, 1))
    with HydroFabric(root) as fabric:
        result = refine(fabric=fabric, footprint=mask, target=Point(2.5, 0.5))
    assert result.outlet_cell.col == 2
    assert result.geometry.equals(box(2, 0, 3, 1))
    assert result.rasters.direction.mask.tolist() == [[True, False, True, True]]


def test_missing_rasters_is_explicit_and_invalid_window_is_configuration_error(tmp_path: Path) -> None:
    with HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric:
        with pytest.raises(OperationError) as caught:
            refine(fabric=fabric, footprint=box(0, 0, 1, 1), target=Point(0.5, 0.5))
        assert caught.value.code == "refinement.missing_rasters"
        with pytest.raises(ValueError, match="positive odd"):
            refine(fabric=fabric, footprint=box(0, 0, 1, 1), target=Point(0.5, 0.5), window_size=2)


def test_merge_nested_groups_and_empty() -> None:
    table = pa.table({"id": [1, 2], "geometry": [box(0, 0, 1, 1).wkb, box(1, 0, 2, 1).wkb]})
    parts = [Subbasin(table, i) for i in range(2)]
    merged = merge(subbasins=[parts, [parts[0]], []])
    assert merged[0].equals(box(0, 0, 2, 1))
    assert merged[1].equals(parts[0].geometry)
    assert merged[2].is_empty


def test_nonzero_window_offset_and_non_aligned_unit_boundaries(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.full((4, 5), 8, dtype="uint8"),
        accumulation=np.arange(20, dtype="int32").reshape(4, 5),
        transform=from_origin(0, 4, 1, 1),
    )
    table = pa.table({"id": [1, 2], "geometry": [box(1.2, 1.2, 2.3, 2.8).wkb, box(2.3, 1.2, 3.8, 2.8).wkb]})
    parts = [Subbasin(table, i) for i in range(2)]
    with HydroFabric(root) as fabric:
        granular = refine(fabric=fabric, subbasins=parts, target=Point(3.5, 1.5), window_size=1)
        merged = refine(fabric=fabric, footprint=merge(subbasins=parts), target=Point(3.5, 1.5), window_size=1)
    assert (granular.outlet_cell.row, granular.outlet_cell.col) == (2, 3)
    assert granular.outlet_cell.geometry.equals(box(3, 1, 4, 2))
    assert granular.geometry.equals(merge(subbasins=granular.subbasins))
    assert merge(subbasins=granular.subbasins).equals(merged.geometry)
    assert merged.geometry.equals(box(1.2, 1.2, 3.8, 2))


@pytest.mark.parametrize("case", ["coverage", "no_outlet", "invalid_direction"])
def test_refinement_does_not_fallback_on_unusable_rasters(tmp_path: Path, case: str) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[9 if case == "invalid_direction" else 8]], dtype="uint8"),
        accumulation=np.array([[-999 if case == "no_outlet" else 1]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    with HydroFabric(root) as fabric:
        if case == "invalid_direction":
            with pytest.raises(ValueError, match="invalid consumed direction"):
                refine(fabric=fabric, footprint=box(0, 0, 1, 1), target=Point(0.5, 0.5))
        else:
            footprint = box(0, 0, 2 if case == "coverage" else 1, 1)
            with pytest.raises(OperationError) as caught:
                refine(fabric=fabric, footprint=footprint, target=Point(0.5, 0.5))
            assert caught.value.code == ("refinement.no_coverage" if case == "coverage" else "refinement.no_outlet")


def test_three_by_three_ties_choose_first_source_row_then_column(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.zeros((3, 3), dtype="uint8"),
        accumulation=np.array([[1, 9, 9], [9, 1, 1], [1, 1, 1]], dtype="int32"),
        transform=from_origin(0, 3, 1, 1),
    )
    with HydroFabric(root) as fabric:
        result = refine(fabric=fabric, footprint=box(0, 0, 3, 3), target=Point(1.5, 1.5))
    assert (result.outlet_cell.row, result.outlet_cell.col) == (0, 1)
    assert result.geometry.equals(box(1, 2, 2, 3))


def test_merit_terminal_outlets_accept_upstream_flow_and_exclude_nodata(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "merit-uint8")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    direction = np.array([[1, 0, 247, 16], [128, 255, 16, 64]], dtype="uint8")
    accumulation = np.array([[10, 50, 999, 5], [15, 40, 12, 3]], dtype="int32")
    add_pair(
        root,
        encoding="merit",
        direction=direction,
        accumulation=accumulation,
        transform=from_origin(0, 2, 1, 1),
        direction_nodata=247,
    )
    footprint = box(0, 0, 4, 2)
    with HydroFabric(root) as fabric:
        mouth = refine(fabric=fabric, footprint=footprint, target=Point(1.5, 1.5), window_size=3)
        depression = refine(fabric=fabric, footprint=footprint, target=Point(1.5, 0.5), window_size=1)
    assert (mouth.outlet_cell.row, mouth.outlet_cell.col) == (0, 1)
    assert mouth.geometry.equals(box(0, 1, 2, 2).union(box(0, 0, 1, 1)))
    assert mouth.rasters.direction.window.raw_direction is not None
    assert mouth.rasters.direction.window.raw_direction.dtype == np.uint8
    np.testing.assert_array_equal(mouth.rasters.direction.window.raw_direction, direction)

    assert (depression.outlet_cell.row, depression.outlet_cell.col) == (1, 1)
    assert depression.geometry.equals(box(1, 0, 3, 1))

    signed = make_spatial_bundle(tmp_path / "merit-int8")
    signed_manifest_path = signed / "manifest.json"
    signed_manifest = json.loads(signed_manifest_path.read_text())
    signed_manifest["auxiliary"] = []
    signed_manifest_path.write_text(json.dumps(signed_manifest))
    signed_dir = np.array([[1, -1, -9]], dtype="int8")
    add_pair(
        signed,
        encoding="merit",
        direction=signed_dir,
        accumulation=np.array([[5, 10, 99]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
        direction_nodata=-9,
    )
    with HydroFabric(signed) as fabric:
        signed_result = refine(fabric=fabric, footprint=box(0, 0, 3, 1), target=Point(1.5, 0.5), window_size=3)
    assert (signed_result.outlet_cell.row, signed_result.outlet_cell.col) == (0, 1)
    assert signed_result.geometry.equals(box(0, 0, 2, 1))
    assert signed_result.rasters.direction.window.raw_direction is not None
    assert signed_result.rasters.direction.window.raw_direction.dtype == np.int8
    np.testing.assert_array_equal(signed_result.rasters.direction.window.raw_direction, signed_dir)


def test_batch_refinement_rejects_non_selection_result_slots(tmp_path: Path) -> None:
    from pourpoint.results import BatchResult, Target

    table = pa.table({"id": [1], "geometry": [box(0, 0, 1, 1).wkb]})
    bad_batch = BatchResult(
        results=[[Subbasin(table, 0)]],  # type: ignore[list-item]
        targets=[Target(0, "raw", Point(0.5, 0.5))],
        issues=[],
    )
    with (
        HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric,
        pytest.raises(TypeError, match="SelectionResult"),
    ):
        refine(fabric=fabric, subbasins=bad_batch)  # type: ignore[arg-type]


def test_refine_clamps_negative_window_bounds_and_rejects_huge_footprint(tmp_path: Path) -> None:
    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    add_pair(
        root,
        direction=np.array([[0, 0], [0, 0]], dtype="uint8"),
        accumulation=np.array([[10, 1], [1, 1]], dtype="int32"),
        transform=from_origin(0, 2, 1, 1),
    )
    with HydroFabric(root) as fabric:
        # Target slightly north/west of grid origin inside a covered footprint: row=-1, col=-1, window_size=1 -> bottom=0, right=0
        # Before clamping, bottom=-0/negative could slice from the end of mask. With clamp, row=col=-1 with window_size=1 yields empty slice -> refinement.no_outlet!
        with pytest.raises(OperationError) as err:
            refine(fabric=fabric, footprint=box(-2, 0, 2, 4), target=Point(-1.0, 3.0), window_size=1)
        assert err.value.code == "refinement.no_coverage"

        # Now use a custom transformer / point slightly outside row=0, col=0 within a valid covered footprint (0..2, 0..2):
        # Point(0.5, 2.5) is outside box(0,0,2,2), so test via huge footprint first:
        with pytest.raises(OperationError) as huge_err:
            refine(fabric=fabric, footprint=box(0, 0, 1e40, 1), target=Point(0.5, 0.5))
        assert huge_err.value.code == "refinement.no_coverage"

        # Simulate a projected coordinate landing at row=-1, col=-1 with window_size=1:
        # without clamping bottom/right >= 0, mask[0:0] vs mask[0:-1] (at row=-2, window_size=3 -> bottom=0 or row=-3 -> bottom=-1)
        # wraps around the end of `mask`.
        import pourpoint.refinement as refinement_mod

        class OffsetTransformer:
            def transform(self, x: object, y: object, **kwargs: object) -> object:
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    # Map point to (-2.5, 4.5), which yields col=-3, row=-3 (with window_size=3 -> bottom=-1, right=-1).
                    return (-2.5, 4.5)
                return (x, y)

        orig_from_crs = refinement_mod.Transformer.from_crs
        try:
            refinement_mod.Transformer.from_crs = staticmethod(lambda *a, **kw: OffsetTransformer())  # type: ignore[method-assign]
            with pytest.raises(OperationError) as neg_err:
                refine(fabric=fabric, footprint=box(0, 0, 2, 2), target=Point(0.5, 0.5), window_size=3)
            assert neg_err.value.code == "refinement.no_outlet"
        finally:
            refinement_mod.Transformer.from_crs = orig_from_crs  # type: ignore[method-assign]


def test_trace_handles_1x1_winding_paths_mask_boundaries_and_cycles() -> None:
    from affine import Affine
    from rasterio.crs import CRS

    import pourpoint.refinement as refinement_mod
    from pourpoint.rasters import RasterGrid, RasterRequest, RasterWindow

    def make_window(direction: np.ndarray, valid: np.ndarray | None = None) -> RasterWindow:
        h, w = direction.shape
        d_valid = np.ones((h, w), dtype=bool) if valid is None else valid
        aff = Affine(1, 0, 0, 0, -1, float(h))
        crs = CRS.from_epsg(4326)
        grid = RasterGrid(crs, aff, w, h, "d", "a")
        req = RasterRequest(0, 0, w, h, grid, object(), 0)
        return RasterWindow(
            direction.astype(np.int8), np.ones((h, w), dtype=np.int32), d_valid, d_valid, aff, crs, "cells", req
        )

    # 1x1 window
    w1 = make_window(np.array([[0]]))
    out1 = refinement_mod._trace(w1, np.array([[True]]), 0, 0)
    assert out1.flags["C_CONTIGUOUS"]
    np.testing.assert_array_equal(out1, [[True]])

    # 8-neighbor winding snake + disconnected cycle + mask=False blocker:
    # Directions: 1:E, 2:SE, 3:S, 4:SW, 5:W, 6:NW, 7:N, 8:NE
    # Chain: (2,2)->E(1) (2,3)->S(3) (3,3)->W(5) (3,2)->N(7) (2,2) is a cycle!
    # Separate snake leading into (0,0): (0,2)->W(5) (0,1)->W(5) (0,0), plus (1,0)->N(7) (0,0), plus (1,1)->NW(6) (0,0)
    dirs = np.array(
        [
            [0, 5, 5, 1],
            [7, 6, 3, 1],
            [1, 1, 1, 3],
            [1, 1, 7, 5],
        ],
        dtype=np.int8,
    )
    mask = np.ones((4, 4), dtype=bool)
    mask[0, 2] = False  # blocks (0,2) even though it points W to (0,1)
    # Trace from cycle at (2,2) terminates cleanly without infinite loop
    cyc = refinement_mod._trace(make_window(dirs), mask, 2, 2)
    assert cyc.flags["C_CONTIGUOUS"]
    assert cyc[2, 2] and cyc[2, 3] and cyc[3, 3] and cyc[3, 2]
    assert not cyc[0, 0]

    # Trace from (0,0): collects (0,0), (0,1), (1,0), (1,1), not (0,2) because masked out
    head = refinement_mod._trace(make_window(dirs), mask, 0, 0)
    assert head.flags["C_CONTIGUOUS"]
    expected = np.zeros((4, 4), dtype=bool)
    expected[0, 0] = expected[0, 1] = expected[1, 0] = expected[1, 1] = True
    np.testing.assert_array_equal(head, expected)


def test_granular_geometry_equivalence_and_delineate_avoids_double_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pourpoint.refinement as refinement_mod
    from pourpoint import delineate

    root = make_spatial_bundle(tmp_path / "fabric")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["auxiliary"] = []
    path.write_text(json.dumps(manifest))
    dir_arr = np.full((10, 20), 8, dtype="uint8")
    dir_arr[4, 10] = 0
    acc_arr = np.ones((10, 20), dtype="int32")
    acc_arr[4, 10] = 999
    add_pair(
        root,
        encoding="grass",
        direction=dir_arr,
        accumulation=acc_arr,
        transform=from_origin(0.0, 2.0, 0.2, 0.2),
    )

    union_calls = 0
    orig_union_subbasins = refinement_mod.union_subbasins

    def counted_union(parts: object, **kwargs: object) -> object:
        nonlocal union_calls
        union_calls += 1
        return orig_union_subbasins(parts, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(refinement_mod, "union_subbasins", counted_union)
    with HydroFabric(root) as fabric:
        # Spatial bundle catchments 10=(1..1.25, 1..1.25) and 20=(2..2.25, 1..1.25).
        # Delineating at Point(2.1, 1.1) with refine=True, merge=True computes the merged mask once
        # inside refine() and reuses refined.geometry rather than running a second merge().
        delin = delineate(fabric=fabric, target=Point(2.1, 1.1), refine=True, merge=True)
        assert union_calls == 1
        assert delin.geometry is not None
        assert delin.geometry.equals(merge(subbasins=delin.subbasins))
        unmerged = delineate(fabric=fabric, target=Point(2.1, 1.1), refine=True, merge=False)
        assert unmerged.geometry is None

        # Explicitly exercise fully covered, partially clipped, and non-intersecting subbasins:
        # Outlet at (row=4, col=10) -> [2.0, 2.2] x [1.0, 1.2], incoming from (4,9) -> [1.8, 2.0] x [1.0, 1.2]
        # and upstream row=4 cols 5..9 ([1.0, 2.0] x [1.0, 1.2]) with constant E=1 direction inside mask.
        sub_tbl = pa.table(
            {
                "id": [101, 102, 103],
                "geometry": [
                    box(1.8, 1.0, 2.2, 1.2).wkb,  # covered inside traced x in [1.0, 2.2]
                    box(1.0, 0.8, 1.8, 1.2).wkb,  # partially clipped vertically
                    box(3.0, 1.0, 3.6, 1.2).wkb,  # disjoint
                ],
            }
        )
        units = [Subbasin(sub_tbl, i) for i in range(3)]
        refined = refine(fabric=fabric, subbasins=units, target=Point(2.1, 1.1), window_size=1)
        assert [part.id for part in refined.subbasins] == [101, 102]
        assert refined.geometry.equals(merge(subbasins=refined.subbasins))
        assert refined.geometry.equals(box(1.0, 1.0, 2.2, 1.2))


def test_composable_merge_and_refine_with_delineation_and_selection_results(tmp_path: Path) -> None:
    from pourpoint import BatchResult, Target, delineate
    from pourpoint.refinement import FootprintRefinement, SubbasinContribution
    from pourpoint.results import DelineationResult, SelectionResult, UpstreamCoverage

    root = make_spatial_bundle(tmp_path / "comp")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest_path.write_text(json.dumps(manifest))
    dir_arr = np.full((10, 20), 8, dtype="uint8")
    dir_arr[4, 10] = 0
    acc_arr = np.ones((10, 20), dtype="int32")
    acc_arr[4, 10] = 999
    add_pair(
        root,
        encoding="grass",
        direction=dir_arr,
        accumulation=acc_arr,
        transform=from_origin(0.0, 2.0, 0.2, 0.2),
    )

    with HydroFabric(root, cache=False) as fabric:
        # SubbasinContribution equality/hashing/concise repr
        tbl = pa.table({"id": [10], "geometry": [box(1.0, 1.0, 1.25, 1.25).wkb]})
        c1 = SubbasinContribution(Subbasin(tbl, 0), box(1.0, 1.0, 1.2, 1.2))
        c2 = SubbasinContribution(Subbasin(tbl, 0), box(1.0, 1.0, 1.2, 1.2))
        assert c1 == c2
        assert hash(c1) == hash(c2)
        assert repr(c1) == "SubbasinContribution(id=10, geometry=Polygon)"

        # DelineationResult(refine=False) composes directly into refine() using post-snap target and preserving coverage
        cov = UpstreamCoverage(
            "incomplete", declared_completeness="partial", cut_counts={10: 1}, unverified_unit_ids=()
        )
        unrefined = delineate(fabric=fabric, target=Point(2.1, 1.1), refine=False, merge=False)
        refined_from_delin = refine(fabric=fabric, subbasins=unrefined)
        assert refined_from_delin.coverage == unrefined.coverage
        assert refined_from_delin.geometry.equals(box(2.0, 1.0, 2.2, 1.2))

        # Manual unrefined DelineationResult uses post-snap target (Point(2.1, 1.1)), not original_target
        s_tbl = pa.table({"id": [20], "geometry": [box(2.0, 1.0, 2.25, 1.25).wkb]})
        manual_unrefined = DelineationResult(
            [Subbasin(s_tbl, 0)], None, None, None, Point(9.9, 9.9), Point(2.1, 1.1), {}, cov
        )
        refined_manual = refine(fabric=fabric, subbasins=manual_unrefined)
        assert refined_manual.coverage == cov

        # SelectionResult with omitted target defaults to selection.target
        sel = SelectionResult([Subbasin(s_tbl, 0)], cov, level=1, target=Point(2.1, 1.1))
        refined_from_sel = refine(fabric=fabric, subbasins=sel)
        assert refined_from_sel.coverage == cov
        assert refined_from_sel.geometry.equals(box(2.0, 1.0, 2.2, 1.2))

        # Already-refined DelineationResult is rejected cleanly (single & batch)
        already_refined = delineate(fabric=fabric, target=Point(2.1, 1.1), refine=True, merge=True)
        with pytest.raises(ValueError, match="already-refined"):
            refine(fabric=fabric, subbasins=already_refined)
        bad_batch = BatchResult([already_refined], [Target(0, "t0", Point(2.1, 1.1))], [])
        with pytest.raises(ValueError, match="already-refined"):
            refine(fabric=fabric, subbasins=bad_batch)

        # Unrefined DelineationResult BatchResult composes cleanly
        good_batch = BatchResult([manual_unrefined], [Target(0, "t0", Point(9.9, 9.9))], [])
        batch_refined = refine(fabric=fabric, subbasins=good_batch)
        assert batch_refined.results[0] is not None
        assert batch_refined.results[0].coverage == cov

        # merge() composes with all result types and nested sequences
        assert merge(subbasins=sel).equals(box(2.0, 1.0, 2.25, 1.25))
        assert merge(subbasins=unrefined).equals(merge(subbasins=unrefined.subbasins))
        assert merge(subbasins=already_refined) is already_refined.geometry
        assert merge(subbasins=refined_from_sel) is refined_from_sel.geometry
        fp_res = FootprintRefinement(refined_from_sel.geometry, refined_from_sel.outlet_cell, refined_from_sel.rasters)
        assert merge(subbasins=fp_res) is fp_res.geometry
        merged_list = merge(subbasins=[sel, refined_from_sel])
        assert len(merged_list) == 2
        assert merged_list[0].equals(box(2.0, 1.0, 2.25, 1.25))
        assert merged_list[1].equals(refined_from_sel.geometry)
