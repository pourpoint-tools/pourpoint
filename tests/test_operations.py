from pathlib import Path

import pytest
from shapely.geometry import Point, box
from spatial_fixture import make_spatial_bundle

from pourpoint import HydroFabric, SelectionResult, UpstreamCoverage
from pourpoint.operations import select_upstream_subbasins
from pourpoint.results import OperationError


def test_point_selection_is_inclusive_and_requested_level(tmp_path: Path) -> None:
    source = make_spatial_bundle(tmp_path / "source")
    with HydroFabric(source, cache=False) as fabric:
        selected = select_upstream_subbasins(fabric=fabric, target=Point(2.1, 1.1))
        assert isinstance(selected, SelectionResult)
        assert [unit.id for unit in selected.subbasins] == [20, 10]
        assert selected.subbasins[1].attributes["source_value"] is None
        assert selected.coverage == UpstreamCoverage(
            status="complete",
            declared_completeness="complete",
            cuts_column=None,
            cut_counts={},
            unverified_unit_ids=(),
        )
        assert selected.level == 1
        assert selected.target.equals(Point(2.1, 1.1))
        assert selected.provenance["fabric_name"] == "example"
        with pytest.raises(OperationError, match="coverage"):
            select_upstream_subbasins(fabric=fabric, target=Point(3.1, 1.1))
        # Polygon boundary membership is inclusive.
        selected = select_upstream_subbasins(fabric=fabric, target=box(1.25, 1, 1.5, 1.25))
        assert [unit.id for unit in selected.subbasins] == [10]


def test_batch_identity_partial_failure_and_duplicate_targets(tmp_path: Path) -> None:
    from pourpoint.results import BatchResult

    source = make_spatial_bundle(tmp_path / "source")
    with HydroFabric(source, cache=True, cache_dir=tmp_path / "cache") as fabric:
        result = select_upstream_subbasins(
            fabric=fabric, target={"a": Point(2.1, 1.1), "bad": Point(9, 9), "b": Point(2.1, 1.1)}, n_jobs=2
        )
        assert isinstance(result, BatchResult)
        assert [target.name for target in result.targets] == ["a", "bad", "b"]
        assert [[unit.id for unit in slot.subbasins] if slot is not None else None for slot in result.results] == [
            [20, 10],
            None,
            [20, 10],
        ]
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [
            (1, "bad", "selection.no_coverage")
        ]
        first = select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1), Point(1.1, 1.1)], n_jobs=1)
        second = select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1)], n_jobs=1)
        assert len({t.name for t in first.targets + second.targets}) == 3
    with HydroFabric(source, cache=True, cache_dir=tmp_path / "cache", offline=True) as fabric:
        replay = select_upstream_subbasins(fabric=fabric, target={"a": Point(2.1, 1.1)}, n_jobs=2)
        assert replay.results[0] is not None
        assert [u.id for u in replay.results[0].subbasins] == [20, 10]


def test_unavailable_connectivity_and_invalid_shared_configuration(tmp_path: Path) -> None:
    import json

    source = make_spatial_bundle(tmp_path / "source")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["catchment_connectivity"] = [{"level": 1, "status": "not_supplied"}]
    manifest_path.write_text(json.dumps(manifest))
    with HydroFabric(source, cache=False) as fabric:
        with pytest.raises(OperationError) as caught:
            select_upstream_subbasins(fabric=fabric, target=Point(1.1, 1.1))
        assert caught.value.code == "selection.connectivity_unavailable"
        with pytest.raises(ValueError, match="n_jobs"):
            select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1)], n_jobs=0)


def test_batch_does_not_swallow_programming_or_integrity_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected programming failure")

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        monkeypatch.setattr(fabric, "candidates", broken)
        with pytest.raises(RuntimeError, match="unexpected programming"):
            select_upstream_subbasins(fabric=fabric, target=[Point(1.1, 1.1)])


def test_batch_worker_bound_context_reuse_and_concurrency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from pourpoint._batch import execute, identify

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    seen: dict[int, list[int]] = {}
    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:

        def operation(reader: HydroFabric, point: Point) -> int:
            assert reader is not fabric
            thread = threading.get_ident()
            with lock:
                first = thread not in seen
                seen.setdefault(thread, []).append(id(reader))
            if first:
                barrier.wait(timeout=10)
            return int(point.geometry.x)

        result = execute(fabric, identify([Point(i, 1) for i in range(6)]), operation, 2)
    assert result.results == list(range(6))
    assert len(seen) == 2
    assert sum(len(ids) for ids in seen.values()) == 6
    assert all(len(set(ids)) == 1 for ids in seen.values())


