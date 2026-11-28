# Experimentation Log — Pourpoint 0.6.0 Overhaul

Linked vision: [Pourpoint 0.6.0 correctness, performance, and API overhaul](../visions/2026-11-28-pourpoint-0-6-0-correctness-performance-and-api-overhaul.md) (originating issue `#231`).

## Decision Gate A — SQLite Cache Schema and Page Size (Track 2)

### Question

After implementing Track 2 reader, concurrency, and prefetch fixes (`missing_ranges`, contiguous column-range merging, bounded sliding-window prefetch, `_IndexedIPC` 16 MiB LRU batch cache, `_IPCDirectoryMeta` sharing, and SQLite WAL + `busy_timeout`), does replacing the unpaged `ranges(key, start, end, data, digest)` table with fixed-size aligned pages (`pages` + remainder `ranges`) materially improve cold or warm remote HFX 0.4.0 performance?

### Workload and Setup

- Dataset: public remote GRIT HFX 0.4.0 (`https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/`).
- Workload 1 (multi-basin vector selection + random probes): `select_upstream_subbasins` + `select_rivers` at Rhine Basel `(7.5890, 47.5596)`, Australia `(129.68, -14.5)`, and Spain `(1.83, 41.61)` (`464` subbasins, `116` rivers total) plus 30 warm `fabric.records` / `fabric.relationships` probes (`rand_s`). Measured across `cold`, `warm_online`, and `warm_offline`.
- Workload 2 (raster refinement COG + vector): `delineate` at Spain `(1.83, 41.61)` (`39` subbasins) across `cold` and `warm_offline`.
- Compared:
  - `main_baseline`: `main` (`cc7a2ef`) unpaged `ranges`.
  - `track2_current`: Track 2 unpaged `ranges`.
  - `track2_page_64k`, `track2_page_256k`, `track2_page_1024k`: Track 2 with prototype aligned page sizes (`65536`, `262144`, `1048576` bytes) + unaligned remainder `ranges`.

### Observed Results

#### Multi-basin vector selection + 30 random probes

| Configuration | Phase | `total_s` | `rand_s` | `sqlite_s` | `hash_s` | `pieces_calls` | `pieces_blob_mb` | `db_mb` | `requests` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `main_baseline` (`page_size=0`) | `cold` | 119.0170 | 0.0000 | 0.5371 | 0.0779 | 1661 | 108.17 | 88.07 | 783 |
| `main_baseline` (`page_size=0`) | `warm_online` | 0.5210 | 0.0346 | 0.1418 | 0.0769 | 1871 | 230.07 | 88.07 | 0 |
| `main_baseline` (`page_size=0`) | `warm_offline` | 0.4090 | 0.0284 | 0.0425 | 0.0552 | 816 | 171.13 | 88.07 | 0 |
| `track2_current` (`page_size=0`) | `cold` | 48.0698 | 0.0000 | 0.0612 | 0.0377 | 239 | 59.86 | 87.43 | 236 |
| `track2_current` (`page_size=0`) | `warm_online` | 0.3956 | 0.0095 | 0.0436 | 0.0370 | 269 | 112.79 | 87.43 | 0 |
| `track2_current` (`page_size=0`) | `warm_offline` | 0.3529 | 0.0082 | 0.0257 | 0.0352 | 269 | 112.79 | 87.43 | 0 |
| `track2_page_64k` (`page_size=65536`) | `cold` | 60.6754 | 0.0000 | 0.0625 | 0.0376 | 239 | 59.86 | 88.56 | 236 |
| `track2_page_64k` (`page_size=65536`) | `warm_online` | 0.4255 | 0.0079 | 0.0722 | 0.0361 | 269 | 108.70 | 88.56 | 0 |
| `track2_page_64k` (`page_size=65536`) | `warm_offline` | 0.3511 | 0.0060 | 0.0233 | 0.0344 | 269 | 108.70 | 88.56 | 0 |
| `track2_page_256k` (`page_size=262144`) | `cold` | 50.8466 | 0.0000 | 0.0564 | 0.0358 | 239 | 59.86 | 87.71 | 236 |
| `track2_page_256k` (`page_size=262144`) | `warm_online` | 0.4053 | 0.0073 | 0.0521 | 0.0357 | 269 | 108.76 | 87.71 | 0 |
| `track2_page_256k` (`page_size=262144`) | `warm_offline` | 0.3493 | 0.0060 | 0.0211 | 0.0338 | 269 | 108.76 | 87.71 | 0 |
| `track2_page_1024k` (`page_size=1048576`) | `cold` | 59.0460 | 0.0000 | 0.0623 | 0.0380 | 239 | 59.86 | 87.52 | 236 |
| `track2_page_1024k` (`page_size=1048576`) | `warm_online` | 0.4064 | 0.0076 | 0.0565 | 0.0356 | 269 | 109.01 | 87.52 | 0 |
| `track2_page_1024k` (`page_size=1048576`) | `warm_offline` | 0.3484 | 0.0061 | 0.0226 | 0.0339 | 269 | 109.01 | 87.52 | 0 |

