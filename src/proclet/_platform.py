"""Platform-specific helpers for spawn options (priority, POSIX-only checks)."""

from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from types import MappingProxyType
from typing import Final

# `ioprio_set` syscall numbers by machine; unknown machines skip the IO class.
_IOPRIO_SYSCALL: Final[Mapping[str, int]] = MappingProxyType(
    {"x86_64": 251, "aarch64": 30}
)
_IOPRIO_WHO_PROCESS: Final[int] = 1
_IOPRIO_CLASS_IDLE: Final[int] = 3
_IOPRIO_CLASS_SHIFT: Final[int] = 13


def low_priority_hook(nice: int | None) -> Callable[[], None] | None:
    """Build the `preexec_fn` lowering CPU (and, on Linux, IO) priority of a child.

    Everything the hook needs is resolved here, before the fork, so the hook
    itself only makes plain syscalls. IO priority is best effort: it is
    skipped off Linux and on unknown machines, and failures are ignored.
    Windows has no `preexec_fn`; see `priority_creationflags` instead.

    Args:
        nice: niceness to apply, `None` for no change.
    Returns:
        the hook, or `None` when no priority change is requested or on Windows.
    """
    if nice is None or sys.platform == "win32":
        return None

    syscall = None
    number = (
        _IOPRIO_SYSCALL.get(platform.machine()) if sys.platform == "linux" else None
    )
    if number is not None:
        with suppress(OSError, AttributeError):
            syscall = ctypes.CDLL(None, use_errno=True).syscall

    def hook() -> None:
        os.setpriority(os.PRIO_PROCESS, 0, nice)
        if syscall is not None:
            syscall(
                number,
                _IOPRIO_WHO_PROCESS,
                0,
                _IOPRIO_CLASS_IDLE << _IOPRIO_CLASS_SHIFT,
            )

    return hook


def priority_creationflags(nice: int | None) -> int:
    """Map a niceness to the Windows priority class of a child.

    Windows has no niceness, only a handful of priority classes, so the value
    is bucketed: >= 15 idle, 1..14 below normal, 0 unchanged, -14..-1 above
    normal, <= -15 high (never realtime).

    Args:
        nice: niceness to apply, `None` for no change.
    Returns:
        the `creationflags` to pass on spawn, `0` off Windows or for no change.
    """
    if nice is None or nice == 0 or sys.platform != "win32":
        return 0
    if nice >= 15:
        name = "IDLE_PRIORITY_CLASS"
    elif nice > 0:
        name = "BELOW_NORMAL_PRIORITY_CLASS"
    elif nice > -15:
        name = "ABOVE_NORMAL_PRIORITY_CLASS"
    else:
        name = "HIGH_PRIORITY_CLASS"
    # Only defined on Windows.
    return getattr(subprocess, name)


def require_os(option: str, func: str) -> None:
    """Fail fast when the platform can't apply a POSIX-only spawn option.

    Args:
        option: name of the option being set, for the error message.
        func: `os` function the option relies on, e.g. `setgroups`.
    Raises:
        ValueError: if `os` lacks `func` (e.g. on Windows).
    """
    if not hasattr(os, func):
        raise ValueError(f"'{option}' is only supported on POSIX platforms")