def test_batch_dynamic_scheduling_does_not_strand_idle_workers(tmp_path: Path) -> None:
    import threading

    from pourpoint._batch import execute, identify

    slow_started = threading.Event()
    fast_done = threading.Event()
    seen_fast = 0
    lock = threading.Lock()
    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:

        def operation(reader: HydroFabric, target_item: object) -> int:
            nonlocal seen_fast
            idx = target_item.index  # type: ignore[attr-defined]
            if idx == 0:
                slow_started.set()
                assert fast_done.wait(timeout=1.0)
                return 0
            assert slow_started.wait(timeout=1.0)
            with lock:
                seen_fast += 1
                if seen_fast == 5:
                    fast_done.set()
            return idx

        result = execute(fabric, identify([Point(i, 1) for i in range(6)]), operation, 2)
    assert result.results == list(range(6))


def test_delineate_composes_selection_and_requested_merge(tmp_path: Path) -> None:
    from pourpoint.operations import delineate

    source = make_spatial_bundle(tmp_path / "source")
    point = Point(2.1, 1.1)
    with HydroFabric(source, cache=False) as fabric:
        result = delineate(fabric=fabric, target=point, merge=False)
        assert [unit.id for unit in result.subbasins] == [20, 10]
        assert result.geometry is None
        assert result.outlet_cell is None and result.rasters is None
        assert result.original_target.equals(point) and result.target.equals(point)
        assert result.provenance["fabric_name"] == "example"
        assert result.coverage.status == "complete"
        assert result.coverage.is_complete


def test_empty_target_is_valid_empty_selection(tmp_path: Path) -> None:
    from shapely.geometry import Polygon

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        selected = select_upstream_subbasins(fabric=fabric, target=Polygon())
        assert isinstance(selected, SelectionResult)
        assert selected.subbasins == []
        assert selected.coverage.status == "not_assessed"
        assert not selected.coverage.is_complete


def test_point_boundary_ownership_uses_smallest_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyarrow as pa

    from pourpoint.features import Subbasin

    table = pa.table({"id": [20, 10], "level": [1, 1], "geometry": [box(0, 0, 1, 1).wkb, box(1, 0, 2, 1).wkb]})
    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        monkeypatch.setattr(fabric, "candidates", lambda *a, **kw: table)
        monkeypatch.setattr(fabric, "traverse", lambda domain, ids: ids)
        monkeypatch.setattr(fabric, "subbasins", lambda ids: [Subbasin(table, 1 if i == 10 else 0) for i in ids])
        assert [u.id for u in select_upstream_subbasins(fabric=fabric, target=Point(1, 0.5)).subbasins] == [10]
        assert [u.id for u in select_upstream_subbasins(fabric=fabric, target=box(0.5, 0.25, 1.5, 0.75)).subbasins] == [
            10,
            20,
        ]


def test_explicit_batch_refinement_preserves_selection_identity(tmp_path: Path) -> None:
    from pourpoint import refine
    from pourpoint.results import BatchResult

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        selection = select_upstream_subbasins(fabric=fabric, target={"ok": Point(1.1, 1.1), "bad": Point(9, 9)})
        refined = refine(fabric=fabric, subbasins=selection, n_jobs=2)
        assert isinstance(refined, BatchResult)
        assert refined.targets is selection.targets
        assert refined.results == [None, None]
        assert [(i.index, i.name, i.code) for i in refined.issues] == [
            (0, "ok", "refinement.missing_rasters"),
            (1, "bad", "selection.no_coverage"),
        ]


def test_delineate_refinement_failure_is_not_coarse_success(tmp_path: Path) -> None:
    from pourpoint import delineate

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        with pytest.raises(OperationError) as caught:
            delineate(fabric=fabric, target=Point(1.1, 1.1), refine=True)
        assert caught.value.code == "refinement.missing_rasters"
        batch = delineate(fabric=fabric, target={"station": Point(1.1, 1.1)}, refine=True)
        assert batch.results == [None]
        assert batch.issues[0].code == "refinement.missing_rasters"