Both `main_baseline` and `track2_*` transferred `91,232,159` bytes on the cold run.

#### Spain `delineate` with COG raster refinement

| Configuration | Phase | `total_s` | `sqlite_s` | `hash_s` | `pieces_calls` | `pieces_blob_mb` | `db_mb` | `requests` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `track2_current` (`page_size=0`) | `cold` | 17.0415 | 0.0137 | 0.0098 | 47 | 13.56 | 20.50 | 53 |
| `track2_current` (`page_size=0`) | `warm_offline` | 0.1334 | 0.0061 | 0.0080 | 47 | 25.11 | 20.50 | 0 |
| `track2_page_256k` (`page_size=262144`) | `cold` | 12.6074 | 0.0121 | 0.0084 | 47 | 13.56 | 20.57 | 53 |
| `track2_page_256k` (`page_size=262144`) | `warm_offline` | 0.1258 | 0.0050 | 0.0077 | 47 | 25.11 | 20.57 | 0 |

### Limitations

- Cold timings include single-run internet latency variance over public HTTPS endpoints; transferred bytes and SQLite call/byte counts are deterministic for the workload.
- Evaluated against the public GRIT HFX 0.4.0 dataset (`hfx-v0.4.0`).

### Decision

Keep the existing unpaged `ranges` SQLite cache schema (`Option A`). Track 2 non-schema optimizations already achieve a `2.48×` cold speedup (`119.02s -> 48.07s`), `3.3×` fewer HTTP requests (`783 -> 236`), and drop warm SQLite + SHA-256 read cost to `0.061s–0.081s` (`0.014s` on Spain `delineate`). Fixed-size paging only trimmed `~3.5%` of read bytes (`112.79 -> 108.76 MiB`, `0%` on Spain `delineate`), saved `~2 ms` on 30 random probes, slightly slowed `warm_online` SQLite reads (`0.0436s -> 0.0521–0.0722s`), and increased database size, which does not justify cache schema complexity or migration.

## Decision Gate B — Hybrid Rust Kernel Evaluation (Track 3)

### Question

After optimizing Track 3 refinement and raster processing in pure Python/NumPy (1-pixel zero-padded 1D NumPy BFS `_trace`, 256-byte LUT `normalize_direction`, `MaskFlags` `read_masks()` skip, thread-local `Transformer` caching, `shapely.prepare` clipping, single-union reuse in `delineate`, dtype-preserving QGIS export, and `_head_cache` reuse in `Source.raster_file`), does introducing a PyO3/Rust native extension (`maturin`) provide sufficient end-to-end speedup over pure Python/NumPy on real watersheds to justify multi-platform native wheel builds?

### Shadow-Mode Behavioral Parity (`rust-anything` Workflow)

