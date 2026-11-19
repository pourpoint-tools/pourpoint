"""Typed eager operation results and target-local failures."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from pourpoint.features import Subbasin

if TYPE_CHECKING:
    from pourpoint.refinement import OutletCell, RefinementRasters, SubbasinContribution

type CoverageStatus = Literal[
    "complete",
    "incomplete",
    "unverified",
    "incomplete_and_unverified",
    "not_assessed",
]
type DeclaredCompleteness = Literal["complete", "partial"]


class OperationError(Exception):
    """An expected target-local failure shared by single and batch calls.

    Attributes
    ----------
    code
        Stable machine-readable operation code; conditions are documented on
        the operation that raises it.
    reason
        Human-readable explanation, also used as the exception message.

    Notes
    -----
    Batch operations collect this exception only. Configuration, integrity and
    unexpected errors propagate rather than becoming target issues."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Target:
    """One accepted batch target, independent of source feature identity.

    Attributes
    ----------
    index
        Zero-based position in the original input order.
    name
        Dictionary key or UUID generated once for a list entry. Preserved by
        subsequent batch refinement and export; list calls get new names.
    geometry
        Original target geometry in EPSG:4326, including multipart targets."""

    index: int
    name: str
    geometry: BaseGeometry


@dataclass(frozen=True)
class BatchIssue:
    """An expected failure attached to one batch input.

    Attributes
    ----------
    index, name
        Original target position and name, matching the batch target metadata.
    code
        Machine-readable code with the same meaning as `OperationError.code`.
    reason
        Human-readable failure explanation."""

    index: int
    name: str
    code: str
    reason: str


@dataclass(frozen=True)
class BatchResult[T]:
    """Ordered results and durable target-local failures.

    Attributes
    ----------
    results
        One slot per input. None means failure, not a valid empty selection.
        Successful slots carry typed results such as `SelectionResult`,
        `DelineationResult` or `GranularRefinement`.
    targets
        Input identities in the same order as result slots. Duplicate geometries
        retain independent identities.
    issues
        Expected failures with input position, name, code and reason. Fatal
        configuration or integrity errors raise instead of returning this object."""

    results: list[T | None]
    targets: list[Target]
    issues: list[BatchIssue]


@dataclass(frozen=True)
class UpstreamCoverage:
    """Source-network connectivity completeness assessment for a selected closure.

    Attributes
    ----------
    status
        Source-network coverage classification across all visited units in the
        inclusive upstream closure:

        - ``"complete"``: verified complete relative to the source network
          (either ``completeness == "complete"`` was declared, or every visited
          unit had a non-null zero in the declared cut column).
        - ``"incomplete"``: one or more visited units have positive cut counts
          and none are unverified (null).
        - ``"unverified"``: one or more visited units have null cut counts and
          none have positive cut counts.
        - ``"incomplete_and_unverified"``: both positive cut counts and null
          unverified units occur in the same closure.
        - ``"not_assessed"``: source-network completeness could not be assessed
          (for example missing completeness metadata, partial connectivity
          without a declared cut column, an empty target geometry, or manually
          supplied sub-basins or footprints).
    declared_completeness
        Manifest ``completeness`` declaration (``"complete"`` or ``"partial"``)
        at the queried level, or ``None`` when absent or not applicable.
    cuts_column
        Declared source catchment integer column used to inspect per-unit
        upstream cuts, or ``None`` when no cut column applies.
    cut_counts
        Immutable mapping of catchment ID to positive immediate upstream cut
        count for affected visited units, sorted by ID. Empty when assessed with
        no cuts; ``None`` when ``status == "not_assessed"``.
    unverified_unit_ids
        Sorted unique catchment IDs whose declared cut count was null. Empty
        when assessed with no unverified units; ``None`` when
        ``status == "not_assessed"``.
    reason
        Human-readable explanation when ``status == "not_assessed"``; ``None``
        otherwise.

    Notes
    -----
    This assessment describes source-network coverage of the selected vector
    closure relative to the HFX declarations, not vector/raster equivalence or
    real-world watershed area. It survives fabric closure and optional raster
    refinement without recertifying the raster output."""

    status: CoverageStatus
    declared_completeness: DeclaredCompleteness | None = None
    cuts_column: str | None = None
    cut_counts: Mapping[int, int] | None = None
    unverified_unit_ids: tuple[int, ...] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.cut_counts is not None:
            object.__setattr__(
                self,
                "cut_counts",
                MappingProxyType({uid: self.cut_counts[uid] for uid in sorted(self.cut_counts)}),
            )
        if self.unverified_unit_ids is not None:
            object.__setattr__(self, "unverified_unit_ids", tuple(sorted(set(self.unverified_unit_ids))))

    @property
    def is_complete(self) -> bool:
        """True only when source-network coverage is verified ``"complete"``."""
        return self.status == "complete"

    @property
    def cut_unit_ids(self) -> tuple[int, ...] | None:
        """Sorted IDs of visited units with positive cut counts, or ``None`` when not assessed."""
        return None if self.cut_counts is None else tuple(self.cut_counts)

    @property
    def total_cuts(self) -> int | None:
        """Sum of immediate upstream cut counts across affected units, or ``None`` when not assessed."""
        return None if self.cut_counts is None else sum(self.cut_counts.values())


@dataclass(frozen=True)
class SelectionResult:
    """Whole-unit upstream catchment selection with attached source-network coverage.

    Attributes
    ----------
    subbasins
        Selected whole source units in traversal order (empty for an empty
        target geometry). Materialized vectors survive fabric closure.
    coverage
        Source-network completeness diagnostics across the inclusive upstream
        closure. Passing this object to `pourpoint.refinement.refine` preserves
        these diagnostics on the refined output.
    level
        Nonnegative catchment level queried for this selection.
    target
        EPSG:4326 target geometry used for selection.
    provenance
        Source manifest declarations, not a certification of raster equivalence
        or recomputed hydrological quantities."""

    subbasins: list[Subbasin]
    coverage: UpstreamCoverage
    level: int
    target: BaseGeometry
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DelineationResult:
    """Materialized outputs of one convenience delineation.

    Attributes
    ----------
    subbasins
        Whole source units without refinement; contributing portions otherwise.
        Source attributes on portions are not recomputed for their smaller area.
    geometry
        EPSG:4326 polygon union when merging was requested, otherwise None.
    outlet_cell
        Selected native raster cell when refined, otherwise None.
    rasters
        Materialized raster subsets over the whole vector mask used in
        refinement, otherwise None. They are not cropped to the final watershed.
    original_target
        Input geometry in EPSG:4326 before optional vector snapping.
    target
        EPSG:4326 target after optional vector snapping. Raster outlet adjustment
        is represented separately by ``outlet_cell``, not by changing this field.
    provenance
        Source manifest declarations, not a certification of vector/raster
        equivalence or recomputed hydrological quantities.
    coverage
        Source-network coverage assessment of the selected upstream closure,
        retained unchanged through optional refinement and merging."""

    subbasins: list[Subbasin | SubbasinContribution]
    geometry: Polygon | MultiPolygon | None
    outlet_cell: OutletCell | None
    rasters: RefinementRasters | None
    original_target: BaseGeometry
    target: BaseGeometry
    provenance: Mapping[str, object]
    coverage: UpstreamCoverage
