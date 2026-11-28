"""Combined-footprint river selection and optional nearest-ground-distance snapping."""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite, sqrt
from typing import cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import shapely
from pyproj import Geod
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from pourpoint._selection import compact_dictionaries, take_rows
from pourpoint.fabric import HydroFabric
from pourpoint.features import River, RiverNetwork, SourceNode, Subbasin
from pourpoint.metadata import bounds
from pourpoint.refinement import SubbasinContribution
from pourpoint.results import OperationError
from pourpoint.spatial import in_float32_range

_GOLDEN_RATIO = (sqrt(5) - 1) / 2
_MIN_METERS_PER_DEGREE = 110_000.0
_GEODS = threading.local()


def _geod() -> Geod:
    geod: Geod | None = getattr(_GEODS, "wgs84", None)
    if geod is None:
        geod = Geod(ellps="WGS84")
        _GEODS.wgs84 = geod
    return geod


@dataclass(frozen=True, slots=True)
class _SnapIndex:
    """Pre-validated immutable segment and geometry index for WGS84 stream snapping."""

    geometries: tuple[LineString | MultiLineString, ...]
    geom_bounds: tuple[tuple[float, float, float, float], ...]
    x0: npt.NDArray[np.float64]
    y0: npt.NDArray[np.float64]
    x1: npt.NDArray[np.float64]
    y1: npt.NDArray[np.float64]
    xmin: npt.NDArray[np.float64]
    ymin: npt.NDArray[np.float64]
    xmax: npt.NDArray[np.float64]
    ymax: npt.NDArray[np.float64]


@dataclass(frozen=True)
class ClippedRiver(River):
    """A derived river portion retaining the original source record.

    Attributes
    ----------
    portion
        Selected linework in EPSG:4326, exposed by ``geometry``. New endpoints
        from clipping are not natural outlets. Inherited ID, attributes and WKB
        still describe the source feature, not recomputed values for the portion.
    """

    portion: BaseGeometry

    @property
    def geometry(self) -> BaseGeometry:
        """Selected river portion in EPSG:4326."""
        return self.portion

    @property
    def source(self) -> River:
        """Original whole river record, with source geometry and attributes."""
        return River(self.table, self.row)


def _linework(geometry: BaseGeometry) -> BaseGeometry:
    if isinstance(geometry, (LineString, MultiLineString)):
        return geometry
    return shapely.union_all(
        [part for part in shapely.get_parts(geometry) if isinstance(part, (LineString, MultiLineString))]
    )


def _oriented_line(
    river: River, nodes_by_id: Mapping[int, SourceNode], relationship_row: Mapping[str, object]
) -> LineString:
    geometry = river.geometry
    parts = list(geometry.geoms) if isinstance(geometry, MultiLineString) else [geometry]
    coordinates: list[tuple[float, ...]] = []
    for part in parts:
        values = list(part.coords)
        if coordinates and coordinates[-1] != values[0]:
            raise OperationError("rivers.ambiguous_direction", f"River {river.id} has disconnected or unordered parts")
        coordinates.extend(values if not coordinates else values[1:])
    line = LineString(coordinates)
    orientation = river.attributes.get("geometry_orientation", 0)
    if orientation == 1:
        return line
    if orientation == 2:
        return shapely.reverse(line)
    node_ids: dict[str, list[int]] = {}
    for end in ("upstream", "downstream"):
        ids = relationship_row.get(f"{end}_node_ids")
        source_id = river.attributes.get(f"{end}_node_id")
        if isinstance(ids, list) and ids:
            node_ids[end] = ids
        else:
            node_ids[end] = [source_id] if isinstance(source_id, int) else []
    votes: set[bool] = set()
    start_point = Point(coordinates[0])
    end_point = Point(coordinates[-1])
    for end, ids in node_ids.items():
        for node_id in ids:
            node = nodes_by_id.get(node_id)
            if node is None:
                continue
            node_geom = node.geometry
            at_start = node_geom.equals(start_point)
            at_end = node_geom.equals(end_point)
            if at_start != at_end:
                votes.add(at_start if end == "upstream" else at_end)
    if len(votes) != 1:
        raise OperationError(
            "rivers.ambiguous_direction", f"River {river.id} lacks unambiguous directed endpoint geometry"
        )
    return line if votes.pop() else shapely.reverse(line)


