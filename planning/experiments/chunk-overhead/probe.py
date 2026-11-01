"""Fresh-process bounded helper comparison; JSON output only."""

import argparse
import gc
import json
import platform
import resource
import time
from collections.abc import Callable

import numpy as np
import psutil
import pyarrow as pa
from candidate import take_rows as candidate

from pourpoint._selection import compact_dictionaries
from pourpoint._selection import take_rows as baseline


def snapshot() -> dict[str, int]:
    return {
        "arrow_live": pa.total_allocated_bytes(),
        "rss": psutil.Process().memory_info().rss,
        "peak_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == "Darwin" else 1024),
    }


def requests(rows: int, pattern: str) -> list[int]:
    values = np.arange(rows)
    if pattern == "reversed":
        values = values[::-1]
    elif pattern in ("interleaved", "duplicates"):
        values = np.random.default_rng(198).permutation(values)
    result = values.tolist()
    return result + result[: rows // 10] if pattern == "duplicates" else result


def synthetic(rows: int, width: int, columns: int, chunks: int) -> pa.Table:
    arrays = {
        f"n{i}": pa.chunked_array([pa.array(part) for part in np.array_split(np.arange(rows), chunks)])
        for i in range(columns)
    }
    if width:
        arrays["payload"] = pa.chunked_array(
            [pa.array([b"x" * width] * len(part), type=pa.binary()) for part in np.array_split(np.arange(rows), chunks)]
        )
    return pa.table(arrays)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--pattern", choices=["ordered", "reversed", "interleaved", "duplicates"], required=True)
    parser.add_argument("--rows", type=int, default=170000)
    parser.add_argument("--width", type=int, default=32)
    parser.add_argument("--columns", type=int, default=9)
    parser.add_argument("--chunks", type=int, default=17)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    table = synthetic(args.rows, args.width, args.columns, args.chunks)
    indices = requests(args.rows, args.pattern)
    select: Callable[[pa.Table, list[int]], pa.Table] = baseline if args.mode == "baseline" else candidate
    if args.verify:
        result = compact_dictionaries(select(table, indices))
        expected = table.take(pa.array(indices, type=pa.int64()))
        assert result.equals(expected, check_metadata=True)
        print(json.dumps({**vars(args), "correct": True}))
        return
    gc.collect()
    before = snapshot()
    started = time.perf_counter()
    result = select(table, indices)
    selected = time.perf_counter()
    result = compact_dictionaries(result)
    finished = time.perf_counter()
    after = snapshot()
    output = {
        **vars(args),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "arrow": pa.__version__,
        "pool": pa.default_memory_pool().backend_name,
        "input_bytes": table.nbytes,
        "output_bytes": result.nbytes,
        "chunks_per_column": [c.num_chunks for c in result.columns],
        "selection_s": selected - started,
        "compaction_s": finished - selected,
        "total_s": finished - started,
        "before": before,
        "after": after,
    }
    del result
    gc.collect()
    output["released"] = snapshot()
    print(json.dumps(output))


if __name__ == "__main__":
    main()
