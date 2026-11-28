"""Retain release documentation privately; update Pages only with explicit approval."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def is_prerelease(flag: str = "", event_path: str | Path | None = None) -> bool:
    normalized = flag.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if normalized:
        raise ValueError(f"GITHUB_RELEASE_PRERELEASE must be 'true' or 'false', got {flag!r}")
    if event_path:
        payload = json.loads(Path(event_path).read_text())
        release = payload.get("release")
        if isinstance(release, dict):
            return bool(release.get("prerelease", False))
    return False


def retention_enabled(event: str, *, prerelease: bool = False) -> bool:
    return event == "release" and not prerelease


def publication_enabled(event: str, gate: str, *, prerelease: bool = False) -> bool:
    return event == "release" and gate == "true" and not prerelease


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args()
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event != "release":
        parser.error("Only a published release workflow may retain or publish documentation")
    prerelease = is_prerelease(
        os.environ.get("GITHUB_RELEASE_PRERELEASE", ""),
        os.environ.get("GITHUB_EVENT_PATH"),
    )
    if not retention_enabled(event, prerelease=prerelease):
        print("Skipping documentation repository update for prerelease.")
        return
    repository = args.repository.resolve()
    for path in repository.iterdir():
        if path.name == ".git":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(ROOT / ".docs-build/site", repository, dirs_exist_ok=True)
    (repository / ".nojekyll").touch()

    def git(*arguments: str) -> None:
        subprocess.run(["git", "-C", str(repository), *arguments], check=True)

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "--all")
    git("commit", "--allow-empty", "-m", "Update release documentation")
    git("push", "origin", "HEAD:main")
    if publication_enabled(event, os.environ.get("DOCS_PUBLICATION_ENABLED", ""), prerelease=prerelease):
        git("push", "origin", "HEAD:gh-pages")


if __name__ == "__main__":
    main()
