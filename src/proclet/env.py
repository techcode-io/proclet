"""Builders for the environment passed to a command."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Self


class EnvCmd:
    """Builder for the env vars passed to `Command.env()`."""

    def __init__(self) -> None:
        """Init an empty env command, same as `empty()`."""
        self.__build: dict[str, str] = {}

    @staticmethod
    def empty() -> EnvCmd:
        """Create an empty env command."""
        return EnvCmd()

    @staticmethod
    def inherit(predicate: Callable[[str, str], bool] | None = None) -> EnvCmd:
        """Create an env command that inherits env vars from the current process.

        Args:
            predicate: keeps only the `(key, value)` pairs it returns True for;
                every variable is kept when omitted.
        Returns:
            the env command.
        """
        env = EnvCmd()
        if predicate:
            env.__build = {
                key: value for key, value in os.environ.items() if predicate(key, value)
            }
        else:
            env.__build = os.environ.copy()
        return env

    def add(self, env_vars: dict[str, str]) -> Self:
        """Add env vars, overriding any already set under the same key.

        Args:
            env_vars: env vars to add.
        Returns:
            self for chaining.
        """
        self.__build.update(env_vars)
        return self

    def build(self) -> dict[str, str]:
        """Build the env vars, for `Command.env()`.

        Returns:
            a copy of the env vars built so far.
        """
        return self.__build.copy()


class PathCmd:
    """Builder for a `PATH` env var value."""

    def __init__(self) -> None:
        """Init an empty path command, same as `empty()`."""
        self.__build: list[str] = []

    @staticmethod
    def empty() -> PathCmd:
        """Create an empty path command."""
        return PathCmd()

    @staticmethod
    def inherit() -> PathCmd:
        """Create a path command that inherits `PATH` from the current process.

        Empty entries are dropped, since a lookup would treat them as the
        current directory.

        Returns:
            the path command.
        """
        path = PathCmd()
        path.__build = [
            entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry
        ]
        return path

    def add(self, value: str) -> Self:
        """Prepend a directory, so it is searched before those already added.

        Args:
            value: directory to prepend.
        Returns:
            self for chaining.
        """
        self.__build.insert(0, value)
        return self

    def build(self) -> str:
        """Build the `PATH` value.

        Returns:
            the `PATH` value, entries joined with `os.pathsep`.
        """
        return os.pathsep.join(self.__build)
