"""Independent small expected outputs and reduced-limit payload safety."""

import pyarrow as pa
import pytest

import pourpoint._selection as candidate


def test_nested_interleaving_and_duplicates() -> None:
    schema = pa.schema(
        [
            pa.field("bbox", pa.struct([pa.field("x", pa.float32())]), metadata={b"unit": b"m"}),
            pa.field("links", pa.list_(pa.int64())),
            pa.field("geometry", pa.binary()),
        ],
        metadata={b"source": b"fixture"},
    )
    rows = [
        {"bbox": {"x": 1.0}, "links": [2, 3], "geometry": b"a"},
        {"bbox": None, "links": [], "geometry": b"bb"},
        {"bbox": {"x": 3.0}, "links": [4], "geometry": None},
    ]
    table = pa.concat_tables([pa.Table.from_pylist([r], schema=schema) for r in rows])
    result = candidate.take_rows(table, [2, 0, 2, 1])
    assert result.to_pylist() == [rows[2], rows[0], rows[2], rows[1]]
    assert result.schema.equals(schema, check_metadata=True)
    assert result["links"].num_chunks == 1


def test_shared_nested_buffers_and_duplicate_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(candidate, "_GATHER_BYTE_LIMIT", 128)
    chunk = pa.array([[b"x" * 40]], type=pa.list_(pa.binary()))
    for chunks, indices in [([chunk] * 2, [0, 1, 0, 1]), ([chunk] * 4, [0, 2, 1, 3, 0])]:
        table = pa.table({"value": pa.chunked_array(chunks)})
        selected = candidate.take_rows(table, indices)
        assert selected["value"].to_pylist() == [[b"x" * 40]] * len(indices)
        assert all(c.values.buffers()[2].size <= 128 for c in selected["value"].chunks)


def test_sliced_nested_values_and_dictionary_fallback() -> None:
    nested = pa.array([[b"unused" * 1000], [b"a"], None, [b"bb", b"c"]], type=pa.list_(pa.binary()))
    table = pa.table({"value": pa.chunked_array([nested.slice(1, 2), nested.slice(2, 2)])})
    selected = candidate.take_rows(table, [3, 0, 2, 1, 0])
    assert selected["value"].to_pylist() == [[b"bb", b"c"], [b"a"], None, None, [b"a"]]
    dictionaries = [
        pa.DictionaryArray.from_arrays(pa.array([0, 1], type=pa.int8()), pa.array(values), ordered=True)
        for values in [["b", "a"], ["d", "c"]]
    ]
    table = pa.table({"value": pa.chunked_array(dictionaries)})
    result = candidate.take_rows(table, [3, 0, 3, 1])
    assert result["value"].to_pylist() == ["c", "b", "c", "a"]
    assert result.schema.equals(table.schema, check_metadata=True)


