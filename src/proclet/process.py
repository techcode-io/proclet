"""Command builder and runner, process types and the process observer protocol."""

from __future__ import annotations

import asyncio
import os
import queue
import shutil
import subprocess
import sys
from asyncio import Task, create_task, gather
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import suppress
from pathlib import Path
from threading import Thread
from typing import IO, NamedTuple, Protocol, Self, overload

from proclet._io import close
from proclet._platform import low_priority_hook, priority_creationflags, require_os
from proclet._streams import (
    STDIN_CHUNK_SIZE,
    dispatch_line,
    encode_input,
    needs_forwarding,
    read_line,
)
from proclet.errors import CommandNotFound, ErrorReturnCode, TimeoutException
from proclet.redirect import RedirectIn, RedirectOut

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
    """Observer notified at process spawn and exit.

    Hooks run synchronously: on the caller's thread or event loop, except
    `on_exit` after `exec(wait_completion=False)`, which runs on a background
    thread. They must not raise, as an error there would escape the cleanup.
    """

    def on_spawn(self, process: Process) -> None:
        """Called once the process is spawned, before waiting for it."""
        ...

    def on_exit(self, process: Process) -> None:
        """Called once the process has exited (or been terminated) and cleaned up."""
        ...


# Strong references to fire-and-forget tasks, so they can't be collected mid-run.
_background_tasks: set[Task[None]] = set()


