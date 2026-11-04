# Public-workflow spatial union validation

Production evidence for #195. The integrated path improves both complete public-workflow
comparisons while retaining source parts and outputs. Final repeated measurements,
input identity checks and separate output validation are complete; laptop and
concurrent-acquisition limits remain explicit below.

## Workload and scope

The authoritative source is
`s3://pourpoint-hfx/tdx/hfx-0.4.0/NGA-TDX-Hydro-20230126-full62-20261027/`.
An uncached current-reader manifest check matched the prior source declarations.
Receipt `manifest_sha256` hashes sorted JSON of `manifest.declarations`, not the source file bytes.
The source declares level-0 catchment connectivity `not_supplied` and has no catchment
`upstream_ids`. Therefore **public `delineate` is unsupported for this source**.
No topology was fabricated. The measured public composition is river `traverse`,
`relationships(local_catchment_ids)`, `subbasins`, then `merge`. It preserves all
materialized source fields and result geometries. This is not Kainji `delineate` validation.

Kainji uses seed river 1681474; the complete tributary uses 1626052. Both select complete
river upstream closures and distinct associated catchments from current source records.
Current candidate ID/WKB hashes establish agreement between new runs; they do not repair
the historical captured-input provenance limits in the [earlier report](../2026-11-03-spatial-union/README.md).
No historical captured geometry is substituted for acquisition.

## Method

`bench_workflow.py` runs each candidate in a fresh process, twice on the same open fabric.
The first full source/result remains alive through the second acquisition and merge.
Baseline is the eager geometry list and direct GEOS expression from main `a953638`;
integrated uses public `merge` with 5,000-part physical-order groups and two threads.
The entire union workspace is serialized across concurrent callers. This does **not**
bound retained source/results, or independent processes.

Record total workflow and open time, traversal, association, materialization and merge;
merge includes ordering, decode, group unions and final combination. Process peak RSS
is sampled before output serialization and correctness work. Second-run RSS is cumulative
process high water, not an isolated incremental peak. Arrow buffer identity checks after
measurement establish whether repeated acquisition retains separate backing storage.
The original tributary comparison uses two fresh processes per candidate, with
baseline/integrated then integrated/baseline order, to help expose variation. The original
Kainji baseline also has two fresh processes with two calls, with its second process
run after acquisition-change confirmation rather than in alternating order.
Persistent, OS and network caches are not controlled; first-process acquisition and warmed
acquisition must not be conflated.

`validate_workflow.py` checks output validity and exact topological equality in separate
processes. Direct GEOS is a comparator, not an independent correctness oracle.
`bench_delineate_fixture.py` additionally exercises real whole/contribution `delineate`,
named batches with one/two workers, retained results and closed-fabric standalone merge
on the existing independent HFX fixture. Its `baseline` flag is an old-union control
within the integrated implementation, not the entire old implementation. Expected geometry is explicitly the unit cell
`box(1,1,1.25,1.25)`. These tiny timings test practicality, not large-basin speed.

## Reproduce

Use the baseline checkout at `a953638` for baseline measurements and the integrated
checkout at `342f992` for final integrated measurements. `--candidate baseline` only
selects the old union expression; it does not revert acquisition. Running both flags
from the integrated checkout would not reproduce the original baseline. The script path may point to
this report's checkout, but `uv run` must execute from the intended runtime checkout;
inspect recorded `runtime_module`, `working_directory` and source hashes afterward.
The provenance JSON contains `source_url` equal to the URL above. Credentials remain
in the local `hetzner-pourpoint` AWS profile. Set paths below to local locations:

```bash
cd <baseline-or-integrated-checkout>
uv run python <experiment-directory>/bench_workflow.py \
  --provenance <source-provenance.json> --cache <persistent-cache-directory> \
  --seed 1681474 --candidate <baseline-or-integrated> --repeats 2 \
  --output <new-private-output-directory>
```

Run each candidate in two fresh processes, serially; use seed `1626052` for the
complete tributary. Keep all output directories separate. After timing completes:

```bash
cd <integrated-checkout>
uv run python <experiment-directory>/validate_workflow.py \
  <baseline-output>/result-1.wkb <candidate-output>/result-1.wkb
uv run python <experiment-directory>/validate_workflow.py \
  <baseline-output>/result-1.wkb <candidate-output>/result-2.wkb
uv run python <experiment-directory>/bench_delineate_fixture.py --candidate integrated
```

Compare `source_id_wkb_sha256` in each output's `input-checks.json` as well as
measurement source/manifest identities. Run correctness checks separately from
performance. The original Kainji baseline can exceed 16 GiB process RSS; do not run
this comparison on a memory-constrained machine merely to reproduce these receipts.

## Host and limits

