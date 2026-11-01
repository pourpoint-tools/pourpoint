# Reduce chunk overhead without slowing retrieval

Determine whether chunk-related retrieval memory overhead can be reduced without losing retrieval speed or large-result correctness. This is an experiment, not authorization to adopt a production design. No preferred candidate is agreed.

Completing the bounded experiment and returning its comparative findings and limitations to [#198](https://github.com/pourpoint-tools/pourpoint/issues/198) completes [experiment #201](https://github.com/pourpoint-tools/pourpoint/issues/201), even if no candidate meets the constraints. Publication alone completes neither issue. Leave #198 open for the maintainer to resume discovery and decide whether to write a production vision.

## Starting evidence

At `58ddc23`, `src/pourpoint/_selection.py::take_rows` gathers distinct-row runs within aligned source batches. Switching batches or repeating a row starts another run. This preserves representability but can produce many small chunks. Numeric-only selections have a separate fast path. `compact_dictionaries` traverses every resulting column chunk, including columns without dictionaries. Record and indexed relationship retrieval in `src/pourpoint/vectors.py` use these helpers to restore requested order and compact dictionaries.

[The originating measurements](https://github.com/pourpoint-tools/pourpoint/issues/198#issuecomment-6562743650) used 170,000 randomly ordered rows across 17 source chunks, with one 32-byte binary field and nine numeric fields. The 18.52 MB logical result produced 159,989 chunks per column and process RSS above 1 GB. Selection and dictionary compaction each took about 2.2 seconds. Ordered and reversed requests stayed around 145 MB. These are bounded synthetic helper measurements, not a giant-watershed reproduction or proof of typical request ordering. Ordinary spatial candidates are publication-ordered.

Existing executable probes and raw outputs are available locally at `/Users/nicolaslazaro/.prime/agent/session-artifacts/01a1988c-b9e9-724c-96e9-6a76ce6968da/investigation-198/`. They are supporting evidence, not portable repository fixtures or a required dependency of the experiment.

## Compare equivalent, correct retrieval

Use controlled comparisons plus a bounded real-HFX retrieval check. Compare candidates against the current implementation across ordered, reversed and interleaved requests, including duplicate IDs. Vary payload and table shape enough to distinguish chunk overhead from payload cost; do not optimize only the measured random-order worst case. Include ordinary paths and the existing numeric fast path where affected. Choose candidate mechanisms, datasets and routine measurement details during the experiment; no benchmark framework or exhaustive candidate tournament is required.

Preserve source schema and types, field and schema metadata, values, requested order and duplicate IDs. Preserve the large variable-width payload guarantees established by [#193](https://github.com/pourpoint-tools/pourpoint/issues/193) and [#200](https://github.com/pourpoint-tools/pourpoint/pull/200), including nested values and dictionary meaning where affected. Ordinary whole-table `take` is not a safe general replacement because it can collapse binary chunks beyond a single-array offset limit. Internal chunk boundaries may change; public eager-table semantics may not.

Establish correctness separately from performance. Use project-native tools (`uv` and the existing test utilities), focused mechanism checks and equivalent outputs. Account for candidate preparation and deferred work, including dictionary compaction, so moving work outside a timed selection does not masquerade as a speed improvement. Include retrieval-level timing in the real-HFX check rather than relying only on helper timings.

A repeatable slowdown in any established request pattern fails the speed constraint. Do not average regressions away with gains elsewhere. Report slower candidates as trade-offs, not recommended replacements. Distinguish repeatable effects from measurement noise; no universal percentage allowance is agreed.

## Evidence to return

Provide reproducible probes or commands and a concise comparison naming the baseline and candidate revisions, data, request patterns, payload sizes, environment and relevant settings. Report runtime and memory together, including where each candidate helps, regresses or remains unproven.

Distinguish logical payload, live allocations, current process RSS and peak RSS. State what each measurement captures and misses, including native allocations outside the Arrow pool. Elevated RSS after releasing objects is not by itself a live-object leak. Keep necessary eager output and workspace separate from avoidable chunk overhead where the measurements support that distinction.

The original giant-watershed reproduction is not required. Explicitly state the limits of bounded real-data checks and any reduced-limit or small mechanism tests; do not claim multi-gigabyte correctness was reproduced when it was only reasoned about or simulated. If suitable real HFX data cannot be accessed, report the blocker rather than silently substituting another synthetic check or claiming the agreed experiment is complete.

Return findings and limitations to #198, with enough durable evidence for a fresh agent to assess the conclusion. A finding that no candidate meets both memory and speed constraints is a valid outcome. A successful candidate is evidence for a later adoption decision, not permission to change production behavior.

## Boundaries

Keep experimental changes isolated from production adoption. River selection, feature ownership, global geometry caching, streaming APIs, faster polygon union and the reported watershed area discrepancy remain outside scope. Do not weaken large-result correctness, change source types or introduce caller batching to obtain better measurements. No production design or implementation follows automatically from completing this vision.
