from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, box
from spatial_fixture import make_spatial_bundle

from pourpoint.fabric import HydroFabric


def test_clipping_preserves_source_and_combines_parts(tmp_path: Path) -> None:
    from pourpoint.rivers import select_rivers

    with HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric:
        network = select_rivers(fabric=fabric, target=box(1.05, 1, 1.2, 2))
        assert [r.id for r in network.rivers] == [10]
        river = network.rivers[0]
        assert river.geometry.equals(LineString([(1.05, 1.05), (1.2, 1.2)]))
        assert river.attributes["source_value"] is None
        assert river.source.geometry.equals(LineString([(1, 1), (1.25, 1.25)]))
        assert network.relationships["downstream_ids"].to_pylist() == [[20, 30]]


def test_snap_metres_and_no_match() -> None:
    from pourpoint.results import OperationError
    from pourpoint.rivers import snap

    line = LineString([(0, -1), (0, 1)])
    result = snap(target=Point(0.001, 0), onto=line, radius=120)
    assert result.distance(Point(0, 0)) < 1e-8
    with pytest.raises(OperationError, match="range"):
        snap(target=Point(0.001, 0), onto=line, radius=100)


def river_publication(path: Path, *, orientation: int = 1, nodes: bool = True) -> Path:
    """Four directed features: trunk, two branches, reconvergence."""
    import hashlib
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from spatial_fixture import write_ipc

    make_spatial_bundle(path)
    manifest = json.loads((path / "manifest.json").read_text())
    lines = {
        10: LineString([(0, 0), (2, 0)]),
        20: LineString([(2, 0), (3, 1), (4, 0)]),
        30: LineString([(2, 0), (3, -1), (4, 0)]),
        40: LineString([(4, 0), (5, 0)]),
    }
    points = {10: Point(0, 0), 20: Point(2, 0), 30: Point(4, 0), 40: Point(5, 0)}
    for domain, geometries in (("rivers", lines), ("river_nodes", points)):
        file = path / f"{domain}.parquet"
        table = pq.read_table(file)
        rows = table.to_pylist()
        for row in rows:
            geometry = geometries[row["id"]]
            if domain == "rivers" and orientation == 2:
                from shapely import reverse

                geometry = reverse(geometry)
            if domain == "river_nodes" and not nodes:
                geometry = Point(99, 99)
            row.update(
                level=1,
                geometry=geometry.wkb,
                bbox=dict(zip(("xmin", "ymin", "xmax", "ymax"), geometry.bounds, strict=True)),
            )
        table = pa.Table.from_pylist(rows, schema=table.schema)
        if domain == "rivers":
            table = table.append_column("geometry_orientation", pa.array([orientation] * 4, type=pa.int8()))
        pq.write_table(table, file, row_group_size=2)
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest["families"][domain]["data_sha256"] = digest
        for declaration in manifest["relationships"].values():
            if domain in declaration["feature_artifacts"]:
                declaration["feature_artifacts"][domain]["data_sha256"] = digest
        lookup_path = path / f"{domain}-lookup.arrow"
        with pa.memory_map(str(lookup_path), "r") as stream:
            lookup = pa.ipc.open_file(stream).read_all()
        write_ipc(lookup_path, lookup.replace_schema_metadata({b"hfx:data_sha256": digest.encode()}))
    for name in manifest["artifacts"]:
        raw = (path / name).read_bytes()
        manifest["artifacts"][name] = {
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "head_sha256": hashlib.sha256(raw[:65536]).hexdigest(),
            "tail_sha256": hashlib.sha256(raw[-65536:]).hexdigest(),
        }
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


@pytest.mark.parametrize("orientation", [0, 1, 2])
def test_downstream_boundary_suffix_branches_and_reconvergence(tmp_path: Path, orientation: int) -> None:
    from pourpoint.rivers import select_rivers

    with HydroFabric(river_publication(tmp_path / "rivers", orientation=orientation)) as fabric:
        network = select_rivers(fabric=fabric, target=box(0.5, -0.5, 1.5, 0.5), include_downstream=True)
    assert [r.id for r in network.rivers] == [10, 20, 30, 40]
    assert network.rivers[0].geometry.equals(LineString([(0.5, 0), (2, 0)]))
    assert network.rivers[1].geometry.equals(LineString([(2, 0), (3, 1), (4, 0)]))
    assert network.rivers[2].geometry.equals(LineString([(2, 0), (3, -1), (4, 0)]))
    assert network.rivers[3].geometry.equals(LineString([(4, 0), (5, 0)]))
    assert len(network.nodes) == 4