Local Mac16,5, 64 GiB unified memory, 16 CPUs; Python 3.13.14, Shapely 2.2.0,
GEOS 3.14.1, PyArrow 25.0.1. This is a shared workstation, not an actual 16 GB laptop.
**System swap increased from 1,090 MiB before measurements to 22,771 MiB after the
baseline and a connection-recovery gap.** At integrated startup the system reported
89–90% memory free and swap around 22,699 MiB. These system-wide observations cannot
attribute swapping to this process or establish its cause. Host state changed between
candidates; process high-water RSS alone is not total memory demand or proof of swap-free use. No claim is made about laptop responsiveness or absence
of swapping. All large measurements run serially; unrelated shared-host noise remains.
No rentals or new spending were used. Private WKB and caches remain outside Git.

## Results

### Complete tributary

19,529 selected rivers and 19,527 source catchments. Each cell lists first / same-reader repeated call.

| Process, execution order | Workflow seconds | Merge seconds | Process peak GiB |
|---|---:|---:|---:|
| tributary-baseline-1 | 82.23 / 57.42 | 55.39 / 56.27 | 3.20 / 3.66 |
| tributary-integrated-1 | 31.78 / 31.98 | 30.02 / 30.86 | 3.39 / 4.16 |
| tributary-integrated-2 | 32.56 / 32.37 | 30.76 / 31.12 | 3.38 / 4.20 |
| tributary-baseline-2 | 58.72 / 58.16 | 56.87 / 56.99 | 3.83 / 4.60 |

First baseline acquisition took 26.26 seconds; warmed materialization took 1.09–1.22 seconds.
Do not attribute this cache difference to union. Warm workflow comparisons show about 44–46%
lower elapsed time. Peak memory varies between processes; this workload does not establish
a memory reduction. Repeated materialization retained distinct Arrow geometry buffers,
with zero shared buffers between the first and second results.

All eight outputs are valid and exactly topologically equal to baseline process 1, call 1.
All selected ID+WKB hashes match across candidates and repeated acquisitions.

Post-acquisition-change confirmation at `342f992`:

| Process | Workflow seconds | Merge seconds | Process peak GiB |
|---|---:|---:|---:|
| tributary-acquisition-integrated-1 | 32.40 / 31.79 | 30.54 / 30.61 | 3.09 / 3.73 |
| tributary-acquisition-integrated-2 | 32.92 / 32.24 | 31.05 / 31.03 | 2.99 / 3.75 |

All four new outputs are valid and exactly topologically equal to original baseline
process 1, call 1. Selected source ID/WKB hashes agree with the original tributary
comparison. These warmed workflows take 31.79–32.92 seconds; two-result retained peaks
are 3.73–3.75 GiB. They preserve the useful speed gain on the second complete basin.

### Kainji

169,805 selected rivers and 169,770 source catchments; full unchunked acquisition succeeded.
Baseline first / repeated workflows: 906.89 / 612.34 seconds, including 293.68 / 11.50
seconds materialization and 600.67 / 595.62 seconds merge. Process high-water RSS was
15.15 GiB at both checkpoints. This is not an acceptable 16 GB laptop memory target.
Integrated first / repeated workflows were 345.74 / 338.06 seconds, including
331.69 / 322.59 seconds merge. Peak RSS was **11.65 / 16.40 GiB**. The repeated case
retains two complete materializations and results, with no shared geometry buffers.
This exceeds a whole 16 GB machine before OS/application headroom; the laptop objective
is not established for repeated retained results. Source/result retention cannot be
removed merely to improve this benchmark. Warm total elapsed time fell about 45%.
All four outputs are valid and exactly topologically equal; all current input ID/WKB
hashes agree. An attempted second integrated fresh-process confirmation was stopped before its first
completed union to prioritize retained-memory diagnosis. It is incomplete, not a measured
repeat. A separate acquisition-only diagnostic retained two full materializations without any
union: live RSS was 8.53 / 12.93 GiB, while unique Arrow table buffers were only
2.95 / 5.89 GiB. Thus much of the observed residency exists before union; it is not
proven to be required retained-source memory. Native allocator/reader retention needs
separate attribution. Each saved result WKB is 21,920,396 bytes; serialized size is not
the GEOS in-memory size. The completed phase diagnostic records live RSS and active Arrow allocation separately:

| Phase | Call 1 live RSS GiB | Call 2 live RSS GiB |
|---|---:|---:|
| After acquisition | 8.522 | 15.891 |
| Before final combination | 10.707 | 16.119 |
| After final combination | 11.669 | 16.197 |
| After merge returns | 11.623 | 16.158 |

