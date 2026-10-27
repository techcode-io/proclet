"""Portable stand-ins for the POSIX tools the tests need.

Every helper runs an inline script with the interpreter running the tests, so
the suite behaves the same on POSIX and Windows without `sh`, `cat`, `echo`...
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from proclet import Command

PYTHON = sys.executable
PYTHON_NAME = Path(PYTHON).name
PYTHON_DIR = str(Path(PYTHON).parent)

# Env vars a child interpreter can't start without: Windows Python needs
# SYSTEMROOT, POSIX needs nothing.
MINIMAL_ENV: dict[str, str] = (
    {"SYSTEMROOT": os.environ["SYSTEMROOT"]} if sys.platform == "win32" else {}
)

# Keep "\n" line endings on Windows too, where text stdout would write "\r\n".
_PRELUDE = """\
import os, shutil, sys, time
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(newline="\\n")
"""

# Statements usable in `py()`.
DRAIN_STDIN = "sys.stdin.buffer.read()"
COPY_STDIN = """\
for chunk in iter(lambda: sys.stdin.buffer.read1(65536), b""):
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()"""


def py(*statements: str, args: tuple[str, ...] = ()) -> Command:
    """Command running `statements` as an inline script, with `args` in `sys.argv[1:]`."""
    return Command(PYTHON).args("-c", _PRELUDE + "\n".join(statements), *args)


def cat() -> Command:
    """Copy stdin to stdout as it arrives, like `cat`."""
    return py(COPY_STDIN)


def echo(*words: str) -> Command:
    """Print `words` separated by spaces, like `echo`."""
    return py("print(*sys.argv[1:])", args=words)


def sleep(seconds: float) -> Command:
    """Sleep for `seconds`, like `sleep`."""
    return py("time.sleep(float(sys.argv[1]))", args=(str(seconds),))


def true() -> Command:
    """Exit 0, like `true`."""
    return py("pass")


def false() -> Command:
    """Exit 1, like `false`."""
    return py("sys.exit(1)")


def pwd() -> Command:
    """Print the working directory, like `pwd`."""
    return py("print(os.getcwd())")


def printenv() -> Command:
    """Print every env var as `KEY=value`, like `env`."""
    return py("for key, value in os.environ.items():\n    print(f'{key}={value}')")


def yes() -> Command:
    """Write lines of `y` until the reader goes away, like `yes`."""
    return py(
        "try:",
        "    while True:",
        "        sys.stdout.buffer.write(b'y\\n' * 4096)",
        "except OSError:",
        "    os._exit(1)",
    )


def head_bytes(count: int) -> Command:
    """Copy the first `count` bytes of stdin then exit, like `head -c`."""
    return py(
        "sys.stdout.buffer.write(sys.stdin.buffer.read(int(sys.argv[1])))",
        args=(str(count),),
    )


def random_bytes(count: int) -> Command:
    """Write `count` random bytes, like `head -c count /dev/urandom`."""
    return py(
        "sys.stdout.buffer.write(os.urandom(int(sys.argv[1])))", args=(str(count),)
    )


def count_bytes() -> Command:
    """Print how many bytes stdin held, like `wc -c`."""
    return py("print(len(sys.stdin.buffer.read()))")


def upper() -> Command:
    """Upper-case stdin, like `tr a-z A-Z`."""
    return py("sys.stdout.write(sys.stdin.read().upper())")


def rev() -> Command:
    """Reverse every line of stdin, like `rev`."""
    return py("for line in sys.stdin:\n    print(line.rstrip('\\n')[::-1])")


def hexdump() -> Command:
    """Print stdin's bytes as space separated hex pairs, like `od -An -tx1 -v`."""
    return py("print(' '.join(f'{byte:02x}' for byte in sys.stdin.buffer.read()))")


def write_stdin_to(path: Path) -> Command:
    """Write stdin to `path`, like `sh -c 'cat >path'`."""
    return py(
        "with open(sys.argv[1], 'wb') as out:",
        "    shutil.copyfileobj(sys.stdin.buffer, out)",
        args=(str(path),),
    )
