# Bounded primitive gather experiment

Related: [vision](../../visions/2026-11-01-reduce-chunk-overhead-without-slowing-retrieval.md), [experiment #201](https://github.com/pourpoint-tools/pourpoint/issues/201), [discussion #198](https://github.com/pourpoint-tools/pourpoint/issues/198).

## Finding

Small primitive tables can avoid the random-order chunk explosion. This is **not a production recommendation**: ordinary multi-chunk selections use more RSS, nested/dictionary/large inputs retain the baseline, and bounded real retrieval shows no established improvement. No repeatable speed regression was established in this matrix; that is not proof of the strict no-slowdown requirement for all supported requests. No production code changed.

Baseline: `ab76562` (selection implementation unchanged from `58ddc23`). Candidate and probe revision: `9bc240e22c9979ab7d85cfc2d000b260ff549d80`. Date: 2026-11-01. Python 3.13.14, Arrow 25.0.1, macOS 15.7.3 arm64, mimalloc. Existing project dependencies; psutil supplied with `uv run --with psutil`. No thread-count overrides. Timings ran serially, not concurrently with other study timings; normal machine activity was not controlled.

## Mechanism and correctness

`candidate.py` permits whole-table take only for numeric/binary/string fields with **total retained source buffers <=64 MiB**. It splits requests before repeated source rows, so each gather's variable payload cannot exceed the source payload. This conservative experimental constant keeps eligible payload well below signed 32-bit offset limits; it is not a selected production setting. Numeric-only selections use the existing fast path. Nested, dictionary, extension and all other types, larger inputs, empty requests and empty projections use the unchanged baseline. There is no preparation cache or deferred candidate work.

The bound uses `get_total_buffer_size`, not logical `nbytes`; a slice retaining a large underlying buffer is ineligible. Unsupported nested/dictionary types never use the new gather. Consequently this candidate does **not** demonstrate a general large-result optimization. Duplicate runs can still be numerous. No caller batching or source type conversion is used.

Four focused tests independently check values, duplicate order, field/schema metadata, numeric/empty paths, nested binary values and chunk-local ordered dictionary meaning. A reduced 128-byte limit proves fallback preserves the existing source-array bound; it does not reproduce a multi-gigabyte offset limit. Sixteen separate synthetic equivalence checks and nine separate real retrieval equivalence checks pass. Performance processes do not perform equivalence checking before timing. Existing vector tests also pass (32 tests including the four new tests).

## Synthetic comparison

Each cell is median **selection plus dictionary compaction milliseconds / current RSS MB**, from three fresh processes. Requests: publication order, reverse order, fixed-seed (198) random interleaving, and interleaving plus a repeated first 10%. All rows selected. Source construction and request generation excluded equally; eligibility checks, duplicate scanning, gather and compaction included. Raw stage timings and memory snapshots are in `synthetic.jsonl`.

| Rows; binary bytes/row; numeric columns; source chunks | Request | Baseline ms / MB | Candidate ms / MB |
|---|---|---:|---:|
| 170000; 32; 9; 17 | ordered | 23.93 / 145 | 13.41 / 167 |
| 170000; 32; 9; 17 | reversed | 24.02 / 144 | 13.22 / 166 |
| 170000; 32; 9; 17 | interleaved | 4105.22 / 1109 | 14.98 / 170 |
| 170000; 32; 9; 17 | duplicates | 4537.61 / 1213 | 16.80 / 172 |
| 170000; 0; 9; 17 | ordered | 3.22 / 132 | 3.24 / 132 |
| 170000; 0; 9; 17 | reversed | 3.21 / 132 | 3.23 / 132 |
| 170000; 0; 9; 17 | interleaved | 3.85 / 134 | 3.86 / 134 |
| 170000; 0; 9; 17 | duplicates | 4.08 / 136 | 4.12 / 137 |
| 20000; 1024; 2; 8 | ordered | 4.01 / 143 | 4.16 / 171 |
| 20000; 1024; 2; 8 | reversed | 4.10 / 143 | 4.10 / 171 |
| 20000; 1024; 2; 8 | interleaved | 149.01 / 185 | 4.94 / 172 |
| 20000; 1024; 2; 8 | duplicates | 164.28 / 190 | 5.54 / 172 |
| 20000; 32; 30; 1 | ordered | 3.52 / 113 | 2.07 / 112 |
| 20000; 32; 30; 1 | reversed | 3.46 / 113 | 2.01 / 112 |
| 20000; 32; 30; 1 | interleaved | 3.72 / 115 | 2.32 / 114 |
| 20000; 32; 30; 1 | duplicates | 4.05 / 116 | 2.65 / 114 |

The original 170k interleaved shape falls from 159,989 chunks per column to one. Numeric-only differences are near timer/process noise. The 1KiB-payload ordered case initially looked slower: ten additional fresh pairs gave baseline median 3.994ms (3.893–4.106), candidate 4.050ms (3.839–4.173). Overlap and changing pair direction do not establish a repeatable regression; neither justify a universal percentage allowance. See `ordered-repeat.jsonl`.

## Real HFX retrieval

Public [GRIT](https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/) HFX v0.4.0 (Upstream Tech public publication), 503 catchments from the existing Spain-river delineation receipt; ~827KB full record output, two lookup batches. `real-requests.json` records public IDs sorted by source lookup `(block,row)` and the public bounding box. No private dataset or cache path is committed. Keep the publication’s attribution/provenance with downloaded data; no source geometries are redistributed here. Published catchment artifact SHA-256: `f91badc69e90a3589862482bfa24c44ac061a23734c330092ac3aa1b7de25b73`; rivers: `7b45cdb8eb852cf8297523f4330f3ac3a099bcbbce0991ad295ed49531dff0ea`; families reference `vectors/2026-10-18-final/`. Existing offline cache supplied the data, so this is disk-cache retrieval, not a network benchmark.

Records select all fields; relationships select `id, upstream_ids` (other relationship columns were not fully cached). Ordinary spatial retrieval selects nine level-1 rivers (~6KB). Full records contain nested bbox and relationships contain lists, so these selections fall back. The numeric lookup path remains active. This check establishes integration correctness and bounds overhead, not a real-data chunk-reduction win. All operations are timed through `HydroFabric`, including lookup/read/gather/compaction. Opening the fabric is excluded equally. Three fresh processes per cell each make one first query then three warm queries. See `real.jsonl` for first-query timing, memory, payload and chunk counts.

| Operation | Request | Baseline warm ms | Candidate warm ms |
|---|---|---:|---:|
| records | ordered | 16.02 | 16.67 |
| records | reversed | 16.03 | 16.22 |
| records | interleaved | 19.76 | 19.79 |
| records | duplicates | 20.71 | 20.43 |
| relationships | ordered | 2.34 | 2.42 |
| relationships | reversed | 2.35 | 2.39 |
| relationships | interleaved | 4.18 | 4.10 |
| relationships | duplicates | 4.24 | 4.39 |
| candidates | ordered | 7.60 | 7.64 |

Live-output warm RSS ranges across request patterns: records 316–358MB baseline / 317–359MB candidate; relationships 247–252MB / 247–252MB; candidates 272–282MB / 272–282MB. No real memory reduction was established.

The initially slower duplicate-relationship result did not repeat: eight further fresh pairs gave warm medians 4.320ms baseline and 4.291ms candidate, with paired median ratios 0.979–1.051 and changing direction (`real-repeat.jsonl`). Other small real timing differences overlap. This limited check cannot establish a universal no-slowdown claim, large-result performance, giant-watershed behavior, or unsupported uncached relationship projections.

## Memory interpretation

`input_bytes`/`output_bytes` are Arrow logical buffer-accounting `nbytes`, not process memory or strictly semantic payload. Validity/offset accounting can differ with chunk layout despite equal values. Original source is 18.36MB; baseline interleaved output reports 18.52MB and candidate 18.38MB. Necessary eager output remains.

`arrow_live` is current Arrow-pool allocation, excluding NumPy-backed source buffers, Python objects and many native allocations. `rss` is current process resident memory; `peak_rss` is process lifetime high-water RSS including setup and transient workspace. These are bytes in raw receipts (macOS ru_maxrss already uses bytes; Linux is converted). Snapshots are before retrieval, after live output, and (synthetic only) after output deletion and GC. No allocator purge is forced.

Example original interleaved first pair: baseline Arrow-live 6.12→129.03MB, RSS 123.50→1106.71MB, peak 1131.36MB. Candidate Arrow-live 6.12→24.50MB, RSS 125.32→169.69MB, peak 169.69MB. After deleting output, Arrow-live returns to 6.12MB in both; RSS stays ~1107/170MB. Elevated RSS alone is not a live-object leak. Large divergence between pool and RSS is consistent with native/Python chunk metadata and allocator retention but does not precisely attribute those bytes. Ordered candidate RSS rises despite fewer chunks, consistent with coalescing workspace/allocator retention; current/peak RSS cannot by themselves partition workspace from retained free pages.

## Reproduce

From repository root:

```sh
uv run python -m pytest planning/experiments/chunk-overhead/test_candidate.py tests/test_vectors.py -q
uv run --with psutil python planning/experiments/chunk-overhead/probe.py --mode candidate --pattern interleaved --verify
uv run --with psutil python planning/experiments/chunk-overhead/run_matrix.py
# Optional complete real matrix, using an existing suitable offline GRIT cache:
uv run --with psutil python planning/experiments/chunk-overhead/run_matrix.py --cache /absolute/cache
uv run --with psutil python planning/experiments/chunk-overhead/real_probe.py --cache /absolute/cache --ids planning/experiments/chunk-overhead/real-requests.json --mode candidate --pattern duplicates --operation relationships --verify
uv run ruff check planning/experiments/chunk-overhead
uv run ruff format --check planning/experiments/chunk-overhead
uv run ty check
```

The matrix runner overwrites timing receipts; preserve original evidence before rerunning. Additional repeats use the same probe command (20k rows, width1024, columns2, chunks8, ordered) ten times per mode; real duplicate-relationship repeats eight times per mode. Exact-output receipts use `--verify` for every shape/pattern or real operation/pattern. `uv run python -m pytest` is required here: direct `uv run pytest` failed collection of existing `tests.spatial_fixture` in this environment.

Experiment files also passed ty with `src.include` overridden to this directory and psutil's uv-resolved site-packages as an extra search path. The ordinary repository ty configuration checks only `src`. No adoption or production design follows from these findings; #198 stays open for maintainer discovery.
