"""Bounded union of materialized catchments and contributions."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import TYPE_CHECKING

import shapely
from shapely.geometry.base import BaseGeometry

from pourpoint.features import Subbasin

if TYPE_CHECKING:
    from pourpoint.refinement import BasinPart


_GROUP_SIZE = 5000
_WORKERS = 2
# Admit an entire workspace, not individual tasks: waiting delineations must not
# accumulate partial unions while another call combines its final geometry.
_WORKSPACE = Lock()


def _union_group(parts: Sequence[BasinPart]) -> BaseGeometry:
    return shapely.union_all([part.geometry for part in parts])


def union_subbasins(parts: Sequence[BasinPart]) -> BaseGeometry:
    with _WORKSPACE:
        if len(parts) <= _GROUP_SIZE:
            return _union_group(parts)
        addresses = [(part if isinstance(part, Subbasin) else part.source)._source_address for part in parts]
        # No source lookup or invented spatial order for manually supplied parts.
        ordered = (
            [parts[index] for index in sorted(range(len(parts)), key=lambda index: addresses[index])]
            if all(address is not None for address in addresses)
            else parts
        )
        groups = (ordered[start : start + _GROUP_SIZE] for start in range(0, len(ordered), _GROUP_SIZE))
        partials: list[BaseGeometry] = []
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            pending = deque()
            for _ in range(_WORKERS):
                if (group := next(groups, None)) is not None:
                    pending.append(pool.submit(_union_group, group))
            while pending:
                partials.append(pending.popleft().result())
                if (group := next(groups, None)) is not None:
                    pending.append(pool.submit(_union_group, group))
        return shapely.union_all(partials)
