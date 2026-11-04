# Bounded spatial union for laptop delineation

## Outcome

Integrate bounded, spatially grouped GEOS union into normal watershed delineation
so large basins finish sooner with practical memory use on ordinary 16 GB laptops.
Shorter waits come first, lower peak memory second; inducing swapping is not a
useful speedup. Preserve room for the operating system and other applications.
No fixed process-memory ceiling or universal speed target is agreed.

This is the production direction selected in [discussion #195](https://github.com/pourpoint-tools/pourpoint/issues/195).
Implementing and validating this vision completes that issue's agreed scope;
publishing the vision alone does not. Further algorithm searches and HFX changes
are deferred unless integration reveals a concrete need, not added to this work
speculatively.

## Selected approach and evidence

Use existing HFX physical ordering to form spatially coherent groups, decode and
union groups with bounded concurrency, then combine their results. The successful
two-thread experiment is the starting point, not a required thread count for every
call. Choose group sizes, small-input behavior, scheduling and integration mechanics
through engineering judgment. Bound simultaneous decoded geometries and GEOS work,
including when callers already execute multiple delineations concurrently.

The [#216 report](../experiments/giant_watershed_union/results/2026-11-03-spatial-union/README.md)
contains methods, raw evidence and limitations. On identical captured Kainji inputs,
physical-order groups with two GEOS threads reduced isolated read-through-write
union time from 763–770 to 387–389 seconds and peak RSS from 9.66 to 5.06–5.20 GiB.
A complete tributary was about 47% faster, with slightly higher peak memory. Outputs
were valid and exactly topologically equal to direct GEOS. These observations are
not whole-delineation or real-laptop validation, and direct GEOS is not an independent
correctness oracle. Preserve the report's historical Kainji provenance limits.

No new HFX metadata or physical layout change is required by the selected direction.
The experiment ordered selected IDs by authoritative HFX `(block, row)` addresses,
not extraction-file groups, and unioned only selected records. The canonical input
contract remains `../hfx/docs/site/index.md`; do not invent an engine-local format.

## Integration constraints

Preserve the existing public API and its behavior: result contents, source identity,
attributes and WKB, ordering of returned parts and batch slots, empty results,
failure semantics, and usability of materialized results after fabric closure.
Do not require callers to manage union batches or acquire new metadata themselves.

`src/pourpoint/refinement.py` currently eagerly accesses every part's geometry before
`shapely.union_all`. Whole `Subbasin` geometries decode from retained Arrow WKB on
access; `SubbasinContribution` already holds a derived geometry and retains its source.
`src/pourpoint/operations.py` composes selection, optional refinement and merge while
retaining individual parts. Avoid unnecessary decoding, copies and lifetimes without
dropping those parts or source values to improve a benchmark.

Public `merge` takes materialized parts without a fabric argument and promises no
source access. Physical source addresses are not the same as `Feature.row`, which
indexes a materialized Arrow table. Integrate available ordering internally without
making standalone merge depend on an open reader or changing its signature. Preserve
support for arbitrary supplied part sequences and refined contributions; selected
subsets and clipped portions cannot be replaced with whole-group pre-unions.

Preserve geometry detail and existing geometry policy. Do not introduce simplification,
snapping, repair, hole filling, source normalization or precision reduction. A different
union order may change serialization; validate geometric agreement rather than requiring
identical bytes. Historical Rust output remains unresolved and is neither an equivalence
oracle nor a performance target. Coverage union, Numba and replacement kernels are not
part of the selected implementation.

## Evidence of completion

Demonstrate the gain through the public workflow, not only the experimental union
kernel. Compare the existing and integrated paths on identical selected geometries
and policies, using Kainji for continuity and another complete basin. Measure total
elapsed time and peak memory with real retained source parts and results. Include
ordering/preparation, decoding, group unions, final combination and any refinement
or assembly exercised; distinguish acquisition costs from union costs rather than
adding measurements from different machines. Include repeated use so setup costs
and cache benefits remain visible.

Use sufficient repeat measurements to distinguish meaningful gains from variation.
Report software versions, workload/source identities, settings, validation results
and reproducible evidence. Keep correctness checks separate from performance: output
validity and topological agreement with the existing GEOS path are both necessary,
and small independently understood cases should protect public behavior. Cover the
materialized standalone merge and integrated whole-unit/contribution paths without
building an exhaustive benchmark framework.

Assess total memory against the shared 16 GB laptop objective, including concurrent
workspaces, retained inputs, intermediates and final output. Verify on an actual
16 GB laptop if available; otherwise state the limitation and use available controlled
evidence without claiming laptop responsiveness or absence of swapping. The earlier
15.39 GiB native-merge control is not an acceptable default laptop target. No new rental
spending is authorized by this vision.

Use project-native tooling and focused test-first changes. Keep small workloads
practical and preserve existing batch-worker semantics while controlling nested
concurrency internally. Report remaining limits honestly; if integration fails to
produce useful complete-workflow gains or needs a public-contract or geometry-policy
change, return that evidence to #195 rather than silently expanding scope. Otherwise
return the implementation and validation evidence there when completing the work.
