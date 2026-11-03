# Spatial union and memory experiment

Findings for [#216](https://github.com/pourpoint-tools/pourpoint/issues/216)
and the [published vision](../../../../visions/2026-11-03-faster-lower-memory-watershed-union-experiment.md).
Performance measurements and export are complete; rented resources were verified deleted.
No production optimization is adopted.

**Measured finding:** existing physical-order batches with two bounded GEOS threads
roughly halved union time on both complete workloads. Kainji peak fell from 9.66 GiB
to 5.06–5.20 GiB. This is promising runtime-only evidence, not whole-delineation or
16 GB laptop validation; no new HFX contract is justified by these comparisons.

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

Retained evidence includes input hashes, current source identities, selected-ID and
physical-address preparation scripts, machine/software versions, exact candidate commands,
raw measurement/validation receipts, and preparation/acquisition timings. Large source
and output WKB, caches, credentials and build artifacts are excluded from Git.

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

## Kainji results

169,770 captured catchments; identical IDs and geometry bytes for every candidate.
Times include input read through output write, not acquisition or external validation.
Peak RSS is fresh-process high-water in GiB.

| Candidate | Repeat 1 seconds | Repeat 2 seconds | Peak GiB, repeats 1 / 2 |
|---|---:|---:|---:|
| Direct GEOS | 763.01 | 769.82 | 9.660 / 9.660 |
| Original native-merge harness | 769.55 | 773.15 | 18.264 / 18.344 |
| Native merge, release duplicate WKB | 762.36 | 773.88 | 15.388 / 15.389 |
| Physical-order serial | 703.21 | 704.35 | 4.724 / 4.724 |
| Physical-order two threads | 388.69 | 387.37 | 5.202 / 5.064 |

[Raw Kainji receipts](evidence/results/kainji/) retain exact values. Repeat 1 ran direct,
original merge, release-WKB control, physical serial, then physical two threads. Repeat 2
reversed that schedule. All outputs are valid; all comparisons against repeat-1 direct
GEOS are exactly equal. Two repeats support the large difference, not a precise universal
speed factor. Two-thread time is about 49% lower and peak about 46–48% lower than direct.
Serial grouping uses still less memory but takes about 81% longer than two threads.

Grouping uses 34 tasks (33 of 5,000 records and one of 4,770), with source WKB retained.
Final combination alone costs 40.30–40.69 seconds, included above. Each thread decodes
only its active group; all partial unions remain for final combination. No precision,
repair, simplification or hole policy changes occur.

Clearing only duplicate captured Python WKB lowers native-merge peak by 2.88–2.96 GiB,
about 16%, with no clear timing improvement. This is a real harness retention contribution,
not evidence that source features can be dropped from public results. The remaining
15.39 GiB peak is still too close to a whole 16 GB machine's memory for ordinary-app
headroom; the original 18.3 GiB result overstates native merge's standalone memory cost.

## Complete tributary results

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
Two threads reduce time about 47% versus direct here, but use slightly **more** peak memory
(1.303 versus 1.264–1.273 GiB). Benefits are not limited to Kainji; memory benefit is not
universal. Input-order and bbox-order serial pilots provide no speed improvement in their
single runs and were not expanded into a large candidate tournament.

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
including the exact preparation scripts. `measure.py.txt` is the original driver;
`measure-with-validation-rss.py.txt` is the later driver with GNU time around validation.
[Driver version boundaries](evidence/driver-versions.json) preserve their hashes. Earlier
validation receipts record elapsed time but not peak RSS; separate `validation-rss-rerun`
receipts now measure those checks without rewriting the original records. The machine/source record contains the measured
commit, experiment source hashes and `uv.lock` hash; use that commit's lock with `uv sync --locked`
before running scripts. Raw receipts retain execution paths and machine identifiers as
captured; they are evidence, not portable path defaults.

[Mapping-cost reruns](evidence/mapping-cost.json), reproduced by `mapping_cost.py.txt`,
measured local metadata-only source open, lookup initialization, selected address reads
and sorting: Kainji 1.506 / 0.181 seconds and tributary 0.638 / 0.025 seconds for fresh /
reopened persistent caches. They exclude input-ID loading, input hashing, saved-permutation
comparison and artifact writes. The original preparation was not timed. These reruns
check exact saved permutations and lookup data binding, not full Kainji source geometry.
Do not add these local times to VM union or claim measured producer-cost amortization.

## Laptop implications and recommendations

No actual 16 GB laptop has been validated. Controlled VM memory and
no-swap observations do not establish shared-laptop responsiveness or safety from swapping.
No fixed laptop memory ceiling or speed target was agreed. Account for retained source,
simultaneous GEOS workspaces, intermediates, final union and separate validation memory.

Recommend further runtime-only evaluation of existing physical ordering, bounded decode
and two threads while preserving real source/result retention. Serial grouping is an
alternative when lower peak matters more than wait time. Runtime bbox preparation did
not beat direct in its tributary pilot; new producer metadata, coverage union, Numba and
new geometry policy were not needed or tested. Existing HFX addresses already support
selected-subset ordering; no format addition is justified by this experiment.

Whole-workflow benefit is bounded by unmeasured integration: no same-open-fabric repeated
union, refinement/contribution assembly, full retained delineation result, shared-laptop
load or swapping test was run. Repeated local acquisition and mapping checks show cache
reuse separately, not an end-to-end speedup. Validation also needs its own working set. Separate GNU-time checks (including later
reruns for initially uninstrumented checks) used 0.151–0.152 GiB and 0.97–1.02 seconds on the tributary, and
0.704–0.706 GiB and 8.29–8.64 seconds on Kainji. Exact values are in validation stderr/execution
receipts. They run sequentially after candidates and are not added to union peak RSS.
The historical Rust comparison remains unresolved and is not a target or correctness oracle.

No production optimization is adopted. Findings and limitations return to #195 for the
human to resume; that discussion remains open.

## Cost, cleanup and evidence return

[Cleanup verification](evidence/cleanup-verification.json) confirms the experiment VM and
both primary IPs absent at 2026-11-03 12:49:53 UTC; an unrelated server was preserved.
No backups, snapshots or volumes were created. [Export verification](evidence/export-verification.json)
confirms all 220 remote result files matched local SHA-256 before deletion; their
[index](evidence/remote-results-index.json) includes large WKB outputs retained outside Git.
Empty logs and WKB are omitted from this report, not from the preserved export.
The compact [input index](evidence/input-index.json) uses basenames in place of local
absolute paths; original raw scientific receipts are unchanged.

[Cost accounting](evidence/cost-summary.json) reserves **€0.722216** for three started
hours of server plus IPv4, including VAT, versus the €10 rental cap. Actual elapsed
lifetime to verified absence was at most 2.31 hours. This is a conservative estimate,
not an invoice; small source-object request costs are unquantified, and IPv6 was free.

Return all findings to [#195](https://github.com/pourpoint-tools/pourpoint/issues/195)
after final review/publication. That discussion remains open. #216 must not close before
that evidence-return comment is posted and read back and the target report is verified.
