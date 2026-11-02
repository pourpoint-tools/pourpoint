"""Extract Kainji upstream rivers and catchments from TDX HFX on S3."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from pourpoint import HydroFabric


def extract_kainji(
    source_url: str,
    output_dir: Path,
    seed_river_id: int = 1681474,
    batch_size: int = 8192,
    max_catchments: int | None = None,
) -> dict[str, Any]:
    """Reconstruct Kainji upstream catchments and save raw inputs."""
    if batch_size <= 0 or (max_catchments is not None and max_catchments <= 0):
        raise ValueError("batch_size and max_catchments must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    provenance: dict[str, Any] = {
        "source_url": source_url,
        "input_order": "first-seen unique local catchments in upstream river traversal order",
        "batch_size": batch_size,
        "max_catchments": max_catchments,
        "seed_river_id": seed_river_id,
        "expected_river_count": 169805,
        "expected_catchment_count": 169770,
        "expected_terminal_catchment": 2235378,
    }

    print(f"Opening HydroFabric from {source_url}...")
    t0 = time.perf_counter()
    with HydroFabric(source_url) as fabric:
        provenance["fabric_open_seconds"] = time.perf_counter() - t0
        manifest_path = output_dir / "kainji_manifest.json"
        manifest_path.write_text(json.dumps(dict(fabric.manifest.declarations), indent=2) + "\n")
        provenance["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        print(f"Traversing upstream rivers from seed {seed_river_id}...")
        t_trav = time.perf_counter()
        river_ids = fabric.traverse("rivers", [seed_river_id], relationship="upstream_ids")
        trav_seconds = time.perf_counter() - t_trav
        provenance["traverse_seconds"] = trav_seconds
        provenance["observed_river_count"] = len(river_ids)
        print(f"Traversed {len(river_ids)} rivers in {trav_seconds:.3f} s (expected 169805)")

        print("Querying river-to-catchment associations...")
        t_rel = time.perf_counter()
        catchment_id_set: set[int] = set()
        catchment_ids_ordered: list[int] = []
        association_rows: list[list[int]] = []

        # Read relationships in chunks
        for i in range(0, len(river_ids), batch_size):
            chunk = river_ids[i : i + batch_size]
            rel_table = fabric.relationships("rivers", chunk, columns=["local_catchment_ids"])
            col = rel_table["local_catchment_ids"]
            rows = col.to_pylist()
            association_rows.extend(rows)
            for row in rows:
                if row:
                    for cid in row:
                        if cid not in catchment_id_set:
                            catchment_id_set.add(cid)
                            catchment_ids_ordered.append(cid)

        associations_path = output_dir / "kainji_river_associations.parquet"
        pq.write_table(pa.table({"river_id": river_ids, "local_catchment_ids": association_rows}), associations_path)
        provenance["seed_local_catchment_ids"] = association_rows[river_ids.index(seed_river_id)]
        provenance["seed_associated_with_expected_terminal"] = 2235378 in (provenance["seed_local_catchment_ids"] or [])
        rel_seconds = time.perf_counter() - t_rel
        provenance["relationships_seconds"] = rel_seconds
        provenance["observed_catchment_count"] = len(catchment_ids_ordered)
        provenance["terminal_catchment_present"] = 2235378 in catchment_id_set
        print(
            f"Associated {len(catchment_ids_ordered)} unique catchments in {rel_seconds:.3f} s "
            f"(terminal 2235378 present: {provenance['terminal_catchment_present']})"
        )

        selected_catchment_ids = catchment_ids_ordered
        if max_catchments is not None and len(selected_catchment_ids) > max_catchments:
            selected_catchment_ids = selected_catchment_ids[:max_catchments]
            print(f"Truncated to {len(selected_catchment_ids)} catchments for scaled test")

        provenance["selected_catchment_count"] = len(selected_catchment_ids)
        provenance["truncated"] = len(selected_catchment_ids) != len(catchment_ids_ordered)
        provenance["selected_terminal_catchment_present"] = 2235378 in selected_catchment_ids
        print("Reading catchment geometry records...")
        t_rec = time.perf_counter()
        # Read records in batches
        all_tables: list[pa.Table] = []
        for i in range(0, len(selected_catchment_ids), batch_size):
            chunk = selected_catchment_ids[i : i + batch_size]
            tbl = fabric.records("catchments", chunk, columns=["geometry"])
            all_tables.append(tbl)

        catchments_table = pa.concat_tables(all_tables) if all_tables else pa.table({})
        rec_seconds = time.perf_counter() - t_rec
        provenance["records_seconds"] = rec_seconds
        print(f"Retrieved {catchments_table.num_rows} records in {rec_seconds:.3f} s")

    # Add id column to the catchments table
    id_array = pa.array(selected_catchment_ids, type=pa.int64())
    catchments_table = catchments_table.append_column("id", id_array)

    # Save to Parquet
    parquet_path = output_dir / "kainji_catchments.parquet"
    print(f"Writing {parquet_path}...")
    pq.write_table(catchments_table, parquet_path, compression="zstd")
    provenance["parquet_size_bytes"] = parquet_path.stat().st_size

    # Save to WKB binary format
    wkb_bin_path = output_dir / "kainji_inputs.wkb_bin"
    print(f"Writing {wkb_bin_path}...")
    wkb_col = catchments_table["geometry"]
    total_wkb_bytes = 0

    with open(wkb_bin_path, "wb") as f:
        for cid, wkb_val in zip(selected_catchment_ids, wkb_col.to_pylist(), strict=True):
            wkb_bytes = bytes(wkb_val)
            f.write(struct.pack("<QI", cid, len(wkb_bytes)))
            f.write(wkb_bytes)
            total_wkb_bytes += len(wkb_bytes)

    provenance["wkb_bin_size_bytes"] = wkb_bin_path.stat().st_size
    provenance["total_wkb_payload_bytes"] = total_wkb_bytes

    print("Calculating exact geometry counts...")
    vertices = holes = polygon_parts = multipart_records = 0
    dimensions: dict[int, int] = {}
    for offset in range(0, len(wkb_col), batch_size):
        geoms = shapely.from_wkb(wkb_col.slice(offset, batch_size).to_pylist())
        vertices += int(shapely.get_num_coordinates(geoms).sum())
        parts = shapely.get_parts(geoms)
        holes += int(shapely.get_num_interior_rings(parts).sum())
        polygon_parts += len(parts)
        multipart_records += int((shapely.get_num_geometries(geoms) > 1).sum())
        for dimension in shapely.get_coordinate_dimension(geoms):
            dimensions[int(dimension)] = dimensions.get(int(dimension), 0) + 1
    provenance.update(
        exact_vertex_count=vertices,
        vertex_count_includes_ring_closure_coordinates=True,
        hole_count=holes,
        polygon_part_count=polygon_parts,
        multipart_record_count=multipart_records,
        coordinate_dimensions=dimensions,
    )

    # Compute SHA-256 hashes
    for p in (parquet_path, wkb_bin_path, associations_path, manifest_path):
        hasher = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        provenance[f"{p.name}_sha256"] = hasher.hexdigest()

    provenance_path = output_dir / "kainji_provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Saved provenance to {provenance_path}")
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="s3://pourpoint-hfx/tdx/hfx-0.4.0/NGA-TDX-Hydro-20230126-full62-20261027/",
        help="Source HFX dataset S3 URL",
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory for extracted data")
    parser.add_argument("--seed-river-id", type=int, default=1681474, help="Kainji river ID")
    parser.add_argument("--max-catchments", type=int, default=None, help="Optional max catchments for testing")
    args = parser.parse_args()

    extract_kainji(
        source_url=args.source,
        output_dir=args.output_dir,
        seed_river_id=args.seed_river_id,
        max_catchments=args.max_catchments,
    )


if __name__ == "__main__":
    main()
