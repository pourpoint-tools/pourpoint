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
without an HFX contract addition. Source WKB comparisons and capture bindings must remain
explicit in the final evidence.

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

## Results

Pending final exported receipts. Report each basin and repetition separately before any
summary, including failed or incomplete runs. Distinguish sampled single-run candidates
from repeated comparisons. Preserve exact agreement status rather than inferring it from
successful execution, individual validity or area.

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