def test_refined_named_batch_exports_one_pair_per_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import sqlite3

    import numpy as np
    import pyarrow as pa
    from conftest import add_pair
    from rasterio.transform import from_origin

    from pourpoint import QGISBundle, delineate
    from pourpoint.features import Subbasin

    source = make_spatial_bundle(tmp_path / "source")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest_path.write_text(json.dumps(manifest))
    add_pair(
        source,
        direction=np.array([[8, 8, 8, 0]], dtype="uint8"),
        accumulation=np.array([[1, 2, 3, 4]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    table = pa.table({"id": [1], "level": [1], "geometry": [box(0, 0, 4, 1).wkb]})
    with HydroFabric(source, cache=False) as fabric:
        monkeypatch.setattr(fabric, "candidates", lambda *a, **kw: table)
        monkeypatch.setattr(fabric, "traverse", lambda domain, ids: ids)
        monkeypatch.setattr(fabric, "subbasins", lambda ids: [Subbasin(table, 0)])
        result = delineate(
            fabric=fabric, target={"upper": Point(1.5, 0.5), "lower": Point(2.5, 0.5)}, refine=True, window_size=1
        )
        assert result.results[0].geometry.equals(box(0, 0, 2, 1))
        assert result.results[1].geometry.equals(box(0, 0, 3, 1))
        folder = QGISBundle().add_result(result).write(tmp_path / "export")
    assert len(list(folder.glob("*.tif"))) == 4
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        names = db.execute("SELECT DISTINCT target_name FROM layer_0").fetchall()
        assert names == [("upper",)]


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_refinement_batch_isolates_empty_point(tmp_path: Path, n_jobs: int) -> None:
    import json

    import numpy as np
    from conftest import add_pair
    from rasterio.transform import from_origin

    from pourpoint import refine

    source = make_spatial_bundle(tmp_path / "source")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest_path.write_text(json.dumps(manifest))
    add_pair(
        source,
        direction=np.array([[0]], dtype="uint8"),
        accumulation=np.array([[1]], dtype="int32"),
        transform=from_origin(1, 1.25, 0.25, 0.25),
    )
    with HydroFabric(source, cache=False) as fabric:
        selection = select_upstream_subbasins(
            fabric=fabric, target={"empty": Point(), "station": Point(1.125, 1.125)}, n_jobs=n_jobs
        )
        result = refine(fabric=fabric, subbasins=selection, window_size=1, n_jobs=n_jobs)
        assert result.targets is selection.targets
        assert result.results[0] is None
        successful = result.results[1]
        assert successful is not None
        assert successful.subbasins[0].id == 10
        assert successful.subbasins[0].geometry.equals(box(1, 1, 1.25, 1.25))
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [
            (0, "empty", "refinement.point_required")
        ]
        with pytest.raises(ValueError, match="window_size"):
            refine(fabric=fabric, subbasins=selection, window_size=2, n_jobs=n_jobs)


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_snapping_delineation_batch_isolates_empty_point(tmp_path: Path, n_jobs: int) -> None:
    from shapely.geometry import LineString

    from pourpoint import delineate

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        targets = {"empty": Point(), "station": Point(1.125, 1.125)}
        stream = LineString([(1, 1), (1.25, 1.25)])
        result = delineate(fabric=fabric, target=targets, snap_onto=stream, n_jobs=n_jobs)
        assert [target.name for target in result.targets] == ["empty", "station"]
        assert result.results[0] is None
        successful = result.results[1]
        assert successful is not None
        assert [unit.id for unit in successful.subbasins] == [10]
        assert successful.target.equals(targets["station"])
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [
            (0, "empty", "snap.point_required")
        ]
        with pytest.raises(ValueError, match="radius"):
            delineate(fabric=fabric, target=targets, snap_onto=stream, radius=-1, n_jobs=n_jobs)
        with pytest.raises(ValueError, match="stream line geometry"):
            delineate(fabric=fabric, target=targets, snap_onto=box(1, 1, 1.25, 1.25), n_jobs=n_jobs)


@pytest.mark.parametrize("point", [Point(float("inf"), 1), Point(181, 1), Point(1, 91), Point()])
def test_snapping_invalid_point_has_single_and_batch_failure_meaning(tmp_path: Path, point: Point) -> None:
    from shapely.geometry import LineString

    from pourpoint import delineate, snap

    stream = LineString([(1, 1), (1.25, 1.25)])
    with pytest.raises(OperationError) as caught:
        snap(target=point, onto=stream, radius=100)
    assert caught.value.code == "snap.point_required"
    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        result = delineate(
            fabric=fabric,
            target={"invalid": point, "station": Point(1.125, 1.125)},
            snap_onto=stream,
        )
        assert result.results[0] is None
        assert result.results[1] is not None
        assert [unit.id for unit in result.results[1].subbasins] == [10]
        assert [(issue.index, issue.name, issue.code) for issue in result.issues] == [(0, "invalid", caught.value.code)]


def _make_connectivity_bundle(
    path: Path,
    *,
    connectivity: list[dict[str, object]] | None,
    cuts: dict[int, object] | None = None,
    cuts_column: str | None = "upstream_boundary_cuts",
    cuts_type: object | None = None,
    include_upstream_relationship: bool = True,
    with_rasters: bool = False,
) -> Path:
    import hashlib
    import json

    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from conftest import add_pair
    from rasterio.transform import from_origin
    from spatial_fixture import write_ipc

    path.mkdir(parents=True, exist_ok=True)
    # 7 non-overlapping level-1 units:
    # 1 -> 2 (complete branch)
    # 3 (cut) -> 4 (zero-cut stranded seed)
    # 5 (null) -> 6 (zero-cut stranded seed)
    # (4, 6) -> 7 (mixed confluence with both cut and null ancestors)
    # plus 8 at level 2 (no upstream) for multi-level testing
    ids = [1, 2, 3, 4, 5, 6, 7, 8]
    levels = [1, 1, 1, 1, 1, 1, 1, 2]
    upstream = {
        1: [],
        2: [1],
        3: [],
        4: [3],
        5: [],
        6: [5],
        7: [4, 6],
        8: [],
    }
    default_cuts: dict[int, object] = {1: 0, 2: 0, 3: 2, 4: 0, 5: None, 6: 0, 7: 0, 8: 0}
    cut_values = default_cuts if cuts is None else cuts
    arrow_type = pa.int32() if cuts_type is None else cuts_type
    fields = [
        pa.field("id", pa.int64(), False),
        pa.field("level", pa.int16(), False),
        pa.field("geometry", pa.binary(), False),
        pa.field("bbox", pa.struct([(k, pa.float32()) for k in ("xmin", "ymin", "xmax", "ymax")])),
    ]
    if cuts_column is not None:
        fields.append(pa.field(cuts_column, arrow_type, True))
    rows = []
    for uid, lvl in zip(ids, levels, strict=True):
        x = float(uid - 1)
        row: dict[str, object] = {
            "id": uid,
            "level": lvl,
            "geometry": box(x, 0, x + 1, 1).wkb,
            "bbox": {"xmin": x, "ymin": 0.0, "xmax": x + 1, "ymax": 1.0},
        }
        if cuts_column is not None:
            row[cuts_column] = cut_values[uid]
        rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(fields)), path / "catchments.parquet")
    data_digest = hashlib.sha256((path / "catchments.parquet").read_bytes()).hexdigest()
    lookup_schema = pa.schema(
        [pa.field("id", pa.int64(), False), pa.field("block", pa.int32(), False), pa.field("row", pa.int32(), False)],
        metadata={b"hfx:data_sha256": data_digest.encode()},
    )
    write_ipc(
        path / "catchments-lookup.arrow",
        pa.Table.from_pylist(
            [{"id": uid, "block": 0, "row": idx} for idx, uid in enumerate(ids)], schema=lookup_schema
        ),
    )
    ranges = {
        "version": 1,
        "batches": [{"batch": b, "min_id": b * 2 + 1, "max_id": b * 2 + 2, "rows": 2} for b in range(4)],
    }
    (path / "catchments-lookup.json").write_text(json.dumps(ranges))
    rel_fields = [pa.field("id", pa.int64(), False), pa.field("parent_ids", pa.list_(pa.int64()), False)]
    rel_rows = [{"id": uid, "parent_ids": []} for uid in ids]
    rel_columns = {"parent_ids": {"source_domain": "catchments", "target_domain": "catchments"}}
    if include_upstream_relationship:
        rel_fields.append(pa.field("upstream_ids", pa.list_(pa.int64()), False))
        for row, uid in zip(rel_rows, ids, strict=True):
            row["upstream_ids"] = upstream[uid]
        rel_columns["upstream_ids"] = {"source_domain": "catchments", "target_domain": "catchments"}
    write_ipc(path / "catchments-relationships.arrow", pa.Table.from_pylist(rel_rows, schema=pa.schema(rel_fields)))
    (path / "catchments-relationships.json").write_text(json.dumps(ranges))
    manifest: dict[str, object] = {
        "format_version": "0.4.0",
        "representation": "spatial-geoparquet-adjacency",
        "representation_version": 1,
        "fabric_name": "connectivity-fixture",
        "crs": "EPSG:4326",
        "bbox": [0, 0, 8, 1],
        "families": {
            "catchments": {
                "data": "catchments.parquet",
                "data_sha256": data_digest,
                "count": 8,
                "lookup": "catchments-lookup.arrow",
                "directory": "catchments-lookup.json",
            }
        },
        "relationships": {
            "catchments": {
                "data": "catchments-relationships.arrow",
                "directory": "catchments-relationships.json",
                "count": 8,
                "columns": rel_columns,
                "feature_artifacts": {"catchments": {"data": "catchments.parquet", "data_sha256": data_digest}},
            }
        },
        "artifacts": {},
    }
    if connectivity is not None:
        manifest["catchment_connectivity"] = connectivity
    if with_rasters:
        manifest["auxiliary"] = []
    artifacts: dict[str, object] = {}
    for artifact in path.iterdir():
        raw = artifact.read_bytes()
        artifacts[artifact.name] = {
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "head_sha256": hashlib.sha256(raw[:65536]).hexdigest(),
            "tail_sha256": hashlib.sha256(raw[-65536:]).hexdigest(),
        }
    manifest["artifacts"] = artifacts
    (path / "manifest.json").write_text(json.dumps(manifest))
    if with_rasters:
        # Unit 4 (cell 3) has terminal direction 0 so cell 2 (cut unit 3) is pruned during refinement.
        add_pair(
            path,
            direction=np.array([[8, 0, 0, 0, 8, 0, 0, 0]], dtype="uint8"),
            accumulation=np.array([[1, 2, 1, 10, 1, 2, 20, 1]], dtype="int32"),
            transform=from_origin(0, 1, 1, 1),
        )
    return path


def test_partial_connectivity_complete_closure_succeeds_without_opt_in(tmp_path: Path) -> None:
    from pourpoint import delineate

    source = _make_connectivity_bundle(
        tmp_path / "partial",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
                "derivation": "river_local_associations",
            }
        ],
    )
    with HydroFabric(source, cache=False) as fabric:
        selection = select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1)
        delineated = delineate(fabric=fabric, target=Point(1.5, 0.5), level=1)
    # Usable after fabric closure.
    expected_coverage = UpstreamCoverage(
        status="complete",
        declared_completeness="partial",
        cuts_column="upstream_boundary_cuts",
        cut_counts={},
        unverified_unit_ids=(),
    )
    assert [u.id for u in selection.subbasins] == [2, 1]
    assert selection.coverage == expected_coverage
    assert selection.coverage.is_complete
    assert selection.coverage.cut_unit_ids == ()
    assert selection.coverage.total_cuts == 0
    assert delineated.coverage == expected_coverage
    assert [u.id for u in delineated.subbasins] == [2, 1]


