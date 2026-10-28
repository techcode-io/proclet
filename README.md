<h1 align="center">Proclet</h1>

<p align="center">
  <i align="center">Proclet is a fluent, typed wrapper around Python's subprocess, with matching sync and async APIs, binary-safe pipelines, privilege dropping and low-priority execution.</i>
</p>

<h4 align="center">
  <a href="https://pypi.org/project/proclet/">
    <img src="https://img.shields.io/pypi/v/proclet?style=flat-square" alt="pypi version" style="height: 20px;">
  </a>
  <a href="https://pypi.org/project/proclet/">
    <img src="https://img.shields.io/pypi/pyversions/proclet?style=flat-square" alt="python versions" style="height: 20px;">
  </a>
  <a href="https://github.com/techcode-io/proclet/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/techcode-io/proclet/ci.yml?branch=main&label=ci&style=flat-square" alt="continuous integration" style="height: 20px;">
  </a>
  <a href="https://github.com/techcode-io/proclet/graphs/contributors">
    <img src="https://img.shields.io/github/contributors-anon/techcode-io/proclet?color=yellow&style=flat-square" alt="contributors" style="height: 20px;">
  </a>
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img src="https://img.shields.io/badge/apache%202.0-blue.svg?style=flat-square&label=license" alt="license" style="height: 20px;">
  </a>
  <br>
</h4>

