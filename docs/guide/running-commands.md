# Running commands

Run a command, react to failures and start long-running processes.

## Run a command

```python
from io import StringIO
from proclet import Command

out = StringIO()
Command("git").args("status", "--short").stdout(out).exec()
print(out.getvalue())
```

- A command is configured with chainable setters, then run with `exec()`. Called without a value, most setters return the current one: `cmd.args()`, `cmd.env()`, `cmd.cwd()`...
- `exec()` waits for completion and returns the finished `subprocess.Popen`.
- Unless redirected, stdin reads from the null device and the output is discarded.

## Handle failures

```python
from proclet import Command, CommandNotFound, ErrorReturnCode, TimeoutException

try:
    plan = (
        Command("terraform")
        .args("plan", "-detailed-exitcode")
        .ok_exit_codes(0, 2)  # 2 means "changes pending", not a failure
        .timeout(300)
        .inherit()
        .exec()
    )
    print("changes pending" if plan.returncode == 2 else "up to date")
except CommandNotFound as err:
    print(f"{err.command} is not installed")
except ErrorReturnCode as err:
    print(f"{err.cmd} failed with exit code {err.exit_code}")
except TimeoutException as err:
    print(f"{err.cmd} took more than {err.timeout}s and was terminated")
```

- `CommandNotFound` is raised at construction for a name missing from `PATH`; a `Path` is only checked when the command runs.
- `ErrorReturnCode` is raised for an exit code outside `ok_exit_codes(...)` (default: only `0`). A process killed by a signal reports a negative exit code.
- `TimeoutException` is raised once `timeout(...)` elapses; the process is terminated first.
- `proclet` never logs, prints or exits on its own: the caller decides what a failure means.

## Run a command in the background

```python
from proclet import Command

server = Command("python").args("-m", "http.server", "8000").exec(wait_completion=False)
try:
    ...  # talk to the server
finally:
    server.terminate()
    server.wait()
```

- `exec(wait_completion=False)` returns the running `subprocess.Popen` as soon as it is spawned.
- `timeout(...)` and `ok_exit_codes(...)` aren't enforced then; redirect files are still closed once the process exits.