- **Synthetic `normalize_direction`**: `2,048` exact byte cases (`256` bytes × `uint8`/`int8` × `esri`/`merit`/`taudem`/`grass` + all-False masks) verified `0` mismatches and matching `ValueError` behavior across `old_python`, `track3_numpy`, and `rust_proto`.
- **Synthetic `_trace`**: `23` edge/random cases (`1×1`, signed GRASS, cycles, 20 random `128×128` D8 grids) plus `1M`-pixel (`1000×1000`) and `10M`-pixel (`3162×3162`) dendritic grids verified `0` bit mismatches across `old_python`, `track3_numpy`, and `rust_proto`.
- **Real GRIT HFX 0.4.0 (`https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/`)**:
  - `spain-hillslope` (`2893×2072`, `5.994M px`, `6` traced cells, `1` subbasin): exact `np.array_equal` on `_trace` and `normalize_direction`; `GranularRefinement.geometry` vs `merge(subbasins)`: `equals=true`, `sym_diff_area=0.0`.
  - `spain-mainstem` (`2893×2072`, `5.994M px`, `3,855,207` traced cells = `64.31%`, `39` subbasins): exact `np.array_equal` on `_trace` and `normalize_direction`; `sym_diff_area=1.794e-12 deg²` (`equals=false` solely from GEOS overlay floating-point precision when intersecting and re-unioning 39 subbasin clips).
  - `chur-alpine-mainstem` (`532×894`, `0.476M px`, `293,027` traced cells = `61.61%`, `3` subbasins): exact `np.array_equal` on `_trace` and `normalize_direction`; `sym_diff_area=8.305e-14 deg²`.

### Observed Benchmarks

#### Isolated Kernel Benchmarks (median ms)

| Workload | `old_python` | `track3_numpy` | `rust_proto` | Speedup (Track 3 vs Old) | Delta (`track3_numpy` vs `rust_proto`) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Synthetic `1M px` dendritic `_trace` | 1436.68 | 22.97 | 6.38 | 62.5× | 16.59 ms |
| Synthetic `10M px` dendritic `_trace` | — | 148.33 | 61.61 | — | 86.72 ms |
| Synthetic `4M px` `normalize_direction` | 14.68 | 3.78 | 2.58 | 3.9× | 1.20 ms |
| GRIT `spain-mainstem` `_trace` (`3.86M` traced / `5.99M` px) | 5362.55 | 107.83 | 47.62 | 49.7× | 60.21 ms |
| GRIT `spain-mainstem` `normalize_direction` (`5.99M` px) | 66.18 | 6.03 | 3.89 | 11.0× | 2.14 ms |
| GRIT `chur-alpine-mainstem` `_trace` (`293k` traced / `0.48M` px) | 421.38 | 13.62 | 3.35 | 30.9× | 10.27 ms |
| GRIT `chur-alpine-mainstem` `normalize_direction` (`0.48M` px) | 4.60 | 0.37 | 0.45 | 12.3× | -0.08 ms |

#### Warm End-to-End `delineate(refine=True, merge=True)` Stage Breakdowns (median ms) & Peak RAM

