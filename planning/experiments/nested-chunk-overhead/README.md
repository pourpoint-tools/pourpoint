# Nested gather comparison

Selected: bounded interleaved gather; production change is limited to `_selection.py`. Related: [published vision](../../visions/2026-11-01-reduce-reader-chunk-overhead-in-real-hfx-retrieval.md), [#198](https://github.com/pourpoint-tools/pourpoint/issues/198).

Selected production revision: `9c8b742` (subsequent evidence-only updates do not change the mechanism). Baseline `fb9963f` uses the unchanged reader from #200; `baseline.py` preserves its selection helpers. `unconditional.py` first tried bounded nested whole-table gather. Ordinary nine-river candidate retrieval slowed repeatedly (see `unconditional-repeat.jsonl`), so that approach was rejected rather than averaged against interleaving gains. `candidate.py` instead considers bounded gather only when requests cross aligned source batches more often than a monotone traversal. It retains the baseline single-batch path and does not coalesce ordinary monotone selections.

## Payload reasoning

The prototype's 128 MiB bound is deliberately well below signed 32-bit offsets; it is not the earlier experiment's 64 MiB constant. Only numeric, boolean, binary/string and recursively ordinary struct/list fields qualify. Dictionary, extension, union, list-view, fixed-size-list, map and other fields retain baseline behavior. Ordinary list offsets partition children rather than reference overlapping children; struct fields pair children with rows. Therefore logical source bytes count each source occurrence, including shared/overlapping source chunks. Maximum request multiplicity times logical bytes conservatively bounds gathered payload. Retained source bytes also bound backing slices/coalescing workspace; neither bound claims precise process RSS. Empty structs are excluded because list child counts need nonzero physical accounting. Null children and boolean bit packing are still below offset capacity with the conservative byte ceiling. No multi-gigabyte arrays were executed: tests reduce the ceiling to 128 bytes and exercise shared nested buffers and duplicate expansion.

## Measurements

Use the same public GRIT 0.4.0 publication, existing offline cache and 503 catchment IDs as [the primitive study](../chunk-overhead/README.md); no geometries or local cache paths are committed. Full catchment records include bbox structs; projected relationships contain list<int64>. Nine ordinary level-1 river candidates and numeric ID projections cover other reader paths. Runtime includes lookup, reading, gather, compaction and validation, excluding only fabric opening/request preparation equally. Correctness runs are separate from timing runs. Five fresh pairs per real cell, alternating process order; first query and three warm queries per process. Additional twelve alternating pairs check ordinary candidates and reversed records. Source publication hashes and machine/dependency details are unchanged from the linked study; every synthetic receipt includes runtime versions.

Logical `nbytes`, Arrow live allocation, current RSS and process lifetime peak RSS are separate. Arrow excludes Python/native metadata and NumPy buffers; RSS includes allocator retention and workspace. Released Arrow allocations do not imply RSS returns to its starting value. Fresh processes prevent previous cases from contaminating memory high-water marks; normal machine activity remains uncontrolled. These bounded cases do not prove universal performance or giant-watershed behavior.

## Reproduce

From repository root, using an existing offline GRIT cache:

```sh
uv run python -m pytest planning/experiments/nested-chunk-overhead/test_candidate.py tests/test_vectors.py -q
uv run --with psutil python planning/experiments/nested-chunk-overhead/real_probe.py --cache /absolute/cache --ids planning/experiments/chunk-overhead/real-requests.json --mode candidate --pattern interleaved --operation records --verify
uv run --with psutil python planning/experiments/nested-chunk-overhead/probe.py --mode candidate --pattern interleaved
```

Repeat without `--verify` for timings, with both modes and all four request patterns. Real operations: records, relationships, candidates (ordered only), projection. Synthetic shapes: default 170000 rows/17 chunks/9 numeric fields with width 32, and width 0 for numeric-only. Receipt files preserve all runs, including no-benefit/regressing cases.

## Results

| Operation | Request | Baseline warm ms / RSS MB | Candidate warm ms / RSS MB |
|---|---|---:|---:|
| records | ordered | 16.733 / 330.0 | 17.063 / 320.2 |
| records | reversed | 16.670 / 319.5 | 16.786 / 320.5 |
| records | interleaved | 19.564 / 342.4 | 16.382 / 323.3 |
| records | duplicates | 20.124 / 331.7 | 16.428 / 333.8 |
| relationships | ordered | 2.370 / 247.8 | 1.822 / 248.6 |
| relationships | reversed | 2.333 / 248.0 | 1.820 / 248.5 |
| relationships | interleaved | 3.937 / 251.2 | 1.815 / 250.6 |
| relationships | duplicates | 4.281 / 251.2 | 1.929 / 250.4 |
| candidates | ordered | 7.554 / 281.4 | 7.704 / 282.0 |

| Operation | Request | Baseline warm ms / RSS MB | Candidate warm ms / RSS MB |
|---|---|---:|---:|
| projection | ordered | 3.024 / 254.7 | 2.982 / 251.4 |
| projection | reversed | 3.024 / 251.0 | 3.038 / 251.2 |
| projection | interleaved | 3.038 / 253.1 | 3.064 / 254.0 |
| projection | duplicates | 3.083 / 253.1 | 3.074 / 254.3 |

| Width | Request | Baseline ms / RSS MB | Candidate ms / RSS MB |
|---|---|---:|---:|
| 0 | ordered | 3.056 / 131.5 | 3.001 / 131.6 |
| 0 | reversed | 3.049 / 131.9 | 3.150 / 132.0 |
| 0 | interleaved | 3.576 / 133.6 | 3.694 / 133.7 |
| 0 | duplicates | 3.965 / 136.5 | 4.006 / 136.5 |
| 32 | ordered | 23.584 / 144.3 | 23.254 / 143.2 |
| 32 | reversed | 23.375 / 144.2 | 23.720 / 143.7 |
| 32 | interleaved | 4082.711 / 1109.7 | 13.077 / 157.4 |
| 32 | duplicates | 4539.651 / 1219.8 | 13.537 / 160.9 |

Real interleaved records reduce current RSS by 19.1 MB and warm retrieval by 3.18 ms. Duplicate records use 2.1 MB more RSS; ordered/reversed relationships and ordinary candidates also have small RSS increases. These are retained process measurements, not precise live payload costs. Relationship order by feature publication is not necessarily monotone by relationship IPC batch, explaining gains even in the “ordered” relationship cell.

The initial ordinary timing differences did not repeat consistently. Twelve alternating fresh pairs give process-warm medians: candidates 7.532 ms baseline / 7.544 candidate; reversed records 16.882 / 16.917, with changing paired directions. Ordered records follow-up instead favors candidate, 17.242 / 17.069. No percentage allowance or average across patterns was used. Numeric-only paths execute identical code; extra reversed/interleaved pairs check their initial small differences. Unconditional candidate ordinary slowdown did repeat and that approach was discarded. This establishes qualification only for these bounded patterns, not universal no-slowdown.


Validation: full project suite 184 passed; focused prototype suite 3 passed; Ruff lint and ty passed. Changed-file formatting passes. Repository-wide formatting reports pre-existing `fabric.py` and `metadata.py` differences; those files are untouched. Ten extra numeric pairs give reversed 3.076/3.097 ms and interleaved 3.783/3.747 ms, mixed paired directions on an unchanged code path. Final production correctness is checked separately against the preserved baseline using `--mode production --verify` for all 13 operation/pattern combinations.
