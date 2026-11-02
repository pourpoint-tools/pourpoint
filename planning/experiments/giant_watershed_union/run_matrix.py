"""Sequential fresh-process repeated measurements of a captured input file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import shapely


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execute(
    command: list[str], directory: Path, metadata: dict[str, Any], timeout: float | None = None
) -> dict[str, Any]:
    """Reserve a fresh directory and persist failure, including interrupted validation."""
    directory.mkdir(parents=True, exist_ok=False)
    record = {**metadata, "command": command, "status": "running"}
    receipt = directory / "execution.json"
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    start = time.perf_counter()
    try:
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
        record.update(returncode=result.returncode, status="success" if result.returncode == 0 else "failed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        record.update(status="failed", error=str(exc))
    record["process_wall_seconds"] = time.perf_counter() - start
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    return record


def command_output(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else result.stderr.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True, help="Must not exist")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument(
        "--validation-seconds",
        type=float,
        default=600,
        help="Per-output exact-check budget; exhaustion records unknown, never equality",
    )
    parser.add_argument("--skip-rust", action="store_true")
    args = parser.parse_args()
    if min(args.repeats, args.workers, args.batch_size) < 1 or args.validation_seconds <= 0:
        parser.error("repeats, workers, batch size and validation budget must be positive")
    root = Path(__file__).resolve().parent
    args.results_dir.mkdir(parents=True, exist_ok=False)
    provenance = {
        "input_sha256": sha256(args.input),
        "input_bytes": args.input.stat().st_size,
        "input_order": "captured record order",
        "acquisition": "previously captured; excluded from candidate timing",
        "machine": platform.uname()._asdict(),
        "cpu_count": os.cpu_count(),
        "python": sys.version,
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
        "source_commit": command_output(["git", "rev-parse", "HEAD"]),
        "source_status": command_output(["git", "status", "--short"]),
        "compiler": command_output(["rustc", "--version"]),
        "file_sha256": {
            str(p): sha256(p)
            for p in [
                *root.glob("*.py"),
                root / "rust/src/main.rs",
                root / "rust/Cargo.lock",
                Path("uv.lock"),
                Path("src/pourpoint/refinement.py"),
                Path("src/pourpoint/features.py"),
            ]
        },
        "configuration": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "historical_algorithm_commit": "60e3d6e383546ea6ed20e68fe4d77959c816a48c",
        "rust_dependencies": "experiment Cargo.lock, not the historical engine lock",
    }
    (args.results_dir / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if not args.skip_rust:
        subprocess.run(
            ["cargo", "build", "--locked", "--release", "--manifest-path", str(root / "rust/Cargo.toml")], check=True
        )
    records: list[dict[str, Any]] = []
    baseline: Path | None = None
    for repeat in range(1, args.repeats + 1):
        candidates = [(name, None, 1) for name in ("shapely_direct_union", "pourpoint_merge", "shapely_batched_union")]
        if not args.skip_rust:
            candidates += [
                ("rust_sorted_serial", "serial", 1),
                ("rust_sorted_parallel", "spatial", args.workers),
                ("rust_unsorted_parallel", "unsorted", args.workers),
            ]
        for name, mode, workers in candidates:
            directory = args.results_dir / f"repeat-{repeat}" / name
            if mode is None:
                command = [
                    sys.executable,
                    "-m",
                    "planning.experiments.giant_watershed_union.bench_python",
                    "--input",
                    str(args.input),
                    "--output-dir",
                    str(directory),
                    "--candidate",
                    name,
                    "--batch-size",
                    str(args.batch_size),
                ]
            else:
                command = [
                    str(root / "rust/target/release/giant-watershed-union-rust"),
                    str(args.input),
                    str(directory),
                    mode,
                    str(workers),
                ]
            record = execute(
                command,
                directory,
                {"input_sha256": provenance["input_sha256"], "repeat": repeat, "candidate": name, "workers": workers},
            )
            record.update(is_valid=None, agreement_status="unknown")
            if record["status"] == "success":
                outputs = list(directory.glob("*.wkb"))
                if len(outputs) != 1:
                    record.update(status="failed", error="Expected one output WKB")
                else:
                    output = outputs[0]
                    record["output_sha256"] = sha256(output)
                    measurement = directory / ("rust_benchmark_results.json" if mode else f"{name}.json")
                    record["measurement"] = json.loads(measurement.read_text())
                    if baseline is None and name == "shapely_direct_union":
                        baseline = output
                    if baseline is not None:
                        validation = directory / "validation"
                        check = execute(
                            [
                                sys.executable,
                                "-m",
                                "planning.experiments.giant_watershed_union.verify_outputs",
                                "--baseline",
                                str(baseline),
                                "--candidates",
                                str(output),
                                "--output",
                                str(validation / "comparison.json"),
                            ],
                            validation,
                            {"agreement_status": "unknown"},
                            args.validation_seconds,
                        )
                        record["validation"] = check
                        if check["status"] == "success":
                            comparison = json.loads((validation / "comparison.json").read_text())[output.stem]
                            record.update(
                                is_valid=comparison["candidate"]["valid"],
                                agreement_status=comparison["agreement_status"],
                                comparison=comparison,
                            )
            records.append(record)
            (args.results_dir / "matrix_summary.json").write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
