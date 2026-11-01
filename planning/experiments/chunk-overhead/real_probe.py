"""Bounded offline GRIT retrieval; cache path is supplied, never recorded."""

import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
from candidate import take_rows as candidate
from probe import requests, snapshot

import pourpoint.vectors as vectors
from pourpoint import HydroFabric


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--ids", type=Path, required=True)
    parser.add_argument("--mode", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--pattern", choices=["ordered", "reversed", "interleaved", "duplicates"], required=True)
    parser.add_argument("--operation", choices=["records", "relationships", "candidates"], required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.ids.read_text())
    original = vectors.take_rows
    if args.mode == "candidate":
        patch.object(vectors, "take_rows", candidate).start()
    ordered = config["ids"]
    ids = [ordered[i] for i in requests(len(ordered), args.pattern)]
    with HydroFabric(
        "https://basin-delineations-public.upstream.tech/grit/hfx-v0.4.0/", cache_dir=args.cache, offline=True
    ) as fabric:

        def retrieve() -> pa.Table:
            if args.operation == "candidates":
                return fabric.candidates("rivers", tuple(config["bounds"]), levels=[1])
            if args.operation == "relationships":
                return fabric.relationships("catchments", ids, columns=["id", "upstream_ids"])
            return fabric.records("catchments", ids)

        observations = []
        for repetition in range(4):
            before = snapshot()
            start = time.perf_counter()
            table = retrieve()
            elapsed = time.perf_counter() - start
            after = snapshot()
            observations.append(
                {
                    "repetition": repetition,
                    "seconds": elapsed,
                    "before": before,
                    "after": after,
                    "output_bytes": table.nbytes,
                    "rows": table.num_rows,
                    "chunks_per_column": [c.num_chunks for c in table.columns],
                }
            )
            if args.verify:
                vectors.take_rows = original
                expected = retrieve()
                assert table.equals(expected, check_metadata=True)
                if args.operation != "candidates":
                    assert table["id"].to_pylist() == ids
                patch.object(vectors, "take_rows", candidate if args.mode == "candidate" else original).start()
                del expected
            del table
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "pattern": args.pattern,
                    "operation": args.operation,
                    "verify": args.verify,
                    "observations": observations,
                    "settings": "offline GRIT v0.4.0; existing disk cache; records full columns, relationships id/upstream_ids, river candidates level 1; first query cold reader, next three warm",
                }
            )
        )


if __name__ == "__main__":
    main()
