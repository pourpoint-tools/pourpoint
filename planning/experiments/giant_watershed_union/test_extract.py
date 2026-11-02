"""Provenance checks on a tiny, independently specified selection."""

from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

from . import extract


def test_capture_preserves_selection_and_exact_geometry_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Fabric:
        manifest = SimpleNamespace(declarations={"fabric_name": "test"})

        def __init__(self, source: str) -> None:
            pass

        def __enter__(self) -> "Fabric":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def traverse(self, *args: object, **kwargs: object) -> list[int]:
            return [7, 8]

        def relationships(self, domain: str, ids: list[int], **kwargs: object) -> pa.Table:
            mapping = {7: [23, 24], 8: [24, 25]}
            return pa.table({"local_catchment_ids": [mapping[i] for i in ids]})

        def records(self, domain: str, ids: list[int], **kwargs: object) -> pa.Table:
            geom = shapely.from_wkt("POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0), (1 1, 1 2, 2 2, 2 1, 1 1))")
            return pa.table({"geometry": [shapely.to_wkb(geom) for _ in ids]})

    monkeypatch.setattr(extract, "HydroFabric", Fabric)
    result = extract.extract_kainji("test", tmp_path, seed_river_id=7, batch_size=1, max_catchments=1)
    assert result["observed_catchment_count"] == 3
    assert result["selected_catchment_count"] == 1
    assert result["truncated"] is True
    assert result["seed_local_catchment_ids"] == [23, 24]
    assert result["exact_vertex_count"] == 10
    assert result["hole_count"] == 1
    assert result["polygon_part_count"] == 1
    assert pq.read_table(tmp_path / "kainji_river_associations.parquet").to_pylist() == [
        {"river_id": 7, "local_catchment_ids": [23, 24]},
        {"river_id": 8, "local_catchment_ids": [24, 25]},
    ]
    assert (tmp_path / "kainji_manifest.json").exists()


@pytest.mark.parametrize("options", [{"batch_size": 0}, {"max_catchments": 0}, {"max_catchments": -1}])
def test_rejects_nonpositive_limits_before_acquisition(tmp_path: Path, options: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="positive"):
        extract.extract_kainji("unused", tmp_path, **options)