def test_ambiguous_crossing_does_not_guess_geometry_order(tmp_path: Path) -> None:
    from pourpoint.results import OperationError
    from pourpoint.rivers import select_rivers

    with (
        HydroFabric(river_publication(tmp_path / "rivers", orientation=0, nodes=False)) as fabric,
        pytest.raises(OperationError) as exc,
    ):
        select_rivers(fabric=fabric, target=box(0.5, -0.5, 1.5, 0.5), include_downstream=True)
    assert exc.value.code == "rivers.ambiguous_direction"


def test_constituent_boundaries_do_not_extend_rivers(tmp_path: Path) -> None:
    import pyarrow as pa
    from shapely import union_all

    from pourpoint.features import Subbasin
    from pourpoint.rivers import select_rivers

    geometries = [box(0.5, -0.5, 1, 0.5), box(1, -0.5, 1.5, 0.5)]
    table = pa.table({"id": [100, 200], "geometry": [g.wkb for g in geometries]})
    with HydroFabric(river_publication(tmp_path / "rivers")) as fabric:
        granular = select_rivers(fabric=fabric, target=[Subbasin(table, 0), Subbasin(table, 1)])
        merged = select_rivers(fabric=fabric, target=union_all(geometries))
        assert granular.geometry.equals(merged.geometry)
        assert granular.geometry.equals(LineString([(0.5, 0), (1.5, 0)]))
        empty = select_rivers(fabric=fabric, target=box(50, 50, 51, 51))
        assert empty.rivers == []


def test_snap_zero_radius_retains_exact_on_stream_point() -> None:
    from pourpoint.rivers import snap

    target = Point(0, 0)
    assert snap(target=target, onto=LineString([(0, -1), (0, 1)]), radius=0).equals(target)


def test_contribution_footprint_uses_clipped_geometry(tmp_path: Path) -> None:
    import pyarrow as pa

    from pourpoint.features import Subbasin
    from pourpoint.refinement import SubbasinContribution
    from pourpoint.rivers import select_rivers

    table = pa.table({"id": [100], "geometry": [box(0, -1, 5, 1).wkb]})
    contribution = SubbasinContribution(Subbasin(table, 0), box(0.5, -0.5, 1.5, 0.5))
    with HydroFabric(river_publication(tmp_path / "rivers")) as fabric:
        selected = select_rivers(fabric=fabric, target=contribution)
    assert selected.geometry.equals(LineString([(0.5, 0), (1.5, 0)]))


def test_snap_uses_ground_distance_not_degree_distance_at_high_latitude() -> None:
    from shapely.geometry import MultiLineString

    from pourpoint.rivers import snap

    # 0.01 longitude degree at 80N is ~194 m, versus ~223 m for 0.002 latitude.
    # Degree-space nearest selection would incorrectly choose the northern line.
    streams = MultiLineString([[(0.01, 79.99), (0.01, 80.01)], [(-0.01, 80.002), (0.01, 80.002)]])
    selected = snap(target=Point(0, 80), onto=streams, radius=200)
    assert selected.x == pytest.approx(0.01)
    assert selected.y == pytest.approx(80, abs=1e-6)


def test_snap_rejects_unwrapped_antimeridian_segment() -> None:
    from pourpoint.rivers import snap

    with pytest.raises(ValueError, match="antimeridian"):
        snap(target=Point(180, 0), onto=LineString([(179, 0), (-179, 0)]), radius=200_000)


def test_snap_endpoint_ties_and_late_validation() -> None:
    from shapely.geometry import MultiLineString

    from pourpoint.rivers import snap

    target = Point(0, 0)
    for x in (1, -1):
        lines = MultiLineString([[(x, 0), (2 * x, 0)], [(-x, 0), (-2 * x, 0)]])
        assert snap(target=target, onto=lines, radius=120_000).equals(Point(x, 0))
    assert snap(target=target, onto=LineString([(1, 0), (1, 0)]), radius=120_000).equals(Point(1, 0))
    with pytest.raises(ValueError, match="antimeridian"):
        snap(target=target, onto=MultiLineString([[(-1, 0), (1, 0)], [(179, 0), (-179, 0)]]), radius=0)


