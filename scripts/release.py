import datetime
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

ROOT_PATH: Final[Path] = Path(__file__).resolve().parent.parent
CHANGELOG_PATH: Final[Path] = ROOT_PATH / "CHANGELOG.md"
REPOSITORY_URL: Final[str] = "https://github.com/techcode-io/proclet"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True)


def _set_version(version: str) -> None:
    # Updates pyproject.toml and uv.lock together, so `uv sync --locked` passes.
    subprocess.run(["uv", "version", version], check=True, cwd=ROOT_PATH)


def _stamp_changelog(version: str) -> None:
    data = CHANGELOG_PATH.read_text()
    unreleased = re.search(
        r"^## \[Unreleased\]\n(.*?)(?=^## \[|^\[)", data, re.MULTILINE | re.DOTALL
    )
    if unreleased is None or not unreleased.group(1).strip():
        sys.exit("CHANGELOG.md has no entry under `## [Unreleased]`")
    previous = re.search(r"^## \[(\d+\.\d+\.\d+)\]", data, re.MULTILINE)
    today = datetime.date.today().isoformat()

    # Move the unreleased entries under the new version, keep an empty section.
    data = data.replace(
        "## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - {today}\n", 1
    )
    # Point the links at the new tag.
    version_url = (
        f"{REPOSITORY_URL}/compare/{previous.group(1)}...{version}"
        if previous
        else f"{REPOSITORY_URL}/releases/tag/{version}"
    )
    data = re.sub(
        r"^\[Unreleased\]: .*$",
        f"[Unreleased]: {REPOSITORY_URL}/compare/{version}...HEAD\n"
        f"[{version}]: {version_url}",
        data,
        count=1,
        flags=re.MULTILINE,
    )
    CHANGELOG_PATH.write_text(data)


def run() -> None:
    # Ensure we have inputs
    release_version = os.getenv("RELEASE_VERSION")
    next_version = os.getenv("NEXT_VERSION")
    if not release_version:
        sys.exit("You must define `RELEASE_VERSION` environment variable")
    if not next_version:
        sys.exit("You must define `NEXT_VERSION` environment variable")

    # Switch to main branch
    _git("checkout", "main")

    # Update all files
    _stamp_changelog(release_version)
    _set_version(release_version)

    # Push changes
    _git("add", "-A")
    _git("commit", "-s", "--no-verify", "-m", f"release: proclet {release_version}")
    _git("tag", release_version)

    # Update all files
    _set_version(next_version)
    _git("add", "-A")
    _git("commit", "-s", "--no-verify", "-m", "chore: prepare for next iteration")
    _git("push", "origin", "main")
    _git("push", "origin", release_version)
