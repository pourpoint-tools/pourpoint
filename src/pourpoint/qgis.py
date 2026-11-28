"""Portable QGIS projects with native-grid rasters and lossless source tables."""

from __future__ import annotations

import io
import json
import sqlite3
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Buffer, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Self
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyproj
import rasterio
from rasterio.crs import CRS  # ty: ignore[unresolved-import]
from shapely.geometry.base import BaseGeometry

from pourpoint._selection import compact_dictionaries, take_rows
from pourpoint.features import Feature, RiverNetwork
from pourpoint.refinement import FootprintRefinement, GranularRefinement, RasterSubset, SubbasinContribution
from pourpoint.results import BatchIssue, BatchResult, DelineationResult, SelectionResult, UpstreamCoverage
from pourpoint.rivers import ClippedRiver

type ExportResult = SelectionResult | DelineationResult | GranularRefinement | FootprintRefinement
type ExportBatch = (
    BatchResult[SelectionResult]
    | BatchResult[DelineationResult]
    | BatchResult[GranularRefinement]
    | BatchResult[FootprintRefinement]
)
type VectorFeature = Feature | SubbasinContribution


@dataclass
class _Vector:
    name: str
    wkbs: list[bytes]
    sources: list[VectorFeature]
    crs: CRS
    group: str | None = None
    target_index: int | None = None
    outline: bool = False


def _text(parent: ET.Element, name: str, value: str) -> ET.Element:
    element = ET.SubElement(parent, name)
    element.text = value
    return element


def _ellipsoid_acronym(crs: CRS) -> str:
    try:
        proj_crs = pyproj.CRS.from_wkt(crs.to_wkt())
    except Exception:
        return ""
    ellipsoid = proj_crs.ellipsoid
    if ellipsoid is None:
        return ""
    identifier = ellipsoid.to_json_dict().get("id")
    if isinstance(identifier, dict) and "authority" in identifier and "code" in identifier:
        return f"{identifier['authority']}:{identifier['code']}"
    return ellipsoid.name or ""


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def _crs(parent: ET.Element, crs: CRS) -> None:
    spatial = ET.SubElement(ET.SubElement(parent, "srs"), "spatialrefsys")
    _text(spatial, "wkt", crs.to_wkt())
    _text(spatial, "authid", crs.to_string())
    _text(spatial, "description", crs.to_string())
    _text(spatial, "projectionacronym", "longlat" if crs.is_geographic else "")
    _text(spatial, "ellipsoidacronym", _ellipsoid_acronym(crs))
    _text(spatial, "geographicflag", "true" if crs.is_geographic else "false")


class _ArrowSink(io.RawIOBase):
    """Store one IPC stream as ordered SQLite values, not one aggregate BLOB."""

    def __init__(self, db: sqlite3.Connection, layer: str, kind: str) -> None:
        super().__init__()
        self.db, self.layer, self.kind = db, layer, kind
        self.stream_id = db.execute("SELECT COALESCE(MAX(stream_id), -1) + 1 FROM source_arrow").fetchone()[0]
        # Leave room for SQLite's complete encoded row, including labels.
        self.part_size = min(8 * 1024 * 1024, db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) // 2)
        self.pending = bytearray()
        self.part = 0

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer) -> int:
        view = memoryview(data)
        size = len(view)
        while view:
            count = min(self.part_size - len(self.pending), len(view))
            self.pending.extend(view[:count])
            view = view[count:]
            if len(self.pending) == self.part_size:
                self.flush()
        return size

    def flush(self) -> None:
        if self.pending:
            self.db.execute(
                "INSERT INTO source_arrow VALUES (?, ?, ?, ?, ?)",
                (self.layer, self.kind, self.pending, self.stream_id, self.part),
            )
            self.pending.clear()
            self.part += 1


def _arrow(db: sqlite3.Connection, layer: str, kind: str, table: pa.Table) -> int:
    with _ArrowSink(db, layer, kind) as sink, pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.stream_id


