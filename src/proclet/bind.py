"""Base class for typed wrappers around one binary."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Self, overload

from proclet.process import Command, ProcessObserver


class Bind:
    """Base class for typed wrappers around one binary.

    A bind holds the context shared by every operation on its binary: env,
    cwd, user, groups, niceness and observers. Subclasses expose operations
    built with `_command()`, which returns a fresh `Command` each time: an
    operation either returns that command for the caller to configure and
    run, or runs it and returns a processed result. Per-operation settings
    (args, redirects, timeout, exit codes) live on that command only, so they
    never leak from one operation into the next.
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
        self._template: Command = self.create(cmd_path, search_path)

    def create(
        self, cmd_path: str | Path, search_path: Sequence[str] | None = None
    ) -> Command:
        """Create the template every operation is copied from; override to customise it.

        Called once, at construction, so the binary is resolved once. Whatever
        the returned command carries (env, exit codes, redirects...) becomes
        the default of every operation.

        Args:
            cmd_path: command name or path.
            search_path: directories to look the binary up in instead of `PATH`.
        Returns:
            the template command.
        Raises:
            CommandNotFound: if the command binary can't be found.
        """
        return Command(cmd_path, search_path)

    def _command(self, *args: str) -> Command:
        """Create a fresh command for one operation, carrying the bind's context.

        Args:
            *args: the operation's arguments.
        Returns:
            a new command, independent from the bind and from other operations.
        """
        cmd = Command(self._template.binary_path()).copy(self._template)
        return cmd.args(*args) if args else cmd

    def add_observer(self, observer: ProcessObserver) -> Self:
        """Register an observer notified on the spawn and exit of every operation."""
        self._template.add_observer(observer)
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
            return self._template.env()
        self._template.env(value)
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
            return self._template.cwd()
        self._template.cwd(value)
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
            return self._template.user()
        self._template.user(value)
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
            return self._template.group()
        self._template.group(value)
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
            return self._template.extra_groups()
        self._template.extra_groups(value)
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
            return self._template.nice()
        self._template.nice(value)
        return self

    def binary_path(self) -> Path:
        """Get the binary this bind runs.

        Returns:
            the binary's path, as resolved at construction.
        """
        return self._template.binary_path()
