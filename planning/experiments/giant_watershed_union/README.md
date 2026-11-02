# Giant watershed union experiment

Bounded harness for [the vision](../../visions/2026-11-02-giant-watershed-union-experiment.md)
and [#212](https://github.com/pourpoint-tools/pourpoint/issues/212). Production code is unchanged.
No performance or correctness conclusion follows from the small smoke fixture.

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
