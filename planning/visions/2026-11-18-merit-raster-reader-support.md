# MERIT raster reader support

Enable Pourpoint to read HFX native D8 raster pairs declaring
`flow_dir_encoding: "merit"`, preserving source values while exposing the
engine's existing normalized directions. This is the implementation handoff for
[Pourpoint #224](https://github.com/pourpoint-tools/pourpoint/issues/224).
Publication of this vision does not complete the issue; implementing and
verifying this reader support does. It does not establish full MERIT delineation
readiness.

## Contract and behavior

Use the canonical [HFX raster contract](../../../hfx/docs/site/raster-data.md)
(also [published in the HFX repository](https://github.com/pourpoint-tools/hfx/blob/main/docs/site/raster-data.md)).
MERIT remains HFX 0.4.0 with auxiliary schema `hfx.aux.d8_raster.v2`;
this is another direction encoding, not a new storage representation.

- Accept `merit` in raster declarations and direction normalization.
- Map its eight ESRI neighbor powers (`1, 2, 4, 8, 16, 32, 64, 128`)
  to the existing clockwise-from-east normalized directions `1` through `8`.
- Treat river mouth `0` and inland depression `255` (`uint8`) or `-1`
  (`int8`) as valid terminals with normalized direction `0`. Preserve their
  distinction and original dtype in `RasterWindow.raw_direction`.
- Determine validity from the existing source-mask/header-nodata handling,
  not guessed codes. Header nodata takes precedence over a value's potential
  direction meaning. In particular, ocean `247` in the delivered unsigned
  MERIT rasters is nodata because its header declares it, not because `247`
  is a universal sentinel. Mask-false normalized payloads remain zero and
  must not be mistaken for valid terminal cells.
- Reject invalid consumed direction codes. Preserve existing ESRI, TauDEM
  and GRASS behavior; do not broaden ESRI to silently accept MERIT terminals.
- Preserve native grids, accumulation values and units, raw direction values,
  and the reader's lifecycle and lazy access behavior. No warping, source
  rewriting, hydrology recalculation, or new public workflow is required.

`src/pourpoint/rasters.py` currently rejects `merit` in `_Declaration.parse`
and only normalizes ESRI, TauDEM and GRASS. `RasterWindow` already carries raw
values and separate validity masks. The reverse-D8 tracer in
`src/pourpoint/refinement.py` already admits incoming neighbors by normalized
direction; terminal cells need no MERIT-specific tracing algorithm. A terminal
chosen as an outlet may still receive upstream flow.

## Evidence of success

Use focused test-first increments with project-native `uv` execution, Ruff and
ty. Reuse existing fixtures and tests where they establish the required behavior.
Verify declaration acceptance and a real reader window, not only a lookup helper.
Cover neighbor normalization, both terminal representations, preservation of raw
values, header-driven nodata, and rejection of invalid valid-cell codes. Retain
regression evidence that strict ESRI and the other encodings are unchanged.

Verify upstream tracing into a valid MERIT terminal while excluding nodata.
Establish expectations independently from the implementation. Keep tests bounded;
no global dataset download or benchmark is needed. Update affected public
NumPy-style docstrings where the added encoding changes their explanation.

A bounded read of the private production bundle can supplement deterministic
fixtures when access is available. Reuse the manifest identity and access context
in [the delivery handoff](https://github.com/pourpoint-tools/hfx/issues/281#issuecomment-6961363360);
do not publish credentials or signed URLs. State whether a production read was
performed and its limits. Synthetic reader/tracing verification is not evidence
of complete real-world watershed delineation.

## Separate provider and integration work

The delivered MERIT bundle declares `catchment_connectivity: not_supplied`.
Pourpoint's `delineate` calls catchment selection before optional refinement,
and that selection rejects unsupplied connectivity. Reading MERIT rasters alone
therefore cannot make this bundle pass the existing delineation workflow.

[HFX #285](https://github.com/pourpoint-tools/hfx/issues/285) owns deriving and
validating catchment connectivity from source river relationships and local
associations. Do not add MERIT-specific river-to-catchment inference to Pourpoint
or bypass its missing-connectivity checks in this effort. Missing source polygons
remain a separate completeness limitation because refinement is restricted to
its input polygon footprint.

Antimeridian handling, including unwrapped Chukotka vector longitudes versus
wrapped raster coordinates, is outside #224. Do not change geographic request
limits or add split-window/mosaic behavior here. End-to-end delineation and
refinement against a provider-updated bundle require separate integration
verification; do not claim worldwide MERIT compatibility on reader support alone.
