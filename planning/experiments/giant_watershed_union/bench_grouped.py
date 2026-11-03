"""Fresh-process spatially grouped GEOS experiment; not a production union path."""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry.base import BaseGeometry

from .bench_python import decode_geometries, get_peak_rss_mb, read_wkb_bin
from .run_matrix import sha256


def order_indices(
    values: list[bytes],
    ids: list[int],
    order: str,
    batch_size: int,
    physical_ids: list[int] | None,
    timings: dict[str, float] | None = None,
) -> list[int]:
    """Order a selected subset without changing geometry or treating groups as pre-unions."""
    if len(ids) != len(values) or len(set(ids)) != len(ids):
        raise ValueError("Input IDs must be unique and match geometry count")
    indices = list(range(len(ids)))
    if order == "input":
        return indices
    if order == "physical":
        if physical_ids is None or len(set(physical_ids)) != len(physical_ids):
            raise ValueError("Physical IDs must be supplied and unique")
        positions = {identifier: position for position, identifier in enumerate(physical_ids)}
        if not set(ids) <= positions.keys():
            raise ValueError("Physical mapping is missing selected IDs")
        return sorted(indices, key=lambda i: positions[ids[i]])
    if order != "bbox":
        raise ValueError(f"Unknown order: {order}")
    preparation_start = time.perf_counter()
    centers: list[tuple[float, float]] = []
    for offset in range(0, len(values), batch_size):
        geometries, _ = decode_geometries(values[offset : offset + batch_size])
        centers.extend(((g.bounds[0] + g.bounds[2]) / 2, (g.bounds[1] + g.bounds[3]) / 2) for g in geometries)
        del geometries
    if timings is not None:
        timings["bbox_decode_bounds_seconds"] = time.perf_counter() - preparation_start
    # STR-style x slices, then y within each slice; no quantization or geometry changes.
    slice_count = max(1, math.ceil(math.sqrt(math.ceil(len(ids) / batch_size))))
    slice_size = max(1, math.ceil(len(ids) / slice_count))
    indices.sort(key=lambda i: (centers[i][0], centers[i][1], ids[i]))
    return [
        i
        for start in range(0, len(ids), slice_size)
        for i in sorted(indices[start : start + slice_size], key=lambda i: (centers[i][1], centers[i][0], ids[i]))
    ]


def grouped_union(
    values: list[bytes],
    *,
    batch_size: int,
    workers: int,
    retain_inputs: bool,
) -> tuple[BaseGeometry, dict[str, float]]:
    """Union bounded waves of tasks, then combine in group order.

    Decode only in worker tasks. Release mode consumes the caller's WKB list. Retain mode
    keeps those references through final union. GEOS/allocator RSS need not fall.
    At most ``workers`` group futures exist; all intermediates remain for final union.
    """
    if batch_size < 1 or workers < 1:
        raise ValueError("batch_size and workers must be positive")
    start = time.perf_counter()
    pending = list(reversed(values))
    if not retain_inputs:
        values.clear()
    intermediates: list[BaseGeometry] = []
    decode_task_seconds = 0.0
    union_task_seconds = 0.0

    def union_group(group: list[bytes]) -> tuple[BaseGeometry, float, float]:
        geometries, decode_seconds = decode_geometries(group)
        t = time.perf_counter()
        result = shapely.union_all(geometries)
        return result, decode_seconds, time.perf_counter() - t

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            futures = []
            for _ in range(workers):
                if not pending:
                    break
                group = [pending.pop() for _ in range(min(batch_size, len(pending)))]
                futures.append(pool.submit(union_group, group))
                del group
            for future in futures:
                geometry, decode_seconds, union_seconds = future.result()
                intermediates.append(geometry)
                decode_task_seconds += decode_seconds
                union_task_seconds += union_seconds
            del future, geometry
            del futures
    group_seconds = time.perf_counter() - start
    start = time.perf_counter()
    result = shapely.union_all(intermediates)
    return result, {
        "group_union_seconds": group_seconds,
        "final_union_seconds": time.perf_counter() - start,
        "decode_task_seconds_sum": decode_task_seconds,
        "union_task_seconds_sum": union_task_seconds,
    }


def read_physical_order(path: Path, input_hash: str, ids: list[int]) -> list[int]:
    metadata = json.loads(path.read_text())
    physical_ids = metadata["ordered_ids"]
    if metadata["input_sha256"] != input_hash:
        raise ValueError("Physical mapping input hash mismatch")
    if (
        not isinstance(physical_ids, list)
        or any(type(i) is not int for i in physical_ids)
        or len(physical_ids) != len(ids)
        or set(physical_ids) != set(ids)
    ):
        raise ValueError("Physical mapping must contain every captured ID exactly once")
    return physical_ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Must not exist")
    parser.add_argument("--order", choices=["input", "bbox", "physical"], default="bbox")
    parser.add_argument("--physical-order", type=Path)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--release-inputs", action="store_true", help="Kernel-only: release consumed source WKB")
    args = parser.parse_args()
    if min(args.batch_size, args.workers) < 1:
        parser.error("batch size and workers must be positive")
    if (args.order == "physical") != (args.physical_order is not None):
        parser.error("--physical-order is required only with --order physical")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    record: dict[str, Any] = {
        "status": "running",
        "python": sys.version,
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
        "machine": platform.uname()._asdict(),
        "input_sha256": sha256(args.input),
        "settings": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "memory_scope": "fresh process high-water through output write, includes hashing; excludes external topology check",
        "timing_scope": "input read through output write; excludes input hashing, imports and external topology check",
        "source_sha256": sha256(Path(__file__)),
    }
    receipt = args.output_dir / "grouped.json"
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    try:
        start = time.perf_counter()
        ids, values, read_seconds = read_wkb_bin(args.input)
        t = time.perf_counter()
        physical_ids = None
        if args.physical_order:
            physical_ids = read_physical_order(args.physical_order, record["input_sha256"], ids)
            record["physical_order_sha256"] = sha256(args.physical_order)
        preparation_timings: dict[str, float] = {}
        indices = order_indices(values, ids, args.order, args.batch_size, physical_ids, preparation_timings)
        record["preparation_timings"] = preparation_timings
        values = [values[i] for i in indices]
        grouping_seconds = time.perf_counter() - t
        result, stages = grouped_union(
            values, batch_size=args.batch_size, workers=args.workers, retain_inputs=not args.release_inputs
        )
        t = time.perf_counter()
        record.update(is_valid=bool(result.is_valid), area=float(result.area))
        stages["validation_seconds"] = time.perf_counter() - t
        t = time.perf_counter()
        (args.output_dir / "grouped.wkb").write_bytes(shapely.to_wkb(result))
        stages["write_seconds"] = time.perf_counter() - t
        record["task_timings"] = {key: stages.pop(key) for key in ("decode_task_seconds_sum", "union_task_seconds_sum")}
        stages.update(read_seconds=read_seconds, grouping_seconds=grouping_seconds)
        record.update(
            status="success",
            input_count=len(ids),
            stage_timings=stages,
            total_seconds=time.perf_counter() - start,
            peak_rss_mb=get_peak_rss_mb(),
        )
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}", peak_rss_mb=get_peak_rss_mb())
        raise
    finally:
        receipt.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