def select_rivers(
    *,
    fabric: HydroFabric,
    target: BaseGeometry | Subbasin | SubbasinContribution | Sequence[Subbasin | SubbasinContribution],
    level: int = 1,
    include_downstream: bool = False,
) -> RiverNetwork:
    """Select river portions inside one combined footprint.

    Parameters
    ----------
    fabric
        Open fabric supplying rivers and their relationships.
    target
        EPSG:4326 polygonal geometry, sub-basin/contribution, or collection of
        parts forming one footprint. Internal part boundaries do not limit flow;
        this collection is not a batch of independent targets.
    level
        Nonnegative river level, independent of catchment levels. No fallback
        or inferred correspondence between catchment and river tiers is used.
    include_downstream
        Extend along every supplied outgoing continuation, including splits and
        reconvergence. Features are deduplicated by source ID. Partial boundary
        features require unambiguous orientation to retain downstream linework.

    Returns
    -------
    RiverNetwork
        Selected portions with original identities, attributes, directed
        relationships and supplied nodes. Source quantities are not recomputed.
        Empty footprints or no intersecting rivers give an empty network when
        the river family and its relationship data are supplied. Materialized
        results survive closure.

    Raises
    ------
    OperationError
        ``rivers.unavailable`` when the river family is absent;
        ``rivers.invalid_target`` when the target footprint is invalid or has
        non-finite or out-of-range coordinates;
        ``rivers.connectivity_unavailable`` when extension needs a missing
        ``downstream_ids`` column in supplied relationship data;
        ``rivers.ambiguous_direction`` when a partial
        feature cannot be oriented from supplied orientation or endpoint nodes.
    KeyError
        River relationship data are not declared, even for an empty footprint
        or no matching rivers. Missing referenced source IDs also raise.
    ValueError
        Invalid level, nonpolygonal nonempty footprint, or downstream flow
        crossing river levels. Integrity errors propagate rather than producing
        a partial network.
    TypeError
        Nonboolean ``include_downstream``.

    Notes
    -----
    Clipping creates endpoints, not natural outlets. Retained relationship
    references outside the selection must not be interpreted as missing flow
    or source-described termination. No mainstem is inferred from node incidence.
    """
    if type(level) is not int or level < 0:
        raise ValueError("level must be a nonnegative integer")
    if type(include_downstream) is not bool:
        raise TypeError("include_downstream must be boolean")
    if isinstance(target, BaseGeometry):
        footprint = target
    elif isinstance(target, (Subbasin, SubbasinContribution)):
        footprint = target.geometry
    else:
        from pourpoint._union import union_subbasins

        parts = list(target)
        footprint = union_subbasins(parts) if parts else Polygon()
    if not footprint.is_valid:
        raise OperationError("rivers.invalid_target", "river target geometry is invalid")
    if not footprint.is_empty and not isinstance(footprint, (Polygon, MultiPolygon)):
        raise ValueError("river target must be a polygon footprint")
    if not footprint.is_empty:
        try:
            checked_bounds = bounds(footprint.bounds)
        except ValueError as error:
            raise OperationError("rivers.invalid_target", str(error)) from error
        if not in_float32_range(checked_bounds):
            raise OperationError("rivers.invalid_target", "river target exceeds float32 coordinate range")
    if "rivers" not in fabric.manifest.families:
        raise OperationError("rivers.unavailable", "Source does not supply rivers")
    if footprint.is_empty:
        return fabric.river_network([])
    table = fabric.candidates("rivers", cast(tuple[float, float, float, float], footprint.bounds), levels=[level])
    shapely.prepare(footprint)
    pieces: dict[int, BaseGeometry] = {}
    matched_rows: list[int] = []
    covered_ids: set[int] = set()
    for row in range(table.num_rows):
        river = River(table, row)
        geom = river.geometry
        if not footprint.intersects(geom):
            continue
        if footprint.covers(geom):
            clipped = _linework(geom)
            if not clipped.is_empty:
                pieces[river.id] = clipped
                matched_rows.append(row)
                covered_ids.add(river.id)
        else:
            clipped = _linework(geom.intersection(footprint))
            if not clipped.is_empty:
                pieces[river.id] = clipped
                matched_rows.append(row)
    if not pieces:
        return fabric.river_network([])
    initial_ids = list(pieces)
    initial_table = compact_dictionaries(take_rows(table, matched_rows))
    initial_relations = fabric.relationships("rivers", initial_ids)
    network = fabric._build_river_network(initial_table, initial_relations)
    if include_downstream:
        declaration = fabric.manifest.relationships.get("rivers", {})
        columns = declaration.get("columns", {})
        if not isinstance(columns, Mapping) or "downstream_ids" not in columns:
            raise OperationError(
                "rivers.connectivity_unavailable", "Source does not supply downstream river connectivity"
            )
        # A source feature reached through another selected feature is wholly downstream.
        rel_rows = network.relationships.to_pylist()
        downstream = {neighbor for row in rel_rows for neighbor in row["downstream_ids"]}
        reached = (
            set(fabric.traverse("rivers", sorted(downstream), relationship="downstream_ids")) if downstream else set()
        )
        nodes_by_id = {node.id: node for node in network.nodes}
        for river in network.rivers:
            if river.id not in reached and river.id not in covered_ids:
                line = _oriented_line(river, nodes_by_id, rel_rows[river.row])
                clipped = pieces[river.id]
                positions = [line.project(Point(xy)) for part in shapely.get_parts(clipped) for xy in part.coords]
                pieces[river.id] = substring(line, min(positions), line.length)
        all_ids = fabric.traverse("rivers", initial_ids, relationship="downstream_ids")
        extra_ids = [rid for rid in all_ids if rid not in pieces]
        if extra_ids:
            extra_table = fabric.records("rivers", extra_ids)
            all_table = pa.concat_tables([initial_table, extra_table])
            all_relations = fabric.relationships("rivers", all_ids)
            extended = fabric._build_river_network(all_table, all_relations)
        else:
            extended = network
        for river in extended.rivers:
            if river.attributes.get("level") != level:
                raise ValueError("downstream relationship crosses river levels")
            if river.id not in pieces or river.id in reached:
                pieces[river.id] = river.geometry
        network = extended
    return RiverNetwork(
        [ClippedRiver(river.table, river.row, pieces[river.id]) for river in network.rivers],
        network.relationships,
        network.nodes,
        network.node_relationships,
        network.declarations,
    )