@pytest.mark.parametrize(
    ("x", "expected_code", "expected_status", "expected_cuts", "expected_unverified"),
    [
        (2.5, "selection.incomplete_coverage", "incomplete", {3: 2}, ()),
        (3.5, "selection.incomplete_coverage", "incomplete", {3: 2}, ()),
        (4.5, "selection.unverified_coverage", "unverified", {}, (5,)),
        (5.5, "selection.unverified_coverage", "unverified", {}, (5,)),
        (6.5, "selection.incomplete_and_unverified_coverage", "incomplete_and_unverified", {3: 2}, (5,)),
    ],
)
def test_partial_connectivity_rejects_cut_unverified_and_mixed_closures_by_default(
    tmp_path: Path,
    x: float,
    expected_code: str,
    expected_status: str,
    expected_cuts: dict[int, int],
    expected_unverified: tuple[int, ...],
) -> None:
    from pourpoint import delineate

    source = _make_connectivity_bundle(
        tmp_path / "partial",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
    )
    point = Point(x, 0.5)
    with HydroFabric(source, cache=False) as fabric:
        with pytest.raises(OperationError) as sel_err:
            select_upstream_subbasins(fabric=fabric, target=point, level=1)
        assert sel_err.value.code == expected_code

        with pytest.raises(OperationError) as del_err:
            delineate(fabric=fabric, target=point, level=1, refine=True)
        assert del_err.value.code == expected_code

        opted_sel = select_upstream_subbasins(fabric=fabric, target=point, level=1, allow_incomplete=True)
        opted_del = delineate(fabric=fabric, target=point, level=1, allow_incomplete=True)

    assert opted_sel.coverage.status == expected_status
    assert opted_sel.coverage.declared_completeness == "partial"
    assert opted_sel.coverage.cuts_column == "upstream_boundary_cuts"
    assert dict(opted_sel.coverage.cut_counts or {}) == expected_cuts
    assert opted_sel.coverage.unverified_unit_ids == expected_unverified
    assert opted_sel.coverage.cut_unit_ids == tuple(sorted(expected_cuts))
    assert opted_sel.coverage.total_cuts == sum(expected_cuts.values())
    assert not opted_sel.coverage.is_complete
    assert opted_del.coverage == opted_sel.coverage


