# NumPy bounding-box inputs

Pourpoint should accept bounding boxes produced by scientific-Python workflows without requiring callers to convert their coordinates or unpack a NumPy array.

This vision implements the agreed scope of [discussion #192](https://github.com/pourpoint-tools/pourpoint/issues/192). Completing the implementation completes that issue; publishing this document does not.

## Supported inputs

- Tuples and lists containing exactly four built-in or NumPy real numeric coordinates, including mixed scalar types.
- One-dimensional NumPy arrays containing exactly four real numeric coordinates.
- The same support for vector bounding-box searches and raster region requests.

Keep the coordinate order and CRS meaning of each existing operation. Preserve finite-value and ordering validation, vector coordinate-range checks, and raster positive-area, geographic-limit, and coverage checks. Continue rejecting booleans, strings, complex numbers, and non-finite coordinates. Do not extend support to arbitrary iterators or multidimensional arrays.

## Repository context

`src/pourpoint/metadata.py:bounds` currently accepts only tuple/list containers and checks coordinate types against exact built-in `int` and `float` types before converting them to a float tuple. This rejects valid NumPy scalars. The failure was reproduced in the project's `uv` environment with `np.float64`, `np.float32`, and `np.int64`.

Vector searches reach this validator through `spatial.conservative_bounds`; raster region requests use it directly. Preserve the operation-specific checks around this shared validation. The current `Bounds` alias describes a normalized float tuple; public input annotations and docstrings should accurately describe the expanded input support without requiring a particular internal representation change.

## Evidence of completion

Focused tests should show equivalent accepted inputs producing the same vector candidates and raster requests as built-in-coordinate inputs. Cover NumPy scalar coordinates in tuples/lists and direct one-dimensional arrays, along with meaningful rejection cases for the stated boundaries. Existing finite-value, ordering, and operation-specific validation must remain effective.

Use the repository's test-first workflow and `uv` execution, with Ruff and ty checks. Leave validator mechanics and test organization to the implementing agent. This work does not change spatial query semantics, raster grids, CRS handling, or the HFX input contract.
