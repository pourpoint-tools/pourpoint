"""Selective final HFX feature and immediate-relationship access."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from pourpoint._selection import compact_dictionaries, take_rows
from pourpoint.metadata import BoundingBox, SpatialManifest
from pourpoint.spatial import RowGroupBounds, conservative_bounds, overlap
from pourpoint.storage import Source

_IPC_BATCH_CACHE_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class _IPCDirectoryMeta:
    ranges: tuple[tuple[int, int, int], ...]
    minima: npt.NDArray[np.int64]
    maxima: npt.NDArray[np.int64]
    schema: pa.Schema


def _ids(values: Iterable[int]) -> list[int]:
    result = []
    for value in values:
        if isinstance(value, np.integer):
            value = int(value)
        if type(value) is not int or not 0 < value < 2**63:
            raise ValueError("IDs must be positive int64 values")
        result.append(value)
    return result


def _columns(schema: pa.Schema, columns: Sequence[str] | None) -> list[str]:
    names = schema.names if columns is None else list(columns)
    if len(set(names)) != len(names) or any(name not in schema.names for name in names):
        raise ValueError("unknown or duplicate column")
    return names


def _empty(schema: pa.Schema, names: list[str]) -> pa.Table:
    return pa.Table.from_batches([], schema=schema).select(names)


class _IndexedIPC:
    """One published ID directory; projected readers acquire selected IPC fields."""

    def __init__(
        self,
        source: Source,
        manifest: SpatialManifest,
        data: str,
        directory: str,
        count: int,
        *,
        shared_meta: dict[str, _IPCDirectoryMeta] | None = None,
        shared_lock: Lock | None = None,
    ) -> None:
        self.source, self.manifest, self.data = source, manifest, data
        self.stream = source.open_artifact(data, manifest.artifacts[data], bounded=True)
        reader = pa.ipc.open_file(self.stream)
        meta: _IPCDirectoryMeta | None = None
        if shared_meta is not None and shared_lock is not None:
            with shared_lock:
                meta = shared_meta.get(data)
        if meta is None:
            schema = reader.schema
            if len(set(schema.names)) != len(schema.names) or schema.field("id") != pa.field("id", pa.int64(), False):
                raise ValueError("IPC: invalid ID schema")
            declaration = source.read_json(directory, manifest.artifacts[directory])
            entries = declaration.get("batches")
            if (
                declaration.get("version") != 1
                or not isinstance(entries, list)
                or len(entries) != reader.num_record_batches
            ):
                raise ValueError("IPC directory: invalid batches")
            ranges_list: list[tuple[int, int, int]] = []
            for number, entry in enumerate(entries):
                if not isinstance(entry, dict) or entry.get("batch") != number:
                    raise ValueError("IPC directory: invalid batch order")
                low, high, rows = (entry.get(k) for k in ("min_id", "max_id", "rows"))
                if any(type(v) is not int for v in (low, high, rows)):
                    raise ValueError("IPC directory: invalid range")
                low, high, rows = cast(int, low), cast(int, high), cast(int, rows)
                if (
                    not 0 < low <= high < 2**63
                    or not 0 < rows <= high - low + 1
                    or (ranges_list and low <= ranges_list[-1][1])
                ):
                    raise ValueError("IPC directory: invalid range")
                ranges_list.append((low, high, rows))
            if sum(r[2] for r in ranges_list) != count:
                raise ValueError("IPC directory: count mismatch")
            meta = _IPCDirectoryMeta(
                tuple(ranges_list),
                np.array([r[0] for r in ranges_list], dtype=np.int64),
                np.array([r[1] for r in ranges_list], dtype=np.int64),
                schema,
            )
            if shared_meta is not None and shared_lock is not None:
                with shared_lock:
                    shared_meta.setdefault(data, meta)
        self.schema = meta.schema
        self.ranges = meta.ranges
        self._minima = meta.minima
        self._maxima = meta.maxima
        self._reader = reader
        self._projection = self.schema.names
        self._batch_cache: OrderedDict[
            tuple[int, tuple[str, ...]], tuple[pa.RecordBatch, npt.NDArray[np.int64], int]
        ] = OrderedDict()
        self._batch_cache_bytes = 0

    @property
    def buffer_bytes(self) -> int:
        return self._batch_cache_bytes

    def _get_batch(self, group: int, projection_key: tuple[str, ...]) -> tuple[pa.RecordBatch, npt.NDArray[np.int64]]:
        cache_key = (group, projection_key)
        cached = self._batch_cache.get(cache_key)
        if cached is not None:
            self._batch_cache.move_to_end(cache_key)
            return cached[0], cached[1]
        batch = self._reader.get_batch(group)
        low, high, rows = self.ranges[group]
        keys = batch.column("id").to_numpy()
        if (
            batch.num_rows != rows
            or batch.column("id").null_count
            or keys[0] != low
            or keys[-1] != high
            or np.any(keys[1:] <= keys[:-1])
        ):
            raise ValueError("IPC: IDs or count disagree with directory")
        cost = batch.nbytes + keys.nbytes
        if cost <= _IPC_BATCH_CACHE_LIMIT:
            while self._batch_cache_bytes + cost > _IPC_BATCH_CACHE_LIMIT:
                _, (_, _, evicted) = self._batch_cache.popitem(last=False)
                self._batch_cache_bytes -= evicted
            self._batch_cache[cache_key] = (batch, keys, cost)
            self._batch_cache_bytes += cost
        return batch, keys

    def read(self, requested: list[int], columns: Sequence[str] | None = None) -> pa.Table:
        self.source.check_artifact(self.data)
        names = _columns(self.schema, columns)
        if not requested:
            return _empty(self.schema, names)
        projection = [n for n in self.schema.names if n in {*names, "id"}]
        if projection != self._projection:
            self.stream.seek(0)
            self._reader = pa.ipc.open_file(
                self.stream,
                options=pa.ipc.IpcReadOptions(included_fields=[self.schema.get_field_index(n) for n in projection]),
            )
            self._projection = projection
        projection_key = tuple(projection)
        req_arr = np.asarray(requested, dtype=np.int64)
        group_ids = np.searchsorted(self._maxima, req_arr, side="left")
        num_groups = len(self.ranges)
        if num_groups == 0:
            raise KeyError(f"unknown ID {requested[0]}")
        clamped = np.minimum(group_ids, num_groups - 1)
        out_of_range = (group_ids == num_groups) | (req_arr < self._minima[clamped])
        if np.any(out_of_range):
            bad_pos = int(np.flatnonzero(out_of_range)[0])
            raise KeyError(f"unknown ID {requested[bad_pos]}")
        groups: dict[int, list[int]] = {}
        for position, group in enumerate(group_ids.tolist()):
            groups.setdefault(group, []).append(position)
        pieces: list[pa.Table] = []
        positions: list[int] = []
        for group, pos_list in groups.items():
            batch, keys = self._get_batch(group, projection_key)
            vals = req_arr[pos_list]
            found = np.searchsorted(keys, vals)
            clamped_found = np.minimum(found, len(keys) - 1)
            misses = (found == len(keys)) | (keys[clamped_found] != vals)
            if np.any(misses):
                miss_idx = int(np.flatnonzero(misses)[0])
                raise KeyError(f"unknown ID {int(vals[miss_idx])}")
            pieces.append(take_rows(pa.Table.from_batches([batch]), found.tolist()))
            positions.extend(pos_list)
        self.source.check_artifact(self.data)
        return compact_dictionaries(take_rows(pa.concat_tables(pieces), np.argsort(positions).tolist()).select(names))


class VectorReader:
    """An eager domain reader. Source owns resources; returned tables own their data."""

    def __init__(
        self,
        source: Source,
        manifest: SpatialManifest,
        domain: str,
        *,
        shared_bounds: dict[str, RowGroupBounds] | None = None,
        shared_ipc_meta: dict[str, _IPCDirectoryMeta] | None = None,
        shared_lock: Lock | None = None,
    ) -> None:
        self.source, self.manifest, self.domain = source, manifest, domain
        self.family = manifest.families[domain]
        family = self.family
        source.open_artifact(family.data, manifest.artifacts[family.data], bounded=True)
        self._file = source.parquet(family.data, manifest.artifacts[family.data])
        self.schema = self._file.schema_arrow
        if self._file.metadata.num_rows != family.count:
            raise ValueError("feature count mismatch")
        if len(set(self.schema.names)) != len(self.schema.names) or self.schema.field("id") != pa.field(
            "id", pa.int64(), False
        ):
            raise ValueError("feature ID schema mismatch")
        self._bounds: RowGroupBounds | None = None
        self._shared_bounds = shared_bounds
        self._shared_ipc_meta = shared_ipc_meta
        self._shared_lock = shared_lock
        self._lookup: _IndexedIPC | None = None
        self._relationships: _IndexedIPC | None = None
        self._adjacency: OrderedDict[tuple[str, int], tuple[int, ...]] = OrderedDict()
        self._adjacency_bytes = 0

    @property
    def buffer_bytes(self) -> int:
        """Retained spatial arrays, IPC batch caches, and estimated adjacency storage."""
        return (
            self._adjacency_bytes
            + (0 if self._bounds is None else self._bounds.buffer_bytes)
            + (0 if self._lookup is None else self._lookup.buffer_bytes)
            + (0 if self._relationships is None else self._relationships.buffer_bytes)
        )

    def records(self, ids: Iterable[int], columns: Sequence[str] | None = None) -> pa.Table:
        table, rows, _ = self._addressed_records(ids, columns)
        return take_rows(table, rows)

    def _addressed_records(
        self, ids: Iterable[int], columns: Sequence[str] | None = None
    ) -> tuple[pa.Table, list[int], list[tuple[int, int]]]:
        requested = _ids(ids)
        names = _columns(self.schema, columns)
        if not requested:
            self.source.check_artifact(self.family.data)
            return _empty(self.schema, names), [], []
        if self._lookup is None:
            family = self.family
            lookup = _IndexedIPC(
                self.source,
                self.manifest,
                family.lookup,
                family.directory,
                family.count,
                shared_meta=self._shared_ipc_meta,
                shared_lock=self._shared_lock,
            )
            expected = pa.schema(
                [
                    pa.field("id", pa.int64(), False),
                    pa.field("block", pa.int32(), False),
                    pa.field("row", pa.int32(), False),
                ]
            )
            if (
                not lookup.schema.equals(expected)
                or (lookup.schema.metadata or {}).get(b"hfx:data_sha256") != family.data_sha256.encode()
            ):
                raise ValueError("lookup schema or feature binding mismatch")
            self._lookup = lookup
        table = self._lookup.read(requested)
        if any(c.null_count for c in table.columns):
            raise ValueError("lookup: null address")
        blocks = table["block"].to_pylist()
        row_nums = table["row"].to_pylist()
        addresses: dict[int, list[tuple[int, int, int]]] = {}
        for pos, (value, group, row) in enumerate(zip(requested, blocks, row_nums, strict=True)):
            addresses.setdefault(group, []).append((pos, value, row))
        selected, rows = self._selected_records(addresses, names)
        return selected, rows, list(zip(blocks, row_nums, strict=True))

    def _records(self, addresses: dict[int, list[tuple[int, int, int]]], names: list[str]) -> pa.Table:
        table, rows = self._selected_records(addresses, names)
        return take_rows(table, rows)

    def _selected_records(
        self, addresses: dict[int, list[tuple[int, int, int]]], names: list[str]
    ) -> tuple[pa.Table, list[int]]:
        self.source.check_artifact(self.family.data)
        pieces: list[pa.Table] = []
        positions: list[int] = []
        projection = list(dict.fromkeys([*names, "id"]))
        for group, queries in sorted(addresses.items()):
            if not 0 <= group < self._file.num_row_groups or any(
                not 0 <= row < self._file.metadata.row_group(group).num_rows for _, _, row in queries
            ):
                raise ValueError("lookup: feature address out of bounds")
            self.source.prefetch_parquet(self.family.data, self.manifest.artifacts[self.family.data], group, projection)
            table = self._file.read_row_group(group, columns=projection, use_threads=False)
            selected = take_rows(table, [r for _, _, r in queries])
            if selected["id"].to_pylist() != [v for _, v, _ in queries]:
                raise ValueError("lookup: addressed feature ID mismatch")
            pieces.append(selected)
            positions.extend(p for p, _, _ in queries)
        self.source.check_artifact(self.family.data)
        if not pieces:
            return _empty(self.schema, names), []
        return compact_dictionaries(pa.concat_tables(pieces).select(names)), np.argsort(positions).tolist()

    def candidates(
        self, bounds: BoundingBox, levels: Sequence[int] | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        """Conservative bbox overlap, not exact geometry intersection."""
        envelope = conservative_bounds(bounds)
        names = _columns(self.schema, columns)
        tiers = None if levels is None else tuple(levels)
        if tiers is not None and any(type(v) is not int or v < 0 for v in tiers):
            raise ValueError("levels must be nonnegative integers")
        self.source.check_artifact(self.family.data)
        if self._bounds is None:
            if self._shared_bounds is not None and self._shared_lock is not None:
                with self._shared_lock:
                    cached_bounds = self._shared_bounds.get(self.domain)
                if cached_bounds is None:
                    cached_bounds = RowGroupBounds(self._file)
                    with self._shared_lock:
                        cached_bounds = self._shared_bounds.setdefault(self.domain, cached_bounds)
                self._bounds = cached_bounds
            else:
                self._bounds = RowGroupBounds(self._file)
        discovery = [n for n in ("id", "level", "bbox") if n in self.schema.names]
        extra = [n for n in names if n not in discovery]
        pieces: list[pa.Table] = []
        target_schema = pa.schema([self.schema.field(n) for n in names], metadata=self.schema.metadata)
        for group in self._bounds.candidate_groups(envelope, levels=tiers):
            self.source.prefetch_parquet(self.family.data, self.manifest.artifacts[self.family.data], group, discovery)
            table = self._file.read_row_group(group, columns=discovery, use_threads=False)
            mask = overlap(table, envelope)
            if tiers is not None:
                mask &= np.isin(table["level"].to_numpy(), tiers)
            if not np.any(mask):
                continue
            row_indices = np.flatnonzero(mask).tolist()
            id_col = table["id"].combine_chunks()
            if id_col.null_count:
                raise ValueError("IDs must be positive int64 values")
            selected_ids = id_col.to_numpy(zero_copy_only=False)[mask]
            if np.any((selected_ids <= 0) | (selected_ids >= 2**63)):
                raise ValueError("IDs must be positive int64 values")
            selected_disc = take_rows(table, row_indices)
            if extra:
                self.source.prefetch_parquet(self.family.data, self.manifest.artifacts[self.family.data], group, extra)
                extra_table = self._file.read_row_group(group, columns=extra, use_threads=False)
                selected_extra = take_rows(extra_table, row_indices)
                cols = [selected_disc[n] if n in discovery else selected_extra[n] for n in names]
                combined = pa.Table.from_arrays(cols, schema=target_schema)
            else:
                combined = selected_disc.select(names if names else ["id"])
            pieces.append(compact_dictionaries(combined))
        self.source.check_artifact(self.family.data)
        if not pieces:
            return _empty(self.schema, names)
        return pa.concat_tables(pieces).select(names)

    def relationships(self, ids: Iterable[int], columns: Sequence[str] | None = None) -> pa.Table:
        """Return supplied immediate relationships without inferring flow or outlets."""
        requested = _ids(ids)
        if self._relationships is None:
            declaration = self.manifest.relationships[self.domain]
            reader = _IndexedIPC(
                self.source,
                self.manifest,
                cast(str, declaration["data"]),
                cast(str, declaration["directory"]),
                self.family.count,
                shared_meta=self._shared_ipc_meta,
                shared_lock=self._shared_lock,
            )
            declared = cast(Mapping[str, object], declaration["columns"])
            if set(reader.schema.names) != {"id", *declared}:
                raise ValueError("relationship columns disagree with declaration")
            for field in reader.schema:
                if field.name != "id" and (
                    field.nullable or not pa.types.is_list(field.type) or field.type.value_type != pa.int64()
                ):
                    raise ValueError("relationship schema must contain nonnull list<int64>")
            self._relationships = reader
        table = self._relationships.read(requested, columns)
        for name in table.column_names:
            if name == "id":
                continue
            col = cast(pa.ListArray, table[name].combine_chunks())
            if col.null_count:
                raise ValueError("relationship IDs must be nonnull, positive, sorted and unique")
            values_arr = col.values
            if values_arr.null_count:
                raise ValueError("relationship IDs must be nonnull, positive, sorted and unique")
            flat = values_arr.to_numpy(zero_copy_only=False)
            if len(flat):
                if np.any(flat <= 0):
                    raise ValueError("relationship IDs must be nonnull, positive, sorted and unique")
                if len(flat) > 1:
                    offsets = col.offsets.to_numpy(zero_copy_only=False)
                    non_empty = offsets[1:] > offsets[:-1]
                    last_idx = (offsets[1:] - 1)[non_empty]
                    last_idx = last_idx[last_idx < len(flat) - 1]
                    same_list = np.ones(len(flat) - 1, dtype=np.bool_)
                    same_list[last_idx] = False
                    if np.any(same_list & (flat[:-1] >= flat[1:])):
                        raise ValueError("relationship IDs must be nonnull, positive, sorted and unique")
        return table

    def traverse(self, ids: Iterable[int], relationship: str = "upstream_ids") -> list[int]:
        """Breadth-first same-domain traversal, including seeds and deduplicating cycles.

        The reusable adjacency cache is capped at 8 MiB of estimated Python storage;
        one call's returned IDs and visited workspace necessarily scale with its result.
        """
        declaration = self.manifest.relationships[self.domain]
        columns = cast(Mapping[str, Mapping[str, object]], declaration["columns"])
        if relationship not in columns or columns[relationship]["target_domain"] != self.domain:
            raise ValueError("traversal requires a declared same-domain relationship")
        self.source.check_artifact(self.family.data)
        frontier = list(dict.fromkeys(_ids(ids)))
        seen = set(frontier)
        result: list[int] = []
        while frontier:
            missing: list[int] = []
            adjacency: dict[int, tuple[int, ...]] = {}
            for value in frontier:
                key = (relationship, value)
                cached = self._adjacency.get(key)
                if cached is not None:
                    self._adjacency.move_to_end(key)
                    adjacency[value] = cached
                else:
                    missing.append(value)
            if missing:
                table = self.relationships(missing, columns=[relationship])
                for value, neighbors in zip(missing, table[relationship].to_pylist(), strict=True):
                    entry = tuple(neighbors)
                    adjacency[value] = entry
                    cost = 192 + 36 * len(entry)
                    if cost <= 8 * 1024 * 1024:
                        while self._adjacency_bytes + cost > 8 * 1024 * 1024:
                            _, removed = self._adjacency.popitem(last=False)
                            self._adjacency_bytes -= 192 + 36 * len(removed)
                        self._adjacency[(relationship, value)] = entry
                        self._adjacency_bytes += cost
            result.extend(frontier)
            following = set()
            for value in frontier:
                following.update(adjacency[value])
            frontier = sorted(following - seen)
            seen.update(frontier)
        if self._relationships is not None:
            self.source.check_artifact(self._relationships.data)
        return result
