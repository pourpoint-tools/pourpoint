# Faster, lower-memory watershed union experiment

## Outcome and completion

Find evidence-backed ways to shorten watershed delineation and reduce memory use on
ordinary 16 GB laptops, leaving room for the operating system and other applications.
Shorter waits come first, lower memory second, but a speedup that induces swapping is
not a useful laptop improvement. No fixed process-memory ceiling or speed target is agreed.
Benefits to other delineation workloads and repeated use are welcome; this is a focused
investigation of measured costs, not an exhaustive optimization tournament.

This bounded experiment follows [#216](https://github.com/pourpoint-tools/pourpoint/issues/216)
and returns **all findings and proposals**, including HFX layout/spec proposals, to
[the originating discussion #195](https://github.com/pourpoint-tools/pourpoint/issues/195).
Both projects are maintained by the same solo developer in pourpoint-tools; no separate
HFX approval process or discussion destination is needed. HFX remains authoritative for
its own format definition and producer behavior.

Completing the measurements, preserving evidence, reporting findings to #195, and cleaning
up rented resources completes **#216 only**. It does not complete #195 or select, adopt,
or ship a production optimization. Publishing this vision does not complete either issue.

## Starting evidence

Reuse the [#212 report and harness](../experiments/giant_watershed_union/results/2026-11-02-kainji/README.md).
On captured Kainji inputs, direct GEOS and native merge took about 765 seconds, but merge
peaked at 18.3 GiB versus direct GEOS's 9.7 GiB. Retrieval-order batches of 5,000 were
5–6% slower and peaked at 7.4 GiB. These are controlled VM observations, not laptop results
or acceptable memory targets. Historical Rust was faster, but its output validity and
agreement remain unknown; it is neither a proven equivalent optimization nor a target
the experiment must match. Preserve the report's input-provenance and reproduction limits.

Source inspection adds useful leads, not measured gains:

- GEOS ordinary polygon unary union already orders polygons through an STRtree and performs
  a serial cascaded union. External grouping tests partitioning and parallel scheduling on
  top of existing spatial organization. See [the inspected upstream implementation](https://github.com/libgeos/geos/blob/8bace6161f6593643e45875d7972247699357720/src/operation/union/CascadedPolygonUnion.cpp).
  Verify the version actually exercised; this upstream snapshot is not benchmark-version evidence.
- Current HFX producer code sorts by tier, Morton code of bounding-box center, and ID, then
  splits physical groups by rows/bytes. Pourpoint groups reads by physical row group but
  restores requested order. Physical locality is therefore an available baseline, not a
  guarantee of good union tasks. The canonical contract is `../hfx/docs/site/index.md`.
- `merge` eagerly decodes contributions before `shapely.union_all`; materialized features
  also retain source WKB, and delineation results retain parts. Copies and lifetimes are
  plausible memory contributors, not an established attribution of the measured difference.

## Investigation scope

Compare runtime-only approaches with reuse of existing HFX spatial layout before proposing
new published grouping information. Explore spatially coherent GEOS tasks with bounded
concurrency, grouping/combination order, and avoidable geometry copies or retention where
profiling supports them. Include final-combination cost and simultaneous working sets:
parallel execution is not inherently faster or lower-memory.

HFX physical layout and reusable producer-side spatial grouping are in scope. Experimental
producer/consumer prototypes are allowed where needed to test the hypothesis. Distinguish
changes within the current layout contract from new published metadata or semantics. Define
any proposed contract additions in HFX rather than inventing a private Pourpoint contract;
report the proposal and evidence in #195. Preserve immutable published datasets and identity
bindings when producing experimental layouts. Stored groups must accommodate selected subsets;
whole-group pre-unions are not interchangeable with arbitrary selected or refined contributions.

Prefer the existing geometry stack without excluding evidence-backed alternatives. Coverage
union is eligible only if unchanged inputs demonstrably satisfy its coverage prerequisites,
including interior-disjoint polygons and matching shared edges. Successful execution or
individual polygon validity is not enough. Numba-assisted numeric preparation is optional
only if profiling justifies it, including compilation cost; JIT does not directly accelerate
GEOS union. Do not turn these leads into a mandatory candidate matrix.

Preserve geometry detail and existing geometry policy. No simplification, new snapping,
repair, or hole-filling policy is authorized. Do not alter source-fabric normalization to
make an algorithm qualify. Changes to result contents or public API promises are not an
implicit way to reduce memory. Production implementation and adoption remain out of scope.

## Workloads and evidence

Use Kainji for continuity with #212. Check promising approaches on another complete basin
before generalizing. Choose the basin and reversible experimental mechanics through
engineering judgment. Assess whole-workflow benefit as well as isolated union cost, and
make benefits to repeated use visible. Account separately for producer preparation, runtime
grouping, reading/decoding, group unions, final combination, and validation where applicable;
do not hide setup or deferred work behind a kernel-only speedup.

Compare identical selected IDs and geometries under unchanged geometry policy. Record
versions, settings, source/input identities, elapsed time and peak memory, with sufficient
repeat evidence to distinguish useful gains from variation. Check output validity and
topological agreement separately from performance; byte identity or equal area alone does
not establish correctness. Keep failed checks and unresolved equivalence explicit rather
than declaring a winner. The existing GEOS result is a comparison, not an independent oracle.

Evaluate memory demands against a 16 GB laptop shared with ordinary applications, rather
than the maintainer's 64 GB machine or today's merge peak. Controlled VM resource limits
can inform this judgment but do not establish laptop responsiveness or swapping behavior.
Verify promising results on a real 16 GB laptop if one is available; otherwise report that
limitation without claiming real-laptop validation. Separate reusable producer costs from
per-query savings and identify whether gains apply only to giant basins or more broadly.

## Execution and delivery

A temporary dedicated VM is authorized with a **€10 total rental cap**, including associated
rented resources. Select its configuration to support useful controlled comparisons; do not
carry over #212's machine, worker-count, or budget choices as requirements. Track spending,
stop within the cap, preserve evidence, and delete the VM and associated resources afterward,
including after failed or inconclusive runs. No production rollout is authorized.

Use project-native tooling (`uv` for Python) and reuse the existing experiment harness where
useful. Keep experimental work separate from production paths. Leave candidate selection,
partition sizes, concurrency, profiling tools, and measurement mechanics to the implementing
agent rather than prescribing an exhaustive benchmark framework.

Deliver a concise report with reproducible methods, retained raw evidence, validated gains,
time/memory trade-offs, correctness limits, and recommendations or rejected hypotheses.
Return its link and any HFX proposal to #195. An inconclusive or negative result is valid
when supported by evidence; neither spending the full budget nor selecting an optimization
is required. Resource cleanup and evidence preservation are part of completion.
