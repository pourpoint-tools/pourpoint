# Bounded primitive gather experiment

Related: [vision](../../visions/2026-11-01-reduce-chunk-overhead-without-slowing-retrieval.md), [experiment #201](https://github.com/pourpoint-tools/pourpoint/issues/201), [discussion #198](https://github.com/pourpoint-tools/pourpoint/issues/198).

## Finding

Small primitive tables can avoid the random-order chunk explosion. This is **not a production recommendation**: ordinary multi-chunk selections use more RSS, nested/dictionary/large inputs retain the baseline, and bounded real retrieval shows no established improvement. The strict no-slowdown requirement remains unproven; small timing differences must not be treated as equivalence. No production code changed.

Baseline: `ab76562` (selection implementation unchanged from `58ddc23`). Final candidate revision: `6b18fc2089f82de358dc2b2473b9d3839079309a`; probes unchanged from `9bc240e22c9979ab7d85cfc2d000b260ff549d80`. Date: 2026-11-01. Python 3.13.14, Arrow 25.0.1, macOS 15.7.3 arm64, mimalloc. Existing project dependencies; psutil supplied with `uv run --with psutil`. No thread-count overrides. Timings ran serially, not concurrently with other study timings; normal machine activity was not controlled.

## Mechanism and correctness

`candidate.py` permits whole-table take only for numeric/binary/string fields with **both total retained source buffers and logical source bytes <=64 MiB**. It splits requests before repeated source rows, so each gather's variable payload cannot exceed the source payload. This conservative experimental constant keeps eligible payload well below signed 32-bit offset limits; it is not a selected production setting. Numeric-only selections use the existing fast path. Nested, dictionary, extension and all other types, larger inputs, empty requests and empty projections use the unchanged baseline. There is no preparation cache or deferred candidate work.

The bound checks both `get_total_buffer_size` and `nbytes`: retained bytes exclude large backing slices, while logical bytes count repeated or overlapping chunks separately. Retained bytes alone deduplicate shared buffers and cannot bound gathered payload. For eligible primitive binary/string fields, logical bytes include every source occurrence's variable payload; each gather selects distinct row positions, so its payload cannot exceed that logical bound. Unsupported nested/dictionary types never use the new gather. Consequently this candidate does **not** demonstrate a general large-result optimization. Duplicate runs can still be numerous. No caller batching or source type conversion is used.

Five focused tests independently check values, duplicate order, field/schema metadata, numeric/empty paths, nested binary values and chunk-local ordered dictionary meaning. Reduced 128-byte checks include four chunks sharing one 64-byte binary array (72 retained bytes, 272 logical bytes), which otherwise gather into a 256-byte payload buffer. They prove fallback preserves the existing source-array bound for these cases; it does not reproduce a multi-gigabyte offset limit. Sixteen separate synthetic equivalence checks and nine separate real retrieval equivalence checks pass on the final candidate (`correctness-final.jsonl`, `real-correctness-final.jsonl`). Performance processes do not perform equivalence checking before timing. Existing vector tests also pass (33 tests including the five new tests).

## Synthetic comparison

Each cell is median **selection plus dictionary compaction milliseconds / current RSS MB**, from three fresh processes. Requests: publication order, reverse order, fixed-seed (198) random interleaving, and interleaving plus a repeated first 10%. All rows selected. Source construction and request generation excluded equally; eligibility checks, duplicate scanning, gather and compaction included. Raw stage timings and memory snapshots for the final candidate are in `synthetic.jsonl`; `synthetic-original.jsonl` preserves the original revision measurements.

| Rows; binary bytes/row; numeric columns; source chunks | Request | Baseline ms / MB | Candidate ms / MB |
|---|---|---:|---:|
| 170000; 32; 9; 17 | ordered | 23.02 / 144 | 12.41 / 167 |
| 170000; 32; 9; 17 | reversed | 23.01 / 144 | 12.02 / 167 |
| 170000; 32; 9; 17 | interleaved | 3988.57 / 1116 | 14.06 / 169 |
| 170000; 32; 9; 17 | duplicates | 4466.03 / 1215 | 16.23 / 172 |
| 170000; 0; 9; 17 | ordered | 3.00 / 132 | 3.04 / 132 |
| 170000; 0; 9; 17 | reversed | 2.98 / 132 | 3.06 / 131 |
| 170000; 0; 9; 17 | interleaved | 3.52 / 134 | 3.61 / 134 |
| 170000; 0; 9; 17 | duplicates | 3.88 / 136 | 3.81 / 136 |
| 20000; 1024; 2; 8 | ordered | 3.75 / 143 | 3.61 / 171 |
| 20000; 1024; 2; 8 | reversed | 3.78 / 143 | 3.62 / 171 |
| 20000; 1024; 2; 8 | interleaved | 143.72 / 185 | 4.41 / 173 |
| 20000; 1024; 2; 8 | duplicates | 160.21 / 190 | 4.99 / 173 |
| 20000; 32; 30; 1 | ordered | 3.38 / 112 | 2.07 / 112 |
| 20000; 32; 30; 1 | reversed | 3.46 / 113 | 2.06 / 112 |
| 20000; 32; 30; 1 | interleaved | 3.62 / 115 | 2.30 / 114 |
| 20000; 32; 30; 1 | duplicates | 4.08 / 115 | 2.66 / 115 |

The original 170k interleaved shape falls from 159,989 chunks per column to one. Numeric-only differences are small; this unchanged fast path still does not establish timing equivalence. At original revision `9bc240e`, the 1KiB-payload ordered case initially looked slower: ten additional fresh pairs gave baseline median 3.994ms (3.893–4.106), candidate 4.050ms (3.839–4.173). Seven of ten pairs were slower for the candidate. Overlap is not evidence of equivalence, and changing pair direction does not prove the strict speed constraint. No universal percentage allowance is applied. See `ordered-repeat.jsonl` (original revision only). The final-candidate matrix instead has ordered 1KiB medians 3.75ms baseline / 3.61ms candidate; these three pairs are not a replacement for a universal no-slowdown proof.

## Real HFX retrieval

Public [GRIT](https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/) HFX v0.4.0 (Upstream Tech public publication), 503 catchments from the existing Spain-river delineation receipt; ~827KB full record output, two lookup batches. `real-requests.json` records public IDs sorted by source lookup `(block,row)` and the public bounding box. No private dataset or cache path is committed. Keep the publication’s attribution/provenance with downloaded data; no source geometries are redistributed here. Published catchment artifact SHA-256: `f91badc69e90a3589862482bfa24c44ac061a23734c330092ac3aa1b7de25b73`; rivers: `7b45cdb8eb852cf8297523f4330f3ac3a099bcbbce0991ad295ed49531dff0ea`; families reference `vectors/2026-10-18-final/`. Existing offline cache supplied the data, so this is disk-cache retrieval, not a network benchmark.

Records select all fields; relationships select `id, upstream_ids` (other relationship columns were not fully cached). Ordinary spatial retrieval selects nine level-1 rivers (~6KB). Full records contain nested bbox and relationships contain lists, so these selections fall back. The numeric lookup path remains active. This check establishes integration correctness and bounds overhead, not a real-data chunk-reduction win. All operations are timed through `HydroFabric`, including lookup/read/gather/compaction. Opening the fabric is excluded equally. Three fresh processes per cell each make one first query then three warm queries. These timing receipts and `real-repeat.jsonl` measure original revision `9bc240e`, not the final guard. All real projections are numeric or unsupported and short-circuit before either byte-bound check; their executed paths are unchanged. Final-candidate equivalence is independently rerun, not relabeled timing evidence. See `real.jsonl` for first-query timing, memory, payload and chunk counts.

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

Example original-revision interleaved first pair (`synthetic-original.jsonl`): baseline Arrow-live 6.12→129.03MB, RSS 123.50→1106.71MB, peak 1131.36MB. Candidate Arrow-live 6.12→24.50MB, RSS 125.32→169.69MB, peak 169.69MB. After deleting output, Arrow-live returns to 6.12MB in both; RSS stays ~1107/170MB. Elevated RSS alone is not a live-object leak. Large divergence between pool and RSS is consistent with native/Python chunk metadata and allocator retention but does not precisely attribute those bytes. Ordered candidate RSS rises despite fewer chunks, consistent with coalescing workspace/allocator retention; current/peak RSS cannot by themselves partition workspace from retained free pages.

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
