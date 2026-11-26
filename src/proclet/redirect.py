"""Types accepted by the stdin/stdout/stderr redirects, and redirect helpers."""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Callable
from pathlib import Path
from typing import IO, TextIO

type SyncRedirectIn = queue.Queue[bytes | str | None] | str | bytes | IO[bytes] | TextIO
"""Stdin sources for `exec()`: a queue (`None` closes stdin), a string, bytes or a file object."""

type AsyncRedirectIn = (
    asyncio.Queue[bytes | str | None] | str | bytes | IO[bytes] | TextIO
)
"""Stdin sources for `aexec()`: an asyncio queue (`None` closes stdin), a string, bytes or a file object."""

type RedirectIn = SyncRedirectIn | AsyncRedirectIn
"""Any stdin source accepted by `Command.stdin()`."""

type RedirectOut = Path | IO[bytes] | TextIO | Callable[[str], None]
"""Stdout/stderr targets: a file path, a file object or a callable receiving each line."""


def strip_redirect(log_fn: Callable[[str], None]) -> Callable[[str], None]:
    """Wrap a log callable to strip trailing newlines from process output lines.

    Use this when redirecting process stdout/stderr to a logger so each line
    is emitted as a clean record without a trailing newline character.

    Args:
        log_fn: callable receiving each stripped line, e.g. `logger.info`.
    Returns:
        a callable to pass to `Command.stdout()`/`Command.stderr()`.
    """
    return lambda line: log_fn(line.rstrip("\n"))