Arrow active allocation remains 3.035 GiB through call 1 union and 6.070 GiB through
call 2 union. The second acquisition adds 4.268 GiB live RSS before union; the second
union adds only about 0.3 GiB peak above that acquisition. First-call RSS remains about
3.1 GiB above acquisition after merge returns. These measurements do not distinguish
live GEOS ownership from native allocator retention. Reducing group concurrency alone
cannot resolve a 15.891 GiB pre-union footprint. All 68 group completions and both final
combinations were recorded; source ID/WKB hashes still match the timed runs.

The second original-baseline process took **620.04 / 625.94 seconds**,
including 11.40 / 11.73 seconds materialization and
605.78 / 608.82 seconds merge. Process peak RSS was
**16.89 GiB** at both checkpoints. This confirms baseline runtime stability on
warmed acquisition, but also exposes higher memory than its first process. Checksums
and WKB export run outside timed work; a sampled stack attributed their long baseline
post-processing delay to repeated PyArrow chunk-device scans during scalar access,
not union. The timing is not whole-script wall time. Both saved outputs are valid and
exactly topologically equal to baseline process 1, call 1; both source ID/WKB hashes
match. Host-wide swap used was 18,901.94 MiB before and 18,829.94 MiB after this process;
these shared-host snapshots do not establish process-specific swapping or its absence.

### Selected-row materialization follow-up

The diagnosis justified one scoped acquisition change, commit `342f992`: `subbasins`
retains selected source tables and maps feature rows into requested order instead of
copying all selected Arrow values through a second gather. Public `records` is unchanged.
Returned source rows, attributes and WKB remain faithful; no whole-group/unselected
backing records, source cache, geometry-policy change or allocator controls were added.

One acquisition-only run was initially mislabeled after kernel recovery reset the working
directory. Its runtime was the original root, not the prototype; the attribution was
withdrawn and preserved as baseline variability. The corrected run explicitly entered
the worktree and used verified module paths/hashes. Original root RSS varied substantially
between acquisition runs; do not interpret one old RSS sample as a universal baseline.
Corrected prototype acquisition-only RSS was 4.25 / 7.40 GiB with two full materializations.

The first correctly bound full Kainji process at `342f992` took **288.04 / 291.58 seconds**,
including 277.50 / 279.57 seconds merge, and peaked at **7.74 / 10.77 GiB**. Both full
source materializations and outputs remained alive. Source ID/WKB hashes match the original
baseline; both output geometries are valid and exactly topologically equal. This is a useful
reduction from union-only integration, not proof of actual 16 GB laptop responsiveness.
The second unchanged fresh-process pair took **291.04 / 293.76 seconds**, including
280.65 / 280.48 seconds merge, and peaked at **7.74 / 10.49 GiB**. Both outputs are
valid and exactly topologically equal to the original baseline, and selected source
ID/WKB hashes match. Runtime module paths and source hashes are recorded inside each
new timing receipt. Across four calls in two fresh processes, complete workflows took
288.04–293.76 seconds with two-result retained peaks of 10.49–10.77 GiB.

The acquisition diagnostic measured 2.95 GiB of unique Arrow table buffers per full
Kainji materialization (5.89 GiB for two), before decoded geometries and output. The
optimized acquisition-only run retained two materializations at 7.40 GiB live RSS;
the first full prototype process reached 10.77 GiB while retaining both outputs too.
These are sequential same-reader measurements, not a total-memory bound for concurrent
callers. The shared union lock limits active union workspaces only. Concurrent acquisition,
refinement, retained source parts and outputs can add memory outside that lock; their
combined giant-basin peak is unmeasured. Independent processes do not share the lock.
The observed footprint leaves nominal room within 16 GiB, but process RSS is not total
system memory and this shared 64 GiB host does not establish laptop responsiveness.

### Final small-workload check

`fixture-integrated-final.txt` records the final integrated rerun: whole-unit and
refined contribution delineation, named batches with one and two workers, 40 retained
batches, independently expected cell geometry and standalone merge after fabric closure
all pass. Ten-batch runs took 0.028–0.155 seconds. This correctness/practicality check
ran during the baseline's post-timing checksum pass, so its timings are not isolated
performance comparisons. Rasterio emitted its existing fixture georeferencing warning.

### Assessment

Across two fresh integrated Kainji processes, four retained-call workflows took
288.04–293.76 seconds, versus 612.34–625.94 seconds for warmed original-baseline calls:
about 52–54% less elapsed time. Final integrated two-result retained peaks were
10.49–10.77 GiB, versus original-baseline process peaks of 15.15–16.89 GiB.
The final tributary confirmation retains its roughly 44–46% warmed workflow reduction.
These are useful public-composition gains with unchanged geometry policy, not a claim
that giant Kainji `delineate` is supported or that all concurrent workloads fit a laptop.
Actual 16 GB responsiveness, absence of swapping, and giant simultaneous acquisition
or refinement remain unverified. No further algorithm search or HFX change was needed.

