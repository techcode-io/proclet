"""Base class for typed wrappers around one binary."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Self, overload

from proclet.process import AsyncProcess, Command, ProcessObserver, SyncProcess
from proclet.redirect import RedirectIn, RedirectOut


class Bind:
    """Base class for typed wrappers around one binary.

    Subclasses call `super().__init__("<binary>")` and drive `self._cmd`, the
    underlying `Command`; the methods below forward to it. A subclass that
    needs extra env vars should merge them onto `self.env()` rather than
    replace it, so a caller's env survives.
    """

    def __init__(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> None:
        """Init bind.

        Args:
            cmd_path: command name or path, see `Command`.
            search_path: directories to look the binary up in instead of `PATH`.
        Raises:
            CommandNotFound: if the command binary can't be found.
        """
        self._cmd: Command = self.create(cmd_path, search_path)

    def create(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> Command:
        """Create the underlying command; override to customise it.

        Args:
            cmd_path: command name or path.
            search_path: directories to look the binary up in instead of `PATH`.
        Returns:
            the command.
        Raises:
            CommandNotFound: if the command binary can't be found.
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
        """Returns current env vars if no value is provided, otherwise set env vars.

        See `Command.env()`: unset inherits the parent's environment.

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
        """Returns current cwd if no value is provided, otherwise set cwd.

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
        """Returns current user if no value is provided, otherwise set user.

        See `Command.user()`: needs the privileges to switch user.

        Args:
            value: user (name or uid) to run the command as.
        Returns:
            current user if no value is provided or self for chaining.
        Raises:
            ValueError: if set on a non-POSIX platform.
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
        """Returns current group if no value is provided, otherwise set group.

        See `Command.group()`.

        Args:
            value: group (name or gid) to run the command as.
        Returns:
            current group if no value is provided or self for chaining.
        Raises:
            ValueError: if set on a non-POSIX platform.
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
        """Returns current supplementary groups if no value is provided, otherwise set them.

        See `Command.extra_groups()`: unset keeps the parent's groups.

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
        """Returns current niceness if no value is provided, otherwise set it.

        See `Command.nice()` for the per-platform behaviour.

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
        """Set stdin redirect, see `Command.stdin()` for the accepted values.

        Args:
            value: stdin to set.
        Returns:
            self for chaining.
        """
        self._cmd.stdin(value)
        return self

    def stdout(self, value: RedirectOut) -> Self:
        """Set stdout redirect, see `Command.stdout()` for the accepted values.

        Args:
            value: stdout to set.
        Returns:
            self for chaining.
        """
        self._cmd.stdout(value)
        return self

    def stderr(self, value: RedirectOut) -> Self:
        """Set stderr redirect, see `Command.stdout()` for the accepted values.

        Args:
            value: stderr to set.
        Returns:
            self for chaining.
        """
        self._cmd.stderr(value)
        return self

    def inherit(self) -> Self:
        """Inherit stdin, stdout and stderr from current process.

        Returns:
            self for chaining.
        """
        self._cmd.inherit()
        return self

    def inherit_out(self, if_unset: bool = False) -> Self:
        """Inherit stdout and stderr from current process.

        Args:
            if_unset: when True, only inherit a stream if it is not already configured.
        Returns:
            self for chaining.
        """
        self._cmd.inherit_out(if_unset=if_unset)
        return self

    def timeout(self, timeout: float) -> Self:
        """Set how long a run may take, see `Command.timeout()`.

        Args:
            timeout: maximum run time, in seconds.
        Returns:
            self for chaining.
        """
        self._cmd.timeout(timeout)
        return self

    def ok_exit_codes(self, *codes: int) -> Self:
        """Set the exit codes considered successful, see `Command.ok_exit_codes()`.

        Args:
            *codes: the successful exit codes; they replace the current set.
        Returns:
            self for chaining.
        Raises:
            ValueError: if no code is given.
        """
        self._cmd.ok_exit_codes(*codes)
        return self

    def binary_path(self) -> Path:
        """Get the binary this command runs.

        Returns:
            the binary's path, as resolved at construction.
        """
        return self._cmd.binary_path()

    def copy(self, cmd: Command) -> Self:
        """Copy all parameters from another command, see `Command.copy()`.

        Args:
            cmd: the command to copy parameters from.
        Returns:
            self for chaining.
        """
        self._cmd.copy(cmd)
        return self

    def pipe(self, other: Bind | Command) -> SyncProcess:
        """Run this bind's command and pipe its stdout directly into `other`'s stdin.

        Shorthand for `pipeline(other)` - see `Command.pipeline()` for the full
        contract (raw OS pipe, error/timeout semantics, what gets ignored/overwritten
        on each side).

        Args:
            other: the downstream bind or command to receive this command's stdout.
        Returns:
            the downstream command's completed subprocess.Popen.
        Raises:
            See `Command.pipeline()`.
        """
        return self.pipeline(other)

    def pipeline(self, *others: Bind | Command) -> SyncProcess:
        """Run this bind's command and each of `others` concurrently, piping every
        stage's stdout directly into the next stage's stdin.

        See `Command.pipeline()` for the full contract (raw OS pipe, error/timeout
        semantics, what gets ignored/overwritten on each stage).

        Args:
            others: the downstream binds or commands to chain after this one, in order.
        Returns:
            the last command's completed subprocess.Popen.
        Raises:
            See `Command.pipeline()`.
        """
        return self._cmd.pipeline(
            *(other._cmd if isinstance(other, Bind) else other for other in others)
        )

    async def apipe(self, other: Bind | Command) -> AsyncProcess:
        """Run this bind's command and pipe its stdout directly into `other`'s stdin
        using asyncio subprocesses.

        Shorthand for `apipeline(other)` - see `Command.apipeline()` for the full
        contract.

        Args:
            other: the downstream bind or command to receive this command's stdout.
        Returns:
            the downstream command's completed asyncio.subprocess.Process.
        Raises:
            See `Command.apipeline()`.
        """
        return await self.apipeline(other)

    async def apipeline(self, *others: Bind | Command) -> AsyncProcess:
        """Run this bind's command and each of `others` concurrently using asyncio
        subprocesses, piping every stage's stdout directly into the next stage's
        stdin.

        See `Command.apipeline()` for the full contract.

        Args:
            others: the downstream binds or commands to chain after this one, in order.
        Returns:
            the last command's completed asyncio.subprocess.Process.
        Raises:
            See `Command.apipeline()`.
        """
        return await self._cmd.apipeline(
            *(other._cmd if isinstance(other, Bind) else other for other in others)
        )
