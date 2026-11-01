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
