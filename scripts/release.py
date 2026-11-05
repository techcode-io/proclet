"""Release proclet, one task per step of the release workflows.

`pre` opens a pull request stamping the changelog and setting the release version,
`run` tags the version on `main`, `github` creates its GitHub release, and `post` opens
a pull request setting the next iteration version. Publishing to PyPI is left to the
`release` workflow.
"""

import datetime
import os
import re
import sys
from io import StringIO
from pathlib import Path
from typing import Final

from proclet import Command

ROOT_PATH: Final[Path] = Path(__file__).resolve().parent.parent
CHANGELOG_PATH: Final[Path] = ROOT_PATH / "CHANGELOG.md"
REPOSITORY_URL: Final[str] = "https://github.com/techcode-io/proclet"


def _git(*args: str) -> None:
    """Run git from the repository root.

    Args:
        *args: git arguments.
    Raises:
        ErrorReturnCode: if git fails.
    """
    Command("git").args(*args).cwd(ROOT_PATH).inherit().exec()


def _create_pull_request(branch: str) -> None:
    """Open a pull request from `branch` against `main`, filled from its commits.

    Args:
        branch: the branch to open the pull request from.
    Raises:
        ErrorReturnCode: if gh fails.
    """
    Command("gh").args("pr", "create", "--fill", "--base=main", f"--head={branch}").cwd(
        ROOT_PATH
    ).inherit().exec()


def _project_version() -> str:
    """Read the project version.

    Returns:
        The version, as normalized by uv.
    Raises:
        ErrorReturnCode: if uv fails.
    """
    out = StringIO()
    Command("uv").args("version", "--short").cwd(ROOT_PATH).stdout(out).exec()
    return out.getvalue().strip()


def _release_version() -> str:
    """Read the project version, which must be a release version.

    Returns:
        The version.
    Raises:
        SystemExit: if the project version isn't a release version, which means the
            pull request opened by `pre` isn't merged.
        ErrorReturnCode: if uv fails.
    """
    version = _project_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"`{version}` isn't a release version: run `pre` first")
    return version


def _version_from_env(name: str, pattern: str) -> str:
    """Read a version from the environment.

    Args:
        name: the environment variable holding the version.
        pattern: the regex the whole version must match.
    Returns:
        The version.
    Raises:
        SystemExit: if the variable is missing or doesn't match `pattern`.
    """
    version = os.getenv(name)
    if not version:
        sys.exit(f"You must define `{name}` environment variable")
    if not re.fullmatch(pattern, version):
        sys.exit(f"`{name}` must match `{pattern}`, got `{version}`")
    return version


def _set_version(version: str) -> None:
    """Set the project version.

    Goes through `uv version`, which updates `pyproject.toml` and `uv.lock`
    together, so `uv sync --locked` keeps passing.

    Args:
        version: the version to set.
    Raises:
        ErrorReturnCode: if uv fails.
    """
    Command("uv").args("version", version).cwd(ROOT_PATH).inherit().exec()


def _stamp_changelog(version: str) -> None:
    """Move the changelog's unreleased entries under `version`, dated today.

    Leaves an empty `## [Unreleased]` section and points the link references
    at the new tag.

    Args:
        version: the version being released.
    Raises:
        SystemExit: if there is no entry under `## [Unreleased]`.
    """
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


def _release_notes(version: str) -> str:
    """Read the changelog entries of `version`.

    Args:
        version: the released version.
    Returns:
        The entries, without the section's heading.
    Raises:
        SystemExit: if the changelog has no entry for `version`.
    """
    section = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[)",
        CHANGELOG_PATH.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    if section is None or not section.group(1).strip():
        sys.exit(f"CHANGELOG.md has no entry under `## [{version}]`")
    return section.group(1).strip() + "\n"


def pre() -> None:
    """Open a pull request preparing the release of `RELEASE_VERSION`.

    Raises:
        SystemExit: if the env var is invalid or the changelog has no entry.
        ErrorReturnCode: if git, uv or gh fails.
    """
    release_version = _version_from_env("RELEASE_VERSION", r"\d+\.\d+\.\d+")

    # Create a new branch
    branch = f"feat/pre-release-v{release_version}"
    _git("checkout", "-b", branch)

    # Update all files
    _stamp_changelog(release_version)
    _set_version(release_version)

    # Push changes
    _git("add", "-A")
    _git("commit", "-s", "--no-verify", "-m", f"release: proclet {release_version}")
    _git("push", "origin", branch)

    # Create a PR
    _create_pull_request(branch)


def run() -> None:
    """Tag the project version and push the tag to `origin`.

    Raises:
        SystemExit: if the project version isn't a release version.
        ErrorReturnCode: if git or uv fails, including when the tag already exists.
    """
    release_version = _release_version()

    _git("tag", release_version)
    _git("push", "origin", release_version)


def github() -> None:
    """Create the GitHub release of the project version from its changelog entries.

    Raises:
        SystemExit: if the project version isn't a release version or has no
            changelog entry.
        ErrorReturnCode: if uv or gh fails, including when the tag doesn't exist.
    """
    release_version = _release_version()

    Command("gh").args(
        "release",
        "create",
        release_version,
        "--verify-tag",
        f"--title={release_version}",
        "--notes-file=-",
    ).cwd(ROOT_PATH).stdin(_release_notes(release_version)).inherit_out().exec()


def post() -> None:
    """Open a pull request preparing the iteration of `NEXT_VERSION`.

    Raises:
        SystemExit: if the env var is invalid.
        ErrorReturnCode: if git, uv or gh fails.
    """
    next_version = _version_from_env("NEXT_VERSION", r"\d+\.\d+\.\d+-dev")

    # Create a new branch
    branch = f"feat/post-release-v{next_version}"
    _git("checkout", "-b", branch)

    # Update all files
    _set_version(next_version)

    # Push changes
    _git("add", "-A")
    _git("commit", "-s", "--no-verify", "-m", "chore: prepare for next iteration")
    _git("push", "origin", branch)

    # Create a PR
    _create_pull_request(branch)
