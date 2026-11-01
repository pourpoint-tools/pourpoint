# Measure river-selection costs on real HFX data

Determine whether real HFX river selection has material, avoidable memory or runtime costs, and whether reducing them is worth pursuing. This is an experiment vision for [#208](https://github.com/pourpoint-tools/pourpoint/issues/208). Completing the investigation and returning its findings completes #208, not the originating discussion [#206](https://github.com/pourpoint-tools/pourpoint/issues/206). Publication alone completes neither issue.

## Evidence to establish

Measure ordinary local river selections and at least one substantially larger basin-scale case, both with and without downstream extension. Choose suitable locations and public HFX data; public R2-hosted data may be downloaded. The incomplete existing cache is not the workload boundary. Report practical download or execution limits and any resulting evidence gap rather than substituting a tiny sample for basin-scale evidence.

Report memory and runtime together. Distinguish necessary output and workspace, shared buffers, allocator retention, avoidable live retention, and repeated work. A high process RSS alone is not evidence of a live-object leak. Include cases with no meaningful problem; a supported finding that no redesign is warranted is a successful outcome.

Establish whether real topology naturally exercises orientation fallback and how much that matters to the full operation. Clearly separate controlled branch probes from representative real-data measurements.

## Starting context

At `3ff20d0`, `src/pourpoint/rivers.py` retains the spatial candidate table while building the initial network and, for downstream extension, an extended network. `_oriented_line` converts the network relationship table to Python rows on each fallback call. Fallback concerns partial initial rivers without explicit orientation during extension, not every selected river. These mechanics are hypotheses about cost, not established real-workflow problems.

The [earlier investigation](https://github.com/pourpoint-tools/pourpoint/issues/198#issuecomment-6562743650) demonstrated overlapping tables and repeated conversion with synthetic backend reads. Its local probe artifacts are linked there. Existing reader experiments cover only nine ordinary real river candidates and do not establish full river-selection costs at scale. Recheck current code before measuring.

Reader chunk-overhead work was delivered in #205. Feature ownership (#207) and polygon union performance (#195) are separate. Use the canonical HFX contract in `../hfx/docs/site/index.md`; do not introduce a parallel format contract.

## Bounded comparisons

If baseline measurements reveal material costs, try small isolated experimental alternatives to determine whether those costs are avoidable. Do not run a broad redesign search or change production code. No mechanism or preferred winner is selected.

Preserve public behavior and the HFX contract in comparisons. Check equivalent outputs separately from performance measurement, including geometry, source identities and attributes, relationships, nodes, and relevant failure behavior. Account for preparation and deferred work so moving costs outside a timed region does not masquerade as an improvement.

The maintainer does not want significant slowdown. No numerical tolerance or strict zero-regression rule is agreed. Report memory/runtime trade-offs and uncertainty rather than choosing an adoption threshold or hiding regressions in averages.

## Deliverable and boundaries

Use project-native tools (`uv` for execution), proportionate probes, and reproducible evidence. Record the tested revision, data/publication, workload settings and sizes, environment, measurement boundaries, results, and material limitations. Preserve enough probe code and receipts to rerun and inspect the comparisons; no particular benchmark framework is required. Routine locations, instrumentation, and measurement mechanics belong to the investigating agent.

Return concise findings and links to reproducible evidence to #206, with the completion record on #208. Explain whether costs are material, which are avoidable, what the comparisons establish, and what remains unknown. Leave #206 open for `grill-me 206` before any production vision is selected.

This vision authorizes bounded experimental work only. It does not authorize a production fix, API or ownership redesign, adoption of an experimental alternative, or merging an implementation PR without separate authorization.
