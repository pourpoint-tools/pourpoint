# Reliable large vector results and QGIS export

Large vector selections should remain usable through retrieval and QGIS export without callers manually splitting requests to avoid aggregate representation limits. Enough memory for the eager result and operation workspace is assumed; this is not a bounded-memory or arbitrary-size guarantee.

This vision defines the delivery outcome for [discussion #193](https://github.com/pourpoint-tools/pourpoint/issues/193). Implementing and validating it completes #193; publishing this document does not.

## Preserve the interface and data

Keep eager Arrow tables and existing public workflows. Preserve source schema, field and schema metadata, values, requested order and duplicate IDs. Do not promote source binary columns to large_binary, simplify geometry, or introduce a streaming API as a workaround. Internal chunk boundaries are not a public ordering or batching contract.

Cover direct record retrieval and spatial candidates, which share the vector reader, and verify their downstream feature and geometry consumers. Include QGIS export of large retrieved results and river-network relationships, preserving lossless source values, metadata and relationship meaning. Remove demonstrated avoidable dictionary serialization amplification in that export path. Leave reversible internal representation choices to implementation.

## Evidence and verification needs

A tester reported “offset overflow while concatenating arrays” for roughly 170,000 catchments on Pourpoint 0.5.0rc4. Splitting requests worked. The reported geometry contains 195,706,524 XY vertices, exceeding 3 GB of coordinate payload before WKB overhead. This is a payload-size problem, not a fixed row-count limit. Reports and discussion are linked from #193; the exact traceback and failing column were not supplied.

Current `VectorReader._records` in `src/pourpoint/vectors.py` selects rows per group, concatenates pieces, and restores order using `Table.take`. An Arrow 25.0.1 small probe confirmed that this take collapses binary chunks even for identity ordering. Binary arrays have roughly a 2 GiB payload limit, while a chunked column can exceed it. This is a strong failure hypothesis, not a reproduced full-size failure. Verify both per-group selection and final ordering, including duplicate requests; inspect analogous indexed relationship selection rather than assuming only the final geometry reorder matters.

Downstream inspection found scalar WKB decoding in `Feature.geometry`, not another whole-WKB coalescing operation. Merge and refinement retain eager data and workspace; they do not establish a further Arrow binary-offset ceiling.

QGIS inspection identified two separate hazards in `src/pourpoint/qgis.py`:

- `_arrow` serializes an entire relationship table to an Arrow buffer, copies it to Python bytes, and stores it as one SQLite BLOB. The tested project environment reports a SQLite length limit of 1,000,000,000 bytes. Arrow chunking alone does not remove that aggregate export limit.
- One-row source-record slices retain their chunk dictionaries. A small IPC probe serialized a row selecting “a” to 100,520 bytes when its dictionary also contained an unselected 100,000-character value. Repeating the export can replicate unrelated dictionary values many times.

Confirm these mechanisms through focused reproductions before implementing fixes. Neither the original giant retrieval failure nor a giant QGIS export failure has been reproduced. Do not claim those reproductions based only on small probes or source inspection.

## Observable success

- A multi-chunk selection whose aggregate binary payload exceeds a single-array limit can be returned without forced coalescing or caller batching, when individual values are representable and memory is sufficient.
- Reordering and duplicate requests remain correct, with source types and metadata intact; candidate and downstream feature paths remain usable.
- QGIS export no longer requires all network relationship IPC to fit in one SQLite value. Source records and relationships round-trip faithfully, without repeatedly serializing unrelated dictionary contents per feature.
- Regression coverage exercises the demonstrated failure mechanisms and data-preservation promises using project-native tools. Use proportionate tests and explicitly state any reduced-limit simulation or large-data validation limits; do not require multi-gigabyte routine CI allocations merely to prove the mechanism.

## Boundaries

Broader downstream memory investigation belongs to [#198](https://github.com/pourpoint-tools/pourpoint/issues/198), not this implementation. That issue is investigation only: gather evidence for a future grill, or explain and close it if there is no material problem. Completing this vision does not complete #198.

Feature ownership redesign, geometry caching, streaming, general memory optimization, faster polygon union, and the reported watershed area discrepancy are outside this outcome. Preserve geometry and eager API semantics rather than introducing approximations to meet size goals. No new universal memory budget or performance target is agreed.