def _iso2d_geom_span(buf: bytes, offset: int) -> tuple[int, str] | None:
    if offset + 5 > len(buf):
        return None
    order = buf[offset]
    if order == 1:
        endian = "<"
    elif order == 0:
        endian = ">"
    else:
        return None
    wkb_type = struct.unpack_from(endian + "I", buf, offset + 1)[0]
    pos = offset + 5
    if wkb_type == 1:
        if pos + 16 > len(buf):
            return None
        x, y = struct.unpack_from(endian + "dd", buf, pos)
        if not (isfinite(x) and isfinite(y)):
            return None
        return pos + 16, "Point"
    if wkb_type == 2:
        if pos + 4 > len(buf):
            return None
        count = struct.unpack_from(endian + "I", buf, pos)[0]
        if count < 2:
            return None
        end = pos + 4 + 16 * count
        if end > len(buf):
            return None
        return end, "LineString"
    if wkb_type == 3:
        if pos + 4 > len(buf):
            return None
        n_rings = struct.unpack_from(endian + "I", buf, pos)[0]
        if n_rings < 1:
            return None
        pos += 4
        for _ in range(n_rings):
            if pos + 4 > len(buf):
                return None
            n_pts = struct.unpack_from(endian + "I", buf, pos)[0]
            if n_pts < 4:
                return None
            ring_end = pos + 4 + 16 * n_pts
            if ring_end > len(buf):
                return None
            p0 = struct.unpack_from(endian + "dd", buf, pos + 4)
            p1 = struct.unpack_from(endian + "dd", buf, ring_end - 16)
            if p0 != p1:
                return None
            pos = ring_end
        return pos, "Polygon"
    if wkb_type in (4, 5, 6):
        child_name = ("Point", "LineString", "Polygon")[wkb_type - 4]
        multi_name = ("MultiPoint", "MultiLineString", "MultiPolygon")[wkb_type - 4]
        if pos + 4 > len(buf):
            return None
        n_parts = struct.unpack_from(endian + "I", buf, pos)[0]
        if n_parts < 1:
            return None
        pos += 4
        for _ in range(n_parts):
            child = _iso2d_geom_span(buf, pos)
            if child is None or child[1] != child_name:
                return None
            pos = child[0]
        return pos, multi_name
    return None


def _validated_feature_wkb(feature: Feature) -> bytes:
    wkb = feature.wkb
    span = _iso2d_geom_span(wkb, 0)
    if span is not None and span[0] == len(wkb) and span[1] in feature.geometry_types:
        return wkb
    return feature.geometry.wkb


def _gather_feature_tables(originals: Sequence[Feature]) -> list[tuple[pa.Table, list[int]]]:
    groups: list[tuple[pa.Schema, list[tuple[int, Feature]]]] = []
    for idx, feat in enumerate(originals):
        pos = idx + 1
        schema = feat.table.schema
        for group_schema, items in groups:
            if group_schema.equals(schema, check_metadata=True):
                items.append((pos, feat))
                break
        else:
            groups.append((schema, [(pos, feat)]))
    gathered: list[tuple[pa.Table, list[int]]] = []
    for _, items in groups:
        run_pieces: list[pa.Table] = []
        i = 0
        while i < len(items):
            tbl = items[i][1].table
            run_rows = [items[i][1].row]
            j = i + 1
            while j < len(items) and items[j][1].table is tbl:
                run_rows.append(items[j][1].row)
                j += 1
            if run_rows == list(range(tbl.num_rows)):
                run_pieces.append(tbl)
            else:
                run_pieces.append(take_rows(tbl, run_rows))
            i = j
        table = run_pieces[0] if len(run_pieces) == 1 else pa.concat_tables(run_pieces)
        gathered.append((compact_dictionaries(table), [pos for pos, _ in items]))
    return gathered


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _scalar_type(dtype: pa.DataType) -> str | None:
    if pa.types.is_boolean(dtype) or (pa.types.is_integer(dtype) and not pa.types.is_uint64(dtype)):
        return "INTEGER"
    if pa.types.is_floating(dtype):
        return "REAL"
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return "TEXT"
    if pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype):
        return "BLOB"
    return None


def _serialize_coverage(coverage: UpstreamCoverage) -> str:
    return json.dumps(
        {
            "status": coverage.status,
            "declared_completeness": coverage.declared_completeness,
            "cuts_column": coverage.cuts_column,
            "cut_counts": (
                None if coverage.cut_counts is None else {str(uid): count for uid, count in coverage.cut_counts.items()}
            ),
            "unverified_unit_ids": (
                None if coverage.unverified_unit_ids is None else list(coverage.unverified_unit_ids)
            ),
            "reason": coverage.reason,
        }
    )


