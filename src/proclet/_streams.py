"""Helpers moving data between Python objects and process pipes."""

from __future__ import annotations

import asyncio
from io import IOBase, TextIOBase

from proclet.redirect import RedirectOut


def needs_forwarding(redirect: RedirectOut | None) -> bool:
    """Check if a redirect needs a forwarding thread or task."""
    if redirect is None:
        return False
    if callable(redirect):
        return True
    if isinstance(redirect, IOBase):
        # File objects (text or binary) without a real fd (e.g. StringIO) need forwarding
        try:
            redirect.fileno()
            return False
        except (OSError, AttributeError):
            return True
    return False


# Read size used to feed a file object without a real fd (e.g. StringIO) to stdin.
STDIN_CHUNK_SIZE = 64 * 1024


def encode_input(data: bytes | str) -> bytes:
    """Encode stdin input, passing bytes through untouched."""
    return data if isinstance(data, bytes) else data.encode()


def dispatch_line(target: RedirectOut, line: bytes) -> None:
    """Hand one line of process output to a forwarded redirect target.

    Callables and text streams get the line decoded (invalid bytes replaced);
    binary streams without a real fd (e.g. BytesIO) get the raw bytes.

    Args:
        target: the redirect target, one that `needs_forwarding` accepted.
        line: the raw line read from the process.
    """
    if callable(target):
        target(line.decode(errors="replace"))
    elif isinstance(target, TextIOBase):
        target.write(line.decode(errors="replace"))
    elif isinstance(target, IOBase):
        target.write(line)


async def read_line(stream: asyncio.StreamReader) -> bytes:
    """Read one line of any length, like the sync forwarder's `readline()`.

    `StreamReader.readline()` raises once a line outgrows the stream's buffer
    limit (64 KiB by default), which would stop the forwarding for good.

    Args:
        stream: the stream reader to read from.
    Returns:
        the line including its trailing newline, or `b""` at EOF.
    """
    chunks: list[bytes] = []
    while True:
        try:
            chunks.append(await stream.readuntil(b"\n"))
            break
        except asyncio.IncompleteReadError as err:
            chunks.append(err.partial)
            break
        except asyncio.LimitOverrunError as err:
            chunks.append(await stream.readexactly(err.consumed))
    return b"".join(chunks)
