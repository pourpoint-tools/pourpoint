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
