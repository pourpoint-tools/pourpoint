"""Whole-unit upstream selection and eager watershed composition."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, cast, overload

if TYPE_CHECKING:
    from pourpoint.refinement import SubbasinContribution
    from pourpoint.rivers import SnapGeometry

import numpy as np
import pyarrow as pa
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from pourpoint._batch import Targets, execute, identify, workers
from pourpoint.fabric import HydroFabric
from pourpoint.features import Subbasin
from pourpoint.metadata import bounds
from pourpoint.results import (
    BatchResult,
    CoverageStatus,
    DeclaredCompleteness,
    DelineationResult,
    OperationError,
    SelectionResult,
    UpstreamCoverage,
)
from pourpoint.spatial import in_float32_range


def _level(level: int) -> None:
    if type(level) is not int or level < 0:
        raise ValueError("level must be a nonnegative integer")


def _validate_cuts_column(schema: pa.Schema, column: str) -> None:
    if column not in schema.names:
        raise ValueError(f"declared upstream_cuts_column {column!r} is missing from catchments schema")
    if not pa.types.is_integer(schema.field(column).type):
        raise ValueError(f"declared upstream_cuts_column {column!r} must have an integer Arrow type")


def _assess_cuts(units: list[Subbasin], *, column: str, declared: DeclaredCompleteness) -> UpstreamCoverage:
    tables: dict[int, pa.Table] = {}
    for unit in units:
        tables.setdefault(id(unit.table), unit.table)
    for table in tables.values():
        _validate_cuts_column(table.schema, column)
    cut_counts: dict[int, int] = {}
    unverified: list[int] = []
    if len(tables) == 1 and units:
        table = units[0].table
        ids = cast(list[int], table["id"].to_pylist())
        values = cast(list[int | None], table[column].to_pylist())
        pairs: Iterable[tuple[int, int | None]] = ((ids[unit.row], values[unit.row]) for unit in units)
    else:
        pairs = ((unit.id, cast(int | None, unit.table[column][unit.row].as_py())) for unit in units)
    for uid, value in pairs:
        if value is None:
            unverified.append(uid)
        elif value < 0:
            raise ValueError(f"catchment {uid}: negative {column} value {value}")
        elif value > 0:
            cut_counts[uid] = value
    if declared == "complete" and (cut_counts or unverified):
        raise ValueError(
            f"catchment_connectivity declares complete for {column!r} but visited units have cuts or nulls"
        )
    if cut_counts and unverified:
        status: CoverageStatus = "incomplete_and_unverified"
    elif cut_counts:
        status = "incomplete"
    elif unverified:
        status = "unverified"
    else:
        status = "complete"
    return UpstreamCoverage(
        status,
        declared_completeness=declared,
        cuts_column=column,
        cut_counts=cut_counts,
        unverified_unit_ids=tuple(sorted(unverified)),
    )


def _sample_ids(ids: Iterable[int], limit: int = 5) -> str:
    items = list(ids)
    sample = ", ".join(str(uid) for uid in items[:limit])
    return f"{sample}, ..." if len(items) > limit else sample


def _coverage_error(coverage: UpstreamCoverage) -> OperationError:
    if coverage.status == "not_assessed":
        return OperationError(
            "selection.coverage_not_assessed",
            coverage.reason or "upstream catchment coverage could not be assessed",
        )
    cuts = coverage.cut_counts or {}
    unverified = coverage.unverified_unit_ids or ()
    if coverage.status == "incomplete":
        return OperationError(
            "selection.incomplete_coverage",
            f"upstream closure has {len(cuts)} cut catchment(s) ({coverage.total_cuts} cut edges; IDs: {_sample_ids(cuts)})",
        )
    if coverage.status == "unverified":
        return OperationError(
            "selection.unverified_coverage",
            f"upstream closure has {len(unverified)} unverified catchment(s) (IDs: {_sample_ids(unverified)})",
        )
    if coverage.status == "incomplete_and_unverified":
        return OperationError(
            "selection.incomplete_and_unverified_coverage",
            f"upstream closure has {len(cuts)} cut catchment(s) ({coverage.total_cuts} cut edges; IDs: {_sample_ids(cuts)}) "
            f"and {len(unverified)} unverified catchment(s) (IDs: {_sample_ids(unverified)})",
        )
    raise ValueError(f"unexpected non-error coverage status {coverage.status!r}")


def _select(
    *, fabric: HydroFabric, target: BaseGeometry, level: int, allow_incomplete: bool = False
) -> SelectionResult:
    if not isinstance(target, BaseGeometry):
        raise TypeError("target must be a Shapely geometry")
    if target.is_empty:
        return SelectionResult(
            [],
            UpstreamCoverage("not_assessed", reason="empty target selected no catchments"),
            level,
            target,
            fabric.manifest.declarations,
        )
    if not target.is_valid:
        raise OperationError("selection.invalid_target", "target geometry is invalid")
    try:
        checked_bounds = bounds(target.bounds)
    except ValueError as error:
        raise OperationError("selection.invalid_target", str(error)) from error
    if not in_float32_range(checked_bounds):
        raise OperationError("selection.invalid_target", "target geometry exceeds float32 coordinate range")
    declaration = fabric.manifest.catchment_connectivity.get(level)
    if declaration is not None and declaration.status == "not_supplied":
        raise OperationError(
            "selection.connectivity_unavailable", f"upstream connectivity is not supplied for level {level}"
        )
    rel_declaration = fabric.manifest.relationships.get("catchments", {})
    columns = rel_declaration.get("columns", {})
    if not isinstance(columns, Mapping) or "upstream_ids" not in columns:
        raise OperationError("selection.connectivity_unavailable", "upstream connectivity is not supplied")
    pre_coverage: UpstreamCoverage | None = None
    if declaration is None or declaration.completeness is None:
        pre_coverage = UpstreamCoverage(
            "not_assessed",
            declared_completeness=None,
            reason=f"catchment completeness metadata is not declared for level {level}",
        )
    elif declaration.completeness == "partial" and declaration.upstream_cuts_column is None:
        pre_coverage = UpstreamCoverage(
            "not_assessed",
            declared_completeness="partial",
            reason=f"level {level} declares partial connectivity without an upstream_cuts_column",
        )
    if pre_coverage is not None and not allow_incomplete:
        raise _coverage_error(pre_coverage)
    if declaration is not None and declaration.upstream_cuts_column is not None:
        _validate_cuts_column(fabric._reader("catchments").schema, declaration.upstream_cuts_column)
    table = fabric.candidates("catchments", target.bounds, levels=[level])
    seeds = sorted(
        Subbasin(table, row).id for row in range(table.num_rows) if Subbasin(table, row).geometry.intersects(target)
    )
    if not seeds:
        raise OperationError("selection.no_coverage", f"no catchment coverage for target at level {level}")
    # Shared polygon edges belong to the smallest supplied ID for point targets.
    if isinstance(target, Point):
        seeds = seeds[:1]
    units = fabric.subbasins(fabric.traverse("catchments", seeds))
    if any(unit.attributes["level"] != level for unit in units):
        raise ValueError("catchment flow relationship crosses levels")
    if declaration is not None and declaration.upstream_cuts_column is not None:
        coverage = _assess_cuts(
            units,
            column=declaration.upstream_cuts_column,
            declared=cast(DeclaredCompleteness, declaration.completeness),
        )
        if not coverage.is_complete and not allow_incomplete:
            raise _coverage_error(coverage)
    elif declaration is not None and declaration.completeness == "complete":
        coverage = UpstreamCoverage(
            "complete",
            declared_completeness="complete",
            cuts_column=None,
            cut_counts={},
            unverified_unit_ids=(),
        )
    else:
        assert pre_coverage is not None
        coverage = pre_coverage
    return SelectionResult(units, coverage, level, target, fabric.manifest.declarations)


@overload
def select_upstream_subbasins(
    *, fabric: HydroFabric, target: BaseGeometry, level: int = 1, allow_incomplete: bool = False, n_jobs: int = 1
) -> SelectionResult: ...


@overload
def select_upstream_subbasins(
    *, fabric: HydroFabric, target: Targets, level: int = 1, allow_incomplete: bool = False, n_jobs: int = 1
) -> BatchResult[SelectionResult]: ...


def select_upstream_subbasins(
    *,
    fabric: HydroFabric,
    target: BaseGeometry | Targets,
    level: int = 1,
    allow_incomplete: bool = False,
    n_jobs: int = 1,
) -> SelectionResult | BatchResult[SelectionResult]:
    """Select whole sub-basins approximating land draining to a target.

    Parameters
    ----------
    fabric
        Open HFX fabric supplying catchments and upstream connectivity.
    target
        EPSG:4326 geometry, or a dictionary of named geometries or list for a
        batch. A multipart geometry is one target. Regions include boundary
        intersections. A point on a shared boundary belongs to the smallest
        supplied catchment ID; river associations do not determine ownership.
    level
        Nonnegative catchment level, selected explicitly without fallback.
    allow_incomplete
        Accept selections whose upstream closure has known missing upstream
        connections, unverified (null) cut counts, or missing completeness
        assessment metadata. Returned `UpstreamCoverage` diagnostics retain
        all affected IDs and counts without promoting them to complete.
        Unavailable connectivity and corrupt source schemas or cut values still
        raise even when ``allow_incomplete=True``.
    n_jobs
        Batch worker limit: 1 is sequential, -1 uses available CPU capacity,
        and positive values bound worker-owned readers. Single calls are serial.

    Returns
    -------
    SelectionResult or BatchResult
        Selected whole units and source-network `UpstreamCoverage` diagnostics.
        An empty geometry succeeds without opt-in with empty sub-basins and
        ``coverage.status == "not_assessed"``. Batch slots preserve input order;
        dictionary keys are names, while list entries get new UUID names on
        each call. Duplicate geometries remain independent targets.

    Raises
    ------
    OperationError
        ``selection.invalid_target`` for invalid geometry or non-finite /
        out-of-range coordinates,
        ``selection.no_coverage`` for a nonempty target without coverage,
        ``selection.connectivity_unavailable`` for unsupplied connectivity,
        ``selection.incomplete_coverage`` when visited units have positive cuts,
        ``selection.unverified_coverage`` when visited units have null cuts,
        ``selection.incomplete_and_unverified_coverage`` when both occur, or
        ``selection.coverage_not_assessed`` when completeness metadata or a
        usable cut column is missing (unless ``allow_incomplete=True``).
        Batches retain these failures as issues with None result slots.
    ValueError
        Invalid level or worker count, a flow relationship crossing levels,
        missing or non-integer declared cut columns, negative cut counts, or
        cuts/nulls contradicting a ``complete`` declaration.
    TypeError
        Invalid target type, nonboolean ``allow_incomplete``, or invalid batch
        names.

    Notes
    -----
    This is whole-unit selection, not exact-point raster delineation. Returned
    vectors and coverage diagnostics survive fabric closure. Integrity,
    configuration and unexpected failures propagate even in batches; they are
    not empty selections.
    """
    _level(level)
    workers(n_jobs)
    if type(allow_incomplete) is not bool:
        raise TypeError("allow_incomplete must be boolean")
    fabric._ensure_open()
    if isinstance(target, (dict, list)):
        return execute(
            fabric,
            identify(target),
            lambda reader, geometry: _select(
                fabric=reader, target=geometry.geometry, level=level, allow_incomplete=allow_incomplete
            ),
            n_jobs,
        )
    return _select(fabric=fabric, target=target, level=level, allow_incomplete=allow_incomplete)


@overload
def delineate(
    *,
    fabric: HydroFabric,
    target: BaseGeometry,
    level: int = 1,
    snap_onto: SnapGeometry | None = None,
    radius: float = 100.0,
    refine: bool = False,
    merge: bool = True,
    window_size: int = 3,
    allow_incomplete: bool = False,
    n_jobs: int = 1,
) -> DelineationResult: ...


@overload
def delineate(
    *,
    fabric: HydroFabric,
    target: Targets,
    level: int = 1,
    snap_onto: SnapGeometry | None = None,
    radius: float = 100.0,
    refine: bool = False,
    merge: bool = True,
    window_size: int = 3,
    allow_incomplete: bool = False,
    n_jobs: int = 1,
) -> BatchResult[DelineationResult]: ...


def delineate(
    *,
    fabric: HydroFabric,
    target: BaseGeometry | Targets,
    level: int = 1,
    snap_onto: SnapGeometry | None = None,
    radius: float = 100.0,
    refine: bool = False,
    merge: bool = True,
    window_size: int = 3,
    allow_incomplete: bool = False,
    n_jobs: int = 1,
) -> DelineationResult | BatchResult[DelineationResult]:
    """Compose snapping, upstream selection, optional refinement and merging.

    Parameters
    ----------
    fabric
        Open fabric used for all source access.
    target
        EPSG:4326 geometry, named dictionary or list of geometries. Batch
        naming and whole-unit selection follow `select_upstream_subbasins`.
    level
        Explicit catchment level; no fallback or automatic finest-level choice.
    snap_onto
        Optional supplied stream geometry accepted by `snap`. Snapping occurs
        before selection and requires a point; it is never inferred.
    radius
        Maximum snap distance in WGS84 ground metres, finite and nonnegative.
        Validated even when snapping is disabled.
    refine
        Request raster refinement of the adjusted point under the equivalence
        assumption described by `pourpoint.refinement.refine`. Failure does
        not fall back to a coarse result.
    merge
        Populate the merged geometry as well as retaining individual sub-basins.
    window_size
        Positive odd native-cell outlet search width. Validated even without
        refinement. This is a practical choice, not a universal scientific optimum.
    allow_incomplete
        Accept selections with incomplete, unverified or unassessed upstream
        source-network coverage, following `select_upstream_subbasins`. Coverage
        is assessed before raster refinement or merging.
    n_jobs
        Batch worker limit: 1 is sequential, -1 uses available CPU capacity,
        and positive values bound worker-owned readers.

    Returns
    -------
    DelineationResult or BatchResult
        Whole units or refined contributions, optional merged geometry, original
        and adjusted targets, source declarations, source-network coverage
        assessment, and any selected outlet and raster subsets. Batch identity
        and order are retained.

    Raises
    ------
    OperationError
        Single-target failures use the codes of `snap`,
        `select_upstream_subbasins` and `pourpoint.refinement.refine`.
        Batches record them as issues and None slots, not coarse successes.
    ValueError
        Invalid level, worker count, radius or window size, invalid supplied
        stream geometry, or corrupt connectivity/cut metadata. Shared
        configuration and integrity failures propagate rather than becoming
        batch issues.
    TypeError
        Invalid target types or nonboolean refinement/merge/completeness
        controls.

    Notes
    -----
    No separate delineation algorithm is used. Vector outputs and coverage
    diagnostics are materialized and remain usable after fabric closure. Vector
    snapping and raster outlet adjustment are distinct: the latter is retained
    in ``outlet_cell``. Source-network coverage on ``DelineationResult`` still
    describes the selected vector closure, not raster completeness.
    """
    _level(level)
    workers(n_jobs)
    if type(window_size) is not int or window_size < 1 or window_size % 2 != 1:
        raise ValueError("window_size must be a positive odd integer")
    if type(refine) is not bool or type(merge) is not bool:
        raise TypeError("refine and merge must be boolean")
    if type(allow_incomplete) is not bool:
        raise TypeError("allow_incomplete must be boolean")
    if (
        isinstance(radius, (bool, np.bool_))
        or not isinstance(radius, (int, float, np.integer, np.floating))
        or not math.isfinite(radius)
        or radius < 0
    ):
        raise ValueError("radius must be finite and nonnegative")
    fabric._ensure_open()

    def one(reader: HydroFabric, geometry: BaseGeometry) -> DelineationResult:
        adjusted = geometry
        if snap_onto is not None:
            from pourpoint.rivers import snap

            adjusted = snap(target=geometry, onto=snap_onto, radius=radius)
        selection = _select(fabric=reader, target=adjusted, level=level, allow_incomplete=allow_incomplete)
        outlet = None
        rasters = None
        parts: list[Subbasin | SubbasinContribution] = list(selection.subbasins)
        if refine:
            from pourpoint.refinement import refine as refine_point

            if not isinstance(adjusted, Point):
                raise OperationError("refinement.point_required", "refinement requires a point target")
            refined = refine_point(fabric=reader, subbasins=selection, target=adjusted, window_size=window_size)
            parts = list(refined.subbasins)
            outlet, rasters = refined.outlet_cell, refined.rasters
        merged = None
        if merge:
            from pourpoint.refinement import merge as merge_subbasins

            merged = merge_subbasins(subbasins=parts)
        return DelineationResult(
            parts,
            merged,
            outlet,
            rasters,
            geometry,
            adjusted,
            reader.manifest.declarations,
            selection.coverage,
        )

    if isinstance(target, (dict, list)):
        return execute(fabric, identify(target), lambda reader, item: one(reader, item.geometry), n_jobs)
    return one(fabric, target)
