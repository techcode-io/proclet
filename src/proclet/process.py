from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
from asyncio import Task, create_task, gather
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import suppress
from io import IOBase, TextIOBase
from pathlib import Path
from threading import Thread
from typing import IO, NamedTuple, Protocol, Self, TextIO, overload

from proclet._io import close

# Type aliases for process types
type SyncProcess = subprocess.Popen[bytes]
type AsyncProcess = asyncio.subprocess.Process
type Process = SyncProcess | AsyncProcess


class _PipelineStage(NamedTuple):
    """Bookkeeping for one spawned stage of a `Command.pipeline()` chain."""

    process: SyncProcess
    io_threads: list[Thread]
    opened: list[IO[bytes]]


class _AsyncPipelineStage(NamedTuple):
    """Bookkeeping for one spawned stage of a `Command.apipeline()` chain."""

    process: AsyncProcess
    io_tasks: list[Task[None]]
    opened: list[IO[bytes]]


class ProcessObserver(Protocol):
    """Observer notified at process spawn and exit."""

    def on_spawn(self, process: Process) -> None: ...

    def on_exit(self, process: Process) -> None: ...


class CommandNotFound(Exception):
    """Raised when a command binary is not found."""

    def __init__(self, command: str) -> None:
        super().__init__(f"Command not found: {command}")
        self.command: str = command


class ErrorReturnCode(Exception):
    """Raised when a command returns a non-zero exit code."""

    def __init__(self, cmd: str, exit_code: int) -> None:
        super().__init__(f"Command '{cmd}' returned non-zero exit code {exit_code}")
        self.__cmd = cmd
        self.__exit_code = exit_code

    @property
    def cmd(self) -> str:
        return self.__cmd

    @property
    def exit_code(self) -> int:
        return self.__exit_code


class TimeoutException(Exception):
    """Raised when a command times out."""

    def __init__(self, cmd: str, timeout: float) -> None:
        super().__init__(f"Command '{cmd}' timed out after {timeout} seconds")
        self.__cmd = cmd
        self.__timeout = timeout

    @property
    def cmd(self) -> str:
        return self.__cmd

    @property
    def timeout(self) -> float:
        return self.__timeout


# Redirect command types
type SyncRedirectIn = queue.Queue[bytes | str | None] | str | bytes | IO[bytes] | TextIO
type AsyncRedirectIn = (
    asyncio.Queue[bytes | str | None] | str | bytes | IO[bytes] | TextIO
)
type RedirectIn = SyncRedirectIn | AsyncRedirectIn
type RedirectOut = Path | IO[bytes] | TextIO | Callable[[str], None]


def strip_redirect(log_fn: Callable[[str], None]) -> Callable[[str], None]:
    """Wrap a log callable to strip trailing newlines from process output lines.

    Use this when redirecting process stdout/stderr to a logger so each line
    is emitted as a clean record without a trailing newline character.
    """
    return lambda line: log_fn(line.rstrip("\n"))


# `ioprio_set` syscall numbers by machine; unknown machines skip the IO class.
_IOPRIO_SYSCALL = {"x86_64": 251, "aarch64": 30}
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_CLASS_SHIFT = 13


def _low_priority_hook(nice: int | None) -> Callable[[], None] | None:
    """
    Build the `preexec_fn` lowering CPU (and, on Linux, IO) priority of a child.

    Everything the hook needs is resolved here, before the fork, so the hook
    itself only makes plain syscalls. IO priority is best effort: it is
    skipped off Linux and on unknown machines, and failures are ignored.
    Windows has no `preexec_fn`; see `_priority_creationflags` instead.

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


def _priority_creationflags(nice: int | None) -> int:
    """
    Map a niceness to the Windows priority class of a child.

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


def _require_os(option: str, func: str) -> None:
    """
    Fail fast when the platform can't apply a POSIX-only spawn option.

    Args:
        option: name of the option being set, for the error message.
        func: `os` function the option relies on, e.g. `setgroups`.
    Raises:
        ValueError: if `os` lacks `func` (e.g. on Windows).
    """
    if not hasattr(os, func):
        raise ValueError(f"'{option}' is only supported on POSIX platforms")


class EnvCmd:
    """Convenient environment variables builder for command."""

    def __init__(self) -> None:
        """Init env command."""
        self.__build: dict[str, str] = {}

    @staticmethod
    def empty() -> EnvCmd:
        """Create an empty env command."""
        return EnvCmd()

    @staticmethod
    def inherit(predicate: Callable[[str, str], bool] | None = None) -> EnvCmd:
        """Create an env command that inherit environment variables from the current process."""
        env = EnvCmd()
        if predicate:
            env.__build = {
                key: value for key, value in os.environ.items() if predicate(key, value)
            }
        else:
            env.__build = os.environ.copy()
        return env

    def add(self, env_vars: dict[str, str]) -> Self:
        """Add environment variables to the env command."""
        self.__build.update(env_vars)
        return self

    def build(self) -> dict[str, str]:
        """
        Returns:
            environment variables built using the env command.
        """
        return self.__build.copy()


class PathCmd:
    """Convenient path builder for command."""

    def __init__(self) -> None:
        """Init path command."""
        self.__build: list[str] = []

    @staticmethod
    def empty() -> PathCmd:
        """Create an empty path command."""
        return PathCmd()

    @staticmethod
    def inherit() -> PathCmd:
        """Create a path command that inherit the PATH environment variable from the current process."""
        path = PathCmd()
        # Drop empty entries: they mean the current directory to a PATH lookup.
        path.__build = [
            entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry
        ]
        return path

    def add(self, value: str) -> Self:
        """Add a binary directory path to the program PATH."""
        self.__build.insert(0, value)
        return self

    def build(self) -> str:
        """
        Returns:
            computed PATH environment variable.
        """
        return os.pathsep.join(self.__build)


def _needs_forwarding(redirect: RedirectOut | None) -> bool:
    """Check if a redirect needs a forwarding thread."""
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
_STDIN_CHUNK_SIZE = 64 * 1024


def _encode_input(data: bytes | str) -> bytes:
    """Encode stdin input, passing bytes through untouched."""
    return data if isinstance(data, bytes) else data.encode()


