import pyarrow as pa
from candidate import take_rows

from pourpoint._selection import compact_dictionaries


def test_bounded_gather_preserves_values_metadata_and_duplicates() -> None:
    schema = pa.schema([pa.field("value", pa.binary(), metadata={b"unit": b"raw"})], metadata={b"source": b"test"})
    table = pa.Table.from_arrays([pa.chunked_array([[b"a", None], [b"c", b"d"]])], schema=schema)
    result = take_rows(table, [3, 0, 2, 1, 3])
    assert result["value"].to_pylist() == [b"d", b"a", b"c", None, b"d"]
    assert result.schema.equals(schema, check_metadata=True)
    assert result["value"].num_chunks == 2


def test_reduced_limit_keeps_large_payload_fallback() -> None:
    table = pa.table({"value": pa.chunked_array([[b"a" * 64, b"b" * 64], [b"c" * 64]])})
    result = take_rows(table, [2, 0, 1, 0, 1, 2], byte_limit=128)
    assert result["value"].to_pylist() == [b"c" * 64, b"a" * 64, b"b" * 64, b"a" * 64, b"b" * 64, b"c" * 64]
    assert all(chunk.buffers()[2].size <= 128 for chunk in result["value"].chunks)


def test_nested_ordered_dictionaries_use_unchanged_fallback() -> None:
    arrays = [
        pa.StructArray.from_arrays(
            [pa.DictionaryArray.from_arrays(pa.array([0, 1], type=pa.int8()), pa.array(values), ordered=True)],
            names=["label"],
        )
        for values in (["b", "a"], ["d", "c"])
    ]
    table = pa.table({"nested": pa.chunked_array(arrays)})
    result = compact_dictionaries(take_rows(table, [3, 0, 3, 1]))
    assert result["nested"].to_pylist() == [{"label": "c"}, {"label": "b"}, {"label": "c"}, {"label": "a"}]
    assert result.schema.equals(table.schema, check_metadata=True)
    assert take_rows(table.select([]), [3, 0, 3]).num_rows == 3


def test_nested_variable_width_and_numeric_values() -> None:
    table = pa.table(
        {
            "nested": pa.chunked_array(
                [
                    pa.array([[b"a" * 64], None], type=pa.list_(pa.binary())),
                    pa.array([[b"c" * 64, b"d" * 64]], type=pa.list_(pa.binary())),
                ]
            )
        }
    )
    result = take_rows(table, [2, 0, 2, 1], byte_limit=128)
    assert result["nested"].to_pylist() == [[b"c" * 64, b"d" * 64], [b"a" * 64], [b"c" * 64, b"d" * 64], None]
    assert result.schema.equals(table.schema, check_metadata=True)
    numeric = pa.table({"id": pa.chunked_array([[1, 2], [3, 4]])})
    assert take_rows(numeric, [3, 0, 3])["id"].to_pylist() == [4, 1, 4]
    assert take_rows(table, []).schema.equals(table.schema, check_metadata=True)
