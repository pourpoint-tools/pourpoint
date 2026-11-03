# Kainji union measurements

Two-repeat measurements for [#212](https://github.com/pourpoint-tools/pourpoint/issues/212)
and the [experiment vision](../../../../visions/2026-11-02-giant-watershed-union-experiment.md).
These conclude the bounded measurements, not an approved optimization. **Rust output
validity and topological agreement remain unknown.** This inconclusive correctness result
is retained alongside the timing evidence; it is not a reason to declare a winner.

## Observations

- Native `merge` and direct GEOS took similar whole-candidate time: roughly 12.8 minutes.
  The observed merge difference was 2.1–5.2 seconds, below 1%; two repeats do not isolate
  that small difference from variation. Merge peak RSS was higher: 18.3 versus 9.7 GiB.
- Caller batches of 5,000 took 40.3–45.5 seconds longer than direct union (5.3–5.9%),
  but reduced peak RSS to 7.4 GiB. The final combination alone took 99.8–100.9 seconds.
  This demonstrates a time/memory trade-off for this captured order, not that batching
  is always undesirable.
- Within raw Rust dissolve, sorted eight-worker kernel time was about 6.55 times shorter
  than sorted serial, and whole-candidate time about 5.03 times shorter. Both produced
  byte-identical outputs in both repeats. Concurrency explains a substantial timing
  difference within this algorithm, not a controlled Python-versus-Rust language effect.
- Sorting at eight workers reduced Rust whole-candidate time from 164–171 to 87 seconds,
  including 17 seconds spent sorting. Unsorted output had many more holes and coordinates.
  Ordering changed both cost and output representation; it is not just a scheduling effect.
- Faster Rust timings do **not** establish an equivalent union. Six 600-second validation
  attempts covered two unique Rust outputs and timed out before completed validity
  evidence, not specifically during equality. Neither equal area nor
  deterministic bytes establishes correctness. No production algorithm is selected.

## Inputs and environment

The unchanged named publication is
`s3://pourpoint-hfx/tdx/hfx-0.4.0/NGA-TDX-Hydro-20230126-full62-20261027/`.
A read-only reconstruction recovered 169,805 upstream rivers and exactly the captured
169,770 catchments, in the same order. Seed river `1681474` maps to catchment `2235378`.
The capture has 195,706,524 coordinates including ring closures, 267,854 polygon parts,
22,192 holes and 42,452 multipart records; every record is 2D. Binary IDs and WKB match
Parquet exactly. Input SHA-256:
`70d6d83b3bebe8a0b3723378d03f3ad839bfc400e12be3de21971cd6bb1067ae`.

The original acquisition did not save its manifest. The later live manifest and recovered
relationships establish current membership/order, **not historical manifest byte identity
or geometry correspondence to that original publication state**. The original sampled
vertex estimate in [acquisition evidence](original-acquisition.json) is superseded by the
exact [input audit](input-audit.json). Original retrieval recorded 84.36 seconds for
geometry records, 5.44 for traversal and 0.79 for associations; those are not repeated
candidate costs or a complete acquisition/serialization total.

The dedicated Hetzner VM used eight dedicated vCPUs and 32 GB RAM, AMD EPYC-Milan exposed
to Linux x86_64, with no swap. The proposed 16-vCPU machine could not be allocated under
the available quota. Candidates ran sequentially, with no competing benchmark jobs.
Versions: Python 3.13.18, Shapely 2.2.0, GEOS 3.14.1, rustc 1.99.1. Measured source:
`1619a22ce6277e59bf05b3fb386f2950e4c7455c`, clean checkout. Exact source/lock hashes and
commands are in [provenance](raw/provenance.json). Rust uses the historical raw algorithm
at `60e3d6e383546ea6ed20e68fe4d77959c816a48c` with the experiment's Cargo.lock, not the
historical binary/dependency lock. No assembly cleanup, hole filling, simplification or
precision-grid operation was applied by the harness.

These are not a reproduction of the tester's Mac, unavailable caller script/order, or
old end-to-end engine/dataset. Earlier shared-vCPU provisional runs are excluded.

## Time and memory

All times are seconds; RSS is GiB (raw `peak_rss_mb` is MiB). “Operation” means union
kernel, except merge includes native geometry decoding and batches include both stages.
Candidate total includes required read/decode/preparation/sort/output write. Python also
includes output validity checking; Rust has no equivalent internal check. Process wall
adds startup/import/report overhead. External check time is separate and **must be added**
when reporting measurement plus correctness-check cost; a timeout is not completed validation.
Peak RSS is each fresh candidate process through output serialization, not machine-wide
or external-validator peak. Acquisition, build and input hashing are outside these totals.

| Method | Repeat | Operation | Candidate total | Process wall | Peak RSS GiB | External check |
|---|---:|---:|---:|---:|---:|---:|
| Direct GEOS | 1 | 756.30 | 765.21 | 765.97 | 9.73 | 9.50 |
| Pourpoint merge | 1 | 766.19 | 770.38 | 771.30 | 18.30 | 9.51 |
| GEOS batches of 5,000 | 1 | 801.95 | 810.73 | 811.35 | 7.42 | 9.40 |
| Rust sorted, 1 worker | 1 | 413.33 | 437.10 | 437.33 | 3.45 | 600.07 |
| Rust sorted, 8 workers | 1 | 63.14 | 86.90 | 87.27 | 5.42 | 600.07 |
| Rust unsorted, 8 workers | 1 | 162.41 | 171.09 | 172.32 | 19.54 | 600.34 |
| Direct GEOS | 2 | 755.66 | 764.35 | 765.13 | 9.73 | 9.50 |
| Pourpoint merge | 2 | 762.27 | 766.49 | 767.42 | 18.29 | 9.55 |
| GEOS batches of 5,000 | 2 | 795.91 | 804.68 | 805.31 | 7.42 | 9.41 |
| Rust sorted, 1 worker | 2 | 414.53 | 438.40 | 438.64 | 3.41 | 600.07 |
| Rust sorted, 8 workers | 2 | 63.22 | 87.03 | 87.38 | 5.24 | 600.06 |
| Rust unsorted, 8 workers | 2 | 155.56 | 164.31 | 165.57 | 19.64 | 600.34 |

Stage details (repeat 1 / repeat 2):

| Method | Read | Decode | Preparation / sort | Union stages |
|---|---:|---:|---|---|
| Direct GEOS | 2.25 / 2.12 | 5.48 / 5.41 | none | 756.30 / 755.66 |
| Merge | 2.15 / 2.18 | included in operation | imports 0.17 / 0.17; wrappers 0.72 / 0.72 | decode + union 766.19 / 762.27 |
| GEOS batches | 2.19 / 2.19 | 5.42 / 5.42 | none | batches 701.09 / 696.07; final 100.87 / 99.84 |
| Rust sorted serial | combined read/decode 6.48 / 6.52 | — | preparation 0.009 / 0.009; sort 17.15 / 17.17 | 413.33 / 414.53 |
| Rust sorted parallel | combined read/decode 6.42 / 6.46 | — | preparation 0.010 / 0.010; sort 17.17 / 17.21 | 63.14 / 63.22 |
| Rust unsorted parallel | combined read/decode 6.44 / 6.43 | — | preparation 0.022 / 0.022; no sort | 162.41 / 155.56 |

Python output validity took 1.08–1.11 seconds and serialization about 0.05 seconds.
Rust area/bounds diagnostics took 0.01–0.18 seconds; serialization took 0.12–0.17 seconds
sorted and 2.04–2.11 seconds unsorted. Raw records preserve all stages at full precision.
The native merge call does not expose separate internal decode and union timings; no
predecoded duplicate geometries were retained for that candidate.

## Geometry evidence

All six Python runs were valid and exactly topologically equal to repeat-1 direct GEOS.
Direct and merge WKB hashes match; batching differs in bytes but has zero symmetric
difference area. Tiny floating-point area-accumulation differences are not geometry loss.
The GEOS baseline is a comparison, not an external correctness oracle.

Actual WKB diagnostics below are identical across repeats. Areas are planar degrees²,
not physical watershed areas. Diagnostics do not validate rings or establish topology.

| Outputs | Parts | Holes | Coordinates | Area |
|---|---:|---:|---:|---:|
| All GEOS methods | 211 | 231 | 1,369,795 | 129.49230837035 |
| Rust sorted serial and parallel | 209 | 805,468 | 5,489,557 | 129.49230604584 |
| Rust unsorted parallel | 218 | 10,041,751 | 56,457,443 | 129.49227298753 |

Rust bounds also differ from GEOS; [raw diagnostics](output-diagnostics.json) preserve
those coordinates ([exact diagnostic script](output-diagnostics-script.py.txt)). Large hole-count differences warrant investigation, but they do not
by themselves establish validity or the exact geometric difference. No symmetric-difference
geometry/area could be established for Rust within the check budget. All three Rust methods
repeat byte-for-byte, with sorted serial and parallel sharing a hash. That is repeatability,
not proof of equivalence. Rust validity and topological agreement remain **unknown**.

## Reproduction and retained evidence

Use the [harness instructions](../../README.md) at the recorded source commit:

```sh
uv run python -m planning.experiments.giant_watershed_union.run_matrix --input /data/kainji_inputs.wkb_bin --results-dir /results/new-run --repeats 2 --workers 8 --batch-size 5000 --validation-seconds 600
```

`raw/repeat-*` retains original per-process measurements, execution receipts and successful
comparisons. Failure receipts retain each timeout. The redundant all-in-one matrix and
checkpoint copies are omitted. [Output hashes](output-index.json), input audits and original
acquisition provenance bind the retained evidence. Raw records retain VM-local paths as
provenance, not downloadable locations. Input polygons, source relationships/IDs, WKB
outputs and credentials are not in Git; reproducing byte-identical input requires the
separately retained capture. Reacquisition alone cannot remove the manifest limitation.

Both the earlier provisional VM and the dedicated VM, including their IP resources,
were removed after evidence export and output-hash verification. No backups or volumes
were created. Estimated combined gross rental was below EUR 2 (about EUR 1.42 reserved),
including IP charges, not an invoice. Small source-storage request fees are not quantified;
the provider invoice remains authoritative. See [cost evidence](cost-summary.json).

The bounded experiment can return these measurements and explicit correctness limits
to #195 without further benchmarks. Publishing and verifying this evidence, returning
findings and reconciling #212 remain owner actions. Returning evidence does not choose
a production optimization or close the #195 design discussion.