async def _read_line(stream: asyncio.StreamReader) -> bytes:
    """
    Read one line of any length, like the sync forwarder's `readline()`.

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


class Command:
    """Convenient wrapper around subprocess for process execution."""

    def __init__(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> None:
        """
        Init command.

        Args:
            cmd_path: command name or path.
            search_path: directories to look the binary up in instead of the
                inherited `PATH`. Ignored when `cmd_path` is a `Path`.
        """
        if isinstance(cmd_path, Path):
            self.__cmd_path = cmd_path.as_posix()
        else:
            found_path = shutil.which(
                cmd_path,
                path=os.pathsep.join(search_path) if search_path is not None else None,
            )
            if found_path is None:
                raise CommandNotFound(cmd_path)
            self.__cmd_path = found_path
        self.__cwd = Path.cwd()
        # None inherits the parent's environment; {} is an empty one.
        self.__env: dict[str, str] | None = None
        self.__args: tuple[str, ...] = ()
        self.__in: RedirectIn | None = None
        self.__out: RedirectOut | None = None
        self.__err: RedirectOut | None = None
        self.__timeout: int | None = None
        self.__ok_exit_codes: frozenset[int] = frozenset({0})
        self.__user: str | int | None = None
        self.__group: str | int | None = None
        self.__extra_groups: list[str | int] | None = None
        self.__nice: int | None = None
        self.__observers: list[ProcessObserver] = []

    @overload
    def args(self) -> tuple[str, ...]: ...

    @overload
    def args(self, first: str, /, *value: str) -> Self: ...

    def args(self, *values: str) -> tuple[str, ...] | Self:
        """
        Returns current arguments if no value is provided, otherwise set arguments.

        Args:
            *values: arguments to set.
        Returns:
            current arguments if no value is provided or self for chaining.
        """
        if not values:
            return self.__args

        self.__args = values
        return self

    @overload
    def env(self) -> dict[str, str]: ...

    @overload
    def env(self, value: dict[str, str]) -> Self: ...

    def env(self, value: dict[str, str] | None = None) -> dict[str, str] | Self:
        """
        Returns current env vars if no value is provided, otherwise set env vars.

        Until env vars are set, the command inherits the parent's environment
        and the getter returns `{}`. Once set, the command gets exactly that
        environment: an empty dict really runs it with no env vars, so build
        on `EnvCmd.inherit()` to keep the parent's.

        Args:
            value: env vars to set.
        Returns:
            current env vars if no value is provided or self for chaining.
        """
        if value is None:
            return {} if self.__env is None else self.__env.copy()

        self.__env = dict(value)
        return self

    @overload
    def cwd(self) -> Path: ...

    @overload
    def cwd(self, value: Path) -> Self: ...

    def cwd(self, value: Path | None = None) -> Path | Self:
        """
        Returns current cwd if no value is provided, otherwise set cwd.

        Args:
            value: cwd to set.
        Returns:
            current cwd if no value is provided or self for chaining.
        """
        if value is None:
            return self.__cwd

        self.__cwd = value
        return self

    @overload
    def user(self) -> str | int | None: ...

    @overload
    def user(self, value: str | int) -> Self: ...

    def user(self, value: str | int | None = None) -> str | int | None | Self:
        """
        Returns current user if no value is provided, otherwise set user.

        The user is passed straight through to `subprocess.Popen`/
        `asyncio.create_subprocess_exec` (a username or uid), which drop
        privileges to it before exec'ing - the calling process must have
        the permissions to do so (typically running as root).

        Args:
            value: user (name or uid) to run the command as.
        Returns:
            current user if no value is provided or self for chaining.
        Raises:
            ValueError: if set on a non-POSIX platform.
        """
        if value is None:
            return self.__user

        _require_os("user", "setreuid")
        self.__user = value
        return self

    @overload
    def group(self) -> str | int | None: ...

    @overload
    def group(self, value: str | int) -> Self: ...

    def group(self, value: str | int | None = None) -> str | int | None | Self:
        """
        Returns current group if no value is provided, otherwise set group.

        Passed straight through to `subprocess.Popen`/
        `asyncio.create_subprocess_exec` (a group name or gid), like `user`.

        Args:
            value: group (name or gid) to run the command as.
        Returns:
            current group if no value is provided or self for chaining.
        Raises:
            ValueError: if set on a non-POSIX platform.
        """
        if value is None:
            return self.__group

        _require_os("group", "setregid")
        self.__group = value
        return self

    @overload
    def extra_groups(self) -> list[str | int] | None: ...

    @overload
    def extra_groups(self, value: Sequence[str | int]) -> Self: ...

    def extra_groups(
        self, value: Sequence[str | int] | None = None
    ) -> list[str | int] | None | Self:
        """
        Returns current supplementary groups if no value is provided, otherwise set them.

        Unset (`None`) keeps the parent's supplementary groups; an empty list
        drops them all, which is what a privilege drop wants. POSIX only.

        Args:
            value: supplementary groups (names or gids) to run the command with.
        Returns:
            current supplementary groups if no value is provided or self for chaining.
        Raises:
            ValueError: if set on a non-POSIX platform.
        """
        if value is None:
            return None if self.__extra_groups is None else list(self.__extra_groups)

        _require_os("extra_groups", "setgroups")
        self.__extra_groups = list(value)
        return self

    @overload
    def nice(self) -> int | None: ...

    @overload
    def nice(self, value: int) -> Self: ...

    def nice(self, value: int | None = None) -> int | None | Self:
        """
        Returns current niceness if no value is provided, otherwise set it.

        The child lowers its CPU priority to this niceness before exec (and,
        on Linux, moves to the idle IO class on a best effort basis). Raising
        priority (a negative value) needs privileges, like `nice(1)`. On
        Windows the value maps to a priority class instead: >= 15 idle,
        1..14 below normal, -14..-1 above normal, <= -15 high.

        Args:
            value: niceness to run the command with, e.g. 19 for the lowest priority.
        Returns:
            current niceness if no value is provided or self for chaining.
        """
        if value is None:
            return self.__nice

        self.__nice = value
        return self

    @overload
    def stdin(self) -> RedirectIn | None: ...

    @overload
    def stdin(self, value: RedirectIn) -> Self: ...

    def stdin(self, value: RedirectIn | None = None) -> RedirectIn | None | Self:
        """
        Returns current stdin if no value is provided, otherwise set stdin.

        Args:
            value: stdin to set.
        Returns:
            current stdin if no value is provided or self for chaining.
        """
        if value is None:
            return self.__in

        self.__in = value
        return self

    @overload
    def stdout(self) -> RedirectOut | None: ...

    @overload
    def stdout(self, value: RedirectOut) -> Self: ...

    def stdout(self, value: RedirectOut | None = None) -> RedirectOut | None | Self:
        """
        Returns current stdout if no value is provided, otherwise set stdout.

        Args:
            value: stdout to set.
        Returns:
            current stdout if no value is provided or self for chaining.
        """
        if value is None:
            return self.__out
        elif isinstance(value, Path):
            self.__out = value.absolute()
        else:
            self.__out = value
        return self

    @overload
    def stderr(self) -> RedirectOut | None: ...

    @overload
    def stderr(self, value: RedirectOut) -> Self: ...

    def stderr(self, value: RedirectOut | None = None) -> RedirectOut | None | Self:
        """
        Returns current stderr if no value is provided, otherwise set stderr.

        Args:
            value: stderr to set.
        Returns:
            current stderr if no value is provided or self for chaining.
        """
        if value is None:
            return self.__err
        elif isinstance(value, Path):
            self.__err = value.absolute()
        else:
            self.__err = value
        return self

    def inherit(self) -> Self:
        """
        Inherit stdin, stdout and stderr from current process.

        Returns:
            self for chaining.
        """
        self.__in = sys.stdin
        self.inherit_out()
        return self

    def inherit_out(self, if_unset: bool = False) -> Self:
        """
        Inherit stdout and stderr from current process.

        Args:
            if_unset: when True, only inherit a stream if it is not already configured.

        Returns:
            self for chaining.
        """
        if not if_unset or self.__out is None:
            self.__out = sys.stdout
        if not if_unset or self.__err is None:
            self.__err = sys.stderr
        return self

    def timeout(self, timeout: int) -> Self:
        """Set a timeout for process to spawn."""
        self.__timeout = timeout
        return self

    def ok_exit_codes(self, *codes: int) -> Self:
        """
        Set the exit codes considered successful (default: only 0).

        Any other exit code raises `ErrorReturnCode`. Useful for tools that use
        non-zero codes for warnings, e.g. GNU tar exits 1 when a file changed
        while being read.
        """
        self.__ok_exit_codes = frozenset(codes)
        return self

    def binary_path(self) -> Path:
        """
        Returns:
            path where binary is installed.
        """
        return Path(self.__cmd_path)

    def add_observer(self, observer: ProcessObserver) -> Self:
        """Register an observer to be notified on process spawn and exit."""
        self.__observers.append(observer)
        return self

    def copy(self, cmd: Command) -> Self:
        """Copy all parameters from another command."""
        self.__env = None if cmd.__env is None else cmd.__env.copy()
        self.__cwd = cmd.__cwd
        self.__args = cmd.__args
        self.__in = cmd.__in
        self.__out = cmd.__out
        self.__err = cmd.__err
        self.__timeout = cmd.__timeout
        self.__ok_exit_codes = cmd.__ok_exit_codes
        self.__user = cmd.__user
        self.__group = cmd.__group
        self.__extra_groups = (
            None if cmd.__extra_groups is None else list(cmd.__extra_groups)
        )
        self.__nice = cmd.__nice
        self.__observers = list(cmd.__observers)
        return self

    def _full_cmd(self) -> str:
        """Build the "path arg1 arg2 ..." string used in raised exceptions."""
        return f"{self.__cmd_path} {' '.join(self.__args)}"

    @staticmethod
    def _opened(*streams: int | IO[bytes]) -> list[IO[bytes]]:
        """Pick out the streams that are real file objects (as opposed to fds/subprocess
        constants) so callers know what needs closing once the process is done with it.
        """
        return [stream for stream in streams if not isinstance(stream, int)]

    @staticmethod
    def _notify_spawn(observers: list[ProcessObserver], process: Process) -> None:
        """Notify observers that a process has been spawned."""
        for obs in observers:
            obs.on_spawn(process)

    def _prepare_stdin(self) -> int | None:
        """Convert the configured stdin redirect into a file descriptor or subprocess constant.

        Returns:
            fileno of a file object backed by a real fd, subprocess.PIPE for input
            fed from Python (Queue, str, bytes or a file object without a real
            fd, e.g. StringIO), or subprocess.DEVNULL when no stdin is configured.
        """
        if self.__in is None:
            return subprocess.DEVNULL
        if isinstance(self.__in, (queue.Queue, asyncio.Queue, str, bytes)):
            return subprocess.PIPE
        try:
            return self.__in.fileno()
        except (OSError, AttributeError):
            return subprocess.PIPE

    @staticmethod
    def _prepare_redirect(
        redirect: RedirectOut | None,
    ) -> int | IO[bytes]:
        """Prepare a stdout/stderr redirect for subprocess.

        Args:
            redirect: the redirect target.
        """
        if redirect is None:
            return subprocess.DEVNULL
        if isinstance(redirect, Path):
            return redirect.open("wb")
        if isinstance(redirect, IOBase):
            try:
                return redirect.fileno()
            except (OSError, AttributeError):
                pass
        # File objects without a real fd (e.g. StringIO) and callables need PIPE for forwarding
        return subprocess.PIPE

    def _popen(
        self,
        stdin: int | IO[bytes] | None,
        stdout: int | IO[bytes],
        stderr: int | IO[bytes],
    ) -> SyncProcess:
        """Spawn this command with subprocess.Popen, translating a missing binary.

        Args:
            stdin: prepared stdin redirect.
            stdout: prepared stdout redirect.
            stderr: prepared stderr redirect.
        Returns:
            the spawned subprocess.
        Raises:
            CommandNotFound: if the binary can't be found.
        """
        try:
            return subprocess.Popen(
                [self.__cmd_path, *self.__args],
                env=self.__env,
                cwd=self.__cwd.as_posix(),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                bufsize=0,  # Unbuffered binary mode
                user=self.__user,
                group=self.__group,
                extra_groups=self.__extra_groups,
                # Only plain syscalls, resolved before the fork (see _low_priority_hook).
                preexec_fn=_low_priority_hook(self.__nice),  # noqa: PLW1509
                creationflags=_priority_creationflags(self.__nice),
            )
        except FileNotFoundError as err:
            raise CommandNotFound(self.__cmd_path) from err

    @staticmethod
    def _create_thread_forwarder(stream: IO[bytes], target: RedirectOut) -> Thread:
        """Create a thread that reads binary lines from stream, decodes them, and dispatches to target.

        Args:
            stream: binary readable stream to read from (e.g. process stdout or stderr pipe).
            target: destination to write decoded lines to; either a callable accepting a str,
                    or a TextIOBase instance (e.g. StringIO).
        Returns:
            daemon thread, not yet started.
        """

        def forward() -> None:
            try:
                for line in iter(stream.readline, b""):
                    decoded = line.decode(errors="replace")
                    if callable(target):
                        target(decoded)
                    elif isinstance(target, TextIOBase):
                        target.write(decoded)
            except (OSError, ValueError):
                pass
            finally:
                stream.close()

        return Thread(target=forward, daemon=True)

    def _handle_stdin(self, process: SyncProcess) -> Thread | None:
        """Start a thread feeding stdin from a Queue, str, bytes or fd-less file.

        Feeding happens off the calling thread so a child blocked on a full
        stdout/stderr pipe can't deadlock the write. A child that exits
        without reading all of its input, or a file that can't be read, just
        stops the feeding.

        Args:
            process: the subprocess to feed stdin to.
        Returns:
            the started thread, or None when stdin isn't fed from Python.
        """
        stdin = process.stdin
        source = self.__in
        if stdin is None or source is None or isinstance(source, asyncio.Queue):
            return None

        def chunks() -> Iterator[bytes]:
            if isinstance(source, (str, bytes)):
                yield _encode_input(source)
            elif isinstance(source, queue.Queue):
                while (data := source.get()) is not None:
                    yield _encode_input(data)
            else:
                while data := source.read(_STDIN_CHUNK_SIZE):
                    yield _encode_input(data)

        def forward() -> None:
            with suppress(OSError):
                for chunk in chunks():
                    stdin.write(chunk)
            close([stdin])

        thread = threading.Thread(target=forward, daemon=True)
        thread.start()
        return thread

    def _handle_stream(
        self, stream: IO[bytes] | None, target: RedirectOut | None
    ) -> Thread | None:
        """Start a forwarding thread for a single stdout/stderr stream, if it needs one.

        Args:
            stream: the process stream to forward from (stdout or stderr).
            target: the configured redirect target for that stream.
        Returns:
            the started thread, or None if this stream doesn't need forwarding.
        """
        if _needs_forwarding(target) and stream is not None and target is not None:
            thread = self._create_thread_forwarder(stream, target)
            thread.start()
            return thread
        return None

    def _handle_stdout_stderr(self, process: SyncProcess) -> list[Thread]:
        """Start forwarding threads for stdout and stderr.

        Args:
            process: the subprocess to forward output from.
        Returns:
            list of threads started for output forwarding.
        """
        threads: list[Thread] = []
        for proc_stream, target in (
            (process.stdout, self.__out),
            (process.stderr, self.__err),
        ):
            thread = self._handle_stream(proc_stream, target)
            if thread is not None:
                threads.append(thread)
        return threads

    def _create_io_threads(
        self,
        process: SyncProcess,
    ) -> list[Thread]:
        """Start threads for forwarding I/O to callbacks/StringIO."""
        # Start draining stdout/stderr before feeding stdin
        threads = self._handle_stdout_stderr(process)

        stdin_thread = self._handle_stdin(process)
        if stdin_thread is not None:
            threads.append(stdin_thread)
        return threads

    @staticmethod
    def _create_gc_thread(
        process: SyncProcess,
        io_threads: list[Thread],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
    ) -> None:
        """Start a background thread to clean up resources after process exits.

        Args:
            process: the subprocess to monitor.
            io_threads: list of I/O forwarding threads to join.
            opened_files: list of file handles to close.
            observers: observers to notify once the process has exited.
        """

        def garbage_collector() -> None:
            try:
                process.wait()
            except OSError:
                pass
            finally:
                for th in io_threads:
                    with suppress(RuntimeError):
                        th.join(timeout=5)
                close(opened_files)
                for obs in observers:
                    obs.on_exit(process)

        thread = threading.Thread(target=garbage_collector, daemon=True)
        thread.start()

    @staticmethod
    def _cleanup(
        process: SyncProcess,
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
    ) -> None:
        """Close opened redirect files and terminate/kill a process if still alive.

        Args:
            process: the subprocess to clean up.
            opened_files: file handles to close.
            observers: observers to notify once the process has exited.
        """
        close(opened_files)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        for obs in observers:
            obs.on_exit(process)

    @staticmethod
    def _is_failure(returncode: int | None, ok_exit_codes: frozenset[int]) -> bool:
        """Whether a finished process' exit code counts as a failure.

        Shared by `_finish()` and `_afinish()`.
        """
        return returncode is not None and returncode not in ok_exit_codes

    @staticmethod
    def _finish(
        process: SyncProcess,
        full_cmd: str,
        timeout: int | None,
        io_threads: list[Thread],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
        ok_exit_codes: frozenset[int],
    ) -> None:
        """Wait for a spawned process, raise on timeout/non-zero exit, then clean up.

        Shared by `exec()` and `pipe()` so both sides of a pipeline get identical
        timeout/error/cleanup semantics.

        Args:
            process: the subprocess to wait for.
            full_cmd: command string, used in raised exceptions.
            timeout: timeout in seconds to wait for completion.
            io_threads: I/O forwarding threads to join.
            opened_files: file handles to close.
            observers: observers to notify once the process has exited.
            ok_exit_codes: exit codes considered successful.
        """
        try:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as err:
                raise TimeoutException(timeout=err.timeout, cmd=full_cmd) from err

            for io_thread in io_threads:
                io_thread.join()

            if Command._is_failure(process.returncode, ok_exit_codes):
                raise ErrorReturnCode(cmd=full_cmd, exit_code=process.returncode)
        finally:
            Command._cleanup(process, opened_files, observers)

    def exec(self, wait_completion: bool = True) -> SyncProcess:
        """
        Run the command and return the subprocess.Popen directly.

        Args:
            wait_completion: wait for completion.
        Returns:
            subprocess.Popen process.
        """
        full_cmd = self._full_cmd()

        stdout = self._prepare_redirect(self.__out)
        stderr = self._prepare_redirect(self.__err)
        opened_files = self._opened(stdout, stderr)

        try:
            process = self._popen(self._prepare_stdin(), stdout, stderr)
        except CommandNotFound:
            close(opened_files)
            raise

        # Start background threads to forward piped stdout/stderr to their targets
        io_threads = self._create_io_threads(process)
        self._notify_spawn(self.__observers, process)

        if wait_completion:
            self._finish(
                process,
                full_cmd,
                self.__timeout,
                io_threads,
                opened_files,
                self.__observers,
                self.__ok_exit_codes,
            )
            return process
        else:
            self._create_gc_thread(process, io_threads, opened_files, self.__observers)
            return process

    def pipe(self, other: Command) -> SyncProcess:
        """
        Run this command and pipe its stdout directly into `other`'s stdin, then run
        `other` and wait for both processes to complete.

        Shorthand for `pipeline(other)` - see its docstring for the full contract
        (raw OS pipe, binary safety, what gets ignored/overwritten on each side,
        error/timeout semantics). See `apipe()` for the asyncio equivalent.

        Args:
            other: the downstream command to receive this command's stdout as its stdin.
        Returns:
            the downstream command's completed subprocess.Popen.
        Raises:
            CommandNotFound: if either binary can't be found.
            ErrorReturnCode: if either process exits with a non-zero code.
            TimeoutException: if either command's configured timeout elapses.
        """
        return self.pipeline(other)

    def pipeline(self, *others: Command) -> SyncProcess:
        """
        Run this command and each of `others` in sequence, piping every stage's
        stdout directly into the next stage's stdin, then wait for all processes to
        complete.

        Generalizes `pipe()` to any number of stages, connected with raw OS pipes
        rather than the thread-based stdout forwarding `exec()` uses for its redirect
        targets, so this stays safe for binary payloads (e.g. streaming a dump
        through a compressor into an upload) that decoding as text would corrupt.
        Every stage's own configured stdout is ignored and always becomes the pipe
        into the next stage, except the last stage's, which is honored normally.
        Every stage's own configured stdin is ignored and always becomes the upstream
        pipe, except the first stage's (self), which is honored normally. Every
        stage's stderr, env, cwd, timeout and observers are honored normally.

        See `apipeline()` for the asyncio equivalent.

        Args:
            others: the downstream commands to chain after this one, in order.
        Returns:
            the last command's completed subprocess.Popen.
        Raises:
            ValueError: if no downstream commands are given.
            CommandNotFound: if any binary can't be found.
            ErrorReturnCode: if any process exits with a non-zero code.
            TimeoutException: if any command's configured timeout elapses.
        """
        if not others:
            raise ValueError("pipeline() requires at least one downstream command")

        stages: list[Command] = [self, *others]
        last_index = len(stages) - 1
        full_cmds = [stage._full_cmd() for stage in stages]

        spawned: list[_PipelineStage] = []
        try:
            for index, stage in enumerate(stages):
                is_first = index == 0
                is_last = index == last_index

                stderr = stage._prepare_redirect(stage.__err)
                stdout = (
                    stage._prepare_redirect(stage.__out) if is_last else subprocess.PIPE
                )
                opened = stage._opened(stdout, stderr)

                upstream = None if is_first else spawned[-1].process
                stdin = stage._prepare_stdin() if upstream is None else upstream.stdout
                try:
                    process = stage._popen(stdin, stdout, stderr)
                except CommandNotFound:
                    close(opened)
                    raise
                finally:
                    if upstream is not None:
                        # Allow the previous stage to receive SIGPIPE if this stage
                        # exits early: once this stage has its own copy of the pipe
                        # (or failed to spawn), the parent's copy must go so it isn't
                        # left as the pipe's last reader - same as pipe().
                        assert upstream.stdout is not None
                        upstream.stdout.close()

                io_threads = self._pipeline_stage_io_threads(
                    stage, process, is_first=is_first, is_last=is_last
                )
                stage._notify_spawn(stage.__observers, process)
                spawned.append(_PipelineStage(process, io_threads, opened))

            for stage, run, full_cmd in zip(stages, spawned, full_cmds, strict=True):
                stage._finish(
                    run.process,
                    full_cmd,
                    stage.__timeout,
                    run.io_threads,
                    run.opened,
                    stage.__observers,
                    stage.__ok_exit_codes,
                )
            return spawned[-1].process
        finally:
            # Anything reached here means _finish() never ran for that stage - if
            # it's still alive, wait/clean it up now rather than leaking it. A stage
            # _finish() already handled always polls non-None by this point, so this
            # is a no-op on the success path - same as pipe(). zip() stops at
            # `spawned`'s length, so a stage that never got spawned (e.g. because an
            # earlier one raised CommandNotFound) is simply skipped here.
            for stage, run in zip(stages, spawned):
                if run.process.poll() is None:
                    stage._cleanup(run.process, run.opened, stage.__observers)

    @staticmethod
    def _pipeline_stage_io_threads(
        stage: Command, process: SyncProcess, *, is_first: bool, is_last: bool
    ) -> list[Thread]:
        """Start the I/O forwarding threads a single `pipeline()` stage needs.

        Every stage's stdin/stdout is a raw OS pipe to a neighboring stage and needs
        no thread, except the first stage's stdin and the last stage's stdout, which
        are the chain's only Python-facing ends. Every stage's stderr is independent
        of the piped data flow (it's never piped between stages) and is always
        handled the same way regardless of position.

        Args:
            stage: the stage that was just spawned.
            process: the stage's spawned subprocess.
            is_first: whether this is the first stage in the chain.
            is_last: whether this is the last stage in the chain.
        Returns:
            the I/O forwarding threads started for this stage, if any.
        """
        threads: list[Thread] = []
        if is_first:
            stdin_thread = stage._handle_stdin(process)
            if stdin_thread is not None:
                threads.append(stdin_thread)
        if is_last:
            threads.extend(stage._handle_stdout_stderr(process))
        else:
            stderr_thread = stage._handle_stream(process.stderr, stage.__err)
            if stderr_thread is not None:
                threads.append(stderr_thread)
        return threads

    async def _prepare_async_redirect(
        self,
        redirect: RedirectOut | None,
    ) -> int | IO[bytes]:
        """Prepare stdout/stderr redirect for async subprocess.

        Args:
            redirect: the redirect target.
        Returns:
            file descriptor or subprocess constant for the redirect.
        """
        if redirect is None:
            return subprocess.DEVNULL
        elif isinstance(redirect, Path):
            return await asyncio.to_thread(open, redirect, "wb")
        elif isinstance(redirect, IOBase):
            try:
                return redirect.fileno()
            except (OSError, AttributeError):
                return subprocess.PIPE
        elif _needs_forwarding(redirect):
            return subprocess.PIPE
        return subprocess.DEVNULL

    async def _apopen(
        self,
        stdin: int | IO[bytes] | None,
        stdout: int | IO[bytes],
        stderr: int | IO[bytes],
    ) -> AsyncProcess:
        """Spawn this command as an asyncio subprocess, translating a missing binary.

        Args:
            stdin: prepared stdin redirect.
            stdout: prepared stdout redirect.
            stderr: prepared stderr redirect.
        Returns:
            the spawned asyncio subprocess.
        Raises:
            CommandNotFound: if the binary can't be found.
        """
        try:
            return await asyncio.create_subprocess_exec(
                self.__cmd_path,
                *self.__args,
                env=self.__env,
                cwd=self.__cwd.as_posix(),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                user=self.__user,
                group=self.__group,
                extra_groups=self.__extra_groups,
                preexec_fn=_low_priority_hook(self.__nice),
                creationflags=_priority_creationflags(self.__nice),
            )
        except FileNotFoundError as err:
            raise CommandNotFound(self.__cmd_path) from err

    @staticmethod
    def _create_task_forwarder(
        stream: asyncio.StreamReader,
        target: RedirectOut,
    ) -> Task[None]:
        """Forward an async stream to configured target.

        Args:
            stream: the stream reader to forward from (stdout or stderr).
            target: the target destination (callable or TextIOBase).
        """

        async def forward() -> None:
            try:
                while True:
                    line = await _read_line(stream)
                    if not line:
                        break
                    decoded = line.decode(errors="replace")
                    if callable(target):
                        target(decoded)
                    elif isinstance(target, TextIOBase):
                        target.write(decoded)
            except (OSError, ValueError):
                pass

        return create_task(forward())

    def _async_handle_stdin(self, process: AsyncProcess) -> Task[None] | None:
        """Start a task feeding stdin from a Queue, str, bytes or fd-less file.

        Feeding happens in its own task so the configured timeout covers it.
        A child that exits without reading all of its input, or a file that
        can't be read, just stops the feeding.

        Args:
            process: the asyncio subprocess to feed stdin to.
        Returns:
            the created task, or None when stdin isn't fed from Python.
        """
        stdin = process.stdin
        source = self.__in
        if stdin is None or source is None or isinstance(source, queue.Queue):
            return None

        async def chunks() -> AsyncIterator[bytes]:
            if isinstance(source, (str, bytes)):
                yield _encode_input(source)
            elif isinstance(source, asyncio.Queue):
                while (data := await source.get()) is not None:
                    yield _encode_input(data)
            else:
                while data := source.read(_STDIN_CHUNK_SIZE):
                    yield _encode_input(data)

        async def forward() -> None:
            with suppress(OSError):
                async for chunk in chunks():
                    stdin.write(chunk)
                    await stdin.drain()
            stdin.close()
            with suppress(OSError):
                await stdin.wait_closed()

        return asyncio.create_task(forward())

    def _async_handle_stream(
        self, stream: asyncio.StreamReader | None, target: RedirectOut | None
    ) -> Task[None] | None:
        """Start a forwarding task for a single stdout/stderr stream, if it needs one.

        Args:
            stream: the process stream to forward from (stdout or stderr).
            target: the configured redirect target for that stream.
        Returns:
            the created task, or None if this stream doesn't need forwarding.
        """
        if _needs_forwarding(target) and stream is not None and target is not None:
            return self._create_task_forwarder(stream, target)
        return None

    def _async_handle_stdout_stderr(
        self,
        process: AsyncProcess,
    ) -> list[Task[None]]:
        """Create forwarding tasks for stdout and stderr.

        Args:
            process: the asyncio subprocess to forward output from.
        Returns:
            list of tasks started for output forwarding.
        """
        tasks: list[Task[None]] = []
        for proc_stream, target in (
            (process.stdout, self.__out),
            (process.stderr, self.__err),
        ):
            task = self._async_handle_stream(proc_stream, target)
            if task is not None:
                tasks.append(task)
        return tasks

    def _create_io_tasks(
        self,
        process: AsyncProcess,
    ) -> list[asyncio.Task[None]]:
        """Start tasks for forwarding I/O to callbacks/StringIO.

        Args:
            process: the asyncio subprocess to forward I/O for.
        Returns:
            list of tasks started for I/O forwarding.
        """
        # Start draining stdout/stderr before feeding stdin
        tasks = self._async_handle_stdout_stderr(process)

        stdin_task = self._async_handle_stdin(process)
        if stdin_task is not None:
            tasks.append(stdin_task)
        return tasks

    @staticmethod
    def _create_gc_task(
        process: AsyncProcess,
        io_tasks: list[asyncio.Task[None]],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
    ) -> None:
        """Clean up resources after async process completes.

        Args:
            process: the asyncio subprocess to wait for.
            io_tasks: list of I/O forwarding tasks to await.
            opened_files: list of file handles to close.
            observers: observers to notify once the process has exited.
        """

        async def garbage_collector() -> None:
            try:
                await process.wait()
            except OSError:
                pass
            finally:
                await gather(
                    *(asyncio.wait_for(task, timeout=5) for task in io_tasks),
                    return_exceptions=True,
                )
                close(opened_files)
                for obs in observers:
                    obs.on_exit(process)

        create_task(garbage_collector())

    @staticmethod
    async def _acleanup(
        process: AsyncProcess,
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
    ) -> None:
        """Async equivalent of `_cleanup`: close opened redirect files and
        terminate/kill a process if still alive.

        Args:
            process: the asyncio subprocess to clean up.
            opened_files: file handles to close.
            observers: observers to notify once the process has exited.
        """
        close(opened_files)
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
        for obs in observers:
            obs.on_exit(process)

    @staticmethod
    async def _afinish(
        process: AsyncProcess,
        full_cmd: str,
        timeout: int | None,
        io_tasks: list[Task[None]],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
        ok_exit_codes: frozenset[int],
    ) -> None:
        """Async equivalent of `_finish`: wait for a spawned process, raise on
        timeout/non-zero exit/cancellation, then clean up.

        Shared by `aexec()` and `apipe()` so both sides of a pipeline get identical
        timeout/error/cleanup semantics. Unlike a plain `except asyncio.CancelledError`,
        routing cleanup through the `finally` here means a still-running process also
        gets terminated on a raised `TimeoutException`, not just on cancellation.

        Args:
            process: the asyncio subprocess to wait for.
            full_cmd: command string, used in raised exceptions.
            timeout: timeout in seconds to wait for completion.
            io_tasks: I/O forwarding tasks to await.
            opened_files: file handles to close.
            observers: observers to notify once the process has exited.
            ok_exit_codes: exit codes considered successful.
        """
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError as err:
                assert timeout is not None
                raise TimeoutException(timeout=timeout, cmd=full_cmd) from err

            await gather(*io_tasks)

            if Command._is_failure(process.returncode, ok_exit_codes):
                raise ErrorReturnCode(cmd=full_cmd, exit_code=process.returncode)
        finally:
            await Command._acleanup(process, opened_files, observers)

    async def aexec(self, wait_completion: bool = True) -> AsyncProcess:
        """
        Run the command asynchronously and return asyncio.subprocess.Process directly.

        Args:
            wait_completion: wait for completion.
        Returns:
            asyncio.subprocess.Process.
        """
        full_cmd = self._full_cmd()

        # Prepare subprocess args for async
        stdout, stderr = await gather(
            self._prepare_async_redirect(self.__out),
            self._prepare_async_redirect(self.__err),
        )
        opened_files = self._opened(stdout, stderr)

        try:
            process = await self._apopen(self._prepare_stdin(), stdout, stderr)
        except CommandNotFound:
            close(opened_files)
            raise

        # Handle I/O concurrently with waiting
        io_tasks = self._create_io_tasks(process)
        self._notify_spawn(self.__observers, process)

        if wait_completion:
            await self._afinish(
                process,
                full_cmd,
                self.__timeout,
                io_tasks,
                opened_files,
                self.__observers,
                self.__ok_exit_codes,
            )
            return process
        else:
            self._create_gc_task(process, io_tasks, opened_files, self.__observers)
            return process

    async def apipe(self, other: Command) -> AsyncProcess:
        """
        Run this command and pipe its stdout directly into `other`'s stdin using
        asyncio subprocesses, then run `other` and wait for both processes to complete.

        Shorthand for `apipeline(other)` - see its docstring for the full contract.

        Args:
            other: the downstream command to receive this command's stdout as its stdin.
        Returns:
            the downstream command's completed asyncio.subprocess.Process.
        Raises:
            CommandNotFound: if either binary can't be found.
            ErrorReturnCode: if either process exits with a non-zero code.
            TimeoutException: if either command's configured timeout elapses.
        """
        return await self.apipeline(other)

    async def apipeline(self, *others: Command) -> AsyncProcess:
        """
        Run this command and each of `others` in sequence using asyncio subprocesses,
        piping every stage's stdout directly into the next stage's stdin, then wait
        for all processes to complete.

        Async equivalent of `pipeline()` - see its docstring for the full contract
        (what gets ignored/overwritten on each stage, error/timeout semantics).
        asyncio subprocess streams are `StreamReader`/`StreamWriter` objects, not file
        objects that can be wired directly into another subprocess call the way
        `pipeline()` wires `subprocess.Popen` streams together, so this opens one OS
        pipe per internal edge with `os.pipe()` and hands one end to each neighboring
        stage - the same raw, binary-safe OS-level connection `pipeline()` uses,
        without decoding through Python.

        Args:
            others: the downstream commands to chain after this one, in order.
        Returns:
            the last command's completed asyncio.subprocess.Process.
        Raises:
            ValueError: if no downstream commands are given.
            CommandNotFound: if any binary can't be found.
            ErrorReturnCode: if any process exits with a non-zero code.
            TimeoutException: if any command's configured timeout elapses.
        """
        if not others:
            raise ValueError("apipeline() requires at least one downstream command")
        stages: list[Command] = [self, *others]
        last_index = len(stages) - 1
        full_cmds = [stage._full_cmd() for stage in stages]

        spawned: list[_AsyncPipelineStage] = []
        pending_read_fd: int | None = None
        try:
            for index, stage in enumerate(stages):
                is_first = index == 0
                is_last = index == last_index

                stderr = await stage._prepare_async_redirect(stage.__err)

                next_read_fd: int | None = None
                write_fd: int | None = None
                if is_last:
                    stdout = await stage._prepare_async_redirect(stage.__out)
                else:
                    next_read_fd, write_fd = os.pipe()
                    stdout = write_fd

                opened = stage._opened(stdout, stderr)
                stdin = stage._prepare_stdin() if is_first else pending_read_fd

                try:
                    try:
                        process = await stage._apopen(stdin, stdout, stderr)
                    except CommandNotFound:
                        close(opened)
                        if next_read_fd is not None:
                            os.close(next_read_fd)
                        raise
                finally:
                    # Same reasoning as apipe(): the parent's copy of each fd handed
                    # to a child must go once that child has its own (or failed to
                    # spawn), or the writer would never see SIGPIPE / the parent
                    # would be left holding the pipe open.
                    if write_fd is not None:
                        os.close(write_fd)
                    if not is_first:
                        assert pending_read_fd is not None
                        os.close(pending_read_fd)

                io_tasks = self._apipeline_stage_io_tasks(
                    stage, process, is_first=is_first, is_last=is_last
                )
                stage._notify_spawn(stage.__observers, process)
                spawned.append(_AsyncPipelineStage(process, io_tasks, opened))
                pending_read_fd = next_read_fd

            for stage, run, full_cmd in zip(stages, spawned, full_cmds, strict=True):
                await stage._afinish(
                    run.process,
                    full_cmd,
                    stage.__timeout,
                    run.io_tasks,
                    run.opened,
                    stage.__observers,
                    stage.__ok_exit_codes,
                )
            return spawned[-1].process
        finally:
            # zip() stops at `spawned`'s length, so a stage that never got spawned
            # (e.g. because an earlier one raised CommandNotFound) is simply skipped
            # here - see the same note in pipeline().
            for stage, run in zip(stages, spawned):
                if run.process.returncode is None:
                    await stage._acleanup(run.process, run.opened, stage.__observers)

    @staticmethod
    def _apipeline_stage_io_tasks(
        stage: Command, process: AsyncProcess, *, is_first: bool, is_last: bool
    ) -> list[Task[None]]:
        """Start the I/O forwarding tasks a single `apipeline()` stage needs.

        Async equivalent of `_pipeline_stage_io_threads` - see its docstring.

        Args:
            stage: the stage that was just spawned.
            process: the stage's spawned asyncio subprocess.
            is_first: whether this is the first stage in the chain.
            is_last: whether this is the last stage in the chain.
        Returns:
            the I/O forwarding tasks started for this stage, if any.
        """
        tasks: list[Task[None]] = []
        if is_first:
            stdin_task = stage._async_handle_stdin(process)
            if stdin_task is not None:
                tasks.append(stdin_task)
        if is_last:
            tasks.extend(stage._async_handle_stdout_stderr(process))
        else:
            stderr_task = stage._async_handle_stream(process.stderr, stage.__err)
            if stderr_task is not None:
                tasks.append(stderr_task)
        return tasks


class Bind:
    """Wrapper to create bind tied to command for process execution."""

    def __init__(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> None:
        """
        Init bind.

        Args:
            cmd_path: command path.
            search_path: directories to look the binary up in instead of `PATH`.
        Raises:
            CommandNotFound: if the command binary can't be found.
        """
        self._cmd: Command = self.create(cmd_path, search_path)

    def create(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> Command:
        """
        Create command wrapper for process execution.

        Args:
            cmd_path: command name or path.
            search_path: directories to look the binary up in instead of `PATH`.
        Returns:
            command wrapper.
        """
        return Command(cmd_path, search_path)

    def add_observer(self, observer: ProcessObserver) -> Self:
        """Register an observer to be notified on process spawn and exit."""
        self._cmd.add_observer(observer)
        return self

    @overload
    def env(self) -> dict[str, str]: ...

    @overload
    def env(self, value: dict[str, str]) -> Self: ...

    def env(self, value: dict[str, str] | None = None) -> dict[str, str] | Self:
        """
        Returns current env vars if no value is provided, otherwise set env vars.

        Args:
            value: env vars to set.
        Returns:
            current env vars if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.env()
        self._cmd.env(value)
        return self

    @overload
    def cwd(self) -> Path: ...

    @overload
    def cwd(self, value: Path) -> Self: ...

    def cwd(self, value: Path | None = None) -> Path | Self:
        """
        Returns current cwd if no value is provided, otherwise set cwd.

        Args:
            value: cwd to set.
        Returns:
            current cwd if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.cwd()
        self._cmd.cwd(value)
        return self

    @overload
    def user(self) -> str | int | None: ...

    @overload
    def user(self, value: str | int) -> Self: ...

    def user(self, value: str | int | None = None) -> str | int | None | Self:
        """
        Returns current user if no value is provided, otherwise set user.

        Args:
            value: user (name or uid) to run the command as.
        Returns:
            current user if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.user()
        self._cmd.user(value)
        return self

    @overload
    def group(self) -> str | int | None: ...

    @overload
    def group(self, value: str | int) -> Self: ...

    def group(self, value: str | int | None = None) -> str | int | None | Self:
        """
        Returns current group if no value is provided, otherwise set group.

        Args:
            value: group (name or gid) to run the command as.
        Returns:
            current group if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.group()
        self._cmd.group(value)
        return self

    @overload
    def extra_groups(self) -> list[str | int] | None: ...

    @overload
    def extra_groups(self, value: Sequence[str | int]) -> Self: ...

    def extra_groups(
        self, value: Sequence[str | int] | None = None
    ) -> list[str | int] | None | Self:
        """
        Returns current supplementary groups if no value is provided, otherwise set them.

        Args:
            value: supplementary groups (names or gids); empty drops them all.
        Returns:
            current supplementary groups if no value is provided or self for chaining.
        Raises:
            ValueError: if set on a non-POSIX platform.
        """
        if value is None:
            return self._cmd.extra_groups()
        self._cmd.extra_groups(value)
        return self

    @overload
    def nice(self) -> int | None: ...

    @overload
    def nice(self, value: int) -> Self: ...

    def nice(self, value: int | None = None) -> int | None | Self:
        """
        Returns current niceness if no value is provided, otherwise set it.

        Args:
            value: niceness to run the command with, e.g. 19 for the lowest priority.
        Returns:
            current niceness if no value is provided or self for chaining.
        """
        if value is None:
            return self._cmd.nice()
        self._cmd.nice(value)
        return self

    def stdin(self, value: RedirectIn) -> Self:
        """
        Set stdin redirect.

        Args:
            value: stdin to set.
        Returns:
            self for chaining.
        """
        self._cmd.stdin(value)
        return self

    def stdout(self, value: RedirectOut) -> Self:
        """
        Set stdout redirect.

        Args:
            value: stdout to set.
        Returns:
            self for chaining.
        """
        self._cmd.stdout(value)
        return self

    def stderr(self, value: RedirectOut) -> Self:
        """
        Set stderr redirect.

        Args:
            value: stderr to set.
        Returns:
            self for chaining.
        """
        self._cmd.stderr(value)
        return self

    def inherit(self) -> Self:
        """
        Inherit stdin, stdout and stderr from current process.

        Returns:
            self for chaining.
        """
        self._cmd.inherit()
        return self

    def inherit_out(self, if_unset: bool = False) -> Self:
        """
        Inherit stdout and stderr from current process.

        Args:
            if_unset: when True, only inherit a stream if it is not already configured.

        Returns:
            self for chaining.
        """
        self._cmd.inherit_out(if_unset=if_unset)
        return self

    def timeout(self, timeout: int) -> Self:
        """Set a timeout for process to spawn."""
        self._cmd.timeout(timeout)
        return self

    def ok_exit_codes(self, *codes: int) -> Self:
        """Set the exit codes considered successful (default: only 0)."""
        self._cmd.ok_exit_codes(*codes)
        return self

    def binary_path(self) -> Path:
        """
        Returns:
            path where binary is installed.
        """
        return self._cmd.binary_path()

    def copy(self, cmd: Command) -> Self:
        """Copy all parameters from another command."""
        self._cmd.copy(cmd)
        return self

    def pipe(self, other: Bind | Command) -> SyncProcess:
        """
        Run this bind's command and pipe its stdout directly into `other`'s stdin.

        Shorthand for `pipeline(other)` - see `Command.pipe()` for the full contract
        (raw OS pipe, error/timeout semantics, what gets ignored/overwritten on each
        side).

        Args:
            other: the downstream bind or command to receive this command's stdout.
        Returns:
            the downstream command's completed subprocess.Popen.
        """
        return self.pipeline(other)

    def pipeline(self, *others: Bind | Command) -> SyncProcess:
        """
        Run this bind's command and each of `others` in sequence, piping every
        stage's stdout directly into the next stage's stdin.

        See `Command.pipeline()` for the full contract (raw OS pipe, error/timeout
        semantics, what gets ignored/overwritten on each stage).

        Args:
            others: the downstream binds or commands to chain after this one, in order.
        Returns:
            the last command's completed subprocess.Popen.
        """
        return self._cmd.pipeline(
            *(other._cmd if isinstance(other, Bind) else other for other in others)
        )

    async def apipe(self, other: Bind | Command) -> AsyncProcess:
        """
        Run this bind's command and pipe its stdout directly into `other`'s stdin
        using asyncio subprocesses.

        Shorthand for `apipeline(other)` - see `Command.apipe()` for the full
        contract.

        Args:
            other: the downstream bind or command to receive this command's stdout.
        Returns:
            the downstream command's completed asyncio.subprocess.Process.
        """
        return await self.apipeline(other)

    async def apipeline(self, *others: Bind | Command) -> AsyncProcess:
        """
        Run this bind's command and each of `others` in sequence using asyncio
        subprocesses, piping every stage's stdout directly into the next stage's
        stdin.

        See `Command.apipeline()` for the full contract.

        Args:
            others: the downstream binds or commands to chain after this one, in order.
        Returns:
            the last command's completed asyncio.subprocess.Process.
        """
        return await self._cmd.apipeline(
            *(other._cmd if isinstance(other, Bind) else other for other in others)
        )
