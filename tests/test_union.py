from pathlib import Path

import pytest
from spatial_fixture import make_spatial_bundle

from pourpoint.fabric import HydroFabric


def test_materialized_subbasins_keep_physical_addresses_after_closure(tmp_path: Path) -> None:
    with HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric:
        parts = fabric.subbasins([30, 10, 30])
    assert [part.id for part in parts] == [30, 10, 30]
    assert [part.row for part in parts] == [0, 1, 2]
    assert [part._source_address for part in parts] == [(0, 0), (0, 1), (0, 0)]


def test_merge_bounds_decoding_and_preserves_supplied_parts() -> None:
    import threading
    import weakref

    import pyarrow as pa
    from shapely.geometry import box
    from shapely.geometry.base import BaseGeometry

    from pourpoint.features import Subbasin
    from pourpoint.refinement import SubbasinContribution, merge

    live: list[weakref.ReferenceType[BaseGeometry]] = []
    peak = 0
    lock = threading.Lock()

    class TrackedSubbasin(Subbasin):
        @property
        def geometry(self) -> BaseGeometry:
            nonlocal peak
            geometry = super().geometry
            with lock:
                live.append(weakref.ref(geometry))
                peak = max(peak, sum(ref() is not None for ref in live))
            return geometry

    count = 10001
    table = pa.table({"id": list(range(1, count + 1)), "geometry": [box(i, 0, i + 1, 1).wkb for i in range(count)]})
    parts = [TrackedSubbasin(table, i) for i in range(count)]
    # A contribution must use its actual clipped geometry, not its source footprint.
    supplied = [*parts, SubbasinContribution(parts[-1], box(count, 0, count + 0.5, 1))]
    result = merge(subbasins=supplied)
    assert result.is_valid
    assert result.equals(box(0, 0, count + 0.5, 1))
    assert peak <= 10000
    assert [part.row for part in parts] == list(range(count))
    assert all(ref() is None for ref in live)


def test_concurrent_merge_workspaces_and_error_release(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    import pyarrow as pa
    from shapely.geometry import box
    from shapely.geometry.base import BaseGeometry

    from pourpoint import _union
    from pourpoint.features import Subbasin
    from pourpoint.refinement import merge

    monkeypatch.setattr(_union, "_GROUP_SIZE", 2)
    entered = Event()
    release = Event()
    state_lock = Lock()
    decoded = 0

    class PausedSubbasin(Subbasin):
        @property
        def geometry(self) -> BaseGeometry:
            nonlocal decoded
            with state_lock:
                decoded += 1
                if decoded == 2:
                    entered.set()
            assert release.wait(5)
            return super().geometry

    table = pa.table({"id": [1, 2, 3], "geometry": [box(i, 0, i + 1, 1).wkb for i in range(3)]})
    parts = [PausedSubbasin(table, i) for i in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(merge, subbasins=parts) for _ in range(3)]
        try:
            assert entered.wait(5)
            assert decoded == 2
        finally:
            release.set()
        for future in futures:
            assert future.result(timeout=5).equals(box(0, 0, 3, 1))
    invalid = pa.table({"id": [4], "geometry": [None]})
    with pytest.raises(ValueError, match="missing geometry"):
        merge(subbasins=[*parts, Subbasin(invalid, 0)])
    assert merge(subbasins=parts).equals(box(0, 0, 3, 1))


def test_merge_groups_by_source_address_without_reordering_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Sequence
    from threading import Lock

    from shapely.geometry import box
    from shapely.geometry.base import BaseGeometry

    from pourpoint import _union
    from pourpoint.refinement import BasinPart, SubbasinContribution, merge

    with HydroFabric(make_spatial_bundle(tmp_path / "fabric")) as fabric:
        parts = fabric.subbasins([20, 10, 40, 30])
    supplied = [parts[0], SubbasinContribution(parts[1], box(1, 1, 1.125, 1.25)), *parts[2:]]
    groups: list[list[int]] = []
    lock = Lock()
    original = _union._union_group

    def capture(group: Sequence[BasinPart]) -> BaseGeometry:
        with lock:
            groups.append([part.id for part in group])
        return original(group)

    monkeypatch.setattr(_union, "_GROUP_SIZE", 2)
    monkeypatch.setattr(_union, "_union_group", capture)
    result = merge(subbasins=supplied)
    assert sorted(groups) == [[30, 10], [40, 20]]
    assert [part.id for part in supplied] == [20, 10, 40, 30]
    assert result.is_valid
    assert result.area == pytest.approx(3 * 0.25**2 + 0.125 * 0.25)
    assert supplied[1].source is parts[1]
    assert parts[1].wkb == box(1, 1, 1.25, 1.25).wkb