*(Note: the `main_7eb9a04` baseline subprocess shimmed `metadata._mapping` in-process to accept `Mapping` because Track 1's deep-freeze change rejected `MappingProxyType` on GRIT `external_artifacts` before Track 3 fixed it.)*

| Basin | Configuration | `select_ms` | `merge_mask_ms` | `raster_read_ms` | `norm_ms` | `geom_mask_ms` | `trace_ms` | `shapes_ms` | `total_ms` | `tracemalloc_peak_mb` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `spain-mainstem` | `main_7eb9a04` | 32.12 | 41.18 | 90.69 | 68.41 | 7.98 | 5568.46 | 38.66 | 5837.80 | 130.04 |
| `spain-mainstem` | `track3_numpy` | 30.53 | 13.66 | 18.21 | 6.12 | 7.26 | 105.63 | 38.20 | **262.62** | **78.64** |
| `spain-mainstem` | `track3_rust_proto` | 30.13 | 13.90 | 15.31 | 3.12 | 7.65 | 49.87 | 37.82 | 203.73 | — |
| `spain-hillslope` | `main_7eb9a04` | 28.55 | 13.88 | 90.26 | 68.28 | 7.46 | 0.22 | 33.18 | 176.73 | 130.04 |
| `spain-hillslope` | `track3_numpy` | 29.05 | 13.83 | 18.58 | 6.18 | 7.37 | 3.56 | 31.60 | **105.79** | **78.64** |
| `spain-hillslope` | `track3_rust_proto` | 29.51 | 13.93 | 16.28 | 3.72 | 7.17 | 1.38 | 31.95 | 101.96 | — |
| `chur-alpine-mainstem` | `main_7eb9a04` | 22.84 | 1.54 | 7.77 | 4.62 | 0.66 | 420.83 | 3.74 | 467.48 | 14.17 |
| `chur-alpine-mainstem` | `track3_numpy` | 22.53 | 0.41 | 2.35 | 0.43 | 0.63 | 14.27 | 3.40 | **53.36** | **12.53** |
| `chur-alpine-mainstem` | `track3_rust_proto` | 22.36 | 0.35 | 2.22 | 0.29 | 0.62 | 3.67 | 3.36 | 42.18 | — |

### Limitations

- Cold raster download timing varies with public HTTPS latency; the warm end-to-end breakdown uses offline cached data across 3 runs (median reported) after 1 warmup call.
- `rhine-basel` (`7.5890, 47.5596`) exceeded the `>90s` cold COG download threshold and was excluded in favor of fast-warming `spain-hillslope`, `spain-mainstem` (`6.0M` pixels, `3.86M` traced), and `chur-alpine-mainstem`, supplemented by synthetic `1M` and `10M` dendritic pixel traces.

### Decision

Keep pure Python and NumPy (`Option A`, retaining `py3-none-any` wheels with no Rust extension in production). Track 3's vectorized NumPy/Shapely changes already cut `spain-mainstem` end-to-end runtime by `22.2×` (`5837.8 ms -> 262.6 ms`), `chur-alpine-mainstem` by `8.8×` (`467.5 ms -> 53.4 ms`), and peak Python memory allocations by `39.5%` (`130.04 -> 78.64 MiB`). Adding a native Rust extension (`maturin`/PyO3) would save only `3.8–58.9 ms` end-to-end (`1.29×` on `3.86M` traced cells) while adding multi-platform native wheel builds to CI and PyPI publishing.

## Track 4 — Rivers, Snapping, and Batch Scheduling Benchmark

### Workload and Setup

- Synthetic HFX 0.4.0 bundle (`500` rivers × `20` vertices = `9,500` segments, `500` catchments, `500` nodes, directed `upstream_ids`/`downstream_ids` chains of length 10).
- Compared `main` (`0e1d11a`) vs `track4` across:
  - Single-target `snap(target=Point(0.251, 1.051), onto=net, radius=2000)` (median of 5 warm runs).
  - Batch `delineate(target=30 points, snap_onto=net, radius=3000)` with prepared/reusable `_SnapIndex` and dynamic batch work queue (median of 3 warm runs).
  - `select_rivers(target=box(0.0, 1.0, 0.4, 1.11), include_downstream=True)` (median of 5 warm runs).

### Observed Results

| Operation | `main_0e1d11a` | `track4` | Speedup | Parity Check |
| --- | ---: | ---: | ---: | --- |
| `snap()` (1 target, `9,500` segments) | 492.33 ms | **7.69 ms** | **64.0×** | Exact snapped coords `[0.2495, 1.042]` |
| `delineate(snap_onto=...)` (`30` targets) | 15,398.81 ms | **66.54 ms** | **231.4×** | `30 / 30` targets succeeded |
| `select_rivers(include_downstream=True)` | 6.68 ms | **3.65 ms** | **1.83×** | `120 / 120` rivers selected |

## Track 5 — QGIS Export & Collective `source_arrow` Benchmark

### Workload and Setup

- Exported a `QGISBundle` containing `5,000` `Subbasin` features (scalar `id`, `area_km2`, `order`, `name` plus list column `tags`) and a `RiverNetwork` (`500` `ClippedRiver` features, `500` `SourceNode` points, `499` `downstream_ids` edges) across 3 runs (median wall time reported).
- Compared pre-Track-5 `main` (`68f02e4`) vs `track5`.

### Observed Results

| Metric | `main_68f02e4` | `track5` | Change |
| --- | ---: | ---: | --- |
| `QGISBundle.write()` wall time | 346.66 ms | **22.18 ms** | **15.6× faster** |
| `vectors.gpkg` file size | 8.113 MiB (`8,507,392 B`) | **1.598 MiB** (`1,675,264 B`) | **-80.3%** (`5.08×` smaller) |
| `source_arrow` SQLite rows | 6,001 | **4** | **1,500× fewer rows** (`features`, `relationships`, `nodes`) |
| `layer_0` / `river_connections` rows | 5,000 / 499 | 5,000 / 499 | Identical row counts |
