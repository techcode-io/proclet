import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True)


def _set_version(version: str) -> None:
    # Update project version
    try:
        data = PROJECT_PATH.read_text()
    except Exception as err:
        print("The `pyproject.toml` can't be read", file=sys.stderr)
        raise err

    data = re.sub(r"version = \"(.+)\"", f'version = "{version}"', data, count=1)
    try:
        PROJECT_PATH.write_text(data)
    except Exception as err:
        print("The `pyproject.toml` file can't be written", file=sys.stderr)
        raise err


def run() -> None:
    # Ensure we have inputs
    release_version = os.getenv("RELEASE_VERSION")
    next_version = os.getenv("NEXT_VERSION")
    if not release_version:
        return print(
            "You must define `RELEASE_VERSION` environment variable", file=sys.stderr
        )
    if not next_version:
        return print(
            "You must define `NEXT_VERSION` environment variable", file=sys.stderr
        )

    # Switch to main branch
    _git("checkout", "main")

    # Update all files
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