- [Source](https://github.com/techcode-io/proclet)
- [Documentation](https://github.com/techcode-io/proclet#readme)
- [Changelog](https://github.com/techcode-io/proclet/blob/main/CHANGELOG.md)
- [Issues](https://github.com/techcode-io/proclet/issues)
- [Contact](mailto:adrien.mannocci@gmail.com)

## :package: Prerequisites

- [Python](https://www.python.org/downloads/) 3.12 or newer, on Linux, macOS or Windows.
- No runtime dependency.
- [uv](https://docs.astral.sh/uv/) to work on the project itself.

## :sparkles: Features

- Ability to configure and run a command through a chainable, fully typed API.
- Ability to run the same command synchronously or with `asyncio`.
- Ability to redirect output to files, file objects, `StringIO`/`BytesIO` or a callable receiving each line.
- Ability to feed stdin from a string, bytes, a file object or a queue.
- Ability to chain commands into pipelines connected with raw OS pipes, safe for binary payloads.
- Ability to enforce timeouts and the exit codes that count as success.
- Ability to build the environment and `PATH` of a command from the current one.
- Ability to drop privileges to another user, group and set of supplementary groups.
- Ability to run a command with a lower CPU (and, on Linux, IO) priority.
- Ability to wrap a binary into a typed API of its own.
- Ability to observe every process spawn and exit.

## :dart: Motivation

- We needed a way to run external tools from Python code without repeating `subprocess` boilerplate.
- Redirecting output to a logger, feeding stdin from a queue or chaining binary-safe pipelines shouldn't require hand-written threads.
- The same API should serve sync and `asyncio` code.
- The library should stay framework-agnostic: no runtime dependencies, no logging setup, failures are exceptions the caller decides about.

Streaming a database dump through a compressor, logging both tools' stderr line by line, with a timeout and an exit code check, looks like this with `subprocess`:

```python
import logging
import subprocess
import threading

log = logging.getLogger(__name__)

dump = subprocess.Popen(
    ["pg_dump", "shop"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
pack = subprocess.Popen(
    ["zstd", "-q", "-o", "shop.sql.zst"], stdin=dump.stdout, stderr=subprocess.PIPE
)
dump.stdout.close()  # let pg_dump see a broken pipe if zstd dies


def forward(stream):
    for line in stream:
        log.warning(line.decode().rstrip("\n"))


threads = [threading.Thread(target=forward, args=(p.stderr,)) for p in (dump, pack)]
for thread in threads:
    thread.start()
try:
    dump.wait(timeout=600)
    pack.wait(timeout=600)
finally:
    for process in (dump, pack):
        if process.poll() is None:
            process.kill()
    for thread in threads:
        thread.join()
for process in (dump, pack):
    if process.returncode != 0:
        raise RuntimeError(f"{process.args} exited with {process.returncode}")
```

With `proclet`:

```python
import logging
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)
to_log = strip_redirect(log.warning)

Command("pg_dump").args("shop").stderr(to_log).timeout(600).pipe(
    Command("zstd").args("-q", "-o", "shop.sql.zst").stderr(to_log).timeout(600)
)
```

## :hammer: Workflow

### Setup

The following steps will ensure your project is cloned properly.

1. Clone repository:
   ```shell
   git clone https://github.com/techcode-io/proclet
   cd proclet
   ```
2. Install dependencies and setup environment:
   ```shell
   uv sync
   uv run poe env:configure
   ```

### Lint

- To lint you have to use the workflow.

```bash
uv run poe lint
```

- It will lint the project code using `ruff`.

### Format

- To format you have to use the workflow.

```bash
uv run poe fmt
```

- It will format the project code using `ruff`, including the Python examples of this README.

### Test

- To test you have to use the workflow.

```bash
uv run poe test
```

- It will run the test suite using `pytest`.
- `uv run poe ci` runs both lint and tests, as the CI does.

## 📖 Usage

### How to install

```bash
pip install proclet
```

### How to run a command

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

### How to handle failures

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

### How to capture and stream output

```python
import logging
from io import BytesIO, StringIO
from pathlib import Path
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)

text = StringIO()
Command("git").args("log", "-1", "--format=%H").stdout(text).exec()

raw = BytesIO()
Command("git").args("show", "HEAD:logo.png").stdout(raw).exec()

Command("make").args("build").stdout(Path("build.log")).stderr(
    strip_redirect(log.warning)
).exec()
```

- `stdout(...)` and `stderr(...)` accept a `Path` (truncated, then written), a file object (text or binary, including `StringIO`/`BytesIO`) or a callable.
- A callable receives each decoded line as it is produced, newline included: `strip_redirect(...)` wraps one to drop it, which suits loggers.
- `inherit()` wires stdin, stdout and stderr to the current process; `inherit_out()` only stdout and stderr, and `inherit_out(if_unset=True)` only the ones not redirected yet.

### How to feed stdin

```python
import threading
from queue import Queue
from proclet import Command

Command("sort").stdin("pear\napple\n").inherit_out().exec()

with open("data.csv", "rb") as source:
    Command("wc").args("-l").stdin(source).inherit_out().exec()

lines: Queue[bytes | str | None] = Queue()


def produce() -> None:
    for number in range(3):
        lines.put(f"event {number}\n")
    lines.put(None)  # closes the command's stdin


threading.Thread(target=produce).start()
Command("cat").stdin(lines).inherit_out().exec()
```

- `stdin(...)` accepts a `str` (encoded as UTF-8) or `bytes` payload, or a file object: one with a real file descriptor is handed to the child as is, others (`StringIO`/`BytesIO`) are read and fed.
- A queue streams items as they come and closes stdin when it yields `None`: a `queue.Queue` with `exec()`, an `asyncio.Queue` with `aexec()`.

### How to run a command in the background

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

### How to run commands with asyncio

```python
import asyncio
from io import StringIO
from proclet import Command


async def uptimes(hosts: list[str]) -> dict[str, str]:
    outputs = {host: StringIO() for host in hosts}
    await asyncio.gather(
        *(
            Command("ssh").args(host, "uptime").stdout(out).timeout(10).aexec()
            for host, out in outputs.items()
        )
    )
    return {host: out.getvalue().strip() for host, out in outputs.items()}


print(asyncio.run(uptimes(["alpha", "beta", "gamma"])))
```

- Every way of running a command has an `asyncio` counterpart prefixed with `a`: `aexec()`, `apipe()` and `apipeline()`. They return an `asyncio.subprocess.Process`.
- The configuration is shared: the same `Command` runs with either.

### How to chain commands into a pipeline

```python
from proclet import Command

Command("tar").args("-c", "data").pipeline(
    Command("zstd").args("-q"),
    Command("tee").args("data.tar.zst"),
)

Command("git").args("log", "--format=%an").pipe(
    Command("sort").args("-u").inherit_out()
)
# or: await Command(...).apipeline(...) / .apipe(...)
```

- `pipe(other)` connects two commands, `pipeline(*others)` any number of them; all stages run concurrently.
- Stages are connected with raw OS pipes, so binary payloads aren't decoded on the way.
- The first stage keeps its stdin, the last one its stdout; each stage keeps its own stderr, env, cwd, timeout and exit codes.
- Stages are checked in order: the first failure raises and the stages still running are terminated. The last stage's process is returned.

### How to set the environment and PATH

```python
from pathlib import Path
from proclet import Command, EnvCmd, PathCmd

env = (
    EnvCmd.inherit(lambda key, _: key.startswith("LC_"))
    .add({"PATH": PathCmd.inherit().add("/opt/tools/bin").build()})
    .build()
)
Command("deploy").env(env).cwd(Path("infra")).exec()

Command("python", search_path=["/opt/python3.13/bin"]).args("--version").exec()
```

- Without `env(...)` a command inherits the current environment; once set, it gets exactly that mapping, so build on `EnvCmd.inherit()` to keep it.
- `EnvCmd.inherit(predicate)` keeps only the variables `predicate(key, value)` accepts; `EnvCmd.empty()` starts from nothing.
- `PathCmd.add(...)` prepends a directory, so it takes precedence over the inherited `PATH`.
- `search_path` looks the binary up in the given directories instead of `PATH`.

### How to drop privileges and lower priority

```python
from proclet import Command

(
    Command("rsync")
    .args("-a", "src/", "dst/")
    .user("backup")  # requires sufficient privileges, e.g. root
    .group("backup")
    .extra_groups([])  # drop supplementary groups
    .nice(19)  # lowest priority
    .exec()
)
```

- `user`, `group` and `extra_groups` accept names or ids. They are POSIX only and raise `ValueError` elsewhere.
- `extra_groups` unset keeps the current supplementary groups; `[]` drops them all.
- `nice(...)` lowers the CPU priority; on Linux, `nice(19)` also puts the process in the idle IO class. On Windows the niceness maps to a priority class.

### How to reuse a configuration

```python
from pathlib import Path
from proclet import Command

base = Command("git").cwd(Path("repo")).timeout(30)
Command("git").copy(base).args("fetch", "--all").exec()
Command("git").copy(base).args("gc").exec()
```

- `copy(other)` takes every setting of `other` (args, env, cwd, redirects, timeout, exit codes, privileges, niceness, observers) while keeping its own binary.

### How to wrap a binary into a typed API

```python
from io import StringIO
from pathlib import Path
from proclet import Bind, Command


class Git(Bind):
    def __init__(self, repo: Path) -> None:
        super().__init__("git")
        self.cwd(repo)

    def log(self, *refs: str) -> Command:
        return self._command("log", "--oneline", *refs)

    def current_branch(self) -> str:
        out = StringIO()
        self._command("branch", "--show-current").stdout(out).exec()
        return out.getvalue().strip()


git = Git(Path("repo")).nice(10)
print(git.current_branch())
git.log("main").inherit_out().timeout(30).exec()
git.log("main", "--", "README.md").pipe(Command("wc").args("-l").inherit_out())
```

- A `Bind` holds the context shared by every operation on its binary: `env`, `cwd`, `user`, `group`, `extra_groups`, `nice` and observers.
- Each operation calls `self._command(...)`, which returns a fresh `Command` carrying that context, then either returns it, for the caller to redirect, run or pipe (`log`), or runs it and returns a result (`current_branch`).
- Settings made on one operation's command never leak into another one.
- Set default context in the constructor, and share per-operation defaults through a private helper:

```python
from proclet import Bind, Command, EnvCmd


class Terraform(Bind):
    def __init__(self) -> None:
        super().__init__("terraform")
        self.env(EnvCmd.inherit().add({"TF_IN_AUTOMATION": "1"}).build())

    def _run(self, *args: str) -> Command:
        return self._command(*args).timeout(600)

    def plan(self) -> bool:
        """Whether changes are pending."""
        plan = self._run("plan", "-detailed-exitcode").ok_exit_codes(0, 2).exec()
        return plan.returncode == 2
```

- A caller's `env(...)` replaces the bind's env: merge onto `bind.env()` to keep its defaults.

### How to observe processes

```python
import time
from proclet import Command, Process


class Timer:
    def __init__(self) -> None:
        self.started: dict[int, float] = {}

    def on_spawn(self, process: Process) -> None:
        self.started[process.pid] = time.monotonic()

    def on_exit(self, process: Process) -> None:
        elapsed = time.monotonic() - self.started.pop(process.pid)
        print(f"pid {process.pid} exited with {process.returncode} in {elapsed:.2f}s")


Command("git").args("fetch").add_observer(Timer()).exec()
```

- `add_observer(...)` registers any object implementing the `ProcessObserver` protocol, notified with `on_spawn(process)` and `on_exit(process)`; `Bind.add_observer(...)` watches every operation.
- Hooks run synchronously and must not raise. After `exec(wait_completion=False)`, `on_exit` runs on a background thread.

## :heart: Contributing

If you find this project useful here's how you can help, please click the :eye: **Watch** button to avoid missing
notifications about new versions, and give it a :star2: **GitHub Star**!

You can also contribute by:

- Sending a [Pull Request](https://github.com/techcode-io/proclet/pulls) with your awesome new features and bug fixed.
- Be part of the community and help resolve [Issues](https://github.com/techcode-io/proclet/issues).

## 🧾 License

The `proclet` project is free and open-source software licensed under the Apache-2.0 license.
