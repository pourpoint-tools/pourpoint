"""Bounded nested gather prototype; not imported by production."""

import numpy as np
import pyarrow as pa
from baseline import take_rows as baseline

BYTE_LIMIT = 128 * 1024 * 1024


def supported(dtype: pa.DataType) -> bool:
    if pa.types.is_struct(dtype):
        return dtype.num_fields > 0 and all(supported(field.type) for field in dtype)
    if pa.types.is_list(dtype) or pa.types.is_large_list(dtype):
        return supported(dtype.value_type)
    return (
        pa.types.is_integer(dtype)
        or pa.types.is_floating(dtype)
        or pa.types.is_boolean(dtype)
        or pa.types.is_binary(dtype)
        or pa.types.is_string(dtype)
        or pa.types.is_large_binary(dtype)
        or pa.types.is_large_string(dtype)
    )


def take_rows(table: pa.Table, indices: list[int]) -> pa.Table:
    if not indices or not table.num_columns:
        return baseline(table, indices)
    if all(
        pa.types.is_integer(f.type) or pa.types.is_floating(f.type) or pa.types.is_boolean(f.type) for f in table.schema
    ):
        return baseline(table, indices)
    if (
        not all(supported(f.type) for f in table.schema)
        or table.nbytes > BYTE_LIMIT
        or table.get_total_buffer_size() > BYTE_LIMIT
    ):
        return baseline(table, indices)
    multiplicity = int(np.bincount(indices, minlength=table.num_rows).max())
    if table.nbytes * multiplicity > BYTE_LIMIT:
        return baseline(table, indices)
    return table.take(pa.array(indices, type=pa.int64()))
