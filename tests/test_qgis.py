import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest
from shapely.geometry import box

from pourpoint.features import Subbasin
from pourpoint.qgis import QGISBundle


def test_portable_vectors_and_lossless_attributes(tmp_path: Path) -> None:
    table = pa.table(
        {
            "id": [7],
            "geometry": [box(0, 0, 1, 1).wkb],
            "optional": pa.array([None], type=pa.int64()),
            "nested": [[1, 2]],
        }
    ).replace_schema_metadata({b"source": b"fixture"})
    folder = QGISBundle().add_vector("basins", [Subbasin(table, 0)]).write(tmp_path / "bundle")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        row = db.execute("SELECT source_id, geometry FROM layer_0").fetchone()
        assert row[0] == 7
        assert db.execute("SELECT optional FROM layer_0").fetchone() == (None,)
        assert row[1][:2] == b"GP"
        ipc = db.execute("SELECT payload FROM source_arrow").fetchone()[0]
        with pa.ipc.open_stream(ipc) as reader:
            assert reader.read_all().equals(table, check_metadata=True)
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    assert project.findtext("projectlayers/maplayer/datasource") == "./vectors.gpkg|layername=layer_0"
    assert project.findtext("projectlayers/maplayer/layername") == "basins"


def test_raw_geometry_requires_crs_and_existing_folder_is_not_overwritten(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CRS"):
        QGISBundle().add_vector("cell", box(0, 0, 1, 1))
    bundle = QGISBundle().add_vector("cell", box(0, 0, 1, 1), crs="EPSG:3857")
    bundle.write(tmp_path / "bundle")
    with pytest.raises(FileExistsError):
        bundle.write(tmp_path / "bundle")


def test_batch_groups_keep_failed_targets(tmp_path: Path) -> None:
    from pourpoint.results import BatchIssue, BatchResult, DelineationResult, Target, UpstreamCoverage

    geometry = box(0, 0, 1, 1)
    coverage = UpstreamCoverage(
        status="complete",
        declared_completeness="complete",
        cut_counts={},
        unverified_unit_ids=(),
    )
    result = DelineationResult([], geometry, None, None, geometry, geometry, {"license": "CC BY-NC 4.0"}, coverage)
    batch = BatchResult(
        [result, None],
        [Target(0, "up/stream", geometry), Target(1, "missing", geometry)],
        [BatchIssue(1, "missing", "selection.no_coverage", "outside")],
    )
    folder = QGISBundle().add_result(batch).write(tmp_path / "batch")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        assert db.execute("SELECT * FROM target_issues").fetchall() == [
            (1, "missing", "selection.no_coverage", "outside")
        ]
        assert db.execute("SELECT target_name,target_index FROM layer_0").fetchall() == [("up/stream", 0)]
        import json

        metadata, encoded_coverage = db.execute("SELECT metadata, coverage FROM result_metadata").fetchone()
        assert json.loads(metadata)["license"] == "CC BY-NC 4.0"
        assert json.loads(encoded_coverage) == {
            "status": "complete",
            "declared_completeness": "complete",
            "cuts_column": None,
            "cut_counts": {},
            "unverified_unit_ids": [],
            "reason": None,
        }
        assert db.execute("SELECT original_target, adjusted_target FROM result_metadata").fetchone() == (
            geometry.wkb,
            geometry.wkb,
        )
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    assert [g.attrib["name"] for g in project.findall("layer-tree-group/layer-tree-group")] == ["up/stream", "missing"]


def test_raster_export_preserves_native_values_and_whole_mask(tmp_path: Path) -> None:
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.crs import CRS

    from pourpoint.rasters import RasterGrid, RasterRequest, RasterWindow
    from pourpoint.refinement import FootprintRefinement, OutletCell, RasterSubset, RefinementRasters

    crs = CRS.from_epsg(3857)
    affine = Affine(10, 0, 100, 0, -10, 200)
    grid = RasterGrid(crs, affine, 2, 2, "direction", "accumulation")
    request = RasterRequest(0, 0, 2, 2, grid, object(), 0)
    valid = np.ones((2, 2), dtype=bool)
    window = RasterWindow(
        np.ones((2, 2), dtype="int8"),
        np.array([[1, 2], [3, 4]], dtype="float32"),
        valid,
        valid,
        affine,
        crs,
        "cells",
        request,
        np.array([[128, 64], [32, 16]], dtype="uint8"),
        255,
        -9999,
    )
    mask = np.array([[True, True], [False, True]])
    rasters = RefinementRasters(RasterSubset(window, mask, "direction"), RasterSubset(window, mask, "accumulation"))
    result = FootprintRefinement(box(0, 0, 0.001, 0.001), OutletCell(grid, 0, 0), rasters)
    folder = QGISBundle().add_result(result).write(tmp_path / "rasters")
    with rasterio.open(folder / "raster_0.tif") as raster:
        assert raster.crs == crs
        assert raster.transform == affine
        assert raster.nodata == 255
        np.testing.assert_array_equal(raster.read(1), [[128, 64], [255, 16]])
    with rasterio.open(folder / "raster_1.tif") as raster:
        np.testing.assert_array_equal(raster.read(1), [[1, 2], [-9999, 4]])
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    outlet = next(
        layer for layer in project.findall("projectlayers/maplayer") if layer.findtext("layername") == "outlet_cell"
    )
    assert outlet.findtext("srs/spatialrefsys/authid") == "EPSG:3857"
    assert outlet.find("renderer-v2/symbols/symbol/layer/Option/Option[@name='style']").attrib["value"] == "no"


def test_rivers_keep_clipped_geometry_and_directed_relationships(tmp_path: Path) -> None:
    from shapely.geometry import LineString

    from pourpoint.features import RiverNetwork
    from pourpoint.rivers import ClippedRiver

    table = pa.table({"id": [10], "geometry": [LineString([(0, 0), (2, 0)]).wkb], "order": [2]})
    relationships = pa.table({"id": [10], "downstream_ids": [[11, 12]]})
    network = RiverNetwork(
        [ClippedRiver(table, 0, LineString([(0, 0), (1, 0)]))],
        relationships,
        [],
        None,
        {"downstream_ids": {"target": "rivers"}},
    )
    folder = QGISBundle().add_vector("streams", network).write(tmp_path / "network")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        assert db.execute("SELECT source_id,relation,target_id FROM river_connections").fetchall() == [
            (10, "downstream_ids", 11),
            (10, "downstream_ids", 12),
        ]
        import shapely

        geometry = shapely.from_wkb(db.execute("SELECT geometry FROM layer_0").fetchone()[0][8:])
        assert geometry.equals(LineString([(0, 0), (1, 0)]))
        payload = db.execute("SELECT payload FROM source_arrow WHERE kind = 'relationships'").fetchone()[0]
        assert pa.ipc.open_stream(payload).read_all().equals(relationships)


@pytest.mark.parametrize("granular", [False, True])
def test_standalone_refinement_export_retains_publication_metadata(tmp_path: Path, granular: bool) -> None:
    import json

    import numpy as np
    from conftest import add_pair
    from rasterio.transform import from_origin
    from shapely.geometry import Point
    from spatial_fixture import make_spatial_bundle

    from pourpoint import HydroFabric, refine

    source = make_spatial_bundle(tmp_path / "fabric")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest["attribution"] = "Independent source authors"
    manifest["license"] = "CC BY-NC 4.0"
    manifest_path.write_text(json.dumps(manifest))
    add_pair(
        source,
        direction=np.array([[0]], dtype="uint8"),
        accumulation=np.array([[1]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    footprint = box(0, 0, 1, 1)
    with HydroFabric(source) as fabric:
        if granular:
            table = pa.table({"id": [7], "geometry": [footprint.wkb]})
            result = refine(fabric=fabric, subbasins=[Subbasin(table, 0)], target=Point(0.5, 0.5))
        else:
            result = refine(fabric=fabric, footprint=footprint, target=Point(0.5, 0.5))
    folder = QGISBundle().add_result(result).write(tmp_path / "bundle")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        rows = db.execute("SELECT metadata FROM result_metadata").fetchall()
    assert len(rows) == 1
    metadata = json.loads(rows[0][0])
    assert metadata["license"] == "CC BY-NC 4.0"
    assert metadata["attribution"] == "Independent source authors"
    from pourpoint import BatchResult, Target

    batch = BatchResult([result], [Target(0, "station", Point(0.5, 0.5))], [])
    batch_folder = QGISBundle().add_result(batch).write(tmp_path / "batch")
    with sqlite3.connect(batch_folder / "vectors.gpkg") as db:
        name, index, encoded = db.execute("SELECT target_name, target_index, metadata FROM result_metadata").fetchone()
    assert (name, index) == ("station", 0)
    assert json.loads(encoded) == metadata


def test_relationship_ipc_exceeds_sqlite_value_limit_without_losing_metadata() -> None:
    from pourpoint.qgis import _arrow, _initialize

    schema = pa.schema(
        [pa.field("id", pa.int64()), pa.field("downstream_ids", pa.list_(pa.int64()), metadata={b"target": b"rivers"})],
        metadata={b"source": b"test"},
    )
    table = pa.Table.from_arrays(
        [pa.array(range(1000)), pa.array([[i + 1, i + 2] for i in range(1000)])], schema=schema
    )
    with sqlite3.connect(":memory:") as db:
        _initialize(db)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4096)
        _arrow(db, "layer_0", "relationships", table)
        parts = db.execute("SELECT payload FROM source_arrow ORDER BY stream_id, part").fetchall()
        assert len(parts) > 1
        restored = pa.ipc.open_stream(b"".join(row[0] for row in parts)).read_all()
        assert restored.equals(table, check_metadata=True)


def test_export_does_not_repeat_unselected_nested_dictionary_values(tmp_path: Path) -> None:
    dictionary = pa.DictionaryArray.from_arrays(pa.array([0, 1], type=pa.int8()), pa.array(["a", "x" * 100000]))
    nested = pa.ListArray.from_arrays(pa.array([0, 1, 2]), dictionary)
    table = pa.table({"id": [1, 2], "geometry": [box(0, 0, 1, 1).wkb] * 2, "nested": nested})
    folder = QGISBundle().add_vector("selected", [Subbasin(table, 0)] * 3).write(tmp_path / "bundle")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        rows = db.execute("SELECT kind, payload FROM source_arrow").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "features"
    payload = rows[0][1]
    assert len(payload) < 2000
    restored = pa.ipc.open_stream(payload).read_all()
    assert restored.schema.equals(table.schema, check_metadata=True)
    assert restored.to_pylist() == [{"id": 1, "geometry": box(0, 0, 1, 1).wkb, "nested": ["a"]}] * 3


def test_segmented_network_preserves_nodes_relationships_and_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from shapely.geometry import LineString, Point

    import pourpoint.qgis as qgis
    from pourpoint.features import River, RiverNetwork, SourceNode

    river = pa.table({"id": [1], "geometry": [LineString([(0, 0), (1, 1)]).wkb]})
    nodes = pa.table({"id": [10, 20], "geometry": [Point(0, 0).wkb, Point(1, 1).wkb]})
    relationships = pa.table({"id": [1], "downstream_ids": [list(range(2, 1002))]}).replace_schema_metadata(
        {b"source": b"rivers"}
    )
    node_relationships = pa.table({"id": [10, 20], "outgoing_river_ids": [[1], []]})
    declarations = {"outgoing_river_ids": {"target_domain": "rivers"}}
    network = RiverNetwork(
        [River(river, 0)], relationships, [SourceNode(nodes, 0), SourceNode(nodes, 1)], node_relationships, declarations
    )
    initialize = qgis._initialize

    def limited(db: sqlite3.Connection) -> None:
        initialize(db)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4096)

    monkeypatch.setattr(qgis, "_initialize", limited)
    folder = QGISBundle().add_vector("rivers", network).write(tmp_path / "network")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        streams = []
        for (stream_id,) in db.execute("SELECT DISTINCT stream_id FROM source_arrow ORDER BY stream_id"):
            payload = b"".join(
                row[0]
                for row in db.execute(
                    "SELECT payload FROM source_arrow WHERE stream_id = ? ORDER BY part", (stream_id,)
                )
            )
            streams.append(pa.ipc.open_stream(payload).read_all())
        kinds = [
            row[0]
            for row in db.execute("SELECT DISTINCT kind, stream_id FROM source_arrow ORDER BY stream_id").fetchall()
        ]
        assert kinds == ["features", "relationships", "node_relationships", "nodes"]
        assert db.execute("SELECT source_id, target_id FROM river_connections ORDER BY target_id").fetchall() == [
            (1, i) for i in range(2, 1002)
        ]
        assert declarations in [json.loads(row[0]) for row in db.execute("SELECT metadata FROM source_metadata")]
    for actual, expected in zip(streams, [river, relationships, node_relationships, nodes], strict=True):
        assert actual.equals(expected, check_metadata=True)


def test_selection_result_and_batch_export_persist_coverage_diagnostics(tmp_path: Path) -> None:
    import json

    from shapely.geometry import Point

    from pourpoint.results import BatchIssue, BatchResult, SelectionResult, Target, UpstreamCoverage

    table = pa.table({"id": [4, 3], "geometry": [box(3, 0, 4, 1).wkb, box(2, 0, 3, 1).wkb]})
    units = [Subbasin(table, 0), Subbasin(table, 1)]
    coverage = UpstreamCoverage(
        status="incomplete_and_unverified",
        declared_completeness="partial",
        cuts_column="upstream_boundary_cuts",
        cut_counts={3: 2},
        unverified_unit_ids=(5,),
    )
    selection = SelectionResult(units, coverage, level=1, target=Point(3.5, 0.5), provenance={"fabric_name": "test"})
    batch = BatchResult(
        [selection, None],
        [Target(0, "kept", Point(3.5, 0.5)), Target(1, "failed", Point(9.5, 0.5))],
        [BatchIssue(1, "failed", "selection.no_coverage", "no coverage")],
    )
    folder = QGISBundle().add_result(batch).write(tmp_path / "selection-export")
    with sqlite3.connect(folder / "vectors.gpkg") as db:
        assert db.execute("SELECT source_id, target_name FROM layer_0").fetchall() == [(4, "kept"), (3, "kept")]
        assert db.execute("SELECT target_name, code FROM target_issues").fetchall() == [
            ("failed", "selection.no_coverage")
        ]
        target_name, metadata, encoded_coverage = db.execute(
            "SELECT target_name, metadata, coverage FROM result_metadata"
        ).fetchone()
        assert target_name == "kept"
        assert json.loads(metadata) == {"fabric_name": "test"}
        assert json.loads(encoded_coverage) == {
            "status": "incomplete_and_unverified",
            "declared_completeness": "partial",
            "cuts_column": "upstream_boundary_cuts",
            "cut_counts": {"3": 2},
            "unverified_unit_ids": [5],
            "reason": None,
        }


def test_qgis_write_closes_sqlite_deduplicates_groups_derives_ellipsoid_and_uses_bigtiff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import MappingProxyType

    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.crs import CRS

    import pourpoint.qgis as qgis_mod
    from pourpoint.rasters import RasterGrid, RasterRequest, RasterWindow
    from pourpoint.refinement import RasterSubset
    from pourpoint.results import BatchResult, SelectionResult, Target, UpstreamCoverage

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.closed_explicitly = False

        def close(self) -> None:
            self.closed_explicitly = True
            super().close()

    connections: list[TrackedConnection] = []
    real_connect = sqlite3.connect

    def tracked_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(database, *args, factory=TrackedConnection, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(qgis_mod.sqlite3, "connect", tracked_connect)

    open_kwargs: list[dict[str, object]] = []
    real_raster_open = rasterio.open

    def tracked_open(fp: object, mode: str = "r", **kwargs: object) -> object:
        if mode == "w":
            open_kwargs.append(dict(kwargs))
        return real_raster_open(fp, mode, **kwargs)

    monkeypatch.setattr(qgis_mod.rasterio, "open", tracked_open)

    geom = box(0, 0, 1, 1)
    table = pa.table({"id": [1], "geometry": [geom.wkb]})
    cov = UpstreamCoverage("complete", declared_completeness="complete", cut_counts={}, unverified_unit_ids=())
    frozen_prov = MappingProxyType({"nested": MappingProxyType({"items": (1, 2)})})
    sel = SelectionResult([Subbasin(table, 0)], cov, 1, geom, frozen_prov)
    batch1 = BatchResult([sel], [Target(0, "dup", geom)], [])
    batch2 = BatchResult([sel], [Target(0, "dup", geom)], [])

    crs_4269 = CRS.from_epsg(4269)
    affine = Affine(1, 0, 0, 0, -1, 1)
    grid = RasterGrid(crs_4269, affine, 1, 1, "d", "a")
    req = RasterRequest(0, 0, 1, 1, grid, object(), 0)
    valid = np.ones((1, 1), dtype=bool)
    win = RasterWindow(
        np.ones((1, 1), dtype="int8"),
        np.ones((1, 1), dtype="float32"),
        valid,
        valid,
        affine,
        crs_4269,
        "cells",
        req,
        np.array([[1]], dtype="uint8"),
        255,
        -9999,
    )
    bundle = (
        QGISBundle().add_result(batch1).add_result(batch2).add_raster("nad83", RasterSubset(win, valid, "direction"))
    )
    folder = bundle.write(tmp_path / "out")

    assert len(connections) == 1
    assert connections[0].closed_explicitly is True
    assert open_kwargs and open_kwargs[0].get("BIGTIFF") == "IF_SAFER"

    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    groups = [g for g in project.findall("layer-tree-group/layer-tree-group") if g.attrib["name"] == "dup"]
    assert len(groups) == 1
    assert len(groups[0].findall("layer-tree-layer")) == 2
    raster_layer = next(
        layer for layer in project.findall("projectlayers/maplayer") if layer.findtext("layername") == "nad83"
    )
    assert raster_layer.findtext("srs/spatialrefsys/ellipsoidacronym") == "EPSG:7019"


def test_raster_export_avoids_float64_promotion_and_preserves_nodata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.crs import CRS

    from pourpoint.rasters import RasterGrid, RasterRequest, RasterWindow
    from pourpoint.refinement import RasterSubset

    crs = CRS.from_epsg(3857)
    affine = Affine(10, 0, 100, 0, -10, 200)
    grid = RasterGrid(crs, affine, 2, 2, "d", "a")
    request = RasterRequest(0, 0, 2, 2, grid, object(), 0)
    valid = np.ones((2, 2), dtype=bool)
    mask = np.array([[True, False], [True, True]])
    window = RasterWindow(
        np.ones((2, 2), dtype="int8"),
        np.array([[1.5, 2.5], [3.5, 4.5]], dtype="float32"),
        valid,
        valid,
        affine,
        crs,
        "cells",
        request,
        np.array([[128, 64], [32, 16]], dtype="uint8"),
        255,
        float("nan"),
    )
    orig_where = np.where

    def forbid_where(*args: object, **kwargs: object) -> object:
        raise AssertionError("np.where should not be called during QGIS raster export")

    monkeypatch.setattr(np, "where", forbid_where)
    try:
        folder = (
            QGISBundle()
            .add_raster("dir", RasterSubset(window, mask, "direction"))
            .add_raster("acc", RasterSubset(window, mask, "accumulation"))
            .write(tmp_path / "no-promote")
        )
    finally:
        monkeypatch.setattr(np, "where", orig_where)

    with rasterio.open(folder / "raster_0.tif") as d_tif:
        assert d_tif.dtypes[0] == "uint8"
        np.testing.assert_array_equal(d_tif.read(1), [[128, 255], [32, 16]])
    with rasterio.open(folder / "raster_1.tif") as a_tif:
        assert a_tif.dtypes[0] == "float32"
        vals = a_tif.read(1)
        assert np.isnan(vals[0, 1])
        np.testing.assert_allclose(vals[mask], [1.5, 3.5, 4.5])


def test_qgis_granular_refinement_exports_watershed_and_collective_source_arrow_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import numpy as np
    import shapely
    from conftest import add_pair
    from rasterio.transform import from_origin
    from shapely.geometry import LineString, Point
    from spatial_fixture import make_spatial_bundle

    from pourpoint import HydroFabric, refine
    from pourpoint.refinement import SubbasinContribution
    from pourpoint.rivers import ClippedRiver

    # 1. GranularRefinement add_result exports both "subbasins" and "watershed"
    source = make_spatial_bundle(tmp_path / "gran_fabric")
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["auxiliary"] = []
    manifest_path.write_text(json.dumps(manifest))
    add_pair(
        source,
        direction=np.array([[0]], dtype="uint8"),
        accumulation=np.array([[1]], dtype="int32"),
        transform=from_origin(0, 1, 1, 1),
    )
    footprint = box(0, 0, 1, 1)
    with HydroFabric(source) as fabric:
        sub_tbl = pa.table({"id": [7], "geometry": [footprint.wkb], "score": [42]})
        gran = refine(fabric=fabric, subbasins=[Subbasin(sub_tbl, 0)], target=Point(0.5, 0.5))
    folder = QGISBundle().add_result(gran).write(tmp_path / "gran_out")
    with zipfile.ZipFile(folder / "watershed.qgz") as archive:
        project = ET.fromstring(archive.read("watershed.qgs"))
    layer_names = [layer.findtext("layername") for layer in project.findall("projectlayers/maplayer")]
    assert "subbasins" in layer_names
    assert "watershed" in layer_names

    # 2. Mixed schemas in one layer produce distinct streams with explicit fid mapping in source_metadata
    t_a = pa.table({"id": [1, 2], "geometry": [box(0, 0, 1, 1).wkb, box(1, 0, 2, 1).wkb], "a": [10, 20]})
    t_b = pa.table({"id": [3], "geometry": [box(2, 0, 3, 1).wkb], "b": ["extra"]})
    mixed_folder = (
        QGISBundle()
        .add_vector("mixed", [Subbasin(t_a, 0), Subbasin(t_b, 0), Subbasin(t_a, 1)])
        .write(tmp_path / "mixed")
    )
    with sqlite3.connect(mixed_folder / "vectors.gpkg") as db:
        rows = db.execute("SELECT fid, source_id, a, b FROM layer_0 ORDER BY fid").fetchall()
        assert rows == [(1, 1, 10, None), (2, 3, None, "extra"), (3, 2, 20, None)]
        meta_json = json.loads(
            db.execute("SELECT metadata FROM source_metadata WHERE layer_name = 'layer_0'").fetchone()[0]
        )
        assert meta_json["attribute_columns"] == {"id": "id", "a": "a", "b": "b"}
        fstreams = meta_json["feature_streams"]
        assert len(fstreams) == 2
        assert fstreams[0]["fids"] == [1, 3]
        assert fstreams[1]["fids"] == [2]
        s0 = pa.ipc.open_stream(
            db.execute("SELECT payload FROM source_arrow WHERE stream_id = ?", (fstreams[0]["stream_id"],)).fetchone()[
                0
            ]
        ).read_all()
        s1 = pa.ipc.open_stream(
            db.execute("SELECT payload FROM source_arrow WHERE stream_id = ?", (fstreams[1]["stream_id"],)).fetchone()[
                0
            ]
        ).read_all()
        assert s0.equals(t_a, check_metadata=True)
        assert s1.equals(t_b, check_metadata=True)

    # 3. Unchanged Feature WKB bypasses shapely.from_wkb while preserving validation and ClippedRiver clipped geometry
    orig_from_wkb = shapely.from_wkb

    def forbid_from_wkb(*args: object, **kwargs: object) -> object:
        raise AssertionError("unchanged Feature should use validated WKB fast path without shapely.from_wkb")

    monkeypatch.setattr(shapely, "from_wkb", forbid_from_wkb)
    try:
        t_valid = pa.table({"id": [5], "geometry": [shapely.to_wkb(box(0, 0, 1, 1), byte_order=1)]})
        fast_dir = QGISBundle().add_vector("fast", [Subbasin(t_valid, 0)]).write(tmp_path / "fast")
        assert (fast_dir / "vectors.gpkg").is_file()
    finally:
        monkeypatch.setattr(shapely, "from_wkb", orig_from_wkb)

    # Wrong-type or malformed WKB still raises ValueError even via add_vector
    bad_tbl = pa.table({"id": [5], "geometry": [Point(0, 0).wkb]})
    with pytest.raises(ValueError, match="invalid Subbasin geometry"):
        QGISBundle().add_vector("bad", [Subbasin(bad_tbl, 0)])

    # SubbasinContribution and ClippedRiver still export their clipped geometry rather than whole source WKB
    contrib = SubbasinContribution(Subbasin(t_a, 0), box(0, 0, 0.5, 0.5))
    clipped_river = ClippedRiver(
        pa.table({"id": [11], "geometry": [LineString([(0, 0), (10, 0)]).wkb]}),
        0,
        LineString([(0, 0), (2, 0)]),
    )
    clip_dir = QGISBundle().add_vector("c_sub", [contrib]).add_vector("c_riv", [clipped_river]).write(tmp_path / "clip")
    with sqlite3.connect(clip_dir / "vectors.gpkg") as db:
        assert shapely.from_wkb(db.execute("SELECT geometry FROM layer_0").fetchone()[0][8:]).equals(
            box(0, 0, 0.5, 0.5)
        )
        assert shapely.from_wkb(db.execute("SELECT geometry FROM layer_1").fetchone()[0][8:]).equals(
            LineString([(0, 0), (2, 0)])
        )
