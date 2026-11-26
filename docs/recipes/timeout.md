# Run a command with a timeout and stop it cleanly

A command can hang: a network call that never returns, a test waiting on a lock, a tool
asking for input. You want to give it a deadline, then stop it **gracefully** (so it can
clean up) and fail loudly.

## With `subprocess`

`subprocess.run(timeout=...)` kills the process outright (`SIGKILL` on POSIX), with no
chance to clean up, and only works when you don't need to stream its output. Giving the
process a grace period means writing the escalation yourself:

```python
import subprocess

process = subprocess.Popen(["pytest", "tests"])
try:
    process.wait(timeout=600)
except subprocess.TimeoutExpired:
    process.terminate()  # SIGTERM: let it clean up
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()  # still there: force it
        process.wait()
    raise
if process.returncode != 0:
    raise subprocess.CalledProcessError(process.returncode, process.args)
```

## With `proclet`

```python
from proclet import Command, TimeoutException

try:
    Command("pytest").args("tests").inherit_out().timeout(600).exec()
except TimeoutException as err:
    print(f"{err.cmd} took more than {err.timeout}s and was stopped")
```

- Once the timeout elapses, the process is terminated (`SIGTERM` on POSIX), given up to
  10 seconds to exit, then killed. `TimeoutException` is raised once it is gone.
- The timeout combines with everything else: output streaming, stdin feeding, exit code
  checks. Redirect files are closed and observers notified either way.
- With `asyncio`, `await Command(...).timeout(600).aexec()` behaves the same.

## Pipelines

Each stage of a [pipeline](/guide/pipelines) keeps its own timeout. When one stage fails or
times out, the stages still running are terminated too, so nothing is left behind:

```python
from proclet import Command

Command("pg_dump").args("shop").timeout(3600).pipe(
    Command("zstd").args("-q", "-o", "shop.sql.zst").timeout(3600)
)
```

## Good to know

- Only the command's own process is signalled. If it spawns children of its own (a shell
  script starting background jobs, for example), make sure it forwards the signal to
  them, as you would with `subprocess`.
- `exec(wait_completion=False)` returns immediately, so the timeout is not enforced:
  you own the process from there.

See also: [Running commands](/guide/running-commands#handle-failures),
[`Command.timeout`](/reference/api#proclet.Command.timeout).