def test_downstream_endpoint_inference_uses_each_selected_river_row(tmp_path: Path) -> None:
    from shapely import union_all

    from pourpoint.rivers import select_rivers

    footprint = union_all([box(0.5, -0.5, 1.5, 0.5), box(2.5, -0.8, 3.5, -0.4)])
    with HydroFabric(river_publication(tmp_path / "rivers", orientation=0)) as fabric:
        network = select_rivers(fabric=fabric, target=footprint, include_downstream=True)
    rivers = {river.id: river for river in network.rivers}
    assert list(rivers) == [30, 10, 20, 40]
    assert rivers[10].geometry.equals(LineString([(0.5, 0), (2, 0)]))
    # River 30 is also reached from river 10, so retain its whole source line.
    assert rivers[30].geometry.equals(LineString([(2, 0), (3, -1), (4, 0)]))


def test_select_rivers_validates_inputs_and_skips_orientation_for_wholly_downstream_rivers(tmp_path: Path) -> None:
    import hashlib
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from shapely.geometry import Polygon

    from pourpoint.results import OperationError
    from pourpoint.rivers import select_rivers, snap
    from tests.spatial_fixture import write_ipc

    path = river_publication(tmp_path / "rivers", orientation=0, nodes=False)
    table = pq.read_table(path / "rivers.parquet")
    orientations = [1 if rid == 10 else 0 for rid in table["id"].to_pylist()]
    idx = table.schema.get_field_index("geometry_orientation")
    table = table.set_column(idx, table.schema.field("geometry_orientation"), pa.array(orientations, type=pa.int8()))
    pq.write_table(table, path / "rivers.parquet", row_group_size=2)

    manifest = json.loads((path / "manifest.json").read_text())
    raw = (path / "rivers.parquet").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest["families"]["rivers"]["data_sha256"] = digest
    manifest["relationships"]["rivers"]["feature_artifacts"]["rivers"]["data_sha256"] = digest
    manifest["relationships"]["river_nodes"]["feature_artifacts"]["rivers"]["data_sha256"] = digest
    with pa.memory_map(str(path / "rivers-lookup.arrow"), "r") as stream:
        lookup = pa.ipc.open_file(stream).read_all()
    write_ipc(path / "rivers-lookup.arrow", lookup.replace_schema_metadata({b"hfx:data_sha256": digest.encode()}))
    for name in ("rivers.parquet", "rivers-lookup.arrow"):
        b = (path / name).read_bytes()
        manifest["artifacts"][name] = {
            "size_bytes": len(b),
            "sha256": hashlib.sha256(b).hexdigest(),
            "head_sha256": hashlib.sha256(b[:65536]).hexdigest(),
            "tail_sha256": hashlib.sha256(b[-65536:]).hexdigest(),
        }
    (path / "manifest.json").write_text(json.dumps(manifest))

    footprint = box(0.5, -0.5, 1.5, 0.5).union(box(2.5, -0.8, 3.5, -0.4))
    with HydroFabric(path, cache=False) as fabric:
        # River 30 is partially inside footprint, has ambiguous orientation, AND is downstream of river 10.
        network = select_rivers(fabric=fabric, target=footprint, include_downstream=True)
        rivers = {r.id: r for r in network.rivers}
        assert set(rivers) == {10, 20, 30, 40}
        assert rivers[30].geometry.equals(LineString([(2, 0), (3, -1), (4, 0)]))

        with pytest.raises(TypeError, match="include_downstream"):
            select_rivers(fabric=fabric, target=footprint, include_downstream=1)  # type: ignore[arg-type]

        bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
        with pytest.raises(OperationError) as invalid_err:
            select_rivers(fabric=fabric, target=bowtie)
        assert invalid_err.value.code == "rivers.invalid_target"

        with pytest.raises(OperationError) as huge_err:
            select_rivers(fabric=fabric, target=box(0, 0, 1e40, 1))
        assert huge_err.value.code == "rivers.invalid_target"

    with pytest.raises(ValueError, match="radius"):
        snap(target=Point(0, 0), onto=LineString([(0, -1), (0, 1)]), radius=True)  # type: ignore[arg-type]


