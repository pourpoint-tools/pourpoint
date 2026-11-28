import pyarrow as pa
from shapely import LineString, to_wkb


def test_materialized_features_retain_arrow_attributes_and_exact_wkb() -> None:
    from pourpoint.features import River

    wkb = to_wkb(LineString([(0, 0), (1, 1)]), byte_order=0)
    schema = pa.schema(
        [
            pa.field("id", pa.int64()),
            pa.field("geometry", pa.binary()),
            pa.field("source_order", pa.int64(), metadata={b"meaning": b"source only"}),
            pa.field("tags", pa.list_(pa.string())),
        ],
        metadata={b"source": b"fixture"},
    )
    table = pa.Table.from_pylist([{"id": 7, "geometry": wkb, "source_order": None, "tags": ["a", "b"]}], schema=schema)
    river = River(table, 0)
    assert river.id == 7
    assert river.wkb == wkb
    assert river.geometry.equals(LineString([(0, 0), (1, 1)]))
    assert river.attributes["source_order"] is None
    assert river.attributes["tags"] == ["a", "b"]
    assert river.attributes.scalar("tags").type == pa.list_(pa.string())
    assert river.attributes.schema.field("source_order").metadata == {b"meaning": b"source only"}
    assert river.attributes.schema.metadata == {b"source": b"fixture"}
    assert list(river.attributes) == ["id", "source_order", "tags"]


def test_network_retains_omitted_neighbors_without_inventing_outlets() -> None:
    from pourpoint.features import River, RiverNetwork

    table = pa.table({"id": [2], "geometry": [to_wkb(LineString([(0, 0), (1, 1)]))]})
    relationships = pa.table({"id": [2], "upstream_ids": [[1]], "downstream_ids": [[3, 4]], "parent_ids": [[90]]})
    network = RiverNetwork([River(table, 0)], relationships, [], None, {"topology": "dag"})
    assert network.boundary_river_ids == frozenset({1, 3, 4})
    assert network.relationships["parent_ids"].to_pylist() == [[90]]
    assert network.geometry.equals(LineString([(0, 0), (1, 1)]))


def test_feature_single_row_equality_hashing_concise_repr_and_uncached_geometry() -> None:
    from pourpoint.features import River, SourceNode, Subbasin

    wkb_a = to_wkb(box_geom := LineString([(0, 0), (1, 0)]).buffer(0.1), byte_order=1)
    wkb_b = to_wkb(LineString([(2, 0), (3, 0)]).buffer(0.1), byte_order=1)
    assert box_geom is not None
    huge = "z" * 200
    t1 = pa.table(
        {
            "id": [1, 2],
            "geometry": [wkb_a, wkb_b],
            "name": ["alpha", huge],
            "unused": [10, 20],
        }
    ).replace_schema_metadata({b"m": b"1"})
    # Different backing table with row 0 matching row 1 of t1 at a different row offset and extra unused dict entry
    dict_col = pa.DictionaryArray.from_arrays(pa.array([0, 1], type=pa.int8()), pa.array(["unused", "ok"]))
    t2 = pa.table(
        {
            "id": [1, 99],
            "geometry": [wkb_a, wkb_b],
            "name": ["alpha", "other"],
            "unused": [10, 999],
        }
    ).replace_schema_metadata({b"m": b"1"})
    t_other_meta = t2.replace_schema_metadata({b"m": b"2"})
    t_dict1 = pa.table({"id": [1, 2], "geometry": [wkb_a, wkb_b], "d": dict_col})
    dict_col2 = pa.DictionaryArray.from_arrays(pa.array([0, 1], type=pa.int8()), pa.array(["other_unused", "ok"]))
    t_dict2 = pa.table({"id": [9, 2], "geometry": [wkb_b, wkb_b], "d": dict_col2})

    s1 = Subbasin(t1, 0)
    s2 = Subbasin(t2, 0)
    s_other_row = Subbasin(t1, 1)
    s_other_meta = Subbasin(t_other_meta, 0)

    # Single-row equality ignores other rows in backing table, respects metadata and type
    assert s1 == s2
    assert hash(s1) == hash(s2)
    assert len({s1, s2, s_other_row}) == 2
    assert s1 != s_other_row
    assert s1 != s_other_meta
    assert River(pa.table({"id": [1], "geometry": [to_wkb(LineString([(0, 0), (1, 1)]))]}), 0) != s1
    assert SourceNode(pa.table({"id": [1], "geometry": [to_wkb(LineString([(0, 0), (1, 1)]).centroid)]}), 0) != s1

    # Dictionary-backed rows with identical values match despite unused dictionary entries
    assert Subbasin(t_dict1, 1) == Subbasin(t_dict2, 1)

    # Concise repr never dumps backing Arrow tables
    assert repr(s1) == "Subbasin(id=1)"
    assert "z" * 100 not in repr(s_other_row)
    assert "alpha" not in repr(s1)

    # Geometry stays uncached across accesses
    assert s1.geometry is not s1.geometry

    # Attributes __contains__ O(1) & concise repr
    attrs = s_other_row.attributes
    assert "id" in attrs
    assert "name" in attrs
    assert "geometry" not in attrs
    assert "missing" not in attrs
    assert 123 not in attrs
    attrs_repr = repr(attrs)
    assert "Attributes(" in attrs_repr
    assert len(attrs_repr) < 200
