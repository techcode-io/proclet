# Coming from plumbum

[plumbum](https://github.com/tomerfiliba/plumbum) builds commands from `local["git"]`
objects combined with operators: `[...]` binds arguments, `|` pipes, `>` redirects, `&`
picks how to run. `proclet` spells each of these as a method on `Command`.

## Why switch

- **asyncio.** plumbum has no `asyncio` API; every `proclet` command runs with `exec()` or
  `await aexec()`, same configuration.
- **Streaming output.** Send each line of stdout or stderr to a callable (a logger, a
  progress bar) as it is produced, without threads of your own.
- **Explicit context.** plumbum's `with local.cwd(...)` changes the working directory of
  the whole Python process (`os.chdir`), and `with local.env(...)` the environment seen by
  every thread. `proclet` sets cwd and env on each command, or
  once on a [`Bind`](/recipes/wrap-a-cli).
- **Privileges and priority.** Run as another user and group, choose supplementary
  groups, lower the CPU and IO priority.

plumbum covers much more than running local commands: remote machines over SSH
(`SshMachine`), a CLI application framework, path and colour utilities. If you rely on
those, keep plumbum for them; `proclet` only runs local processes.

## Cheat sheet

| plumbum | `proclet` |
|---|---|
| `local["git"]("status")` returns stdout as `str` | `Command("git").args("status").stdout(out).exec()`, with `out = StringIO()` |
| `local["git"]["log", "--oneline"]` | `Command("git").args("log", "--oneline")` |
| `local[Path("/opt/bin/tool")]` | `Command(Path("/opt/bin/tool"))` |
| `cmd.run(retcode=(0, 2))` | `.ok_exit_codes(0, 2).exec()` |
| `cmd.run(timeout=10)` | `.timeout(10).exec()` |
| `cmd & FG` | `.inherit().exec()` |
| `cmd & BG` | `.exec(wait_completion=False)` |
| `cmd.popen()` | `.exec(wait_completion=False)` |
| `ls \| grep["py"]` | `Command("ls").pipe(Command("grep").args("py"))` |
| `cmd > "out.txt"` | `.stdout(Path("out.txt"))` |
| `cmd < "in.txt"` | `.stdin(open("in.txt", "rb"))` |
| `cmd << "data"` | `.stdin("data")` |
| `with local.cwd("repo"):` | `.cwd(Path("repo"))` on each command, or on a `Bind` |
| `with local.env(DEBUG="1"):` / `cmd.with_env(DEBUG="1")` | `.env(EnvCmd.inherit().add({"DEBUG": "1"}).build())` |
| `ProcessExecutionError` | `proclet.ErrorReturnCode` |
| `ProcessTimedOut` | `proclet.TimeoutException` |
| `CommandNotFound` | `proclet.CommandNotFound` |

## Getting output back

Calling a plumbum command returns its stdout. In `proclet`, collect it in a `StringIO`,
or wrap that in a helper:

```python
from io import StringIO
from proclet import Command


def output(cmd: Command) -> str:
    out = StringIO()
    cmd.stdout(out).exec()
    return out.getvalue()


files = output(Command("git").args("ls-files")).splitlines()
```

`cmd & TEE` (show the output *and* keep it) becomes a callable doing both:

```python
import sys
from io import StringIO
from proclet import Command

kept = StringIO()


def tee(line: str) -> None:
    sys.stdout.write(line)
    kept.write(line)


Command("make").args("build").stdout(tee).exec()
```

## Errors

```python
# plumbum
from plumbum import ProcessExecutionError, local

try:
    local["make"]("build")
except ProcessExecutionError as err:
    print(err.retcode, err.stderr)
```

```python
# proclet
from io import StringIO
from proclet import Command, ErrorReturnCode

errors = StringIO()
try:
    Command("make").args("build").stderr(errors).exec()
except ErrorReturnCode as err:
    print(err.exit_code, errors.getvalue())
```

`ProcessExecutionError` carries the captured `stdout`/`stderr`; `ErrorReturnCode` carries
`exit_code` and `cmd`: capture the streams you need with a redirect.

## Working directory and environment

plumbum's context managers change the cwd and env of `local` itself: every command run
while the block is open, from any thread, is affected, and `local.cwd` moves the working
directory of the Python process too. In `proclet` they belong to the command:

```python
from pathlib import Path
from proclet import Command, EnvCmd

env = EnvCmd.inherit().add({"DEBUG": "1"}).build()
Command("make").args("test").cwd(Path("repo")).env(env).exec()
```

To share them between commands, set them once on a [`Bind`](/recipes/wrap-a-cli) or copy
them with `Command("make").copy(base)`.

## Differences to watch for

- **Pipelines.** `proclet` checks every stage: the first failing one raises
  `ErrorReturnCode` and the others are terminated. The last stage's process is returned.
- **Timeouts.** `.timeout(...)` terminates the process gracefully (`SIGTERM`, then kill
  after a grace period) and raises `TimeoutException`.
- **Output by default.** plumbum captures stdout when you call a command; `proclet`
  discards output unless redirected (`.stdout(...)`, `.inherit_out()`).

See also: [Why proclet](/guide/why-proclet), [Recipes](/recipes/).