class Command:
    """Fluent builder and runner for one external command.

    Configure it with the chainable setters (most double as a getter when
    called without a value), then run it with `exec()`/`aexec()` or chain
    commands with `pipeline()`/`apipeline()`.
    """

    def __init__(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> None:
        """Init command.

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
        self.__timeout: float | None = None
        self.__ok_exit_codes: frozenset[int] = frozenset({0})
        self.__user: str | int | None = None
        self.__group: str | int | None = None
        self.__extra_groups: list[str | int] | None = None
        self.__nice: int | None = None
        self.__observers: list[ProcessObserver] = []

    @overload
    def args(self) -> tuple[str, ...]: ...

    @overload
    def args(self, first: str, /, *values: str) -> Self: ...

    def args(self, *values: str) -> tuple[str, ...] | Self:
        """Returns current arguments if no value is provided, otherwise set arguments.

        Setting replaces the previous arguments. Calling with no value reads
        them, so arguments can't be cleared back to none this way.

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
        """Returns current env vars if no value is provided, otherwise set env vars.

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
        """Returns current cwd if no value is provided, otherwise set cwd.

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
        """Returns current user if no value is provided, otherwise set user.

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

        require_os("user", "setreuid")
        self.__user = value
        return self

    @overload
    def group(self) -> str | int | None: ...

    @overload
    def group(self, value: str | int) -> Self: ...

    def group(self, value: str | int | None = None) -> str | int | None | Self:
        """Returns current group if no value is provided, otherwise set group.

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

        require_os("group", "setregid")
        self.__group = value
        return self

    @overload
    def extra_groups(self) -> list[str | int] | None: ...

    @overload
    def extra_groups(self, value: Sequence[str | int]) -> Self: ...

    def extra_groups(
        self, value: Sequence[str | int] | None = None
    ) -> list[str | int] | None | Self:
        """Returns current supplementary groups if no value is provided, otherwise set them.

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

        require_os("extra_groups", "setgroups")
        self.__extra_groups = list(value)
        return self

    @overload
    def nice(self) -> int | None: ...

    @overload
    def nice(self, value: int) -> Self: ...

    def nice(self, value: int | None = None) -> int | None | Self:
        """Returns current niceness if no value is provided, otherwise set it.

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
        """Returns current stdin if no value is provided, otherwise set stdin.

        Accepts a `str` (encoded as UTF-8) or `bytes` payload, a file object
        (one with a real fd is handed to the child, others such as `StringIO`
        are read and fed), or a queue fed until it yields `None`: a
        `queue.Queue` for `exec()`/`pipeline()`, an `asyncio.Queue` for
        `aexec()`/`apipeline()`. Unset, the child reads from /dev/null.

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
        """Returns current stdout if no value is provided, otherwise set stdout.

        Accepts a `Path` (truncated and written; a relative one is resolved
        against the current directory now, not against `cwd()`), a file object,
        or a callable receiving each decoded line. Unset, output is discarded.

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
        """Returns current stderr if no value is provided, otherwise set stderr.

        Accepts the same targets as `stdout()`.

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
        """Inherit stdin, stdout and stderr from current process.

        Returns:
            self for chaining.
        """
        self.__in = sys.stdin
        self.inherit_out()
        return self

    def inherit_out(self, if_unset: bool = False) -> Self:
        """Inherit stdout and stderr from current process.

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

    def timeout(self, timeout: float) -> Self:
        """Set how long a run may take before `TimeoutException` is raised.

        The process is terminated once it elapses. Only enforced when the run
        waits for completion: `exec(wait_completion=False)` and
        `aexec(wait_completion=False)` ignore it. In a pipeline each stage keeps
        its own timeout, counted from when the previous stage finished.

        Args:
            timeout: maximum run time, in seconds.
        Returns:
            self for chaining.
        """
        self.__timeout = timeout
        return self

    def ok_exit_codes(self, *codes: int) -> Self:
        """Set the exit codes considered successful (default: only 0).

        Any other exit code raises `ErrorReturnCode`. Useful for tools that use
        non-zero codes for warnings, e.g. GNU tar exits 1 when a file changed
        while being read.

        Args:
            *codes: the successful exit codes; they replace the current set.
        Returns:
            self for chaining.
        Raises:
            ValueError: if no code is given, which would make every run fail.
        """
        if not codes:
            raise ValueError("ok_exit_codes() requires at least one exit code")
        self.__ok_exit_codes = frozenset(codes)
        return self

    def binary_path(self) -> Path:
        """Get the binary this command runs.

        Returns:
            the binary's path, as resolved at construction.
        """
        return Path(self.__cmd_path)

    def add_observer(self, observer: ProcessObserver) -> Self:
        """Register an observer to be notified on process spawn and exit."""
        self.__observers.append(observer)
        return self

    def copy(self, cmd: Command) -> Self:
        """Copy all parameters from another command, keeping this one's binary.

        Collections are copied; redirect targets and observers are shared.

        Args:
            cmd: the command to copy parameters from.
        Returns:
            self for chaining.
        """
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

    def _check_stdin_mode(self, *, asynchronous: bool) -> None:
        """Reject a stdin queue that the requested execution mode can't consume.

        Args:
            asynchronous: whether the command is about to run through asyncio.
        Raises:
            TypeError: if stdin is a `queue.Queue` for an async run, or an
                `asyncio.Queue` for a sync one.
        """
        if asynchronous and isinstance(self.__in, queue.Queue):
            raise TypeError("async runs need an asyncio.Queue stdin, not a queue.Queue")
        if not asynchronous and isinstance(self.__in, asyncio.Queue):
            raise TypeError("sync runs need a queue.Queue stdin, not an asyncio.Queue")

    def _prepare_stdin(self) -> int | None:
        """Convert the configured stdin into a file descriptor or subprocess constant.

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
        Returns:
            the opened file for a `Path`, otherwise see `_redirect_fd`.
        """
        if isinstance(redirect, Path):
            return redirect.open("wb")
        return Command._redirect_fd(redirect)

    @staticmethod
    def _redirect_fd(redirect: RedirectOut | None) -> int:
        """Pick the fd or subprocess constant for a non-`Path` stdout/stderr redirect.

        Shared by `_prepare_redirect()` and `_aprepare_redirect()`.

        Args:
            redirect: the redirect target.
        Returns:
            subprocess.PIPE for targets forwarded from Python (callables, file
            objects without a real fd), the fd of other file objects, or
            subprocess.DEVNULL when unset or unusable.
        """
        if needs_forwarding(redirect):
            return subprocess.PIPE
        try:
            return redirect.fileno()
        except (OSError, AttributeError):
            return subprocess.DEVNULL

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
                # Only plain syscalls, resolved before the fork (see low_priority_hook).
                preexec_fn=low_priority_hook(self.__nice),  # noqa: PLW1509
                creationflags=priority_creationflags(self.__nice),
            )
        except FileNotFoundError as err:
            raise CommandNotFound(self.__cmd_path) from err

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
                yield encode_input(source)
            elif isinstance(source, queue.Queue):
                while (data := source.get()) is not None:
                    yield encode_input(data)
            else:
                while data := source.read(STDIN_CHUNK_SIZE):
                    yield encode_input(data)

        def forward() -> None:
            with suppress(OSError):
                for chunk in chunks():
                    stdin.write(chunk)
            close([stdin])

        thread = Thread(target=forward, daemon=True)
        thread.start()
        return thread

    def _handle_stream(
        self, stream: IO[bytes] | None, target: RedirectOut | None
    ) -> Thread | None:
        """Start a thread forwarding a stdout/stderr stream's lines, if it needs one.

        Args:
            stream: the process stream to forward from (stdout or stderr).
            target: the configured redirect target for that stream, see
                `dispatch_line`.
        Returns:
            the started thread, or None if this stream doesn't need forwarding.
        """
        if stream is None or target is None or not needs_forwarding(target):
            return None

        def forward() -> None:
            try:
                for line in iter(stream.readline, b""):
                    dispatch_line(target, line)
            except (OSError, ValueError):
                pass
            finally:
                stream.close()

        thread = Thread(target=forward, daemon=True)
        thread.start()
        return thread

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

    def _start_io(
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
    def _reap_in_background(
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

        def reap() -> None:
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

        thread = Thread(target=reap, daemon=True)
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
        timeout: float | None,
        io_threads: list[Thread],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
        ok_exit_codes: frozenset[int],
    ) -> None:
        """Wait for a spawned process, raise on timeout/failed exit, then clean up.

        Shared by `exec()` and `pipeline()` so a lone command and every stage of
        a pipeline get identical timeout/error/cleanup semantics.

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
        """Run the command.

        By default this waits for the process and enforces `timeout()` and
        `ok_exit_codes()`. With `wait_completion=False` it returns as soon as
        the process is spawned: neither is enforced, and redirect files are
        closed and observers' `on_exit` called from a background thread once
        the process exits.

        Args:
            wait_completion: wait for the process to exit before returning.
        Returns:
            the subprocess.Popen process, finished when waited for.
        Raises:
            CommandNotFound: if the binary can't be found.
            ErrorReturnCode: if the process exits with a code outside `ok_exit_codes`.
            TimeoutException: if the configured timeout elapses.
            TypeError: if stdin is an `asyncio.Queue`.
        """
        self._check_stdin_mode(asynchronous=False)
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
        io_threads = self._start_io(process)
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
            self._reap_in_background(
                process, io_threads, opened_files, self.__observers
            )
            return process

    def pipe(self, other: Command) -> SyncProcess:
        """Run this command and pipe its stdout directly into `other`'s stdin, then run
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
            ErrorReturnCode: if either process exits with a code outside its `ok_exit_codes`.
            TimeoutException: if either command's configured timeout elapses.
        """
        return self.pipeline(other)

    def pipeline(self, *others: Command) -> SyncProcess:
        """Run this command and each of `others` concurrently, piping every stage's
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
        stage's stderr, env, cwd, timeout, exit codes and observers are honored
        normally.

        Stages are waited on in order, so a stage's timeout counts from when the
        previous stage finished. The first failure is raised and every stage
        still running is then terminated.

        See `apipeline()` for the asyncio equivalent.

        Args:
            others: the downstream commands to chain after this one, in order.
        Returns:
            the last command's completed subprocess.Popen.
        Raises:
            ValueError: if no downstream commands are given.
            TypeError: if this command's stdin queue doesn't match the run mode.
            CommandNotFound: if any binary can't be found.
            ErrorReturnCode: if any process exits with a code outside its `ok_exit_codes`.
            TimeoutException: if any command's configured timeout elapses.
        """
        if not others:
            raise ValueError("pipeline() requires at least one downstream command")
        self._check_stdin_mode(asynchronous=False)

        stages: list[Command] = [self, *others]
        last_index = len(stages) - 1
        full_cmds = [stage._full_cmd() for stage in stages]

        spawned: list[_PipelineStage] = []
        finished = 0
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
                        # left as the pipe's last reader.
                        assert upstream.stdout is not None
                        upstream.stdout.close()

                io_threads = self._start_stage_io(
                    stage, process, is_first=is_first, is_last=is_last
                )
                stage._notify_spawn(stage.__observers, process)
                spawned.append(_PipelineStage(process, io_threads, opened))

            for stage, run, full_cmd in zip(stages, spawned, full_cmds, strict=True):
                finished += 1  # _finish() cleans its stage up even when it raises
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
            # Clean up every spawned stage _finish() never got to, running or
            # already exited, so its files are closed and observers notified.
            # Nothing is left on the success path. zip() stops at `spawned`'s
            # length, so a stage that never got spawned (e.g. because an earlier
            # one raised CommandNotFound) is skipped.
            for stage, run in zip(stages[finished:], spawned[finished:]):
                stage._cleanup(run.process, run.opened, stage.__observers)

    @staticmethod
    def _start_stage_io(
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

    @staticmethod
    async def _aprepare_redirect(
        redirect: RedirectOut | None,
    ) -> int | IO[bytes]:
        """Async equivalent of `_prepare_redirect`, opening a `Path` off the event loop.

        Args:
            redirect: the redirect target.
        Returns:
            the opened file for a `Path`, otherwise see `_redirect_fd`.
        """
        if isinstance(redirect, Path):
            return await asyncio.to_thread(redirect.open, "wb")
        return Command._redirect_fd(redirect)

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
                preexec_fn=low_priority_hook(self.__nice),
                creationflags=priority_creationflags(self.__nice),
            )
        except FileNotFoundError as err:
            raise CommandNotFound(self.__cmd_path) from err

    def _ahandle_stdin(self, process: AsyncProcess) -> Task[None] | None:
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
                yield encode_input(source)
            elif isinstance(source, asyncio.Queue):
                while (data := await source.get()) is not None:
                    yield encode_input(data)
            else:
                while data := source.read(STDIN_CHUNK_SIZE):
                    yield encode_input(data)

        async def forward() -> None:
            with suppress(OSError):
                async for chunk in chunks():
                    stdin.write(chunk)
                    await stdin.drain()
            stdin.close()
            with suppress(OSError):
                await stdin.wait_closed()

        return create_task(forward())

    def _ahandle_stream(
        self, stream: asyncio.StreamReader | None, target: RedirectOut | None
    ) -> Task[None] | None:
        """Start a task forwarding a stdout/stderr stream's lines, if it needs one.

        Args:
            stream: the process stream to forward from (stdout or stderr).
            target: the configured redirect target for that stream, see
                `dispatch_line`.
        Returns:
            the created task, or None if this stream doesn't need forwarding.
        """
        if stream is None or target is None or not needs_forwarding(target):
            return None

        async def forward() -> None:
            try:
                while True:
                    line = await read_line(stream)
                    if not line:
                        break
                    dispatch_line(target, line)
            except (OSError, ValueError):
                pass

        return create_task(forward())

    def _ahandle_stdout_stderr(
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
            task = self._ahandle_stream(proc_stream, target)
            if task is not None:
                tasks.append(task)
        return tasks

    def _astart_io(
        self,
        process: AsyncProcess,
    ) -> list[Task[None]]:
        """Start tasks for forwarding I/O to callbacks/StringIO.

        Args:
            process: the asyncio subprocess to forward I/O for.
        Returns:
            list of tasks started for I/O forwarding.
        """
        # Start draining stdout/stderr before feeding stdin
        tasks = self._ahandle_stdout_stderr(process)

        stdin_task = self._ahandle_stdin(process)
        if stdin_task is not None:
            tasks.append(stdin_task)
        return tasks

    @staticmethod
    def _areap_in_background(
        process: AsyncProcess,
        io_tasks: list[Task[None]],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
    ) -> None:
        """Start a background task to clean up resources after process exits.

        Args:
            process: the asyncio subprocess to wait for.
            io_tasks: list of I/O forwarding tasks to await.
            opened_files: list of file handles to close.
            observers: observers to notify once the process has exited.
        """

        async def reap() -> None:
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

        # The event loop only keeps weak references to tasks.
        task = create_task(reap())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    @staticmethod
    async def _acleanup(
        process: AsyncProcess,
        io_tasks: list[Task[None]],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
    ) -> None:
        """Async equivalent of `_cleanup`: close opened redirect files,
        terminate/kill a process if still alive and cancel its pending I/O tasks.

        Args:
            process: the asyncio subprocess to clean up.
            io_tasks: I/O forwarding tasks, cancelled if still pending (e.g. a
                stdin queue never fed its `None` sentinel).
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
        for task in io_tasks:
            task.cancel()
        await gather(*io_tasks, return_exceptions=True)
        for obs in observers:
            obs.on_exit(process)

    @staticmethod
    async def _afinish(
        process: AsyncProcess,
        full_cmd: str,
        timeout: float | None,
        io_tasks: list[Task[None]],
        opened_files: list[IO[bytes]],
        observers: list[ProcessObserver],
        ok_exit_codes: frozenset[int],
    ) -> None:
        """Async equivalent of `_finish`: wait for a spawned process, raise on
        timeout/failed exit, then clean up.

        Shared by `aexec()` and `apipeline()`. Cleanup runs on every exit path,
        so a still-running process is terminated on a timeout, an error or a
        cancellation of the awaiting task alike.

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
            await Command._acleanup(process, io_tasks, opened_files, observers)

    async def aexec(self, wait_completion: bool = True) -> AsyncProcess:
        """Run the command with asyncio, the async equivalent of `exec()`.

        With `wait_completion=False` the cleanup runs in a background task
        instead of a thread; see `exec()`.

        Args:
            wait_completion: wait for the process to exit before returning.
        Returns:
            the asyncio.subprocess.Process, finished when waited for.
        Raises:
            CommandNotFound: if the binary can't be found.
            ErrorReturnCode: if the process exits with a code outside `ok_exit_codes`.
            TimeoutException: if the configured timeout elapses.
            TypeError: if stdin is a `queue.Queue`.
        """
        self._check_stdin_mode(asynchronous=True)
        full_cmd = self._full_cmd()

        # Prepare subprocess args for async
        stdout, stderr = await gather(
            self._aprepare_redirect(self.__out),
            self._aprepare_redirect(self.__err),
        )
        opened_files = self._opened(stdout, stderr)

        try:
            process = await self._apopen(self._prepare_stdin(), stdout, stderr)
        except CommandNotFound:
            close(opened_files)
            raise

        # Handle I/O concurrently with waiting
        io_tasks = self._astart_io(process)
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
            self._areap_in_background(process, io_tasks, opened_files, self.__observers)
            return process

    async def apipe(self, other: Command) -> AsyncProcess:
        """Run this command and pipe its stdout directly into `other`'s stdin using
        asyncio subprocesses, then run `other` and wait for both processes to complete.

        Shorthand for `apipeline(other)` - see its docstring for the full contract.

        Args:
            other: the downstream command to receive this command's stdout as its stdin.
        Returns:
            the downstream command's completed asyncio.subprocess.Process.
        Raises:
            CommandNotFound: if either binary can't be found.
            ErrorReturnCode: if either process exits with a code outside its `ok_exit_codes`.
            TimeoutException: if either command's configured timeout elapses.
        """
        return await self.apipeline(other)

    async def apipeline(self, *others: Command) -> AsyncProcess:
        """Run this command and each of `others` concurrently using asyncio
        subprocesses, piping every stage's stdout directly into the next stage's
        stdin, then wait for all processes to complete.

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
            TypeError: if this command's stdin queue doesn't match the run mode.
            CommandNotFound: if any binary can't be found.
            ErrorReturnCode: if any process exits with a code outside its `ok_exit_codes`.
            TimeoutException: if any command's configured timeout elapses.
        """
        if not others:
            raise ValueError("apipeline() requires at least one downstream command")
        self._check_stdin_mode(asynchronous=True)
        stages: list[Command] = [self, *others]
        last_index = len(stages) - 1
        full_cmds = [stage._full_cmd() for stage in stages]

        spawned: list[_AsyncPipelineStage] = []
        finished = 0
        pending_read_fd: int | None = None
        try:
            for index, stage in enumerate(stages):
                is_first = index == 0
                is_last = index == last_index

                stderr = await stage._aprepare_redirect(stage.__err)

                next_read_fd: int | None = None
                write_fd: int | None = None
                if is_last:
                    stdout = await stage._aprepare_redirect(stage.__out)
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
                    # Same reasoning as in pipeline(): the parent's copy of each fd
                    # handed to a child must go once that child has its own (or
                    # failed to spawn), or the writer would never see SIGPIPE / the
                    # parent would be left holding the pipe open.
                    if write_fd is not None:
                        os.close(write_fd)
                    if not is_first:
                        assert pending_read_fd is not None
                        os.close(pending_read_fd)

                io_tasks = self._astart_stage_io(
                    stage, process, is_first=is_first, is_last=is_last
                )
                stage._notify_spawn(stage.__observers, process)
                spawned.append(_AsyncPipelineStage(process, io_tasks, opened))
                pending_read_fd = next_read_fd

            for stage, run, full_cmd in zip(stages, spawned, full_cmds, strict=True):
                finished += 1  # _afinish() cleans its stage up even when it raises
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
            # Same as pipeline(): clean up every spawned stage _afinish() never
            # got to, running or already exited.
            for stage, run in zip(stages[finished:], spawned[finished:]):
                await stage._acleanup(
                    run.process, run.io_tasks, run.opened, stage.__observers
                )

    @staticmethod
    def _astart_stage_io(
        stage: Command, process: AsyncProcess, *, is_first: bool, is_last: bool
    ) -> list[Task[None]]:
        """Start the I/O forwarding tasks a single `apipeline()` stage needs.

        Async equivalent of `_start_stage_io` - see its docstring.

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
            stdin_task = stage._ahandle_stdin(process)
            if stdin_task is not None:
                tasks.append(stdin_task)
        if is_last:
            tasks.extend(stage._ahandle_stdout_stderr(process))
        else:
            stderr_task = stage._ahandle_stream(process.stderr, stage.__err)
            if stderr_task is not None:
                tasks.append(stderr_task)
        return tasks
