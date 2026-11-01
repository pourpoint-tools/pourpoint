"""Run probes serially in fresh project-native processes (no concurrent timings)."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    directory = Path(__file__).parent
    shapes = [(170000, 32, 9, 17), (170000, 0, 9, 17), (20000, 1024, 2, 8), (20000, 32, 30, 1)]
    patterns = ["ordered", "reversed", "interleaved", "duplicates"]

    def run(name: str, calls: list[list[str]]) -> None:
        with (directory / name).open("w") as stream:
            for number, call in enumerate(calls):
                result = subprocess.run([sys.executable, *call], check=True, capture_output=True, text=True)
                row = json.loads(result.stdout)
                row["sequence"] = number
                stream.write(json.dumps(row) + "\n")
                stream.flush()

    calls: list[list[str]] = []
    for rows, width, columns, chunks in shapes:
        for pattern in patterns:
            for repetition in range(3):
                for mode in ["baseline", "candidate"] if repetition % 2 == 0 else ["candidate", "baseline"]:
                    calls.append(
                        [
                            str(directory / "probe.py"),
                            "--mode",
                            mode,
                            "--pattern",
                            pattern,
                            "--rows",
                            str(rows),
                            "--width",
                            str(width),
                            "--columns",
                            str(columns),
                            "--chunks",
                            str(chunks),
                        ]
                    )
    run("synthetic.jsonl", calls)
    if args.cache:
        calls = []
        for operation in ["records", "relationships", "candidates"]:
            for pattern in ["ordered"] if operation == "candidates" else patterns:
                for repetition in range(3):
                    for mode in ["baseline", "candidate"] if repetition % 2 == 0 else ["candidate", "baseline"]:
                        calls.append(
                            [
                                str(directory / "real_probe.py"),
                                "--cache",
                                str(args.cache),
                                "--ids",
                                str(directory / "real-requests.json"),
                                "--mode",
                                mode,
                                "--pattern",
                                pattern,
                                "--operation",
                                operation,
                            ]
                        )
        run("real.jsonl", calls)


if __name__ == "__main__":
    main()