def test_delineate_assesses_coverage_before_refinement_or_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pourpoint.refinement as refinement_mod
    from pourpoint import delineate

    source = _make_connectivity_bundle(
        tmp_path / "partial",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
    )
    called: list[str] = []
    monkeypatch.setattr(refinement_mod, "refine", lambda **kw: called.append("refine"))
    monkeypatch.setattr(refinement_mod, "merge", lambda **kw: called.append("merge"))
    with HydroFabric(source, cache=False) as fabric:
        with pytest.raises(OperationError) as err:
            delineate(fabric=fabric, target=Point(3.5, 0.5), level=1, refine=True, merge=True)
        assert err.value.code == "selection.incomplete_coverage"
        assert called == []


def test_custom_cuts_column_and_multilevel_declarations(tmp_path: Path) -> None:
    source = _make_connectivity_bundle(
        tmp_path / "custom",
        cuts_column="custom_cuts",
        connectivity=[
            {"level": 1, "status": "supplied", "completeness": "partial", "upstream_cuts_column": "custom_cuts"},
            {"level": 2, "status": "not_supplied"},
        ],
    )
    with HydroFabric(source, cache=False) as fabric:
        ok = select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1)
        assert ok.coverage.cuts_column == "custom_cuts"
        assert ok.coverage.status == "complete"

        opted = select_upstream_subbasins(fabric=fabric, target=Point(3.5, 0.5), level=1, allow_incomplete=True)
        assert opted.coverage.cuts_column == "custom_cuts"
        assert dict(opted.coverage.cut_counts or {}) == {3: 2}

        with pytest.raises(OperationError) as unavailable:
            select_upstream_subbasins(fabric=fabric, target=Point(7.5, 0.5), level=2, allow_incomplete=True)
        assert unavailable.value.code == "selection.connectivity_unavailable"


