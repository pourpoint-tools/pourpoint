"""One fresh-process Python union measurement; acquisition is outside this command."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import struct
import time
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry.base import BaseGeometry


def get_peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024.0 * 1024.0 if platform.system() == "Darwin" else 1024.0)


def read_wkb_bin(path: Path) -> tuple[list[int], list[bytes], float]:
    start = time.perf_counter()
    ids: list[int] = []
    values: list[bytes] = []
    with path.open("rb") as stream:
        while header := stream.read(12):
            if len(header) != 12:
                raise ValueError("Truncated record header")
            identifier, size = struct.unpack("<QI", header)
            value = stream.read(size)
            if len(value) != size:
                raise ValueError("Truncated WKB record")
            ids.append(identifier)
            values.append(value)
    return ids, values, time.perf_counter() - start


def decode_geometries(values: list[bytes]) -> tuple[list[BaseGeometry], float]:
    start = time.perf_counter()
    geometries = list(shapely.from_wkb(values))
    if any(g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon") for g in geometries):
        raise ValueError("Inputs must be nonempty polygons or multipolygons")
    return geometries, time.perf_counter() - start


def run_candidate(
    candidate: str,
    geometries: list[BaseGeometry],
    output_dir: Path,
    batch_size: int = 5000,
    ids: list[int] | None = None,
    wkb_list: list[bytes] | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    stages: dict[str, float] = {}
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if candidate in ("pourpoint_merge", "pourpoint_merge_release_wkb"):
        if ids is None or wkb_list is None or geometries:
            raise ValueError("merge requires source IDs/WKB and no predecoded geometries")
        t = time.perf_counter()
        import pyarrow as pa

        from pourpoint.features import Subbasin
        from pourpoint.refinement import merge

        stages["merge_import_seconds"] = time.perf_counter() - t
        t = time.perf_counter()
        table = pa.table({"id": pa.array(ids, type=pa.int64()), "geometry": pa.array(wkb_list, type=pa.binary())})
        subbasins = [Subbasin(table, i) for i in range(table.num_rows)]
        if candidate == "pourpoint_merge_release_wkb":
            wkb_list.clear()
        stages["wrapper_seconds"] = time.perf_counter() - t
        t = time.perf_counter()
        result = merge(subbasins=subbasins)
        stages["merge_decode_and_union_seconds"] = time.perf_counter() - t
    else:
        if candidate == "shapely_batched_union":
            t = time.perf_counter()
            intermediates = [
                shapely.union_all(geometries[i : i + batch_size]) for i in range(0, len(geometries), batch_size)
            ]
            stages["batches_seconds"] = time.perf_counter() - t
            t = time.perf_counter()
            result = shapely.union_all(intermediates)
            stages["final_union_seconds"] = time.perf_counter() - t
        elif candidate == "shapely_direct_union":
            t = time.perf_counter()
            result = shapely.union_all(geometries)
            stages["union_seconds"] = time.perf_counter() - t
        else:
            raise ValueError(f"Unknown candidate: {candidate}")
    t = time.perf_counter()
    valid = bool(result.is_valid)
    area = float(result.area)
    stages["validation_seconds"] = time.perf_counter() - t
    output_dir.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    output = output_dir / f"{candidate}.wkb"
    output.write_bytes(shapely.to_wkb(result))
    stages["write_seconds"] = time.perf_counter() - t
    record: dict[str, Any] = {
        "candidate": candidate,
        "status": "success",
        "stage_timings": stages,
        "candidate_seconds": time.perf_counter() - start,
        "peak_rss_mb": get_peak_rss_mb(),
        "is_valid": valid,
        "area": area,
        "wkb_file": str(output),
        "memory_scope": "fresh candidate process through output serialization; excludes external topology check",
    }
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()
    start = time.perf_counter()
    ids, values, read_seconds = read_wkb_bin(args.input)
    if args.candidate in ("pourpoint_merge", "pourpoint_merge_release_wkb"):
        geometries, decode_seconds = [], 0.0
    else:
        geometries, decode_seconds = decode_geometries(values)
        values.clear()
    record = run_candidate(args.candidate, geometries, args.output_dir, args.batch_size, ids, values)
    record.update(
        input_count=len(ids),
        read_seconds=read_seconds,
        decode_seconds=decode_seconds,
        total_seconds=time.perf_counter() - start,
        total_scope="input read through output write; interpreter/import startup recorded by matrix wall clock",
    )
    (args.output_dir / f"{args.candidate}.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
