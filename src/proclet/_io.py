from collections.abc import Sequence
from contextlib import suppress
from typing import Protocol


class Closeable(Protocol):
    """Protocol for objects that support closing."""

    def close(self) -> None: ...


def close(files: Sequence[Closeable]) -> None:
    """
    Close a list of file handles, ignoring any errors.

    Args:
        files: list of file handles to close.
    """
    for file in files:
        with suppress(OSError):
            file.close()
