import pytest


@pytest.mark.parametrize("path", [".", "a/../b", "%2e%2e/x", "a\\b", "a%5cb", "a//b", "a/./b", "a%00b"])
def test_artifact_paths_reject_ambiguous_or_escaping_names(path: str) -> None:
    from pourpoint.metadata import artifact_path

    with pytest.raises(ValueError):
        artifact_path(path)


def test_empty_provenance_artifact_fingerprint_is_valid() -> None:
    from pourpoint.metadata import Fingerprint

    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert (
        Fingerprint.parse({"size_bytes": 0, "sha256": digest, "head_sha256": digest, "tail_sha256": digest}).size_bytes
        == 0
    )


def test_bounds_numpy_scalar_and_array_support() -> None:
    import numpy as np

    from pourpoint.metadata import bounds

    # NumPy scalars in tuples and lists
    assert bounds((np.float64(-10.0), np.float32(50.0), np.float64(-5.0), np.float32(55.0))) == (
        -10.0,
        50.0,
        -5.0,
        55.0,
    )
    assert bounds([np.int64(-10), 50.0, np.float64(-5.0), 55]) == (-10.0, 50.0, -5.0, 55.0)

    # 1D NumPy arrays
    assert bounds(np.array([-10.0, 50.0, -5.0, 55.0], dtype=np.float64)) == (-10.0, 50.0, -5.0, 55.0)
    assert bounds(np.array([-10, 50, -5, 55], dtype=np.int32)) == (-10.0, 50.0, -5.0, 55.0)


def test_bounds_rejections_and_boundaries() -> None:
    import numpy as np

    from pourpoint.metadata import bounds

    # Rejection of booleans
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds((True, 50.0, -5.0, 55.0))
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds([np.bool_(True), 50.0, -5.0, 55.0])
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds(np.array([True, False, True, False]))

    # Rejection of strings and complex numbers
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds(("-10.0", 50.0, -5.0, 55.0))
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds(np.array(["-10", "50", "-5", "55"]))
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds((1 + 2j, 2, 3, 4))
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds(np.array([1 + 2j, 2, 3, 4]))

    # Rejection of non-finite numbers
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds((np.nan, 50.0, -5.0, 55.0))
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds((np.inf, 50.0, -5.0, 55.0))
    with pytest.raises(ValueError, match="bbox must be finite numbers"):
        bounds(np.array([-10.0, 50.0, np.nan, 55.0]))

    # Rejection of multidimensional arrays and arbitrary iterators
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds(np.array([[-10.0, 50.0], [-5.0, 55.0]]))
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds(np.zeros((1, 4)))
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds(np.zeros((4, 1)))
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds(np.array(1.0))
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds(iter([-10.0, 50.0, -5.0, 55.0]))
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds({-10.0, 50.0, -5.0, 55.0})
    with pytest.raises(ValueError, match="bbox must contain four coordinates"):
        bounds(np.array([-10.0, 50.0, -5.0]))

    # Ordering validation
    with pytest.raises(ValueError, match="bbox must be ordered"):
        bounds(np.array([10.0, 50.0, 5.0, 55.0]))


def spatial_manifest_data() -> dict[str, object]:
    fingerprint = {"size_bytes": 100, "sha256": "a" * 64, "head_sha256": "b" * 64, "tail_sha256": "c" * 64}
    families = {
        name: {
            "data": f"{name}.parquet",
            "data_sha256": "a" * 64,
            "lookup": f"{name}.arrow",
            "directory": f"{name}.json",
            "count": 3,
        }
        for name in ("catchments", "rivers", "river_nodes")
    }
    return {
        "format_version": "0.4.0",
        "representation": "spatial-geoparquet-adjacency",
        "representation_version": 1,
        "crs": "EPSG:4326",
        "families": families,
        "relationships": {},
        "auxiliary": [],
        "artifacts": {
            path: fingerprint.copy()
            for family in families.values()
            for key, path in family.items()
            if key in ("data", "lookup", "directory")
        },
        "source_caveat": "unknown connectivity",
    }


def test_spatial_manifest_selects_representation_and_binds_addresses() -> None:
    from pourpoint.metadata import SpatialManifest

    data = spatial_manifest_data()
    manifest = SpatialManifest.parse(data)
    assert manifest.families["rivers"].data == "rivers.parquet"
    assert manifest.declarations["source_caveat"] == "unknown connectivity"
    data["representation"] = "ipc"
    with pytest.raises(ValueError, match="representation"):
        SpatialManifest.parse(data)
    data = spatial_manifest_data()
    data["families"]["rivers"]["data_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="binding"):
        SpatialManifest.parse(data)


def test_spatial_relationship_binding_uses_target_domain_not_source_domain():
    from pourpoint.metadata import SpatialManifest

    data = spatial_manifest_data()
    fingerprints = data["artifacts"]
    fingerprints["relations.arrow"] = fingerprints["rivers.arrow"].copy()
    fingerprints["relations.json"] = fingerprints["rivers.json"].copy()
    data["relationships"] = {
        "catchments": {
            "data": "relations.arrow",
            "directory": "relations.json",
            "count": 3,
            "columns": {"local_river_ids": {"source_domain": "rivers", "target_domain": "rivers"}},
            "feature_artifacts": {
                name: {"data": data["families"][name]["data"], "data_sha256": "a" * 64}
                for name in ("rivers", "catchments")
            },
        }
    }
    assert SpatialManifest.parse(data).relationships["catchments"]["count"] == 3
    data["relationships"]["catchments"]["feature_artifacts"]["rivers"]["data_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="binding"):
        SpatialManifest.parse(data)
