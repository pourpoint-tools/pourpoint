# Giant watershed union experiment

Bounded harness for [the vision](../../visions/2026-11-02-giant-watershed-union-experiment.md)
and [#212](https://github.com/pourpoint-tools/pourpoint/issues/212). Production code is unchanged.
No performance or correctness conclusion follows from the small smoke fixture.

[Kainji dedicated-VM measurements](results/2026-11-02-kainji/README.md) report two repeats,
validated GEOS agreement and unresolved Rust correctness.

Run from the repository root with `uv`. Source inputs and benchmark outputs stay outside Git.

```sh
uv run python -m pytest -q planning/experiments/giant_watershed_union
cargo test --locked --manifest-path planning/experiments/giant_watershed_union/rust/Cargo.toml
uv run python -m planning.experiments.giant_watershed_union.smoke_inputs /path/to/smoke.bin
uv run python -m planning.experiments.giant_watershed_union.run_matrix --input /path/to/smoke.bin --results-dir /path/to/new-smoke-results --repeats 2 --workers 2 --batch-size 1
```

The independent smoke geometry has one hole and two output parts, total planar area 17.
Rust tests also exercise actual WKB read/write and CLI execution. Python tests exercise
all measured Python paths, repeated fresh runs, failed subprocess records and exact comparison.

## Full captured-input run

Only on the separately authorized dedicated benchmark VM, without concurrent jobs:

```sh
uv run python -m planning.experiments.giant_watershed_union.run_matrix --input /data/kainji_inputs.wkb_bin --results-dir /results/new-run --repeats 2 --workers 8 --batch-size 5000 --validation-seconds 600
```

The results directory must not exist. Acquisition is a separate explicit command, never
triggered or silently reused by this runner. Hash and archive captured inputs and their
selection provenance; no new data acquisition is needed to rerun a capture. Prefix
captures are preparation samples, not complete smaller basins.

The fixed schedule has two repeats of direct GEOS, native Pourpoint merge, caller batches,
sorted Rust serial, sorted Rust parallel and unsorted Rust parallel. Each candidate has
its own process. There is no worker-count tournament. Eight workers, 5,000-polygon batches,
two repeats and a 600-second per-output validation budget are experiment defaults, not
production settings. Adjust the validation budget to the remaining rental budget, not
to force a candidate to pass. The precise tester ordering/script is unknown.

## Interpreting records

- `provenance.json`: input SHA-256, machine, Python/Shapely/GEOS/compiler, commit, working
  tree state, source/lock hashes and command configuration. Preserve the matching source.
- Each candidate directory has a durable `execution.json`, logs, measured JSON and WKB.
  Failures never reuse prior artifacts. Interrupted jobs may retain `running`, which is
  incomplete evidence, not success. `matrix_summary.json` contains only this invocation.
- Candidate totals include input read through WKB write. Matrix process wall time also
  includes interpreter startup/imports and JSON serialization. Acquisition and input
  hashing are outside candidate totals. External exact topology checks have separate
  process wall time. Add that if reporting complete measurement-and-check cost.
- Python reports read, vectorized decode, union (batch and final separately), output
  validation/write. Merge wraps original WKB once and reports wrapper/import time plus
  native decode-and-union together: the public API does not expose those internal stages.
  No duplicate predecoded geometry or regenerated WKB is retained for merge. Direct
  union has different decoding overhead; compare both whole-candidate and kernel costs.
- Rust reports combined streamed read/decode, preparation, sort, kernel, diagnostics and
  output write. No pre-union clone is needed. Peak RSS is the fresh-process lifetime
  high-water through output serialization (MiB), excluding external topology validation.
  Rust `is_valid` is unknown until external GEOS validation succeeds.
- Exact GEOS `equals` checks topology without precision grids, repair or tolerance.
  Disagreement records symmetric-difference area in square coordinate units (degrees²
  for this capture), not physical area. Invalid inputs/outputs, failed checks or budget
  exhaustion preserve unknown agreement unless equality already completed. Completed
  validity/diagnostics are checkpointed before equality, and equality before symmetric
  difference, so a later timeout does not erase established evidence. The direct GEOS baseline is a comparison,
  not a correctness oracle. No area-only or approximate check establishes equivalence.
- Rust reproduces the raw historical spatial sort/recursive pairwise algorithm, including
  leaf empty-union, holes and multipart flattening, not subsequent assembly cleanup.
  The measured Cargo.lock differs from the historical engine lock. Sorted serial versus
  parallel isolates concurrency within that kernel; sorted versus unsorted parallel
  tests ordering. These do not isolate GEOS versus geo under identical orchestration.

Save source, raw records, input provenance and output checks outside the VM before
resource deletion. Full measurements, independent review, findings returned to #195,
and rental cleanup remain necessary before #212 can be complete.


## Spatial grouping and source retention follow-up

Experimental support for [#216](https://github.com/pourpoint-tools/pourpoint/issues/216)
and its [vision](../../visions/2026-11-03-faster-lower-memory-watershed-union-experiment.md).
No production behavior changes or speed/memory conclusions follow from the smoke tests.

```sh
uv run python -m planning.experiments.giant_watershed_union.bench_grouped --input /data/inputs.wkb_bin --output-dir /results/new-grouped --order bbox --batch-size 5000 --workers 2
uv run python -m planning.experiments.giant_watershed_union.bench_python --input /data/inputs.wkb_bin --output-dir /results/new-control --candidate pourpoint_merge_release_wkb
```

`bench_grouped` compares captured `input` order, runtime `bbox` order (STR-style
bbox-center x slices sorted by y), and `physical` order from `--physical-order FILE`.
It uses fixed-count batches, **not actual HFX row-group boundaries**. Physical ordering
reuses layout but does not imply balanced geometry work. Defaults are bbox order,
5,000 records per task and one thread; these are experimental settings only.

The physical-order JSON contains `input_sha256` and `ordered_ids`: every captured ID
exactly once, in actual source physical row-group/row order. The CLI rejects a wrong
capture hash, missing IDs or duplicates. The sidecar producer must verify addresses
against the authoritative HFX lookup and exact selected WKB against the capture, and
retain source feature/lookup identity and preparation costs alongside the sidecar.
Captured extraction Parquet groups are **not** source HFX groups. No HFX metadata or
contract is introduced here. A full-source order can be filtered to selected IDs;
never substitute whole-group pre-unions for arbitrary selected contributions.

Each worker decodes only its own batch. At most `workers` tasks are submitted in a
wave; results are collected in group order before submitting the next wave. All
intermediate results remain until a single final GEOS union. This simple scheduling
can leave workers idle behind a slow task. It deliberately avoids an unbounded queue
of decoded geometries or futures. All captured source WKB stays alive by default;
`--release-inputs` drops consumed source WKB references and is a **kernel-only**
lifetime experiment, not equivalent public-result retention. Keeping Python WKB is
not an exact model of Arrow features or full delineation results. Neither release
mode nor bounded task count imposes an RSS ceiling: individual geometries, simultaneous
GEOS workspaces, intermediates and allocator retention can dominate memory.

Bbox preparation decodes bounded batches once to compute centers, then drops those
geometries; worker decoding is repeated and charged. `preparation_timings` is a subset
of `grouping_seconds`. `task_timings` sums per-task decode/union elapsed times which can
overlap: do **not** add those to wall time. `stage_timings` separates read, grouping,
group decode/union wall, final union, validation and write. Total excludes input hashing,
imports and external topology checks; peak RSS includes the fresh process through write.
Record outer process wall time and keep source commit/lock, logs and physical sidecar
alongside the JSON/WKB. New output directories prevent accidental evidence reuse.
Use the existing `verify_outputs` command in a separate process to check validity and
exact topology against direct GEOS. Baseline GEOS is not an independent oracle.

`pourpoint_merge_release_wkb` changes only the old harness's duplicate lifetime:
after constructing the same Arrow table and Subbasin wrappers it clears the captured
Python WKB list, while retaining source table/features through native merge and write.
The original `pourpoint_merge` remains unchanged. The control does not remove source
WKB from public results and does not prove an attribution for the old measured peak.
Run each candidate in a fresh process; preserve repeated results and failures. These
commands do not acquire datasets, rent resources, run repetitions or claim whole-workflow
benefit. Acquisition, source preparation, repeated open-fabric use and external checks
must be measured separately for any end-to-end or laptop claim.
