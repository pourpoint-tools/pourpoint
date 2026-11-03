# Spatial union and memory experiment

Work in progress for [#216](https://github.com/pourpoint-tools/pourpoint/issues/216)
and the [published vision](../../../../visions/2026-11-03-faster-lower-memory-watershed-union-experiment.md).
This report is not a completion claim or a production recommendation. Kainji measurements,
final evidence export, cost accounting and resource cleanup are still pending.

## Questions and comparisons

Compare unchanged captured geometries using direct GEOS, fixed-count input-order batches,
runtime bbox-center ordering, and existing HFX physical ordering. For useful grouping,
compare one and two threads while bounding decoded tasks. Include final combination and
retain captured source WKB by default. This retention is not an exact model of public
Arrow features or delineation results.

A separate native-merge control releases only the old harness's duplicate Python WKB
list after Arrow wrapping. The old merge measurement retained both representations;
its memory gap versus direct GEOS cannot be attributed entirely to production merge.
The control retains Arrow-backed source features and does not change geometry policy.

## Inputs and reproducibility

Kainji reuses the full #212 capture, not a prefix sample. Preserve its historical
manifest/provenance limits; current source checks cannot establish missing historical
identity. The second workload is the complete upstream closure of river 1626052:
19,529 rivers and 19,527 supplied catchments, selected from unchanged captured WKB.

Final evidence will include input hashes, current source identities, selected-ID and
physical-address preparation scripts, machine/software versions, exact candidate commands,
raw measurement/validation receipts, and preparation/acquisition timings. Large source
and output WKB, caches, credentials and build artifacts will not be committed.

Physical order comes from the authoritative HFX lookup, not extraction Parquet groups.
The experiment reorders selected records into fixed-count union tasks; it does not union
whole physical groups or add published metadata. Existing layout supports this experiment
without an HFX contract addition. The JSON sidecar schema is `input_sha256` plus
`ordered_ids`, a permutation of every captured ID sorted by source `(block, row)`.
Grouped receipts record its SHA-256. `prepare_topology.py.txt` derives Kainji ordering;
`acquire_tributary.py.txt` filters the same addresses to the complete tributary.
The current [manifest](evidence/source-manifest.json) and [tributary provenance](evidence/tributary-provenance.json)
retain authoritative data bindings. Lookup binding verification is **not** verification
of all source geometry bytes: the later [tributary acquisition check](evidence/acquisition-check.json)
compares every selected WKB against current records, while full Kainji retains the original
capture's provenance limitation. Neither check reconstructs the missing historical manifest.

## Measurement scope

Run each candidate in a fresh process, recording total time and peak RSS. Show two repeats
for promising candidates, including their group-union and final-combination costs. Keep
bbox preparation within runtime grouping. Per-task decode/union sums overlap under threads
and must not be added to elapsed wall time. Separate validation from performance and report
validity and exact topological agreement independently; direct GEOS is not an oracle.

Local acquisition checks and VM union measurements are separate observations, not a summed
end-to-end benchmark. Reopened-reader persistent-cache checks expose repeated acquisition
cost, with uncontrolled OS/network caches. They do not establish same-open-fabric repeated
union or complete delineation speedups. Report those limits rather than hiding unmeasured
work behind isolated union improvements.

## Results so far: complete tributary

Receipt-derived read-through-write times and fresh-process peak RSS (GiB):

| Candidate | Repeat 1 seconds | Repeat 2 seconds | Peak GiB, repeats 1 / 2 |
|---|---:|---:|---:|
| Direct GEOS | 79.62 | 80.89 | 1.273 / 1.264 |
| Input-order serial | 84.38 | not repeated | 1.104 / — |
| Bbox-order serial | 80.86 | not repeated | 1.009 / — |
| Physical-order serial | 77.12 | 76.63 | 0.945 / 0.945 |
| Physical-order two threads | 42.27 | 42.86 | 1.303 / 1.303 |

All outputs are valid. Every compared result agrees exactly with repeat-1 direct GEOS;
that first direct result is the comparison baseline, not independently validated truth.
Repeat 1 ran direct, input-serial, physical-serial, bbox-serial, physical-threads2;
repeat 2 reversed the retained comparison: physical-threads2, physical-serial, direct.
Serial differences are small; two repeats do not establish a precise small speedup.
These are not Kainji or whole-delineation conclusions.

[Raw receipts](evidence/results/tributary/) preserve timings, commands, logs and separate
validation. [Machine/source provenance](evidence/machine-source-provenance.txt) records
32 GB RAM, no swap, Python 3.13.18, Shapely 2.2.0 and GEOS 3.14.1 on the dedicated VM.
The measured source is `d395ca9784bc7fc4f7485af4dfa0b89d4bd2ce2e`.
[Acquisition checks](evidence/acquisition-check.json) took 25.81 seconds with a fresh
persistent range cache and 3.53 seconds after reopening with that cache on the local Mac;
all 19,527 selected WKB values matched current source records. These times cannot be
added to VM union times. They do not repair the missing original acquisition manifest.

[Exact script snapshots](scripts/) are retained as text evidence, not a maintained API.
To reproduce, copy them as `.py` files into a separate artifact directory, supply the
recorded source access/profile and prior full Kainji artifacts, then run with project
`uv run python`. Preparation assumes the original sibling artifact directory layout;
measurement assumes `/root` inputs and `/results` output on the VM. Adjust those paths
explicitly, retain the adjusted scripts, and follow each receipt's exact command. Never
copy credentials into a report. Inputs, physical sidecars and output WKB are retained
outside Git; [hashes](evidence-sha256.json) bind the committed small evidence snapshots,
including the exact preparation scripts. The machine/source record contains the measured
commit, experiment source hashes and `uv.lock` hash; use that commit's lock with `uv sync --locked`
before running scripts. Raw receipts retain execution paths and machine identifiers as
captured; they are evidence, not portable path defaults.

Kainji measurements, final export, cost and cleanup remain pending. Report failed or
incomplete runs explicitly; do not infer agreement from validity or area alone.

## Laptop implications and recommendations

Pending Kainji results. No actual 16 GB laptop has been validated. Controlled VM memory and
no-swap observations do not establish shared-laptop responsiveness or safety from swapping.
No fixed laptop memory ceiling or speed target was agreed. Account for retained source,
simultaneous GEOS workspaces, intermediates, final union and separate validation memory.

No production optimization is adopted by this experiment. Recommendations, rejected leads,
uncertainty and any HFX implications return to #195; that discussion remains open.

## Cost, cleanup and evidence return

Pending final rental accounting and verified deletion receipts. The authorized total cap
is €10, including associated rented resources. Report retained evidence and resource cleanup
before completion of #216. Add the final #195 evidence-return link after publication and
read-back; publishing this draft or merging tooling does not complete either issue.