type SnapGeometry = RiverNetwork | River | BaseGeometry | Sequence[River]


def _prepare_snap(onto: SnapGeometry) -> _SnapIndex:
    if isinstance(onto, RiverNetwork):
        raw_geoms = [river.geometry for river in onto.rivers]
    elif isinstance(onto, River):
        raw_geoms = [onto.geometry]
    elif isinstance(onto, BaseGeometry):
        raw_geoms = [onto]
    else:
        raw_geoms = [river.geometry for river in onto]
    geometries: list[LineString | MultiLineString] = []
    geom_bounds: list[tuple[float, float, float, float]] = []
    x0_parts: list[npt.NDArray[np.float64]] = []
    y0_parts: list[npt.NDArray[np.float64]] = []
    x1_parts: list[npt.NDArray[np.float64]] = []
    y1_parts: list[npt.NDArray[np.float64]] = []
    for geometry in raw_geoms:
        if not isinstance(geometry, (LineString, MultiLineString)):
            raise ValueError("snap requires stream line geometry")
        for part in shapely.get_parts(geometry):
            coords = shapely.get_coordinates(part)
            if len(coords):
                if not np.all(np.isfinite(coords)) or np.any(
                    (coords[:, 0] < -180) | (coords[:, 0] > 180) | (coords[:, 1] < -90) | (coords[:, 1] > 90)
                ):
                    raise ValueError("snap streams must use finite WGS84 longitude/latitude")
                if len(coords) >= 2:
                    if np.any(np.abs(coords[1:, 0] - coords[:-1, 0]) >= 180):
                        raise ValueError("split antimeridian-spanning stream segments before snapping")
                    x0_parts.append(coords[:-1, 0])
                    y0_parts.append(coords[:-1, 1])
                    x1_parts.append(coords[1:, 0])
                    y1_parts.append(coords[1:, 1])
        geometries.append(geometry)
        if not geometry.is_empty:
            geom_bounds.append(geometry.bounds)
        else:
            geom_bounds.append((float("inf"), float("inf"), float("-inf"), float("-inf")))
    if x0_parts:
        x0 = np.ascontiguousarray(np.concatenate(x0_parts), dtype=np.float64)
        y0 = np.ascontiguousarray(np.concatenate(y0_parts), dtype=np.float64)
        x1 = np.ascontiguousarray(np.concatenate(x1_parts), dtype=np.float64)
        y1 = np.ascontiguousarray(np.concatenate(y1_parts), dtype=np.float64)
        xmin = np.minimum(x0, x1)
        ymin = np.minimum(y0, y1)
        xmax = np.maximum(x0, x1)
        ymax = np.maximum(y0, y1)
    else:
        empty = np.empty(0, dtype=np.float64)
        x0 = y0 = x1 = y1 = xmin = ymin = xmax = ymax = empty
    for arr in (x0, y0, x1, y1, xmin, ymin, xmax, ymax):
        arr.flags.writeable = False
    return _SnapIndex(tuple(geometries), tuple(geom_bounds), x0, y0, x1, y1, xmin, ymin, xmax, ymax)