@pytest.mark.parametrize(
    ("connectivity", "expected_declared"),
    [
        (None, None),
        ([], None),
        ([{"level": 1, "status": "supplied"}], None),
        ([{"level": 1, "status": "supplied", "completeness": "partial"}], "partial"),
    ],
)
def test_missing_or_uninstrumented_completeness_requires_opt_in_and_stays_not_assessed(
    tmp_path: Path,
    connectivity: list[dict[str, object]] | None,
    expected_declared: str | None,
) -> None:
    from shapely.geometry import Polygon

    from pourpoint import delineate

    source = _make_connectivity_bundle(tmp_path / "missing-meta", connectivity=connectivity, cuts_column=None)
    with HydroFabric(source, cache=False) as fabric:
        # Empty target remains valid without opt-in and never claims complete.
        empty = select_upstream_subbasins(fabric=fabric, target=Polygon(), level=1)
        assert empty.subbasins == []
        assert empty.coverage.status == "not_assessed"
        assert empty.coverage.reason

        with pytest.raises(OperationError) as sel_err:
            select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1)
        assert sel_err.value.code == "selection.coverage_not_assessed"

        with pytest.raises(OperationError) as del_err:
            delineate(fabric=fabric, target=Point(1.5, 0.5), level=1)
        assert del_err.value.code == "selection.coverage_not_assessed"

        opted = select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1, allow_incomplete=True)
        assert [u.id for u in opted.subbasins] == [2, 1]
        assert opted.coverage.status == "not_assessed"
        assert opted.coverage.declared_completeness == expected_declared
        assert opted.coverage.cut_counts is None
        assert opted.coverage.unverified_unit_ids is None
        assert opted.coverage.cut_unit_ids is None
        assert opted.coverage.total_cuts is None
        assert opted.coverage.reason
        assert not opted.coverage.is_complete


def test_explicit_complete_without_cuts_column_succeeds_and_not_supplied_ignores_opt_in(tmp_path: Path) -> None:
    complete_bundle = _make_connectivity_bundle(
        tmp_path / "complete",
        connectivity=[{"level": 1, "status": "supplied", "completeness": "complete"}],
        cuts_column=None,
    )
    with HydroFabric(complete_bundle, cache=False) as fabric:
        res = select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1)
        assert res.coverage == UpstreamCoverage(
            status="complete",
            declared_completeness="complete",
            cuts_column=None,
            cut_counts={},
            unverified_unit_ids=(),
        )

    no_rel_bundle = _make_connectivity_bundle(
        tmp_path / "no-rel",
        connectivity=[{"level": 1, "status": "supplied", "completeness": "complete"}],
        cuts_column=None,
        include_upstream_relationship=False,
    )
    with HydroFabric(no_rel_bundle, cache=False) as fabric:
        with pytest.raises(OperationError) as err:
            select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1, allow_incomplete=True)
        assert err.value.code == "selection.connectivity_unavailable"


