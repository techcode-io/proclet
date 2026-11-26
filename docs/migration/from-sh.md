# Coming from `sh`

[`sh`](https://github.com/amoffat/sh) turns commands into Python functions:
`sh.git("status")`. `proclet` builds them explicitly: `Command("git").args("status")`.
Most `sh` special keyword arguments (`_timeout`, `_ok_code`, `_in`, `_out`...) map to one
`proclet` method each.

## Why switch

- **Windows.** `sh` is POSIX only; `proclet` runs on Linux, macOS and Windows.
- **Typed.** Every setting is a method your type checker and IDE know about, instead of
  `**kwargs` resolved at run time.
- **Streaming pipelines.** `sh.wc("-l", _in=sh.ls())` runs `ls` to completion and buffers
  its output before starting `wc` (unless `_piped=True`). `proclet` pipelines run all stages
  at once, connected with raw OS pipes.
- **Graceful timeouts.** `sh` kills a timed-out process with `SIGKILL` by default;
  `proclet` sends `SIGTERM` first and kills only after a grace period.
- **Privileges and priority.** Run as another user *and* group, choose supplementary
  groups, lower the CPU and IO priority.

`sh` is terser for quick, interactive-style scripts on POSIX: if that is what you use it
for and you don't need Windows, it remains a fine choice.

## Cheat sheet

| `sh` | `proclet` |
|---|---|
| `sh.git("status")` returns stdout as `str` | `Command("git").args("status").stdout(out).exec()`, with `out = StringIO()` |
| `sh.Command("/opt/bin/tool")` | `Command(Path("/opt/bin/tool"))` |
| `_ok_code=[0, 2]` | `.ok_exit_codes(0, 2)` |
| `_timeout=10` | `.timeout(10)` |
| `_cwd="repo"` | `.cwd(Path("repo"))` |
| `_env={...}` | `.env({...})`, or `.env(EnvCmd.inherit().add({...}).build())` to keep the current env |
| `_in="data"` | `.stdin("data")` |
| `_out=callback` / `_err=callback` | `.stdout(callback)` / `.stderr(callback)` |
| `_out="build.log"` | `.stdout(Path("build.log"))` |
| `_err_to_out=True` | pass the same callable or file object to `.stdout(...)` and `.stderr(...)` |
| `_fg=True` | `.inherit()` |
| `_bg=True` | `.exec(wait_completion=False)` |
| `await sh.ls(_async=True)` | `await Command("ls").aexec()` |
| `sh.wc("-l", _in=sh.ls())` | `Command("ls").pipe(Command("wc").args("-l"))` |
| `git = sh.git.bake("-C", repo)` | `Command("git").copy(base)`, or a [`Bind`](/recipes/wrap-a-cli) subclass |
| `_uid=1000` | `.user(1000)`, plus `.group(...)` and `.extra_groups(...)` |
| `sh.ErrorReturnCode` | `proclet.ErrorReturnCode` |
| `sh.TimeoutException` | `proclet.TimeoutException` |
| `sh.CommandNotFound` | `proclet.CommandNotFound` |

## Getting output back

`sh` returns stdout as a string. In `proclet`, output goes where you send it, so collect it
in a `StringIO`. If your code does this a lot, a three-line helper brings back the `sh` feel:

```python
from io import StringIO
from proclet import Command


def output(cmd: Command) -> str:
    out = StringIO()
    cmd.stdout(out).exec()
    return out.getvalue()


branch = output(Command("git").args("branch", "--show-current")).strip()
```

Unless redirected, `proclet` discards output and reads stdin from the null device.

## Errors

The exception names are the same, the details differ:

```python
# sh
import sh

try:
    sh.make("build")
except sh.ErrorReturnCode as err:
    print(err.exit_code, err.stderr.decode())
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

- `sh` raises a subclass per exit code (`ErrorReturnCode_2`...) and attaches the captured
  `stdout`/`stderr`. `proclet` raises `ErrorReturnCode` with `exit_code` and `cmd`: capture
  the streams you need with a redirect.
- `sh.CommandNotFound` is raised when the command is looked up; `proclet` raises
  `CommandNotFound` when the `Command` is built (for a name) or run (for a `Path`).

## Baking

`bake(...)` presets arguments. In `proclet`, `copy(...)` takes every setting of another
command, and `Bind` turns a binary into a class:

```python
from proclet import Command

base = Command("git").args("-C", "repo").timeout(30)
# args() replaces the arguments: start from the copied ones to extend them.
Command("git").copy(base).args(*base.args(), "fetch", "--all").exec()
```

For anything used in more than one place, prefer a [`Bind`](/recipes/wrap-a-cli): it keeps
the shared context (cwd, env, user, priority) and gives each operation a typed method.

## Differences to watch for

- **No TTY.** `sh` connects stdout to a pseudo-terminal by default (`_tty_out=True`), so
  tools behave as in a terminal: colours, progress bars, line buffering. `proclet` uses
  pipes, like `subprocess`: some tools drop colours or buffer their output. Pass the tool's
  own flag when you need it (`--color=always`, `PYTHONUNBUFFERED=1`...).
- **Callbacks.** `sh` passes each line with its newline, so does `proclet`: wrap a logger
  with `strip_redirect(...)`. `sh`'s extra callback arguments (stdin queue, process) have no
  equivalent; use a `Queue` with `.stdin(...)` to feed input.
- **Iteration.** `_iter=True` has no direct equivalent: handle lines in a `.stdout(callback)`.
- **User switching.** `_uid` sets the user's primary group and all its groups. In
  `proclet`, set `.group(...)` explicitly; supplementary groups are kept unless you pass
  `.extra_groups(...)`.

See also: [Why proclet](/guide/why-proclet), [Recipes](/recipes/).
