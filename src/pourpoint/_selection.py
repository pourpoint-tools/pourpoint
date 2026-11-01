"""Chunk-preserving Arrow selection and release of unused dictionary values."""

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc


def _compact_dictionary_values(array: pa.Array) -> pa.Array:
    if isinstance(array, pa.DictionaryArray):
        # Sorted original indices preserve the meaning of ordered dictionaries.
        used = pc.call_function("unique", [pc.call_function("drop_null", [array.indices])])
        used = pc.take(used, pc.call_function("array_sort_indices", [used]))
        indices = pc.cast(
            pc.call_function("index_in", [array.indices], options=pc.SetLookupOptions(used)), array.type.index_type
        )
        values = _compact_dictionary_values(pc.take(array.dictionary, used))
        return pa.DictionaryArray.from_arrays(indices, values, ordered=array.type.ordered)
    if isinstance(array, pa.ExtensionArray):
        return pa.ExtensionArray.from_storage(array.type, _compact_dictionary_values(array.storage))
    children: list[pa.Array]
    if pa.types.is_struct(array.type) or pa.types.is_union(array.type):
        children = [array.field(i) for i in range(array.type.num_fields)]
    elif (
        pa.types.is_list(array.type)
        or pa.types.is_large_list(array.type)
        or pa.types.is_fixed_size_list(array.type)
        or pa.types.is_map(array.type)
        or pa.types.is_list_view(array.type)
        or pa.types.is_large_list_view(array.type)
    ):
        children = [array.values]
    elif pa.types.is_run_end_encoded(array.type):
        children = [array.run_ends, array.values]
    else:
        return array
    return pa.Array.from_buffers(
        array.type,
        len(array),
        array.buffers()[: array.type.num_buffers],
        null_count=array.null_count,
        offset=array.offset,
        children=[_compact_dictionary_values(child) for child in children],
    )


def compact_dictionaries(table: pa.Table) -> pa.Table:
    """Keep exact schema and selected values without unreferenced dictionary data.

    Call after Arrow take/filter has gathered ordinary value buffers. Arrow's
    gather kernels retain dictionaries even when only one entry is selected.
    """
    if not table.num_columns:
        return table
    return pa.Table.from_arrays(
        [
            pa.chunked_array([_compact_dictionary_values(chunk) for chunk in column.chunks], type=column.type)
            for column in table.columns
        ],
        schema=table.schema,
    )


def take_rows(table: pa.Table, indices: list[int]) -> pa.Table:
    """Gather distinct-row runs without combining variable-width buffers.

    A run never repeats a row or crosses an aligned source batch, so its payload
    cannot exceed an already representable source array. Duplicate requests form
    separate runs. Chunk boundaries are an internal representation detail.
    """
    if not indices:
        return table.slice(0, 0)
    if not table.num_columns:
        return pa.table({"_row": pa.nulls(len(indices))}).replace_schema_metadata(table.schema.metadata).select([])
    # ID lookups contain only fixed-width numeric addresses, with no offset ceiling.
    if all(
        pa.types.is_integer(field.type) or pa.types.is_floating(field.type) or pa.types.is_boolean(field.type)
        for field in table.schema
    ):
        return table.take(pa.array(indices, type=pa.int64()))
    batches = table.to_batches()
    ends = np.cumsum([batch.num_rows for batch in batches])
    groups = np.searchsorted(ends, indices, side="right")
    pieces: list[pa.RecordBatch] = []
    start = 0
    while start < len(indices):
        group = int(groups[start])
        stop = start + 1
        seen = {indices[start]}
        while stop < len(indices) and groups[stop] == group and indices[stop] not in seen:
            seen.add(indices[stop])
            stop += 1
        offset = 0 if group == 0 else int(ends[group - 1])
        # Take gathers nested buffers as well as outer values; slices alone retain
        # unrelated list children and their dictionaries during IPC serialization.
        pieces.append(batches[group].take(pa.array([index - offset for index in indices[start:stop]], type=pa.int64())))
        start = stop
    return pa.Table.from_batches(pieces, schema=table.schema)
