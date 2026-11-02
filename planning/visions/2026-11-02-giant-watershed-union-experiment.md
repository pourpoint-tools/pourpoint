# Giant watershed union experiment

Produce controlled measurements that explain giant-watershed polygon-union cost
and identify avoidable work. This is a bounded experiment, not authorization to
ship an optimization or choose a production algorithm.

This vision delivers experiment [#212](https://github.com/pourpoint-tools/pourpoint/issues/212).
Completing its measurements and returning findings completes that experiment issue,
not the originating design discussion [#195](https://github.com/pourpoint-tools/pourpoint/issues/195).
Publishing this vision completes neither issue.

## Question and scope

A tester reports about 21 minutes combining roughly 170,000 catchment polygons
upstream of Kainji, a dam on the Niger River in Nigeria. The reported workload has
about 196 million vertices. The previous engine and dataset together finished the
whole workflow in about four minutes; that is not a controlled engine regression.
The reports and historical reference are recorded in #195.

Determine how caller batching, engine overhead, union strategy and concurrency
contribute to cost on identical input polygons. Measure time, peak memory and
output geometry rather than assuming a winning approach. There is no agreed speed
target, and reproducing the tester's Mac settings or absolute timings is not a goal.

Start with Kainji using the unchanged publication:

`s3://pourpoint-hfx/tdx/hfx-0.4.0/NGA-TDX-Hydro-20230126-full62-20261027/`

The reported Kainji river ID is `1681474`, terminal catchment `2235378`, and snapped
outlet longitude/latitude `(4.614222, 9.864222)`. The report lists 169,805 upstream
river segments and 169,770 catchments. Reconstruct the selection through upstream
river relationships and river-to-catchment associations: this publication has level
0 catchments and does not supply catchment connectivity. Verify membership/counts
and report differences rather than forcing agreement. Capture the actual selected
IDs and polygon inputs so every method consumes the same data. Smaller upstream
workloads may support preparation and scaling checks. Danube or Rhine are follow-up
examples only if the initial case leaves an evidence gap; do not start a broad
multi-basin benchmark campaign.

Preserve geometry detail and leave the HFX hydrofabric unchanged. Do not simplify,
snap to a precision grid, fill holes, or silently repair inputs/outputs to make a
candidate pass. Keep production code and geometry policy unchanged.

## Comparisons and evidence

Include current Pourpoint/Shapely/GEOS and the historical Rust dissolve, using the
same captured polygon coordinates, holes and multipart contents. Distinguish:

- Current `merge` overhead versus its underlying union on materialized inputs.
- One union versus caller batches followed by a final union. The reported caller
  used 5,000-polygon batches; its precise script and input ordering are unavailable.
- Ordering and combination strategy versus concurrency. Include serial and parallel
  observations that allow those explanations to be separated.

Current `src/pourpoint/refinement.py::merge` calls `shapely.union_all` once per group;
it does not automatically batch one watershed into intermediate unions. Chunked
retrieval does not itself require intermediate unions.

The historical reference is `CooperBigFoot/pourpoint` commit
`60e3d6e383546ea6ed20e68fe4d77959c816a48c`, available during discovery in
`../deprecated/pourpoint`. In `crates/core/src/algo/dissolve.rs`, custom orchestration
spatially sorts inputs and performs recursive parallel pairwise unions using `geo`
and Rayon. It is not a hand-written polygon Boolean kernel. Compare the raw dissolve,
not the later assembly cleanup, hole filling, canonicalization and area calculation.
The historical function named `dissolve_unary_union_strategy` is a pairwise alias;
the raw flat-MultiPolygon comparison has overlap-cancellation semantics and must not
be treated as an equivalent union merely because it runs faster.

Keep input acquisition, decoding, conversion, sorting, union and output validation
costs distinguishable. Report complete candidate costs as well as useful kernel
measurements; do not hide required preparation or final combination work. Measure
peak memory with its scope stated. Use enough repeated measurements to expose
variation without building an exhaustive tournament. Record machine, versions,
source commits, inputs, ordering, worker settings and failures for reproducibility.

Evaluate correctness separately from speed: report polygon validity, topological
agreement and geometric differences where outputs disagree. Byte-identical WKB is
not required across kernels; equal area alone is not proof of equivalent geometry.
Do not silently accept approximate output as a valid optimization. The old result
is not a correctness oracle. If equivalence cannot be established, preserve that
uncertainty alongside the timing instead of declaring a winner.

Reuse relevant existing utilities selectively. `scripts/measure_delineation_warm.py`
has union diagnostics and coverage/equality checks; existing `experiments/` show
isolated uv projects and memory/provenance records. Neither supplies giant-basin
union evidence already. Use project-native tooling and a small experiment harness;
exact sample sizes, scheduling and reversible implementation details are left to
the implementing agent.

## Dedicated benchmark environment

Rent a Hetzner VM dedicated to this experiment, avoiding contention with the user's
other laptop work. Use dedicated vCPUs; no GPU is needed. Run candidate measurements
without competing benchmark jobs on the VM. Linux is acceptable: controlled
comparisons matter, not matching the original Mac environment.

The total authorized budget is **30 bucks**. Currency was not specified; verify the
billing currency and conversion before spending and stay conservatively below the
cap, including ancillary charges. Existing `hcloud` context `pourpoint` worked during
discovery. Read-only access to the named TDX manifest worked with AWS CLI profile
`hetzner-pourpoint` and endpoint `https://fsn1.your-objectstorage.com` in region `fsn1`.
Do not assume credentials or capacity will remain available, expose credentials in
logs, or grant the VM broader access than needed for read-only data retrieval.

A suitable discovered option is `ccx43` in `fsn1`: 16 dedicated vCPUs, 64 GB RAM and
360 GB disk. The API listed hourly net/gross prices of 0.4423/0.4781263; recheck current
pricing, currency and availability before provisioning. This is a candidate, not a
fixed hardware requirement. No VM was provisioned during the grill.

Keep rental time bounded, account for costs, and arrange cleanup so an interrupted
run does not leave an unattended bill. Save scripts, raw measurements, provenance,
geometry-check evidence and necessary reproduction artifacts outside the disposable
VM before deleting experiment resources. Keep source data and credentials out of
Git, respect dataset notices, and avoid unnecessary full-publication downloads.
Stop or reduce the comparison if resource needs threaten the budget; report the
remaining gap rather than silently expanding it.

## Completion

Return concise findings to #195, with durable links to the experiment code and
measurement evidence: demonstrated inefficiencies, explanations supported or ruled
out, output agreement, remaining uncertainties and options worth discussing. Keep
reported observations distinct from hypotheses and production recommendations.

The experiment is complete when the agreed comparisons have interpretable time,
memory and geometry evidence, reproduction limits are stated, findings have been
returned to #195, and rented resources have been removed after preserving evidence.
If access, correctness, budget or capacity prevents that outcome, report partial
findings and blockers rather than treating the experiment as complete. Selection
of a production optimization remains a later design discussion.