def test_snap_antimeridian_polar_tiebreak_and_delineate_eager_validation(tmp_path: Path) -> None:
    from shapely.geometry import MultiLineString

    from pourpoint import delineate
    from pourpoint.rivers import snap

    # Antimeridian wrap: point at +179.95 is ~11.1 km from (-179.95, 0) and ~22.2 km from (+179.75, 0).
    streams_anti = MultiLineString([[(-179.95, -0.1), (-179.95, 0.1)], [(179.75, -0.1), (179.75, 0.1)]])
    snapped_anti = snap(target=Point(179.95, 0.0), onto=streams_anti, radius=15_000)
    assert snapped_anti.x == pytest.approx(-179.95)
    assert snapped_anti.y == pytest.approx(0.0, abs=1e-8)

    # Near-polar latitude: longitude differences shrink dramatically; never falsely prune.
    streams_pole = MultiLineString([[(120.0, 89.8), (120.0, 89.9)], [(-60.0, 89.85), (-60.0, 89.95)]])
    snapped_pole = snap(target=Point(0.0, 89.85), onto=streams_pole, radius=40_000)
    assert snapped_pole.x == pytest.approx(-60.0)
    assert 89.85 <= snapped_pole.y <= 89.95

    # Input-order tie-breaking across equidistant branches.
    tie_lines = MultiLineString([[(0.01, -1.0), (0.01, 1.0)], [(-0.01, -1.0), (-0.01, 1.0)]])
    assert snap(target=Point(0.0, 0.0), onto=tie_lines, radius=5_000).x == pytest.approx(0.01)

    # Eager validation of snap_onto in delineate() even on empty batch.
    with (
        HydroFabric(make_spatial_bundle(tmp_path / "fabric"), cache=False) as fabric,
        pytest.raises(ValueError, match="antimeridian"),
    ):
        delineate(
            fabric=fabric,
            target=[],
            snap_onto=LineString([(179.0, 0.0), (-179.0, 0.0)]),
            radius=100,
        )


def test_select_rivers_eliminates_redundant_river_reads_and_uses_bounded_union(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyarrow as pa

    from pourpoint import _union
    from pourpoint.features import Subbasin
    from pourpoint.rivers import select_rivers

    path = river_publication(tmp_path / "rivers", orientation=0)
    with HydroFabric(path, cache=False) as fabric:
        record_calls: list[tuple[str, list[int]]] = []
        orig_records = fabric.records

        def spy_records(domain: str, ids: object, **kwargs: object) -> pa.Table:
            record_calls.append((domain, list(ids)))  # type: ignore[arg-type]
            return orig_records(domain, ids, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(fabric, "records", spy_records)

        # include_downstream=False should reuse candidate table rows without calling records("rivers", ...).
        res_simple = select_rivers(fabric=fabric, target=box(0.5, -0.5, 1.5, 0.5), include_downstream=False)
        assert [r.id for r in res_simple.rivers] == [10]
        assert not [c for c in record_calls if c[0] == "rivers"]

        record_calls.clear()
        # include_downstream=True should only fetch extra downstream rivers [20, 30, 40], never re-reading [10].
        res_down = select_rivers(fabric=fabric, target=box(0.5, -0.5, 1.5, 0.5), include_downstream=True)
        assert [r.id for r in res_down.rivers] == [10, 20, 30, 40]
        river_fetches = [c[1] for c in record_calls if c[0] == "rivers"]
        assert river_fetches == [[20, 30, 40]]

        # Sequence target must route through _union.union_subbasins instead of raw shapely.union_all.
        union_calls = 0
        orig_union_subbasins = _union.union_subbasins

        def spy_union(parts: object) -> object:
            nonlocal union_calls
            union_calls += 1
            return orig_union_subbasins(parts)  # type: ignore[arg-type]

        monkeypatch.setattr(_union, "union_subbasins", spy_union)
        tbl = pa.table({"id": [1, 2], "geometry": [box(0.5, -0.5, 1, 0.5).wkb, box(1, -0.5, 1.5, 0.5).wkb]})
        seq_res = select_rivers(fabric=fabric, target=[Subbasin(tbl, 0), Subbasin(tbl, 1)])
        assert union_calls == 1
        assert seq_res.geometry.equals(LineString([(0.5, 0), (1.5, 0)]))
