"""Independent geometry and scheduling checks for grouped union experiments."""

from pathlib import Path

import pytest
import shapely
from shapely.geometry import box

from planning.experiments.giant_watershed_union.bench_grouped import grouped_union, order_indices


def test_spatial_order_and_physical_subset() -> None:
    geometries = [box(x, y, x + 1, y + 1) for x, y in [(10, 10), (0, 0), (10, 0), (0, 10)]]
    assert order_indices([g.wkb for g in geometries], [40, 10, 30, 20], "bbox", 2, None) == [1, 3, 2, 0]
    assert order_indices([g.wkb for g in geometries], [40, 10, 30, 20], "physical", 2, [99, 20, 30, 10, 40]) == [
        3,
        2,
        1,
        0,
    ]
    with pytest.raises(ValueError, match="missing"):
        order_indices([g.wkb for g in geometries], [40, 10, 30, 20], "physical", 2, [20, 30, 10])


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("retain", [False, True])
def test_grouped_union_preserves_hole_overlap_and_disconnected_part(workers: int, retain: bool) -> None:
    frame = shapely.geometry.Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]])
    geometries = [frame, box(3, 0, 5, 4), box(10, 0, 11, 1)]
    expected = shapely.geometry.MultiPolygon(
        [
            shapely.geometry.Polygon([(0, 0), (5, 0), (5, 4), (0, 4)], holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]]),
            box(10, 0, 11, 1),
        ]
    )
    values = [geometry.wkb for geometry in geometries]
    result, stages = grouped_union(values, batch_size=1, workers=workers, retain_inputs=retain)
    assert result.is_valid and result.equals(expected)
    assert len(values) == (3 if retain else 0)
    assert stages["group_union_seconds"] >= 0
    assert stages["final_union_seconds"] >= 0


@pytest.mark.parametrize("order", ["input", "bbox", "physical"])
def test_cli_multipart_and_bound_mapping(tmp_path: Path, order: str) -> None:
    import json
    import subprocess
    import sys

    from planning.experiments.giant_watershed_union.run_matrix import sha256
    from planning.experiments.giant_watershed_union.smoke_inputs import write_fixture

    inputs = tmp_path / "input.bin"
    write_fixture(inputs)
    output = tmp_path / "result"
    command = [
        sys.executable,
        "-m",
        "planning.experiments.giant_watershed_union.bench_grouped",
        "--input",
        str(inputs),
        "--output-dir",
        str(output),
        "--order",
        order,
        "--workers",
        "2",
        "--batch-size",
        "1",
    ]
    if order == "physical":
        sidecar = tmp_path / "physical.json"
        sidecar.write_text(json.dumps({"input_sha256": sha256(inputs), "ordered_ids": [300, 100]}))
        command += ["--physical-order", str(sidecar)]
    subprocess.run(command, check=True)
    result = shapely.from_wkb((output / "grouped.wkb").read_bytes())
    expected = shapely.geometry.MultiPolygon(
        [
            shapely.geometry.Polygon([(0, 0), (5, 0), (5, 4), (0, 4)], holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]]),
            box(10, 0, 11, 1),
        ]
    )
    assert result.is_valid and result.equals(expected)
    record = json.loads((output / "grouped.json").read_text())
    assert record["status"] == "success"
    assert record["input_count"] == 2
    assert record["geos"] == shapely.geos_version_string
    assert record["total_seconds"] >= sum(record["stage_timings"].values())
    assert subprocess.run(command, capture_output=True).returncode != 0


@pytest.mark.parametrize("mapping,hash_value", [([100, 100], "abc"), ([100], "abc"), ([100, 300], "wrong")])
def test_mapping_rejects_wrong_identity(tmp_path: Path, mapping: list[int], hash_value: str) -> None:
    import json

    from planning.experiments.giant_watershed_union.bench_grouped import read_physical_order

    sidecar = tmp_path / "mapping.json"
    sidecar.write_text(json.dumps({"input_sha256": hash_value, "ordered_ids": mapping}))
    with pytest.raises(ValueError):
        read_physical_order(sidecar, "abc", [100, 300])


def test_merge_memory_control_releases_only_caller_wkb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from planning.experiments.giant_watershed_union.bench_python import run_candidate

    values = [box(0, 0, 1, 1).wkb, box(1, 0, 2, 1).wkb]
    from shapely.geometry.base import BaseGeometry

    from pourpoint import refinement
    from pourpoint.features import Subbasin

    original_merge = refinement.merge

    def checked_merge(*, subbasins: list[Subbasin]) -> BaseGeometry:
        assert values == []
        assert subbasins[0].geometry.equals(box(0, 0, 1, 1))
        assert subbasins[1].geometry.equals(box(1, 0, 2, 1))
        return original_merge(subbasins=subbasins)

    monkeypatch.setattr(refinement, "merge", checked_merge)
    record = run_candidate("pourpoint_merge_release_wkb", [], tmp_path, ids=[1, 2], wkb_list=values)
    assert values == []
    assert record["is_valid"]
    assert shapely.from_wkb(Path(record["wkb_file"]).read_bytes()).equals(box(0, 0, 2, 1))
