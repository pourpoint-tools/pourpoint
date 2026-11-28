"""Whole-footprint reverse-D8 refinement under vector/raster equivalence.

Vector inputs and outputs use WGS84. Native cells are admitted by center using
GDAL's rasterization convention. Pixel polygons are clipped to the vector mask;
source contributions partition that same geometry at original unit boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import floor, isfinite
from typing import Literal, cast, overload

import numpy as np
import numpy.typing as npt
import shapely
from pyproj import Transformer
from rasterio.features import geometry_mask, shapes
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry

from pourpoint._batch import execute, workers
from pourpoint._union import union_subbasins
from pourpoint.fabric import HydroFabric
from pourpoint.features import Attributes, Subbasin
from pourpoint.metadata import bounds
from pourpoint.rasters import MissingRastersError, RasterCoverageError, RasterGrid, RasterWindow, _transformer
from pourpoint.results import (
    BatchResult,
    DelineationResult,
    OperationError,
    SelectionResult,
    Target,
    UpstreamCoverage,
)


def _unassessed_subbasins_coverage() -> UpstreamCoverage:
    return UpstreamCoverage("not_assessed", reason="coverage was not assessed for manually supplied subbasins")


def _unassessed_footprint_coverage() -> UpstreamCoverage:
    return UpstreamCoverage("not_assessed", reason="coverage was not assessed for a manually supplied footprint")


@dataclass(frozen=True, repr=False)
class SubbasinContribution:
    """A contributing polygonal portion retaining its whole source unit.

    Attributes
    ----------
    source
        Original sub-basin with unchanged attributes, metadata and source WKB.
    geometry
        Derived contribution in EPSG:4326. Source area and other quantities are
        not recomputed for this geometry. Materialized portions survive closure."""

    source: Subbasin
    geometry: BaseGeometry

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id}, geometry={self.geometry.geom_type})"

    @property
    def id(self) -> int:
        """Original source sub-basin ID."""
        return self.source.id

    @property
    def attributes(self) -> Attributes:
        """Unchanged source attributes, including nulls and original quantities."""
        return self.source.attributes


@dataclass(frozen=True)
class OutletCell:
    """A selected cell addressed on the original native raster grid.

    Attributes
    ----------
    grid
        Source grid identity, CRS, affine transform and dimensions.
    row, col
        Zero-based source-grid indices, not offsets within a read window."""

    grid: RasterGrid
    row: int
    col: int

    @property
    def center(self) -> Point:
        """Cell center in the native grid CRS; a display location, not a station correction."""
        return Point(self.grid.transform @ (self.col + 0.5, self.row + 0.5))

    @property
    def geometry(self) -> Polygon:
        """Cell boundary polygon derived from the native grid affine transform."""
        return Polygon(
            [self.grid.transform @ (self.col + x, self.row + y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
        )


@dataclass(frozen=True)
class RasterSubset:
    """Materialized native raster window with the refinement mask.

    Attributes
    ----------
    window
        Retained paired-band values, validity, native CRS and affine transform.
    mask
        Boolean array aligned with the window; True admits cells in the whole
        vector delineation mask, not just cells in the final watershed.
    band
        Selects direction or accumulation values for export.

    Notes
    -----
    Export keeps source values at valid masked cells and uses source nodata
    elsewhere. The window is already read, not a deferred fabric request."""

    window: RasterWindow
    mask: npt.NDArray[np.bool_]
    band: Literal["direction", "accumulation"]


@dataclass(frozen=True)
class RefinementRasters:
    """Paired raster subsets over the same whole vector mask.

    Attributes
    ----------
    direction, accumulation
        Native-grid subsets for the corresponding bands. Neither is restricted
        to the final contributing watershed. Source values and nodata are retained."""

    direction: RasterSubset
    accumulation: RasterSubset


@dataclass(frozen=True)
class GranularRefinement:
    """Point refinement partitioned at original sub-basin boundaries.

    Attributes
    ----------
    subbasins
        Nonempty contributing portions in source input order, with unchanged
        source identity and attributes. Geometries use EPSG:4326.
    geometry
        Dissolved watershed boundary in EPSG:4326, topologically equivalent to
        merging ``subbasins``.
    outlet_cell
        Selected source-grid cell, whose geometry uses the native raster CRS.
    rasters
        Materialized band subsets over the complete combined vector mask.
    provenance
        Source manifest declarations; not proof of vector/raster equivalence.
    coverage
        Retained source-network coverage assessment when refining a
        `SelectionResult` or selection batch, or an explicit ``"not_assessed"``
        assessment for manually supplied sub-basins. Retaining this assessment
        does not recertify the raster output."""

    subbasins: list[SubbasinContribution]
    geometry: Polygon | MultiPolygon
    outlet_cell: OutletCell
    rasters: RefinementRasters
    provenance: Mapping[str, object] = field(default_factory=dict)
    coverage: UpstreamCoverage = field(default_factory=_unassessed_subbasins_coverage)


@dataclass(frozen=True)
class FootprintRefinement:
    """Point refinement returned as one merged footprint.

    Attributes
    ----------
    geometry
        Polygon or MultiPolygon contribution in EPSG:4326, not a fabricated
        source sub-basin. Equivalent to merging granular refinement of the
        same combined mask and target.
    outlet_cell
        Selected source-grid cell in the native raster CRS.
    rasters
        Materialized band subsets over the complete input vector mask.
    provenance
        Source manifest declarations; not proof of vector/raster equivalence.
    coverage
        Explicit source-network coverage assessment (``"not_assessed"`` for
        manually supplied footprints)."""

    geometry: Polygon | MultiPolygon
    outlet_cell: OutletCell
    rasters: RefinementRasters
    provenance: Mapping[str, object] = field(default_factory=dict)
    coverage: UpstreamCoverage = field(default_factory=_unassessed_footprint_coverage)


type BasinPart = Subbasin | SubbasinContribution
type MergeInput = SelectionResult | GranularRefinement | DelineationResult | FootprintRefinement | Sequence[BasinPart]


def _polygonal(geometry: BaseGeometry) -> Polygon | MultiPolygon:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    polygons = [part for part in shapely.get_parts(geometry) if isinstance(part, (Polygon, MultiPolygon))]
    return cast(Polygon | MultiPolygon, shapely.union_all(polygons)) if polygons else Polygon()


@overload
def merge(*, subbasins: MergeInput) -> Polygon | MultiPolygon: ...


@overload
def merge(*, subbasins: Sequence[MergeInput]) -> list[Polygon | MultiPolygon]: ...


def merge(*, subbasins: MergeInput | Sequence[MergeInput]) -> Polygon | MultiPolygon | list[Polygon | MultiPolygon]:
    """Merge sub-basin geometries without creating a new source feature.

    Parameters
    ----------
    subbasins
        A `SelectionResult`, `DelineationResult`, `GranularRefinement`,
        `FootprintRefinement`, whole units or contributions forming one
        footprint, or a sequence of those groups representing independent
        footprints. Geometries use EPSG:4326. When a result already carries its
        merged ``.geometry``, it is returned directly without recomputation.

    Returns
    -------
    Polygon or MultiPolygon or list
        Polygonal union for one group or result; an ordered list of unions for
        a sequence of groups. An empty outer sequence returns an empty Polygon,
        not a list. Empty inner groups produce empty Polygons. No source
        attributes are aggregated, and no source access or geometry repair is
        performed.

    Notes
    -----
    Large inputs exceeding 5,000 parts are unioned in bounded groups using
    retained HFX physical addresses when available, inside a process-wide
    serialized workspace with up to two group unions active at a time. Single
    valid parts and small groups up to 5,000 parts execute without workspace
    serialization. This bounds simultaneous large decoding and union work, not
    retained caller inputs, outputs or total process memory. Independent
    processes have independent bounds.
    """
    if isinstance(subbasins, (FootprintRefinement, GranularRefinement)):
        return subbasins.geometry
    if isinstance(subbasins, DelineationResult):
        if subbasins.geometry is not None:
            return subbasins.geometry
        return _polygonal(union_subbasins(subbasins.subbasins)) if subbasins.subbasins else Polygon()
    if isinstance(subbasins, SelectionResult):
        return _polygonal(union_subbasins(subbasins.subbasins)) if subbasins.subbasins else Polygon()
    if not subbasins:
        return Polygon()
    if isinstance(subbasins[0], (Subbasin, SubbasinContribution)):
        parts = cast(Sequence[BasinPart], subbasins)
        return _polygonal(union_subbasins(parts))
    groups = cast(Sequence[MergeInput], subbasins)
    return [merge(subbasins=group) for group in groups]


def _trace(window: RasterWindow, mask: npt.NDArray[np.bool_], row: int, col: int) -> npt.NDArray[np.bool_]:
    height, width = mask.shape
    stride = width + 2
    padded_dir = np.zeros((height + 2) * stride, dtype=np.int8)
    valid_dir = np.where(mask & window.direction_valid, window.direction, np.int8(0))
    padded_dir.reshape(height + 2, stride)[1:-1, 1:-1] = valid_dir
    # Incoming neighbor offset is the inverse of its outgoing D8 direction.
    deltas = (
        (1, -1),
        (2, -stride - 1),
        (3, -stride),
        (4, -stride + 1),
        (5, 1),
        (6, stride + 1),
        (7, stride),
        (8, stride - 1),
    )
    visited = np.zeros(padded_dir.size, dtype=np.bool_)
    seed = (row + 1) * stride + (col + 1)
    visited[seed] = True
    frontier = np.array([seed], dtype=np.intp)
    while frontier.size:
        next_parts: list[npt.NDArray[np.intp]] = []
        for code, delta in deltas:
            candidates = frontier + delta
            hits = candidates[padded_dir[candidates] == code]
            if hits.size:
                next_parts.append(hits)
        if not next_parts:
            break
        merged = next_parts[0] if len(next_parts) == 1 else np.concatenate(next_parts)
        fresh = merged[~visited[merged]]
        if not fresh.size:
            break
        visited[fresh] = True
        frontier = fresh
    return np.ascontiguousarray(visited.reshape(height + 2, stride)[1:-1, 1:-1])


def _is_refined_delineation(result: DelineationResult) -> bool:
    return (
        result.outlet_cell is not None
        or result.rasters is not None
        or any(isinstance(part, SubbasinContribution) for part in result.subbasins)
    )


@overload
def _refine(
    *,
    fabric: HydroFabric,
    target: BaseGeometry,
    subbasins: Sequence[Subbasin],
    footprint: None = None,
    window_size: int = 3,
    coverage: UpstreamCoverage | None = None,
    combined: Polygon | MultiPolygon | None = None,
) -> GranularRefinement: ...


@overload
def _refine(
    *,
    fabric: HydroFabric,
    target: BaseGeometry,
    footprint: BaseGeometry,
    subbasins: None = None,
    window_size: int = 3,
    coverage: UpstreamCoverage | None = None,
    combined: Polygon | MultiPolygon | None = None,
) -> FootprintRefinement: ...


def _refine(
    *,
    fabric: HydroFabric,
    target: BaseGeometry,
    subbasins: Sequence[Subbasin] | None = None,
    footprint: BaseGeometry | None = None,
    window_size: int = 3,
    coverage: UpstreamCoverage | None = None,
    combined: Polygon | MultiPolygon | None = None,
) -> GranularRefinement | FootprintRefinement:
    """Select the largest valid local accumulation, ties in source row/column order.

    Outlet selection needs both valid bands. Upstream tracing needs only valid
    direction, and treats zero and signed negative directions as terminals.
    """
    if type(window_size) is not int or window_size <= 0 or window_size % 2 != 1:
        raise ValueError("window_size must be a positive odd integer")
    if (subbasins is None) == (footprint is None):
        raise ValueError("provide exactly one of subbasins or footprint")
    if not isinstance(target, Point) or target.is_empty or not all(isfinite(v) for v in target.coords[0]):
        raise OperationError("refinement.point_required", "refinement requires a nonempty finite point target")
    if subbasins is not None:
        effective = combined if combined is not None and subbasins else merge(subbasins=subbasins)
    else:
        effective = footprint
    if not isinstance(effective, (Polygon, MultiPolygon)) or effective.is_empty:
        raise OperationError("refinement.empty_mask", "refinement requires a nonempty polygon mask")
    if not effective.covers(target):
        raise OperationError("refinement.outside_mask", "target lies outside the vector mask")
    try:
        region = bounds(effective.bounds)
    except ValueError as error:
        raise OperationError("refinement.no_coverage", str(error)) from error
    if not (-180 <= region[0] < region[2] <= 180 and -90 <= region[1] < region[3] <= 90):
        raise OperationError("refinement.no_coverage", "mask bounds are outside geographic WGS84 limits")
    try:
        request = fabric.rasters.prepare(region)
        window = fabric.rasters.read(request)
    except MissingRastersError as error:
        raise OperationError("refinement.missing_rasters", str(error)) from error
    except RasterCoverageError as error:
        raise OperationError("refinement.no_coverage", str(error)) from error
    to_native = _transformer("EPSG:4326", window.crs, from_crs=Transformer.from_crs)
    native_mask = shapely.transform(effective, to_native.transform, interleaved=False)
    mask = geometry_mask([native_mask], out_shape=window.direction.shape, transform=window.transform, invert=True)
    native_point = to_native.transform(target.x, target.y)
    x, y = ~window.transform @ native_point
    col, row = floor(x), floor(y)
    half = window_size // 2
    top, left = max(0, row - half), max(0, col - half)
    bottom, right = max(top, min(mask.shape[0], row + half + 1)), max(left, min(mask.shape[1], col + half + 1))
    valid = (
        mask[top:bottom, left:right]
        & window.direction_valid[top:bottom, left:right]
        & window.accumulation_valid[top:bottom, left:right]
    )
    ys, xs = np.nonzero(valid)
    if not len(ys):
        raise OperationError("refinement.no_outlet", "local window contains no valid outlet cell inside the mask")
    index = int(np.argmax(window.accumulation[ys + top, xs + left]))
    row, col = int(ys[index]) + top, int(xs[index]) + left
    selected = _trace(window, mask, row, col)
    polygons = [
        shape(geometry)
        for geometry, value in shapes(selected.view(np.uint8), mask=selected, transform=window.transform)
        if value == 1
    ]
    native_geometry = shapely.union_all(polygons)
    to_wgs84 = _transformer(window.crs, "EPSG:4326", from_crs=Transformer.from_crs)
    geometry = _polygonal(
        shapely.transform(native_geometry, to_wgs84.transform, interleaved=False).intersection(effective)
    )
    cell = OutletCell(request.grid, row + request.row, col + request.col)
    rasters = RefinementRasters(RasterSubset(window, mask, "direction"), RasterSubset(window, mask, "accumulation"))
    if subbasins is None:
        return FootprintRefinement(
            geometry,
            cell,
            rasters,
            fabric.manifest.declarations,
            coverage or _unassessed_footprint_coverage(),
        )
    shapely.prepare(geometry)
    contributions = []
    for source in subbasins:
        geom = source.geometry
        if not geometry.intersects(geom):
            continue
        piece = _polygonal(geom) if geometry.covers(geom) else _polygonal(geometry.intersection(geom))
        if not piece.is_empty:
            contributions.append(SubbasinContribution(source, piece))
    return GranularRefinement(
        contributions,
        geometry,
        cell,
        rasters,
        fabric.manifest.declarations,
        coverage or _unassessed_subbasins_coverage(),
    )


@overload
def refine(
    *,
    fabric: HydroFabric,
    subbasins: BatchResult[SelectionResult] | BatchResult[DelineationResult],
    target: None = None,
    footprint: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> BatchResult[GranularRefinement]: ...


@overload
def refine(
    *,
    fabric: HydroFabric,
    subbasins: SelectionResult | DelineationResult,
    target: BaseGeometry | None = None,
    footprint: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> GranularRefinement: ...


@overload
def refine(
    *,
    fabric: HydroFabric,
    target: Point,
    subbasins: Sequence[Subbasin],
    footprint: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> GranularRefinement: ...


@overload
def refine(
    *,
    fabric: HydroFabric,
    target: Point,
    footprint: BaseGeometry,
    subbasins: None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> FootprintRefinement: ...


def refine(
    *,
    fabric: HydroFabric,
    target: BaseGeometry | None = None,
    subbasins: SelectionResult
    | DelineationResult
    | Sequence[Subbasin]
    | BatchResult[SelectionResult]
    | BatchResult[DelineationResult]
    | None = None,
    footprint: BaseGeometry | None = None,
    window_size: int = 3,
    n_jobs: int = 1,
) -> GranularRefinement | FootprintRefinement | BatchResult[GranularRefinement]:
    """Trace incoming native D8 flow inside a complete vector mask.

    Parameters
    ----------
    fabric
        Open fabric supplying the native direction/accumulation pair.
    target
        Nonempty point in EPSG:4326 covered by the mask. Optional when
        ``subbasins`` is a `SelectionResult` or unrefined `DelineationResult`,
        defaulting to the result's post-snap ``.target``. Omit for a batch,
        which carries per-target geometries.
    subbasins
        A `SelectionResult`, an unrefined `DelineationResult`, whole source
        units forming one mask, or a batch of selection or unrefined delineation
        results. Mutually exclusive with ``footprint``. Passing a
        `SelectionResult`, unrefined `DelineationResult`, or batch preserves its
        source-network `UpstreamCoverage` diagnostics on the refined output;
        manually supplied sub-basins receive ``status="not_assessed"``. Batch
        identity, order and prior issues are preserved; failed slots are not
        retried. Already-refined `DelineationResult` inputs are rejected.
    footprint
        Nonempty Polygon or MultiPolygon in EPSG:4326, used as one mask
        (``coverage.status == "not_assessed"``). Supply exactly one mask form.
        Batch footprints come from ``subbasins``.
    window_size
        Positive odd search width in native cells around the cell containing
        the target. Size 1 permits no move beyond that cell. Selects the highest
        valid accumulation inside the mask, breaking ties by source row then
        column. Both bands must be valid for outlet selection.
    n_jobs
        Batch worker limit: 1 is sequential, -1 uses available CPU capacity,
        and positive values bound worker-owned readers.

    Returns
    -------
    GranularRefinement or FootprintRefinement or BatchResult
        Contributions for sub-basin input or one footprint for geometry input,
        the selected native outlet cell, materialized raster subsets, and
        retained or ``"not_assessed"`` source-network coverage diagnostics.
        Empty source pieces are omitted. Both forms trace the same combined
        mask; merging granular contributions yields the footprint result.

    Raises
    ------
    OperationError
        ``refinement.point_required`` for a nonpoint, empty, or non-finite point
        geometry,
        ``refinement.empty_mask`` for an empty or nonpolygon mask,
        ``refinement.outside_mask`` when the point is not covered,
        ``refinement.missing_rasters`` for an absent raster pair,
        ``refinement.no_coverage`` for inadequate raster coverage or
        out-of-bounds geographic mask extents, or
        ``refinement.no_outlet`` when the local search has no valid masked cell.
        Batches retain these failures as issues with None slots.
    ValueError
        Invalid window/worker count, mutually incompatible mask arguments, or an
        already-refined `DelineationResult`. Integrity and unexpected errors
        also propagate, including in batches.
    TypeError
        A raw sub-basin/footprint call omits ``target``, or a batch slot is not
        a `SelectionResult` or `DelineationResult`.

    Notes
    -----
    Assumes the whole vector mask equals the raster watershed at the terminal
    unit outlet, and that the requested point drains to that outlet. Execution
    does not verify this scientific equivalence. The default local search is a
    practical choice, not a universal optimum; larger windows can switch branches.

    Mask cells use GDAL center-based rasterization. Reverse D8 tracing spans
    the entire mask and needs only valid direction, not valid accumulation;
    zero and signed negative directions are terminal. Paths cannot leave the
    mask. Native direction pointers are never warped. Selected cell polygons
    are transformed to EPSG:4326 and clipped to the vector mask, then partitioned
    at source boundaries for granular output. No hole filling is applied.
    Materialized vectors survive fabric closure; perform source-dependent
    operations and export within the fabric context.
    """
    workers(n_jobs)
    if type(window_size) is not int or window_size < 1 or window_size % 2 != 1:
        raise ValueError("window_size must be a positive odd integer")
    fabric._ensure_open()
    if isinstance(subbasins, BatchResult):
        if target is not None or footprint is not None:
            raise ValueError("selection batches carry their own targets and footprints")
        for slot in subbasins.results:
            if slot is None:
                continue
            if isinstance(slot, DelineationResult):
                if _is_refined_delineation(slot):
                    raise ValueError("cannot refine an already-refined DelineationResult")
            elif not isinstance(slot, SelectionResult):
                raise TypeError("batch refinement requires a BatchResult of SelectionResult or DelineationResult")

        def one(reader: HydroFabric, item: Target) -> GranularRefinement | None:
            slot = subbasins.results[item.index]
            if slot is None:
                return None
            if isinstance(slot, SelectionResult):
                slot_target = slot.target
                slot_subbasins = slot.subbasins
                slot_combined = None
            else:
                assert isinstance(slot, DelineationResult)
                slot_target = slot.target
                slot_subbasins = cast(Sequence[Subbasin], slot.subbasins)
                slot_combined = slot.geometry if slot.subbasins else None
            if not isinstance(slot_target, Point):
                raise OperationError("refinement.point_required", "refinement requires a point target")
            return _refine(
                fabric=reader,
                subbasins=slot_subbasins,
                target=slot_target,
                window_size=window_size,
                coverage=slot.coverage,
                combined=slot_combined,
            )

        result = execute(fabric, subbasins.targets, one, n_jobs)
        return BatchResult(
            result.results,
            subbasins.targets,
            sorted([*subbasins.issues, *result.issues], key=lambda issue: issue.index),
        )
    if subbasins is not None:
        if footprint is not None:
            raise ValueError("provide exactly one of subbasins or footprint")
        if isinstance(subbasins, SelectionResult):
            effective_target = subbasins.target if target is None else target
            return _refine(
                fabric=fabric,
                target=effective_target,
                subbasins=subbasins.subbasins,
                window_size=window_size,
                coverage=subbasins.coverage,
            )
        if isinstance(subbasins, DelineationResult):
            if _is_refined_delineation(subbasins):
                raise ValueError("cannot refine an already-refined DelineationResult")
            effective_target = subbasins.target if target is None else target
            return _refine(
                fabric=fabric,
                target=effective_target,
                subbasins=cast(Sequence[Subbasin], subbasins.subbasins),
                window_size=window_size,
                coverage=subbasins.coverage,
                combined=subbasins.geometry if subbasins.subbasins else None,
            )
        if target is None:
            raise TypeError("target is required for single refinement")
        return _refine(
            fabric=fabric,
            target=target,
            subbasins=subbasins,
            window_size=window_size,
            coverage=_unassessed_subbasins_coverage(),
        )
    if target is None:
        raise TypeError("target is required for single refinement")
    if footprint is None:
        raise ValueError("provide exactly one of subbasins or footprint")
    return _refine(
        fabric=fabric,
        target=target,
        footprint=footprint,
        window_size=window_size,
        coverage=_unassessed_footprint_coverage(),
    )
