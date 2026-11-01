"""Experimental selection only; never imported by production code."""

import pyarrow as pa

from pourpoint._selection import take_rows as baseline


def take_rows(table: pa.Table, indices: list[int], *, byte_limit: int = 64 * 1024 * 1024) -> pa.Table:
    # Deliberately narrow: nested, dictionary and extension values retain the
    # proven baseline rather than relying on logical bytes to bound child buffers.
    if not indices or not table.num_columns:
        return baseline(table, indices)
    numeric = all(
        pa.types.is_integer(field.type) or pa.types.is_floating(field.type) or pa.types.is_boolean(field.type)
        for field in table.schema
    )
    supported = all(
        pa.types.is_integer(field.type)
        or pa.types.is_floating(field.type)
        or pa.types.is_boolean(field.type)
        or pa.types.is_binary(field.type)
        or pa.types.is_string(field.type)
        for field in table.schema
    )
    # Retained buffers catch large backing slices; logical bytes count shared
    # chunks repeatedly, bounding the payload of a distinct-row gather.
    if numeric or not supported or table.get_total_buffer_size() > byte_limit or table.nbytes > byte_limit:
        return baseline(table, indices)
    pieces: list[pa.Table] = []
    start = 0
    while start < len(indices):
        seen: set[int] = set()
        stop = start
        while stop < len(indices) and indices[stop] not in seen:
            seen.add(indices[stop])
            stop += 1
        pieces.append(table.take(pa.array(indices[start:stop], type=pa.int64())))
        start = stop
    return pa.concat_tables(pieces)
