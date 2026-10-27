"""Private I/O helpers shared by the process module."""

from collections.abc import Sequence
from contextlib import suppress
from typing import Protocol


class Closeable(Protocol):
    """Protocol for objects that support closing."""

    def close(self) -> None: ...


def close(files: Sequence[Closeable]) -> None:
    """Close file handles, ignoring any `OSError`.

    Args:
        files: file handles to close.
    """
    for file in files:
        with suppress(OSError):
            file.close()