def _closest_on_segment(
    geod: Geod,
    target_x: float,
    target_y: float,
    sx0: float,
    sy0: float,
    sx1: float,
    sy1: float,
) -> tuple[float, float]:
    dx = sx1 - sx0
    dy = sy1 - sy0
    if dx == 0.0 and dy == 0.0:
        return geod.inv(target_x, target_y, sx0, sy0)[2], 0.0
    d0 = geod.inv(target_x, target_y, sx0, sy0)[2]
    d1 = geod.inv(target_x, target_y, sx1, sy1)[2]
    low, high = 0.0, 1.0
    ratio = _GOLDEN_RATIO
    c = high - ratio * (high - low)
    d = low + ratio * (high - low)
    fc = geod.inv(target_x, target_y, sx0 + c * dx, sy0 + c * dy)[2]
    fd = geod.inv(target_x, target_y, sx0 + d * dx, sy0 + d * dy)[2]
    for _ in range(64):
        if high - low <= 1e-12:
            break
        if fc <= fd:
            high = d
            d = c
            fd = fc
            c = high - ratio * (high - low)
            fc = geod.inv(target_x, target_y, sx0 + c * dx, sy0 + c * dy)[2]
        else:
            low = c
            c = d
            fc = fd
            d = low + ratio * (high - low)
            fd = geod.inv(target_x, target_y, sx0 + d * dx, sy0 + d * dy)[2]
    t_mid = (low + high) * 0.5
    d_mid = geod.inv(target_x, target_y, sx0 + t_mid * dx, sy0 + t_mid * dy)[2]
    best_d, best_t = (d0, 0.0) if d0 <= d1 else (d1, 1.0)
    if d_mid < best_d:
        return d_mid, t_mid
    return best_d, best_t


def _lon_bound_degrees(radius_m: float, target_y: float, dlat: float) -> float | None:
    if radius_m >= 1_000_000.0:
        return None
    max_abs_lat = min(90.0, abs(target_y) + dlat)
    if max_abs_lat >= 89.5:
        return None
    cos_lat = math.cos(math.radians(max_abs_lat))
    if cos_lat <= 0.0:
        return None
    return radius_m / (_MIN_METERS_PER_DEGREE * cos_lat)


