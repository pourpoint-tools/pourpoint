# Connectivity completeness safeguards

Pourpoint must not silently present a watershed as complete when upstream catchment coverage is missing or cannot be verified. Implement generic HFX connectivity-completeness checks, with safe defaults and an explicit way to inspect incomplete results.

This vision designates [Pourpoint #227](https://github.com/pourpoint-tools/pourpoint/issues/227) for completion by implementation and verification, not by publication of this document. It unblocks the consumer safeguard required for [HFX #285](https://github.com/pourpoint-tools/hfx/issues/285); it does not complete that issue or authorize production promotion.

## Required behavior

Use the requested catchment level's HFX declarations and declared column names. Follow the authoritative HFX contract in `../hfx/docs/site/index.md`, particularly `bundle.md` and `vector-data.md`. Do not infer MERIT-specific relationships or duplicate the producer contract in Pourpoint.

- For supplied connectivity declared partial, assess every catchment in the inclusive upstream closure, not only the target's seed catchments. The selection is verified complete relative to the source network only when every visited catchment has a non-null zero in the declared upstream cut column.
- Positive counts mean known missing upstream connections. Null values mean connectivity is unverified. Preserve this distinction, including when both occur in one selection.
- Reject incomplete or unverified selections by default in both `select_upstream_subbasins` and `delineate`. Assess the selection before raster refinement or merging; these operations cannot restore missing upstream coverage.
- Provide one explicit caller opt-in that accepts both known-incomplete and unverified coverage. It must not suppress the diagnostics or turn either condition into verified completeness.
- Missing completeness metadata is not evidence of completeness. Reject such selections by default; the same opt-in may return them with an explicit not-assessed assessment. A valid explicit complete declaration can support a source-network completeness assessment; do not require partial-dataset cut columns on complete declarations.
- A partial declaration without usable declared cut-column information cannot establish completeness and must not pass by default. Distinguish unavailable assessment from corrupt data; opt-in is not permission to ignore integrity errors. Keep fatal configuration/integrity failures separate from expected target-local coverage failures.
- Connectivity declared `not_supplied`, or unavailable upstream relationships, remains unavailable for upstream selection. The opt-in does not manufacture a graph or turn that condition into a seed-only watershed.

Expected target-local failures use stable `OperationError` codes; batches retain failed slots and issues while allowing other targets to succeed. Preserve valid empty-target behavior, without claiming that an empty result proves dataset coverage. Exact codes and internal validation placement are implementation choices.

## Results and API design

Refactor upstream selection to return a typed result object consistently, rather than an ordinary list. It contains selected subbasins and attached coverage diagnostics. Delineation carries the same coverage information, and batch results retain it for each successful target.

Diagnostics must be durable data, not just logs, warnings, or raw provenance. Identify the assessment and affected catchment IDs, distinguish positive cuts from unverified units, and retain available cut information. Report absent assessment information explicitly rather than inventing per-unit evidence. Keep the assessment meaningful after fabric closure and after optional refinement: it describes source-network coverage of the selected closure, not a new certification of the raster output.

The existing standalone selection-to-refinement workflow must also retain coverage diagnostics when consuming the new typed selection results, including selection batches. Do not merely unwrap the subbasins and discard the assessment. Manually supplied units or footprints must not acquire an invented verified assessment. Include a regression showing that an opted-in incomplete selection retains its original source-closure diagnostics through standalone refinement.

Choose clear field names and small typed structures during implementation. Broader refactoring needed for a coherent design is in scope. There are no external users requiring backward compatibility: do not add compatibility wrappers, legacy return modes, or migration guides. Update affected repository callers, tests, examples and public docstrings to the new API rather than retaining two interfaces.

## Evidence and verification

The current shared `_select` implementation in `src/pourpoint/operations.py` checks `not_supplied` but does not inspect completeness. Both public operations use it. `results.py` and `_batch.py` already distinguish target-local operation failures from fatal errors; retain that distinction while evolving result types.

[#227](https://github.com/pourpoint-tools/pourpoint/issues/227) records candidate identity, manifest hash and observed failures. [HFX #287](https://github.com/pourpoint-tools/hfx/pull/287) merged the producer contract, MERIT preparation and candidate provenance. Reuse those records rather than treating current silent successes as correct behavior.

Observable success includes:

- A partial dataset whose entire selected closure has zero cuts succeeds without opt-in and reports verified source-network completeness.
- A zero-cut seed with a positive-cut upstream ancestor fails by default. The recorded MERIT witnesses include seed 794064 with cut ancestor 794046, and seed 2214927 with cut ancestor 2210307.
- Null-cut witnesses such as MERIT IDs 41899 and 2879551 fail by default. Opted-in incomplete and unverified selections return accurate, attached diagnostics, including mixed cases.
- Missing completeness metadata requires opt-in and stays not assessed; partial declarations lacking assessment information cannot silently succeed as complete. Explicit complete declarations and unavailable connectivity follow their distinct rules above.
- Selection and coarse/refined delineation agree on coverage policy. Mixed batches preserve successful results, durable diagnostics and target-local failure identity without swallowing integrity failures.
- Recheck the recorded MERIT candidate through the actual consumer, including complete source ID 27000015 / catchment 792777, and record the observed scope and any access limits. Use small independent fixtures for regression coverage rather than requiring remote credentials for the normal test suite.

Use project-native `uv` execution and the repository's test-first guidance, Ruff, ty and tests. Document stable failure codes, assessment meanings and the new returned types with the repository's public API docstring conventions.

## Dataset coordination and limits

The owner will update the other datasets after this consumer change lands. [HFX #288](https://github.com/pourpoint-tools/hfx/issues/288) tracks the public GRIT R2 update; [HFX #289](https://github.com/pourpoint-tools/hfx/issues/289) tracks private TDX Hydro on Hetzner. Their existence does not justify silently accepting missing metadata in Pourpoint. TDX's source interpretation remains separately unresolved in [HFX #268](https://github.com/pourpoint-tools/hfx/issues/268); do not invent catchment connectivity to make it pass.

This work does not update those bundles, promote the MERIT candidate, authorize paid infrastructure, repair source geometry or bridge omitted polygons. Source-network completeness is not proof of vector–raster consistency or complete real-world watershed area. Antimeridian refinement and other recorded MERIT limitations remain separate. Do not claim blanket MERIT compatibility after adding the safeguard.