def test_corrupt_cuts_column_or_contradictory_complete_raises_fatal_error_even_with_opt_in(tmp_path: Path) -> None:
    import pyarrow as pa

    from pourpoint import delineate

    missing_col = _make_connectivity_bundle(
        tmp_path / "missing-col",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
        cuts_column=None,
    )
    for n_jobs in (1, 2):
        with HydroFabric(missing_col, cache=False) as fabric, pytest.raises(ValueError, match="missing"):
            select_upstream_subbasins(
                fabric=fabric, target=[Point(1.5, 0.5)], level=1, allow_incomplete=True, n_jobs=n_jobs
            )

    for label, cuts_type, bad_cuts in (
        ("str", pa.string(), {1: "0", 2: "0", 3: "2", 4: "0", 5: None, 6: "0", 7: "0", 8: "0"}),
        ("bool", pa.bool_(), {1: False, 2: False, 3: True, 4: False, 5: None, 6: False, 7: False, 8: False}),
        ("float32", pa.float32(), {1: 0.0, 2: 0.0, 3: 2.0, 4: 0.0, 5: None, 6: 0.0, 7: 0.0, 8: 0.0}),
    ):
        wrong_type = _make_connectivity_bundle(
            tmp_path / f"wrong-type-{label}",
            connectivity=[
                {
                    "level": 1,
                    "status": "supplied",
                    "completeness": "partial",
                    "upstream_cuts_column": "upstream_boundary_cuts",
                }
            ],
            cuts=bad_cuts,
            cuts_type=cuts_type,
        )
        with HydroFabric(wrong_type, cache=False) as fabric, pytest.raises(ValueError, match="integer"):
            delineate(fabric=fabric, target=Point(1.5, 0.5), level=1, allow_incomplete=True)

    negative_val = _make_connectivity_bundle(
        tmp_path / "negative",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
        cuts={1: -1, 2: 0, 3: 0, 4: 0, 5: None, 6: 0, 7: 0, 8: 0},
    )
    with HydroFabric(negative_val, cache=False) as fabric:
        with pytest.raises(ValueError, match="negative"):
            select_upstream_subbasins(fabric=fabric, target=Point(1.5, 0.5), level=1, allow_incomplete=True)
        for n_jobs in (1, 2):
            with pytest.raises(ValueError, match="negative"):
                select_upstream_subbasins(
                    fabric=fabric,
                    target={"ok": Point(3.5, 0.5), "corrupt": Point(1.5, 0.5)},
                    level=1,
                    allow_incomplete=True,
                    n_jobs=n_jobs,
                )

    contradictory = _make_connectivity_bundle(
        tmp_path / "contradictory",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "complete",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
    )
    with HydroFabric(contradictory, cache=False) as fabric, pytest.raises(ValueError, match="complete"):
        select_upstream_subbasins(fabric=fabric, target=Point(3.5, 0.5), level=1, allow_incomplete=True)


def test_non_boolean_allow_incomplete_raises_type_error(tmp_path: Path) -> None:
    from pourpoint import delineate

    with HydroFabric(make_spatial_bundle(tmp_path / "source"), cache=False) as fabric:
        with pytest.raises(TypeError, match="allow_incomplete"):
            select_upstream_subbasins(fabric=fabric, target=Point(1.1, 1.1), allow_incomplete=1)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="allow_incomplete"):
            delineate(fabric=fabric, target=Point(1.1, 1.1), allow_incomplete="yes")  # type: ignore[arg-type]


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_mixed_batch_preserves_complete_and_failed_or_opted_in_slots(tmp_path: Path, n_jobs: int) -> None:
    from pourpoint import delineate

    source = _make_connectivity_bundle(
        tmp_path / "batch",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
    )
    targets = {
        "complete": Point(1.5, 0.5),
        "stranded": Point(3.5, 0.5),
        "unverified": Point(4.5, 0.5),
        "mixed": Point(6.5, 0.5),
    }
    with HydroFabric(source, cache=False) as fabric:
        default_sel = select_upstream_subbasins(fabric=fabric, target=targets, level=1, n_jobs=n_jobs)
        default_del = delineate(fabric=fabric, target=targets, level=1, n_jobs=n_jobs)
        opted_sel = select_upstream_subbasins(
            fabric=fabric, target=targets, level=1, allow_incomplete=True, n_jobs=n_jobs
        )
        opted_del = delineate(fabric=fabric, target=targets, level=1, allow_incomplete=True, n_jobs=n_jobs)

    assert default_sel.results[0] is not None and default_sel.results[0].coverage.is_complete
    assert default_sel.results[1:] == [None, None, None]
    assert [(i.index, i.name, i.code) for i in default_sel.issues] == [
        (1, "stranded", "selection.incomplete_coverage"),
        (2, "unverified", "selection.unverified_coverage"),
        (3, "mixed", "selection.incomplete_and_unverified_coverage"),
    ]
    assert default_del.results[0] is not None and default_del.results[0].coverage.is_complete
    assert default_del.results[1:] == [None, None, None]
    assert [(i.index, i.name, i.code) for i in default_del.issues] == [
        (1, "stranded", "selection.incomplete_coverage"),
        (2, "unverified", "selection.unverified_coverage"),
        (3, "mixed", "selection.incomplete_and_unverified_coverage"),
    ]

    assert opted_sel.issues == [] and opted_del.issues == []
    statuses = ["complete", "incomplete", "unverified", "incomplete_and_unverified"]
    assert [slot.coverage.status for slot in opted_sel.results if slot is not None] == statuses
    assert [slot.coverage.status for slot in opted_del.results if slot is not None] == statuses


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_standalone_refinement_preserves_coverage_diagnostics_after_pruning_and_closure(
    tmp_path: Path, n_jobs: int
) -> None:
    import json
    import sqlite3

    from pourpoint import QGISBundle, delineate, refine

    source = _make_connectivity_bundle(
        tmp_path / "refined",
        connectivity=[
            {
                "level": 1,
                "status": "supplied",
                "completeness": "partial",
                "upstream_cuts_column": "upstream_boundary_cuts",
            }
        ],
        with_rasters=True,
    )
    with HydroFabric(source, cache=False) as fabric:
        single_sel = select_upstream_subbasins(fabric=fabric, target=Point(3.5, 0.5), level=1, allow_incomplete=True)
        assert [u.id for u in single_sel.subbasins] == [4, 3]
        single_ref = refine(fabric=fabric, subbasins=single_sel, target=Point(3.5, 0.5), window_size=1)
        single_del = delineate(
            fabric=fabric, target=Point(3.5, 0.5), level=1, refine=True, window_size=1, allow_incomplete=True
        )

        batch_sel = select_upstream_subbasins(
            fabric=fabric,
            target={"ok": Point(1.5, 0.5), "stranded": Point(3.5, 0.5), "missing": Point(9.5, 0.5)},
            level=1,
            allow_incomplete=True,
            n_jobs=n_jobs,
        )
        batch_ref = refine(fabric=fabric, subbasins=batch_sel, window_size=1, n_jobs=n_jobs)

        folder = QGISBundle().add_result(single_ref).write(tmp_path / f"qgis-{n_jobs}")

    # Survives fabric closure and retains cut ancestor diagnostics despite raster pruning unit 3 out.
    assert [c.id for c in single_ref.subbasins] == [4]
    assert single_ref.coverage == single_sel.coverage
    assert single_ref.coverage.status == "incomplete"
    assert dict(single_ref.coverage.cut_counts or {}) == {3: 2}
    assert single_del.coverage == single_sel.coverage
    assert [c.id for c in single_del.subbasins] == [4]

    assert batch_ref.results[0] is not None and batch_ref.results[0].coverage.status == "complete"
    assert batch_ref.results[1] is not None and [c.id for c in batch_ref.results[1].subbasins] == [4]
    assert batch_ref.results[1].coverage == single_sel.coverage
    assert batch_ref.results[2] is None
    assert [(i.index, i.name, i.code) for i in batch_ref.issues] == [(2, "missing", "selection.no_coverage")]

    with sqlite3.connect(folder / "vectors.gpkg") as db:
        encoded = db.execute("SELECT coverage FROM result_metadata").fetchone()[0]
    assert json.loads(encoded) == {
        "status": "incomplete",
        "declared_completeness": "partial",
        "cuts_column": "upstream_boundary_cuts",
        "cut_counts": {"3": 2},
        "unverified_unit_ids": [],
        "reason": None,
    }