def _snap_point(target: BaseGeometry, index: _SnapIndex, radius: float) -> Point:
    if not isinstance(target, Point) or target.is_empty or not all(isfinite(v) for v in target.coords[0]):
        raise OperationError("snap.point_required", "snap target must be a nonempty finite Point")
    if not -180 <= target.x <= 180 or not -90 <= target.y <= 90:
        raise OperationError("snap.point_required", "snap target must use WGS84 longitude/latitude")
    target_x, target_y = float(target.x), float(target.y)
    for geometry, (gx0, gy0, gx1, gy1) in zip(index.geometries, index.geom_bounds, strict=True):
        if gx0 <= target_x <= gx1 and gy0 <= target_y <= gy1 and geometry.covers(target):
            return target
    if not index.x0.size:
        raise OperationError("snap.no_match", "No supplied stream is in range")
    dlat = radius / _MIN_METERS_PER_DEGREE
    dy = np.maximum(0.0, np.maximum(index.ymin - target_y, target_y - index.ymax))
    keep = dy <= dlat
    dlon = _lon_bound_degrees(radius, target_y, dlat)
    if dlon is not None:
        dx0 = np.maximum(0.0, np.maximum(index.xmin - target_x, target_x - index.xmax))
        dx_plus = np.maximum(0.0, np.maximum((index.xmin + 360.0) - target_x, target_x - (index.xmax + 360.0)))
        dx_minus = np.maximum(0.0, np.maximum((index.xmin - 360.0) - target_x, target_x - (index.xmax - 360.0)))
        dx = np.minimum(dx0, np.minimum(dx_plus, dx_minus))
        keep &= dx <= dlon
    candidate_indices = np.flatnonzero(keep)
    if not candidate_indices.size:
        raise OperationError("snap.no_match", "No supplied stream is in range")
    geod = _geod()
    x0_arr, y0_arr, x1_arr, y1_arr = index.x0, index.y0, index.x1, index.y1
    xmin_arr, ymin_arr, xmax_arr, ymax_arr = index.xmin, index.ymin, index.xmax, index.ymax
    best_distance = float("inf")
    best_point: Point | None = None
    cur_dlat = dlat
    cur_dlon = dlon
    for seg_idx in candidate_indices.tolist():
        if best_distance < float("inf"):
            seg_ymin = float(ymin_arr[seg_idx])
            seg_ymax = float(ymax_arr[seg_idx])
            if max(0.0, seg_ymin - target_y, target_y - seg_ymax) > cur_dlat:
                continue
            if cur_dlon is not None:
                seg_xmin = float(xmin_arr[seg_idx])
                seg_xmax = float(xmax_arr[seg_idx])
                seg_dx = min(
                    max(0.0, seg_xmin - target_x, target_x - seg_xmax),
                    max(0.0, (seg_xmin + 360.0) - target_x, target_x - (seg_xmax + 360.0)),
                    max(0.0, (seg_xmin - 360.0) - target_x, target_x - (seg_xmax - 360.0)),
                )
                if seg_dx > cur_dlon:
                    continue
        sx0 = float(x0_arr[seg_idx])
        sy0 = float(y0_arr[seg_idx])
        sx1 = float(x1_arr[seg_idx])
        sy1 = float(y1_arr[seg_idx])
        value, t = _closest_on_segment(geod, target_x, target_y, sx0, sy0, sx1, sy1)
        if value < best_distance:
            best_distance = value
            best_point = Point(sx0 + t * (sx1 - sx0), sy0 + t * (sy1 - sy0))
            tight = value + 1e-3
            cur_dlat = tight / _MIN_METERS_PER_DEGREE
            cur_dlon = _lon_bound_degrees(tight, target_y, cur_dlat)
    if best_point is None or best_distance > radius:
        raise OperationError("snap.no_match", "No supplied stream is in range")
    return best_point


def snap(*, target: BaseGeometry, onto: SnapGeometry, radius: float) -> Point:
    """Find the nearest point on supplied streams within a search radius.

    Parameters
    ----------
    target
        Nonempty finite Point in EPSG:4326 (longitude, latitude).
    onto
        LineString, MultiLineString, River, RiverNetwork or sequence of rivers.
        Stream vertices must be finite and in EPSG:4326.
    radius
        Maximum WGS84 ground distance in metres, finite and nonnegative.

    Returns
    -------
    Point
        Snapped location in EPSG:4326. Equal-distance matches retain input order.

    Raises
    ------
    OperationError
        ``snap.point_required`` for an invalid WGS84 point;
        ``snap.no_match`` when no supplied stream is in range.
    ValueError
        Invalid radius or stream geometry. Split segments spanning 180 degrees
        or more of longitude before calling.

    Notes
    -----
    Intended for local stream segments, not arbitrary globe-spanning lines.
    Segments interpolate straight in longitude/latitude, not along great-circle
    arcs; distances are measured on the WGS84 ellipsoid. This optional operation
    neither infers a network nor selects a raster outlet cell.
    """
    if not isinstance(target, Point) or target.is_empty or not all(isfinite(v) for v in target.coords[0]):
        raise OperationError("snap.point_required", "snap target must be a nonempty finite Point")
    if not -180 <= target.x <= 180 or not -90 <= target.y <= 90:
        raise OperationError("snap.point_required", "snap target must use WGS84 longitude/latitude")
    if (
        isinstance(radius, (bool, np.bool_))
        or not isinstance(radius, (int, float, np.integer, np.floating))
        or not isfinite(radius)
        or radius < 0
    ):
        raise ValueError("radius must be finite and nonnegative")
    index = _prepare_snap(onto)
    return _snap_point(target, index, float(radius))
