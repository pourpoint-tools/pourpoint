# Hydrological operations

All vector targets and source features use WGS84 longitude/latitude. Operations
are eager; use `HydroFabric` as a context manager. HFX supplies source identities,
attributes, relationships and provenance; clipped pieces do not recompute source
areas or orders.


## Selection and refinement conventions

Selection defaults to catchment level 1, never the finest available level.
Point boundary ownership uses the smallest intersecting source ID. Regions include
boundary intersections. Empty geometries give valid empty `SelectionResult` values
with `coverage.status == "not_assessed"`; nonempty targets with no intersecting
unit raise `selection.no_coverage`. Missing or explicitly unsupplied connectivity
fails with `selection.connectivity_unavailable` rather than returning an apparently
complete answer. Malformed flow edges crossing levels raise an integrity error.

Selection and `delineate` check the requested level's HFX
`catchment_connectivity` completeness declarations and per-unit cut column across
the entire inclusive upstream closure before raster refinement or merging. When
completeness is `"partial"`, every visited catchment must have a non-null zero
in the declared `upstream_cuts_column` to pass by default (`coverage.status == "complete"`).
Closures with positive cut counts (`selection.incomplete_coverage`), null cut counts
(`selection.unverified_coverage`), both (`selection.incomplete_and_unverified_coverage`),
or missing/uninstrumented completeness metadata (`selection.coverage_not_assessed`)
fail by default. Pass `allow_incomplete=True` to return those results with their
attached `UpstreamCoverage` diagnostics; `allow_incomplete=True` does not bypass
unavailable connectivity or corrupt schemas, types, negative cut values, or values
contradicting a `"complete"` declaration. Passing a `SelectionResult` (or selection
`BatchResult`) to `refine` retains the original source-network coverage diagnostics
on `GranularRefinement.coverage`, whereas manually supplied sub-basins or footprints
receive `coverage.status == "not_assessed"`.

Refinement assumes vector/raster equivalence; it does not certify that scientific
assumption. The whole selected vector footprint is the computational mask.
Native cells are admitted by their centers using GDAL rasterization. The local
window defaults to 3 by 3 cells, with positive odd sizes accepted. Outlet choice
requires valid direction and accumulation, maximizes accumulation, and breaks ties
in source row/column order. Reverse tracing uses direction validity alone: an
upstream cell with missing accumulation can still contribute. Zero and negative
signed directions terminate flow. No direction pointers are warped.

Polygonized cells are clipped to the original combined vector footprint, then
intersected with source polygons for granular output. This keeps merged granular
and footprint outputs equivalent, without hole filling or untouched upstream units.
The outlet cell retains global source-grid row/column, CRS and affine. Its center
is a display location, not improved station accuracy.

### Flow-direction loops in GRIT v1.0

Where the original GRIT v1.0 drainage-direction rasters were merged, some cells
point to one another in a loop. Water routed into a loop never reaches the
outlet. Raster refinement can therefore leave holes in the watershed and
underestimate its contributing area even when `coverage.status == "complete"`.
That status describes the upstream connections between source sub-basins.
To delineate whole upstream sub-basins using those connections, use
`delineate(..., refine=False)`.

The GRIT maintainer plans to correct the raster merge in v1.1. Once the
corrected rasters reach the hosted HFX bundle, the planned check repeats
the delineation at CAMELS gauge `02472000`, where the loops were verified.
See the [upstream diagnosis and retest plan](https://github.com/pourpoint-tools/hfx/issues/269#issuecomment-7104717277).

## Batches and failures

Dictionary keys name targets; list entries receive UUIDs once per accepted batch.
Duplicate geometries remain independent targets. `BatchResult.targets` preserves
names, original geometries and input indices alongside ordered result slots.
Expected failures have a `None` slot and a durable `BatchIssue`. Configuration,
integrity and unexpected programming failures raise instead of becoming issues.
Single-target `OperationError` exposes the same `code` and `reason`.

`n_jobs=1` executes sequentially. Positive bounds and `-1` (available CPUs) use
bounded threads with one independent fabric context per worker chunk. Contexts
are reused across that chunk, never shared between worker threads. They use the
same persistent cache and verify the same manifest snapshot. Parent `fabric.stats`
includes worker requests, received payload bytes and cache-hit bytes. Concurrent
first requests may duplicate acquisitions; this is not a single-flight cache.
The number of simultaneous results and raster arrays grows with worker count.

Explicit batch selection can feed `refine(fabric=fabric, subbasins=selection)`
without regenerating identity. Existing failures remain failures.

## Rivers and snapping

River level defaults independently to 1. `select_rivers` clips to the combined
footprint, not each constituent unit. `include_downstream=True` follows supplied
directed edges through splits and reconvergence. For a boundary inside a feature,
its downstream suffix is retained. Source orientation or exactly matching supplied
endpoint-node coordinates establish direction; ambiguous geometry raises
`rivers.ambiguous_direction` rather than inventing flow. Source features,
attributes, relationships and nodes survive clipping. Their endpoints are not
reclassified as natural outlets.

`snap` minimizes WGS84 ground distance to supplied straight longitude/latitude
stream segments. It uses 64 golden-section iterations per segment, plus endpoints
and exact on-segment checks; this is a local stream-segment numerical method, not
a global optimizer for globe-spanning linework. Split antimeridian crossings before
calling. Radius is metres and `delineate` defaults it to 100 when snapping is
requested. Snapping and raster outlet adjustment are separate operations.

## Portable output

`QGISBundle` writes a new folder containing `watershed.qgz`, `vectors.gpkg` and
GeoTIFFs. It refuses to overwrite an existing folder. Project references are relative;
move the whole folder together. Raw vector geometries require an explicit `crs=`.
`add_result` includes available outputs only, with per-target groups and issues.
It does not merge or trace rivers implicitly.

Each refined target receives one native-grid direction/accumulation pair, masked
to the whole vector delineation. Direction values retain their source encoding,
not the normalized codes used internally for tracing. Values outside the mask or
invalid in the corresponding band become source nodata. The outlet outline is in
the raster CRS. Raster descriptors currently hold eager caller-owned arrays, so
export adds no source fetches; export inside the fabric context remains the supported
workflow. Memory includes these arrays for every retained refinement result.

GeoPackage features expose ordinary scalar attributes. Rich values, exact Arrow
types/nulls and field/schema metadata remain lossless Arrow IPC stream blobs in
`source_arrow`, keyed by layer and source row or relationship kind. Use
`pyarrow.ipc.open_stream(payload).read_all()` to read them. `source_metadata` keeps
attribute-column mappings and source declarations; colliding scalar names receive
`source_` prefixes. `result_metadata` retains target provenance, WGS84 target WKB
and JSON-encoded `UpstreamCoverage` diagnostics across `SelectionResult`,
`DelineationResult`, `GranularRefinement` and `FootprintRefinement` exports. `river_connections` exposes directed supplied relations as
ordinary rows, alongside lossless relationship and node tables. Target identity
columns are separate from source IDs. Original source geometry remains in Arrow
provenance even where a displayed feature is clipped.

Exports retain GRIT's CC BY-NC 4.0 terms, attribution and source caveats. The code
license does not grant different rights to data or exported results.