def _initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        PRAGMA application_id = 1196444487;
        PRAGMA user_version = 10300;
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
          organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
        CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL,
          identifier TEXT UNIQUE, description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL,
          geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
          PRIMARY KEY (table_name, column_name));
        CREATE TABLE source_arrow (layer_name TEXT NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL,
          stream_id INTEGER NOT NULL, part INTEGER NOT NULL, PRIMARY KEY (stream_id, part));
        CREATE TABLE source_metadata (layer_name TEXT NOT NULL, metadata TEXT NOT NULL);
        CREATE TABLE target_issues (target_index INTEGER, target_name TEXT, code TEXT, reason TEXT);
        CREATE TABLE result_metadata (target_name TEXT, target_index INTEGER, metadata TEXT, original_target BLOB, adjusted_target BLOB, coverage TEXT);
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('result_metadata','attributes','result_metadata');
        CREATE TABLE river_connections (layer_name TEXT, source_id INTEGER, relation TEXT, target_id INTEGER);
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('river_connections','attributes','river_connections');
        INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined Cartesian', -1, 'NONE', -1, 'undefined', NULL);
        INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined geographic', 0, 'NONE', 0, 'undefined', NULL);
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('source_arrow','attributes','source_arrow');
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('source_metadata','attributes','source_metadata');
        INSERT INTO gpkg_contents (table_name, data_type, identifier) VALUES ('target_issues','attributes','target_issues');
    """)


class QGISBundle:
    """Build a portable QGIS folder incrementally without hydrological computation.

    Notes
    -----
    Add methods retain data and return this builder for chaining. `write` creates
    a new folder with a relative-reference project, GeoPackage and native-grid
    GeoTIFFs; it never replaces an existing path. No merging, delineation or river
    tracing is performed implicitly. Perform source-dependent export inside the
    fabric context; current raster subsets already contain materialized arrays.

    Scalar source fields become ordinary vector columns. Reserved-name collisions
    gain ``source_`` prefixes; ``source_metadata.attribute_columns`` records names.
    Rich values, original WKB and Arrow schemas/metadata are losslessly retained
    as collective IPC streams in ``source_arrow`` (``kind="features"`` for
    vector layers and ``kind="nodes"`` for supplied river nodes). Each
    ``stream_id`` identifies one table aligned with layer row order when one
    schema backs the layer, or recorded per-``fid`` in ``source_metadata``
    (``feature_streams`` / ``node_streams``) when mixed backing schemas occur.
    Concatenate its ``payload`` values in ascending ``part`` order before opening
    the IPC stream. Small streams occupy one row. Parts are byte segments, not
    independent IPC streams. Individual geometry and ordinary attribute values
    must still fit SQLite's value/row limits; segmentation removes the aggregate
    source-IPC limit only. Result locations are WGS84 WKB in
    ``result_metadata``, alongside provenance and JSON-encoded
    ``UpstreamCoverage`` diagnostics; these are not extra map layers.
    """

    def __init__(self) -> None:
        self._vectors: list[_Vector] = []
        self._networks: list[tuple[int, RiverNetwork]] = []
        self._rasters: list[tuple[str, RasterSubset, str | None]] = []
        self._groups: list[str] = []
        self._issues: list[BatchIssue] = []
        self._results: list[tuple[str | None, int | None, ExportResult]] = []

    def add_vector(
        self,
        name: str,
        data: BaseGeometry | VectorFeature | Sequence[VectorFeature] | RiverNetwork,
        *,
        crs: str | CRS | None = None,
    ) -> Self:
        """Add one vector layer, retaining supplied source records.

        Parameters
        ----------
        name
            Display layer name. Storage table names are generated separately.
        data
            Raw geometry, feature, collection of features, or river network.
            Networks also retain relationships, source nodes and declarations
            in companion tables; individual feature identities are preserved.
        crs
            Required for raw geometry. Feature inputs default to EPSG:4326.
            This assigns the CRS; it does not transform coordinates. Native
            outlet-cell geometry must use its grid CRS explicitly.

        Raises
        ------
        ValueError
            Raw geometry has no explicit CRS.
        """
        sources: list[VectorFeature]
        if isinstance(data, BaseGeometry):
            if crs is None:
                raise ValueError("raw vector geometry requires an explicit CRS")
            wkbs, sources = [data.wkb], []
        else:
            if isinstance(data, RiverNetwork):
                self._networks.append((len(self._vectors), data))
                sources = list(data.rivers)
            elif isinstance(data, (Feature, SubbasinContribution)):
                sources = [data]
            else:
                sources = list(data)
            wkbs = [
                feature.geometry.wkb
                if isinstance(feature, (SubbasinContribution, ClippedRiver))
                else _validated_feature_wkb(feature)
                for feature in sources
            ]
        self._vectors.append(_Vector(name, wkbs, sources, CRS.from_user_input(crs or "EPSG:4326")))
        return self

    def add_raster(self, name: str, data: RasterSubset) -> Self:
        """Add a retained raster subset without reading or warping source data.

        Parameters
        ----------
        name
            Display layer name; a safe storage filename is generated on write.
        data
            Native-grid band and whole-vector mask. Write preserves valid source
            values inside the mask and uses source nodata elsewhere, including
            unrelated corners of the rectangular window.
        """
        self._rasters.append((name, data, None))
        return self

    def add_result(self, result: ExportResult | ExportBatch | BatchResult[ExportResult]) -> Self:
        """Add available selection, delineation or refinement outputs without computing more.

        Parameters
        ----------
        result
            A single result (`SelectionResult`, `DelineationResult`,
            `GranularRefinement` or `FootprintRefinement`) or its batch
            container. Each batch target gets its named group and retained
            identity; failures go into ``target_issues`` without fabricated
            features. Available outputs include sub-basins, merged footprint
            (including retained ``watershed`` geometry for `GranularRefinement`),
            outlined native-CRS outlet cell and one raster pair per refined
            target. Overlapping targets may duplicate pixels. Granular
            refinement is exported with both ``subbasins`` and its retained
            ``watershed`` boundary with no implicit merging during export.

        Notes
        -----
        Original and vector-adjusted targets, provenance and source-network
        coverage diagnostics are retained in ``result_metadata`` when available,
        not added as extra map layers.
        """
        if isinstance(result, BatchResult):
            self._issues.extend(result.issues)
            for target, value in zip(result.targets, result.results, strict=True):
                self._groups.append(target.name)
                if value is not None:
                    self._result(value, target.name, target.index)
        else:
            self._result(result, None, None)
        return self

    def _result(self, result: ExportResult, group: str | None, index: int | None) -> None:
        self._results.append((group, index, result))
        start = len(self._vectors)
        if isinstance(result, FootprintRefinement):
            self.add_vector("watershed", result.geometry, crs="EPSG:4326")
        else:
            if result.subbasins:
                self.add_vector("subbasins", result.subbasins)
            if isinstance(result, (DelineationResult, GranularRefinement)) and result.geometry is not None:
                self.add_vector("watershed", result.geometry, crs="EPSG:4326")
        if not isinstance(result, SelectionResult) and result.outlet_cell is not None:
            self.add_vector("outlet_cell", result.outlet_cell.geometry, crs=result.outlet_cell.grid.crs)
            self._vectors[-1].outline = True
        for vector in self._vectors[start:]:
            vector.group, vector.target_index = group, index
        if not isinstance(result, SelectionResult) and result.rasters is not None:
            self._rasters.extend(
                (
                    ("flow_direction", result.rasters.direction, group),
                    ("flow_accumulation", result.rasters.accumulation, group),
                )
            )

    def write(self, path: str | Path) -> Path:
        """Write the accumulated layers to a new portable folder.

        Parameters
        ----------
        path
            New output directory. Missing parents are created.

        Returns
        -------
        Path
            Output folder containing ``watershed.qgz``, ``vectors.gpkg`` and
            any ``raster_*.tif`` files. Project references are relative so the
            entire folder can be moved together.

        Raises
        ------
        FileExistsError
            The destination already exists; it is never overwritten.
        ValueError
            A raster subset lacks retained source values or source nodata.

        Notes
        -----
        Serializes retained arrays and vector records, with no hidden hydrological
        operations. Cost scales with exported features and pixels, including
        duplicated pixels across targets. Writes are not transactional across
        the folder: a failure may leave partial files for the caller to remove.
        """
        folder = Path(path)
        folder.mkdir(parents=True, exist_ok=False)
        project = ET.Element("qgis", version="3.40.0", projectname="Pourpoint")
        _text(project, "title", "Pourpoint")
        tree = ET.SubElement(project, "layer-tree-group", name="", checked="Qt::Checked", expanded="1")
        layers = ET.SubElement(project, "projectlayers")
        groups = {
            name: ET.SubElement(tree, "layer-tree-group", name=name, checked="Qt::Checked", expanded="1")
            for name in dict.fromkeys(self._groups)
        }
        with closing(sqlite3.connect(folder / "vectors.gpkg")) as db, db:
            _initialize(db)
            db.executemany(
                "INSERT INTO result_metadata VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        name,
                        index,
                        json.dumps(_thaw(result.provenance)),
                        result.original_target.wkb if isinstance(result, DelineationResult) else None,
                        result.target.wkb if isinstance(result, (DelineationResult, SelectionResult)) else None,
                        _serialize_coverage(result.coverage),
                    )
                    for name, index, result in self._results
                ],
            )
            db.executemany(
                "INSERT INTO target_issues VALUES (?, ?, ?, ?)",
                [(issue.index, issue.name, issue.code, issue.reason) for issue in self._issues],
            )
            for index, vector in enumerate(self._vectors):
                table = f"layer_{index}"
                srid = vector.crs.to_epsg() or 100000 + index
                db.execute(
                    "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        vector.crs.to_string(),
                        srid,
                        "EPSG" if vector.crs.to_epsg() else "NONE",
                        srid,
                        vector.crs.to_wkt(),
                        None,
                    ),
                )
                columns: dict[str, str] = {}
                col_defs: list[str] = []
                used = {"fid", "geometry", "source_id", "target_name", "target_index"}
                originals = [
                    source.source if isinstance(source, SubbasinContribution) else source for source in vector.sources
                ]
                seen_tables: set[int] = set()
                for orig in originals:
                    if id(orig.table) in seen_tables:
                        continue
                    seen_tables.add(id(orig.table))
                    for arrow_field in orig.attributes.schema:
                        sql_type = _scalar_type(arrow_field.type)
                        if arrow_field.name in columns or sql_type is None:
                            continue
                        column = arrow_field.name
                        while column.lower() in used:
                            column = "source_" + column
                        used.add(column.lower())
                        columns[arrow_field.name] = column
                        col_defs.append(f"{_quote(column)} {sql_type}")
                base_defs = [
                    "fid INTEGER PRIMARY KEY",
                    "geometry BLOB",
                    "source_id INTEGER",
                    "target_name TEXT",
                    "target_index INTEGER",
                    *col_defs,
                ]
                db.execute(f'CREATE TABLE "{table}" ({", ".join(base_defs)})')
                db.execute(
                    "INSERT INTO gpkg_contents (table_name,data_type,identifier,srs_id) VALUES (?, 'features', ?, ?)",
                    (table, table, srid),
                )
                db.execute(
                    "INSERT INTO gpkg_geometry_columns VALUES (?, 'geometry', 'GEOMETRY', ?, 0, 0)", (table, srid)
                )
                header = b"GP" + bytes((0, 1)) + struct.pack("<i", srid)
                scalar_fields = list(columns)
                scalar_sql_cols = [columns[name] for name in scalar_fields]
                if originals:
                    feature_groups = _gather_feature_tables(originals)
                    source_ids = [0] * len(originals)
                    scalar_rows: list[list[object]] = [[] for _ in range(len(originals))]
                    for gathered_table, fids in feature_groups:
                        select_cols = list(
                            dict.fromkeys(
                                ["id", *(f for f in scalar_fields if gathered_table.schema.get_field_index(f) != -1)]
                            )
                        )
                        py_rows = gathered_table.select(select_cols).to_pylist()
                        for fid, row_dict in zip(fids, py_rows, strict=True):
                            pos = fid - 1
                            source_ids[pos] = row_dict["id"]
                            scalar_rows[pos] = [row_dict.get(name) for name in scalar_fields]
                    insert_cols = [
                        "fid",
                        "geometry",
                        "source_id",
                        "target_name",
                        "target_index",
                        *(_quote(col) for col in scalar_sql_cols),
                    ]
                    placeholders = ", ".join("?" * len(insert_cols))
                    db.executemany(
                        f'INSERT INTO "{table}" ({", ".join(insert_cols)}) VALUES ({placeholders})',
                        [
                            (
                                row + 1,
                                header + wkb,
                                source_ids[row],
                                vector.group,
                                vector.target_index,
                                *scalar_rows[row],
                            )
                            for row, wkb in enumerate(vector.wkbs)
                        ],
                    )
                    stream_refs = [
                        {"stream_id": _arrow(db, table, "features", gathered_table), "fids": fids}
                        for gathered_table, fids in feature_groups
                    ]
                    metadata_payload: dict[str, object] = {}
                    if columns:
                        metadata_payload["attribute_columns"] = columns
                    if len(stream_refs) > 1:
                        metadata_payload["feature_streams"] = stream_refs
                    if metadata_payload:
                        db.execute("INSERT INTO source_metadata VALUES (?, ?)", (table, json.dumps(metadata_payload)))
                else:
                    db.executemany(
                        f'INSERT INTO "{table}" (fid,geometry,source_id,target_name,target_index) VALUES (?, ?, ?, ?, ?)',
                        [
                            (row + 1, header + wkb, None, vector.group, vector.target_index)
                            for row, wkb in enumerate(vector.wkbs)
                        ],
                    )
                self._layer(
                    layers,
                    groups[vector.group] if vector.group is not None else tree,
                    vector.name,
                    f"./vectors.gpkg|layername={table}",
                    "vector",
                    vector.crs,
                    vector.outline,
                )
            for index, network in self._networks:
                table = f"layer_{index}"
                _arrow(db, table, "relationships", network.relationships)
                connections = [
                    (table, row["id"], relation, target)
                    for row in network.relationships.to_pylist()
                    for relation, targets in row.items()
                    if relation != "id" and isinstance(targets, list)
                    for target in targets
                ]
                if connections:
                    db.executemany("INSERT INTO river_connections VALUES (?, ?, ?, ?)", connections)
                if network.node_relationships is not None:
                    _arrow(db, table, "node_relationships", network.node_relationships)
                if network.nodes:
                    node_groups = _gather_feature_tables(network.nodes)
                    node_refs = [
                        {
                            "stream_id": _arrow(db, table, "nodes", node_table),
                            "indices": [pos - 1 for pos in positions],
                        }
                        for node_table, positions in node_groups
                    ]
                    if len(node_refs) > 1:
                        db.execute(
                            "INSERT INTO source_metadata VALUES (?, ?)",
                            (table, json.dumps({"node_streams": node_refs})),
                        )
                db.execute(
                    "INSERT INTO source_metadata VALUES (?, ?)", (table, json.dumps(_thaw(network.declarations)))
                )
        for index, (name, subset, group) in enumerate(self._rasters):
            filename = f"raster_{index}.tif"
            window = subset.window
            if subset.band == "direction":
                values = window.raw_direction
                nodata = window.direction_nodata
                valid = window.direction_valid & subset.mask
            else:
                values = window.accumulation
                nodata = window.accumulation_nodata
                valid = window.accumulation_valid & subset.mask
            if values is None or nodata is None:
                raise ValueError("raster export requires retained source values and nodata")
            fill = np.asarray(nodata, dtype=values.dtype).item()
            output = values.copy()
            output[~valid] = fill
            with rasterio.open(
                folder / filename,
                "w",
                driver="GTiff",
                width=output.shape[1],
                height=output.shape[0],
                count=1,
                dtype=output.dtype,
                crs=window.crs,
                transform=window.transform,
                nodata=nodata,
                compress="deflate",
                BIGTIFF="IF_SAFER",
            ) as raster:
                raster.write(output, 1)
                raster.write_mask(valid.astype("uint8") * 255)
            self._layer(
                layers, groups[group] if group is not None else tree, name, "./" + filename, "raster", window.crs
            )
        with zipfile.ZipFile(folder / "watershed.qgz", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("watershed.qgs", ET.tostring(project, encoding="utf-8", xml_declaration=True))
        return folder

    @staticmethod
    def _layer(
        layers: ET.Element, tree: ET.Element, name: str, source: str, kind: str, crs: CRS, outline: bool = False
    ) -> None:
        identity = "layer_" + uuid4().hex
        tree_layer = ET.SubElement(
            tree,
            "layer-tree-layer",
            id=identity,
            name=name,
            source=source,
            providerKey="ogr" if kind == "vector" else "gdal",
            checked="Qt::Checked",
            expanded="1",
        )
        if outline:
            tree.remove(tree_layer)
            tree.insert(0, tree_layer)
        layer = ET.SubElement(layers, "maplayer", type=kind, autoRefreshEnabled="0")
        _text(layer, "id", identity)
        _text(layer, "layername", name)
        _text(layer, "datasource", source)
        _text(layer, "provider", "ogr" if kind == "vector" else "gdal")
        _crs(layer, crs)
        if outline:
            renderer = ET.SubElement(layer, "renderer-v2", type="singleSymbol", symbollevels="0")
            symbol = ET.SubElement(ET.SubElement(renderer, "symbols"), "symbol", type="fill", name="0", alpha="1")
            symbol_layer = ET.SubElement(symbol, "layer", {"class": "SimpleFill", "enabled": "1"})
            options = ET.SubElement(symbol_layer, "Option", type="Map")
            for key, value in {"style": "no", "outline_color": "255,0,0,255", "outline_width": "0.6"}.items():
                ET.SubElement(options, "Option", name=key, value=value, type="QString")
