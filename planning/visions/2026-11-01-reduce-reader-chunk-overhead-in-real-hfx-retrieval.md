# Reduce reader chunk overhead in real HFX retrieval

Reduce avoidable chunk-related memory overhead in Pourpoint's reader without losing retrieval speed or large-result correctness. Target actual HFX record and relationship structures, not only primitive synthetic tables. HFX 0.4.0's physical layout and contract remain unchanged.

The implementing agent may prototype alternatives, compare them, discard poor ones, and implement the simplest qualifying production solution. No mechanism or preferred candidate is selected. This is a production handoff with room for experimentation, not another experiment requiring a separate adoption discussion. Fast, proportionate prototypes are useful; an exhaustive tournament or benchmark platform is not.

Related discussion: [#198](https://github.com/pourpoint-tools/pourpoint/issues/198). Publication does not complete it. Delivery completes the reader chunk-overhead work selected from that discussion; record the outcome there without claiming to resolve its separate river-lifetime or feature-ownership questions. If no candidate meets the constraints, return the evidence and leave the discussion open rather than ship a compromise.

## Starting point

[Experiment #201](https://github.com/pourpoint-tools/pourpoint/issues/201), delivered in [#203](https://github.com/pourpoint-tools/pourpoint/pull/203), provides [reproducible probes and findings](../experiments/chunk-overhead/README.md). Production was unchanged at `1218f43`.

`src/pourpoint/_selection.py::take_rows` gathers distinct-row runs within aligned source batches. Switching batches or repeating rows starts another run. This protects variable-width arrays from offset overflow, but interleaved requests can create many tiny chunks. `compact_dictionaries` then visits each column chunk. Both feature records and indexed relationships in `src/pourpoint/vectors.py` use these helpers when restoring request order. Ordinary spatial candidates are publication-ordered; do not assume that they exhibit the synthetic worst case.

The bounded primitive-table experiment reduced a 170,000-row interleaved case from about 4 seconds / 1,116 MB RSS to 14 milliseconds / 169 MB. Ordered retrieval used more RSS, and real catchment records and relationships fell back because of nested fields. No real retrieval benefit or strict no-slowdown result was established. Its 64 MiB guard is experimental, not a selected production constant. Reuse this evidence rather than treating its candidate as the solution.

## Constraints

- Preserve eager public results, source schema and types, field and schema metadata, values, requested order and duplicate IDs. Internal chunk boundaries may change.
- Preserve the large variable-width payload and export guarantees established by [#193](https://github.com/pourpoint-tools/pourpoint/issues/193) and [#200](https://github.com/pourpoint-tools/pourpoint/pull/200), including nested values, dictionary meaning and release of unused dictionary values where affected. Unrestricted whole-table `take` is not a safe general replacement: it can collapse binary chunks beyond a single-array offset limit. Account for repeated rows and shared or overlapping buffers in any payload-bound reasoning.
- A repeatable slowdown in any established request pattern fails the speed constraint. Do not average regressions away with gains elsewhere. Distinguish noise from repeatable effects; no universal percentage allowance is agreed.
- Keep the HFX input contract unchanged; `../hfx/docs/site/index.md` is canonical. No publication rewrite, source-type conversion, caller batching requirement or streaming API.
- River-selection lifetimes, feature ownership, blanket geometry caching, raster refinement, faster polygon union and the reported watershed area discrepancy are outside this work.

## Choose through evidence

Use project-native tools and focused test-first increments. Choose reversible mechanisms and routine measurement details during implementation. Prefer the simplest candidate with demonstrated benefit under the constraints; discard unnecessary prototype machinery from production.

Compare against the unchanged reader across ordered, reversed and interleaved requests, including duplicates. Cover affected ordinary paths, numeric lookups, projections, nested records and relationships, and dictionary handling. Use controlled cases to expose the mechanism and bounded real-HFX retrieval to establish that the improvement reaches actual data structures and operations. Do not call a primitive-only synthetic win a real-workflow improvement.

Establish equivalent correct outputs separately from timing. Include preparation, gathering, dictionary compaction and any deferred work; shifting work outside the measurement is not a speed improvement. Include retrieval-level measurements, not only helper timings. Report runtime and memory together, including regressions and cases with no benefit. A qualifying solution needs a demonstrated memory benefit in relevant real-HFX retrieval and no repeatable slowdown in the established patterns tested. Bounded measurements are evidence for selection, not proof of universal performance.

Distinguish logical payload, live allocations, current RSS and peak RSS. Explain limits such as native allocations outside Arrow's pool and allocator retention after objects are released. The objective is avoidable overhead, not eliminating the necessary eager output or all workspace. Ordinary-path memory increases must remain visible in the choice; do not hide them behind worst-case savings.

The original giant-watershed reproduction is not required. Preserve large-payload safety through explicit mechanism reasoning and meaningful tests, and state when offset limits are tested at reduced scale rather than executed with multi-gigabyte data. If suitable real HFX data is unavailable or speed results remain inconclusive, report the blocker instead of claiming qualification.

## Deliverable

Deliver a focused production change when a candidate qualifies, with regression coverage and concise reproducible comparison evidence identifying baseline and selected revisions, data, environment, request patterns and measurement limits. Explain why the chosen approach is preferable to the alternatives actually tried; no fixed candidate count is required. Run the relevant tests, Ruff and ty through `uv`.

If none qualifies, retain useful evidence and explain which constraint prevented adoption. Do not weaken the constraints, ship the least-bad candidate, or broaden scope to force success. Report either outcome on #198. Implementation and its normal review remain a separate workflow from publishing this vision.
