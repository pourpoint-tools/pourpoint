"""Fresh run directories and subprocess failure evidence."""

import json
import sys
from pathlib import Path

import pytest

from planning.experiments.giant_watershed_union.run_matrix import execute


def test_failed_run_cannot_reuse_output(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    record = execute([sys.executable, "-c", "raise SystemExit(7)"], output, {"input_sha256": "abc"})
    assert record["status"] == "failed"
    assert record["returncode"] == 7
    assert json.loads((output / "execution.json").read_text())["input_sha256"] == "abc"
    with pytest.raises(FileExistsError):
        execute([sys.executable, "-c", "pass"], output, {})


def test_python_matrix_holes_multipart(tmp_path: Path) -> None:
    import subprocess

    import shapely
    from shapely.geometry import MultiPolygon, Polygon, box

    from planning.experiments.giant_watershed_union.smoke_inputs import write_fixture

    inputs = tmp_path / "input.bin"
    write_fixture(inputs)
    results = tmp_path / "results"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "planning.experiments.giant_watershed_union.run_matrix",
            "--input",
            str(inputs),
            "--results-dir",
            str(results),
            "--skip-rust",
            "--repeats",
            "2",
            "--batch-size",
            "1",
        ],
        check=True,
    )
    records = json.loads((results / "matrix_summary.json").read_text())
    assert len(records) == 6
    expected = MultiPolygon(
        [
            Polygon([(0, 0), (5, 0), (5, 4), (0, 4), (0, 0)], holes=[[(1, 1), (1, 3), (3, 3), (3, 1), (1, 1)]]),
            box(10, 0, 11, 1),
        ]
    )
    for record in records:
        assert record["status"] == "success"
        assert record["is_valid"] is True
        assert record["agreement_status"] == "equal"
        actual = shapely.from_wkb(Path(record["measurement"]["wkb_file"]).read_bytes())
        assert actual.equals(expected)
        assert record["measurement"]["input_count"] == 2
