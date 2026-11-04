"""Measure public river selection, materialization and merge; no geometry policy changes.

Run with project ``uv run python``. Credentials stay in the named local AWS profile.
Validation and WKB writing happen after measured workflow phases.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import platform
import resource
import time
from pathlib import Path

import pyarrow as pa
import shapely

import pourpoint.refinement as refinement
from pourpoint import HydroFabric
from pourpoint.refinement import _polygonal, merge

BASELINE_REVISION = "a953638"


def peak_gib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024**3 if platform.system() == "Darwin" else 1024**2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", choices=["baseline", "integrated"], required=True)
    parser.add_argument("--profile", default="hetzner-pourpoint")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--materialize-chunk", type=int, default=0)
    args = parser.parse_args()
    provenance = json.loads(args.provenance.read_text())
    config = configparser.ConfigParser()
    config.read(Path.home() / ".aws/credentials")
    profile = config[args.profile]
    options = {
        "aws_access_key_id": profile["aws_access_key_id"],
        "aws_secret_access_key": profile["aws_secret_access_key"],
        "aws_endpoint": "https://fsn1.your-objectstorage.com",
        "aws_region": "fsn1",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    retained = []
    receipts = []
    start = time.perf_counter()
    with HydroFabric(provenance["source_url"], storage_options=options, cache_dir=args.cache) as fabric:
        open_seconds = time.perf_counter() - start
        manifest = dict(fabric.manifest.declarations)
        for repeat in range(args.repeats):
            phases = {}
            begin = time.perf_counter()
            t = begin
            rivers = fabric.traverse("rivers", [args.seed], relationship="upstream_ids")
            phases["traverse_seconds"] = time.perf_counter() - t
            t = time.perf_counter()
            relations = fabric.relationships("rivers", rivers, columns=["local_catchment_ids"])
            ids = list(
                dict.fromkeys(cid for row in relations["local_catchment_ids"].to_pylist() for cid in (row or []))
            )
            del relations
            phases["associations_seconds"] = time.perf_counter() - t
            t = time.perf_counter()
            if args.materialize_chunk:
                parts = [
                    part
                    for offset in range(0, len(ids), args.materialize_chunk)
                    for part in fabric.subbasins(ids[offset : offset + args.materialize_chunk])
                ]
            else:
                parts = fabric.subbasins(ids)
            phases["materialize_seconds"] = time.perf_counter() - t
            print(
                json.dumps(
                    {"stage": "materialized", "repeat": repeat + 1, "parts": len(parts), "phase_seconds": phases}
                ),
                flush=True,
            )
            t = time.perf_counter()
            if args.candidate == "baseline":
                geometry = _polygonal(shapely.union_all([part.geometry for part in parts]))
            else:
                geometry = merge(subbasins=parts)
            phases["merge_seconds"] = time.perf_counter() - t
            elapsed = time.perf_counter() - begin
            retained.append((parts, geometry))
            # Record high water before any serialization or correctness checks.
            receipt = {
                "candidate": args.candidate,
                "repeat": repeat + 1,
                "open_seconds": open_seconds,
                "phase_seconds": phases,
                "workflow_seconds": elapsed,
                "workflow_with_open_seconds": elapsed + (open_seconds if repeat == 0 else 0),
                "process_peak_gib": peak_gib(),
                "river_count": len(rivers),
                "part_count": len(parts),
                "retained_results": len(retained),
                "materialize_chunk": args.materialize_chunk,
                "id_sha256": hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
                "source": provenance["source_url"],
                "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                "baseline_revision": BASELINE_REVISION,
                "runtime_module": refinement.__file__,
                "working_directory": str(Path.cwd()),
                "vectors_sha256": hashlib.sha256(
                    (Path(refinement.__file__).parent / "vectors.py").read_bytes()
                ).hexdigest(),
                "fabric_sha256": hashlib.sha256(
                    (Path(refinement.__file__).parent / "fabric.py").read_bytes()
                ).hexdigest(),
                "implementation_source_sha256": hashlib.sha256(Path(refinement.__file__).read_bytes()).hexdigest(),
                "versions": {
                    "python": platform.python_version(),
                    "shapely": shapely.__version__,
                    "geos": shapely.geos_version_string,
                    "pyarrow": pa.__version__,
                },
                "machine": platform.uname()._asdict(),
                "scope": "river traverse -> associations -> materialized subbasins -> public merge; not delineate",
                "cache": "existing persistent cache; uncontrolled OS/network caches; same open reader across repeats",
            }
            receipts.append(receipt)
            (args.output / "measurement.json").write_text(json.dumps(receipts, indent=2) + "\n")
            print(json.dumps(receipt), flush=True)
        # Outputs and input checks are outside measured repeats. Keep every result
        # alive until all measurement finishes; never drop source parts to lower RSS.
        checks = []
        previous_buffers: set[int] = set()
        for index, (parts, geometry) in enumerate(retained, 1):
            digest = hashlib.sha256()
            for part in parts:
                digest.update(part.id.to_bytes(8, "little"))
                digest.update(part.wkb)
            (args.output / f"result-{index}.wkb").write_bytes(shapely.to_wkb(geometry))
            tables = {id(part.table): part.table for part in parts}
            buffers = {
                buffer.address
                for table in tables.values()
                for chunk in table["geometry"].chunks
                for buffer in chunk.buffers()
                if buffer is not None
            }
            checks.append(
                {
                    "repeat": index,
                    "source_id_wkb_sha256": digest.hexdigest(),
                    "source_table_count": len(tables),
                    "geometry_buffer_count": len(buffers),
                    "shared_geometry_buffers_with_previous": len(buffers & previous_buffers),
                }
            )
            previous_buffers = buffers
        (args.output / "input-checks.json").write_text(json.dumps(checks, indent=2) + "\n")


if __name__ == "__main__":
    main()