def test_out_of_float32_range_targets_fail_target_locally_and_bool_radius_rejected(tmp_path: Path) -> None:
    from pourpoint import delineate

    source = make_spatial_bundle(tmp_path / "source")
    huge = Point(1e40, 0)
    nan_pt = Point(float("nan"), 0)
    with HydroFabric(source, cache=False) as fabric:
        for bad in (huge, nan_pt):
            with pytest.raises(OperationError) as err:
                select_upstream_subbasins(fabric=fabric, target=bad)
            assert err.value.code == "selection.invalid_target"
        batch = select_upstream_subbasins(
            fabric=fabric,
            target={"huge": huge, "ok": Point(1.1, 1.1)},
        )
        assert batch.results[0] is None
        assert batch.results[1] is not None
        assert [(i.index, i.name, i.code) for i in batch.issues] == [(0, "huge", "selection.invalid_target")]
        del_batch = delineate(fabric=fabric, target={"huge": huge, "ok": Point(1.1, 1.1)})
        assert del_batch.results[0] is None and del_batch.results[1] is not None
        assert [(i.index, i.name, i.code) for i in del_batch.issues] == [(0, "huge", "selection.invalid_target")]
        with pytest.raises(ValueError, match="radius"):
            delineate(fabric=fabric, target=Point(1.1, 1.1), radius=True)  # type: ignore[arg-type]


def test_fork_workers_coordinate_sqlite_wal_and_shared_metadata(tmp_path: Path) -> None:
    import sqlite3

    source = make_spatial_bundle(tmp_path / "source")
    cache_dir = tmp_path / "cache"
    with HydroFabric(source, cache=True, cache_dir=cache_dir) as fabric:
        targets = {f"p{i}": Point(1.1 + (i % 2), 1.1) for i in range(8)}
        batch = select_upstream_subbasins(fabric=fabric, target=targets, n_jobs=4)
        assert len(batch.issues) == 0
        assert all(slot is not None for slot in batch.results)
        assert fabric.stats.requests > 0
        # Parent cache remains open and usable after worker forks close.
        assert fabric.records("catchments", [10])["id"].to_pylist() == [10]
    db_path = next(cache_dir.rglob("ranges.sqlite3"))
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
