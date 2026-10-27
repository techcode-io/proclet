"""Exceptions raised when running commands."""


class CommandNotFound(Exception):
    """Raised when a command binary is not found.

    Raised by `Command(...)` for a name missing from the search path, or at
    spawn time for a `Path` (or a binary removed since construction).

    Attributes:
        command: the name or path that couldn't be found.
    """

    def __init__(self, command: str) -> None:
        super().__init__(f"Command not found: {command}")
        self.command: str = command


class ErrorReturnCode(Exception):
    """Raised when a command exits with a code outside its `ok_exit_codes`.

    Attributes:
        cmd: the command line, binary path followed by its arguments.
        exit_code: the process' exit code (negative signal number if killed).
    """

    def __init__(self, cmd: str, exit_code: int) -> None:
        super().__init__(f"Command '{cmd}' returned exit code {exit_code}")
        self.cmd: str = cmd
        self.exit_code: int = exit_code


class TimeoutException(Exception):
    """Raised when a command outlives its `timeout()`; the process is terminated.

    Attributes:
        cmd: the command line, binary path followed by its arguments.
        timeout: the timeout that elapsed, in seconds.
    """

    def __init__(self, cmd: str, timeout: float) -> None:
        super().__init__(f"Command '{cmd}' timed out after {timeout} seconds")
        self.cmd: str = cmd
        self.timeout: float = timeout
