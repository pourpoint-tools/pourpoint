"""Small real delineate workflows on the independent repository HFX fixture.

These timings check small-workload practicality, not large-basin performance.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import shapely
from rasterio.transform import from_origin
from shapely.geometry import Point, box

import pourpoint.refinement as refinement
from pourpoint import HydroFabric, delineate, merge


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=["baseline", "integrated"], required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
    from conftest import add_pair
    from spatial_fixture import make_spatial_bundle

    if args.candidate == "baseline":
        refinement.union_subbasins = lambda parts: shapely.union_all([part.geometry for part in parts])
    receipts = []
    with TemporaryDirectory() as temporary:
        source = make_spatial_bundle(Path(temporary) / "source")
        manifest_path = source / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["auxiliary"] = []
        manifest_path.write_text(json.dumps(manifest))
        add_pair(
            source,
            direction=np.array([[0]], dtype="uint8"),
            accumulation=np.array([[1]], dtype="int32"),
            transform=from_origin(1, 1.25, 0.25, 0.25),
        )
        retained = []
        with HydroFabric(source, cache=False) as fabric:
            for refined in (False, True):
                for workers in (1, 2):
                    start = time.perf_counter()
                    for _ in range(10):
                        result = delineate(
                            fabric=fabric,
                            target={"a": Point(1.125, 1.125), "b": Point(1.125, 1.125)},
                            refine=refined,
                            window_size=1,
                            n_jobs=workers,
                        )
                        retained.append(result)
                    elapsed = time.perf_counter() - start
                    receipts.append(
                        {
                            "candidate": args.candidate,
                            "refine": refined,
                            "n_jobs": workers,
                            "repeats": 10,
                            "seconds": elapsed,
                            "retained_batch_results": len(retained),
                        }
                    )
        # Materialized source/derived geometry and standalone merge survive closure.
        for batch in retained:
            assert not batch.issues
            assert [target.name for target in batch.targets] == ["a", "b"]
            for result in batch.results:
                assert result is not None
                assert result.geometry.is_valid and result.geometry.equals(box(1, 1, 1.25, 1.25))
                assert merge(subbasins=result.subbasins).equals(result.geometry)
                assert result.subbasins[0].id == 10
        print(
            json.dumps(
                {
                    "measurements": receipts,
                    "independent_expected_geometry": "box(1,1,1.25,1.25)",
                    "valid_and_equal": True,
                    "materialized_merge_after_close": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