def test_compact_dictionaries_handles_all_sliced_nested_arrow_types() -> None:
    dictionary = pa.DictionaryArray.from_arrays(
        pa.array([0, 1, 2, 1], type=pa.int8()),
        pa.array(["unused", "b", "c", "extra"]),
    )
    bbox_type = pa.struct([pa.field(name, pa.float32()) for name in ("xmin", "ymin", "xmax", "ymax")])
    bbox = pa.array(
        [
            {"xmin": 0.0, "ymin": 0.0, "xmax": 1.0, "ymax": 1.0},
            {"xmin": 1.0, "ymin": 1.0, "xmax": 2.0, "ymax": 2.0},
            {"xmin": 2.0, "ymin": 2.0, "xmax": 3.0, "ymax": 3.0},
            {"xmin": 3.0, "ymin": 3.0, "xmax": 4.0, "ymax": 4.0},
        ],
        type=bbox_type,
    ).slice(2, 2)
    struct_with_dict = pa.StructArray.from_arrays(
        [dictionary],
        fields=[pa.field("d", dictionary.type, metadata={b"m": b"1"})],
        mask=pa.array([False, True, False, False]),
    ).slice(1, 3)
    sparse_union = pa.Array.from_buffers(
        pa.sparse_union([pa.field("d", dictionary.type, metadata={b"k": b"v"}), pa.field("n", pa.int64())], [7, 9]),
        4,
        [None, pa.array([7, 9, 7, 9], type=pa.int8()).buffers()[1]],
        children=[dictionary, pa.array([10, 20, 30, 40])],
    ).slice(2, 2)
    dense_union = pa.Array.from_buffers(
        pa.dense_union([pa.field("d", dictionary.type, metadata={b"k": b"v"}), pa.field("n", pa.int64())], [7, 9]),
        4,
        [
            None,
            pa.array([7, 9, 7, 9], type=pa.int8()).buffers()[1],
            pa.array([0, 0, 1, 1], type=pa.int32()).buffers()[1],
        ],
        children=[dictionary.slice(1, 2), pa.array([10, 20])],
    ).slice(2, 2)
    list_arr = pa.ListArray.from_arrays([0, 1, 2, 3, 4], dictionary).slice(2, 2)
    large_list = pa.LargeListArray.from_arrays(pa.array([0, 1, 2, 3, 4], type=pa.int64()), dictionary).slice(2, 2)
    fixed_size_list = pa.FixedSizeListArray.from_arrays(dictionary, 2).slice(1, 1)
    map_arr = pa.MapArray.from_arrays([0, 1, 2, 3, 4], pa.array(["k0", "k1", "k2", "k3"]), dictionary).slice(2, 2)
    map_dict_keys = pa.MapArray.from_arrays(
        [0, 2, 3, 4],
        pa.DictionaryArray.from_arrays(pa.array([0, 1, 2, 3], type=pa.int8()), pa.array(["u0", "a", "b", "u3"])),
        pa.array([10, 20, 30, 40]),
    ).slice(1, 2)
    list_view = pa.Array.from_buffers(
        pa.list_view(dictionary.type),
        4,
        [
            None,
            pa.array([0, 1, 2, 3], type=pa.int32()).buffers()[1],
            pa.array([1, 1, 1, 1], type=pa.int32()).buffers()[1],
        ],
        children=[dictionary],
    ).slice(2, 2)
    large_list_view = pa.Array.from_buffers(
        pa.large_list_view(dictionary.type),
        4,
        [
            None,
            pa.array([0, 1, 2, 3], type=pa.int64()).buffers()[1],
            pa.array([1, 1, 1, 1], type=pa.int64()).buffers()[1],
        ],
        children=[dictionary],
    ).slice(2, 2)
    ree = pa.RunEndEncodedArray.from_arrays(pa.array([2, 4], type=pa.int32()), dictionary.slice(1, 2)).slice(1, 2)
    empty_dict = pa.DictionaryArray.from_arrays(pa.array([], type=pa.int8()), pa.array(["unused"]))
    null_dict = pa.DictionaryArray.from_arrays(pa.array([None, None], type=pa.int8()), pa.array(["unused"]))

    for name, arr in (
        ("bbox", bbox),
        ("struct_with_dict", struct_with_dict),
        ("sparse_union", sparse_union),
        ("dense_union", dense_union),
        ("list", list_arr),
        ("large_list", large_list),
        ("fixed_size_list", fixed_size_list),
        ("map", map_arr),
        ("map_dict_keys", map_dict_keys),
        ("list_view", list_view),
        ("large_list_view", large_list_view),
        ("ree", ree),
        ("empty_dict", empty_dict),
        ("null_dict", null_dict),
    ):
        table = pa.table({name: arr})
        compacted = candidate.compact_dictionaries(table)
        compacted[name].chunk(0).validate(full=True)
        assert compacted[name].to_pylist() == arr.to_pylist(), name
        assert compacted.schema.equals(table.schema, check_metadata=True), name

    plain = pa.table({"id": [1, 2], "bbox": bbox})
    assert candidate.compact_dictionaries(plain) is plain
