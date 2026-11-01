# Reduce repeated work in downstream river extension

Speed up downstream river extension by reducing demonstrated repeated work, while preserving public behavior and avoiding significant slowdowns in other river-selection cases. Memory savings are not required: proportionate temporary-memory increases are acceptable when justified by meaningful end-to-end speed gains.

Related discussion: [#206](https://github.com/pourpoint-tools/pourpoint/issues/206). Publication leaves it open. Delivery of a qualifying solution completes its selected repeated-work scope; broader memory-retention investigation is outside this change, not claimed resolved. If no candidate qualifies, return evidence to #206 and leave it open rather than weaken the constraints.

The implementing agent may explore alternatives and select the simplest qualifying production solution without another adoption discussion. No mechanism is prescribed. Use proportionate prototypes, not an exhaustive tournament or benchmark platform.

## Starting evidence

[Experiment #208](https://github.com/pourpoint-tools/pourpoint/issues/208) returned [findings to #206](https://github.com/pourpoint-tools/pourpoint/issues/206#issuecomment-6564064774) at revision `6a2484b`. Public GRIT HFX 0.4.0 measurements covered local selections, an exact 3,432 km² upstream basin, and a larger regional rectangle, each with and without downstream extension. The regional rectangle is not an exact watershed.

In `src/pourpoint/rivers.py`, `select_rivers` calls `_oriented_line` for partial initial rivers when downstream extension is requested. Explicit orientation bypasses endpoint inference. Otherwise, each call converts the entire initial relationship table to Python rows, searches for the river, and scans initial nodes for endpoint evidence. Real GRIT data naturally exercised this fallback; it was not forced by modifying orientation values.

Local and basin fallback costs were negligible. Regional extension returned 16,536 rivers and made 77 fallback calls. A bounded experimental relationship-row index removed repeated conversion and linear row search, reducing warm whole-selection time from approximately 3.0 to 2.1 seconds, including index preparation and destruction. It retained approximately 14.9 MB of Python objects until return. This is evidence of avoidable work, not the selected implementation or a memory-saving claim. Node scanning and other work remained.

Two warm repeats in nonrandomized order do not establish whether small-case slowdowns were systematic. Candidate/initial table overlap was approximately 21.3 MB in the regional case, much smaller than process RSS. Arrow allocation returned to zero after closure; no live result-table leak was established, and remaining native memory was not exhaustively attributed.

The linked findings identify preserved local probes, raw receipts, workload geometry and provenance, detailed reports, and independent review. These are local artifacts, not public downloads or committed fixtures. Reuse them where available; identify unavailable evidence rather than assuming another checkout has it. Public HFX data may be fetched for representative comparisons.

## Constraints and scope

- Preserve public APIs and results: ordered source identities, source attributes and geometry, derived clipped/extended geometry, relationships, supplied nodes, declarations, and usability after fabric closure. Internal representation may change without changing those semantics.
- Preserve empty/no-match behavior and existing failure behavior, including ambiguous direction, unavailable connectivity, cross-level continuation, and propagated integrity errors. Do not silently produce partial results or infer a different river network.
- Keep the HFX contract unchanged; `../hfx/docs/site/index.md` is canonical. No publication rewrite or source normalization in the engine.
- Avoid significant slowdown in affected ordinary selections, small selections, and selections without extension. Do not average regressions away with gains on larger selections. This is not a strict rejection of every repeatable slowdown; no numerical allowance is agreed. Distinguish meaningful effects from noise using both absolute and relative timings.
- Report temporary-memory costs alongside speed gains. Proportionate increases are allowed, but not hidden behind post-return RSS or mislabeled as memory savings. Neither the experimental index nor its measured footprint is a required design or budget.
- Broader memory-retention investigation, feature ownership, general API redesign, reader chunk-overhead work, raster refinement, and polygon union performance are outside this change.

## Select and verify a solution

Use project-native tools and focused test-first increments. Let implementation choose reversible mechanisms and routine measurement details. Reuse existing coverage where it already proves behavior; add focused regression coverage for relevant changes.

Establish output and failure equivalence separately from timing. Compare complete river-selection operations, including preparation, extension, and any deferred work; moving work outside the timer is not a gain. Cover affected ordinary and larger cases with and without extension, including natural fallback and paths that do not need it. Bounded real-HFX comparisons must demonstrate meaningful end-to-end speed improvement, not only a synthetic helper win. Use enough repeated, appropriately ordered comparisons to resolve the earlier small-case timing uncertainty before claiming qualification.

Measure runtime and memory together. Distinguish necessary output, temporary workspace, shared buffers, live allocations, current RSS, and process-lifetime peak RSS. Do not infer a leak from retained RSS or treat summed logical table sizes as unique memory. State limits of native-memory attribution and workload coverage; bounded evidence is not a universal guarantee.

Select the simplest candidate supported by meaningful gains, proportionate memory costs, and no significant slowdowns in the tested affected workflows. Explain the trade-offs and alternatives actually tried; no fixed candidate count or numerical performance target is required. If performance qualification remains inconclusive or no candidate qualifies, preserve useful evidence and report the blocker instead of shipping a compromise or broadening scope.

## Deliverable

Deliver a focused production change with meaningful regression coverage and concise reproducible comparison evidence identifying baseline and selected revisions, data, environment, workloads, and measurement limits. Keep experimental machinery out of production. Update affected documentation and public API docstrings where needed; do not rewrite usage documentation when behavior is unchanged. Run relevant tests, Ruff, and ty through `uv`.

Report the delivered result or non-qualification findings on #206. This vision authorizes that work only through the separate implementation workflow; publication itself starts no implementation.
