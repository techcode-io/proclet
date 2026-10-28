<h1 align="center">Proclet</h1>

<p align="center">
  <i align="center">Proclet is a fluent, typed wrapper around Python's subprocess, with matching sync and async APIs, binary-safe pipelines, privilege dropping and low-priority execution.</i>
</p>

<h4 align="center">
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
- [Documentation](https://github.com/techcode-io/proclet)
- [Issues](https://github.com/techcode-io/proclet/issues)
- [Contact](mailto:adrien.mannocci@gmail.com)

## :package: Prerequisites

- [Python](https://www.python.org/downloads/) 3.12 or newer, on Linux, macOS or Windows.
- [uv](https://docs.astral.sh/uv/) for build system.

## :sparkles: Features

- Ability to configure and run a command through a chainable, fully typed API.
- Ability to run the same command synchronously or with `asyncio`.
- Ability to redirect output to files, file objects, `StringIO`/`BytesIO` or a callable receiving each line.
- Ability to feed stdin from a string, bytes, a file object or a queue.
- Ability to chain commands into pipelines connected with raw OS pipes, safe for binary payloads.
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

- `exec()` waits for completion and raises `ErrorReturnCode` on a non-zero exit (`ok_exit_codes(...)` widens what counts as success) and `TimeoutException` once `timeout(...)` elapses.
- A binary name missing from `PATH` (or from `Command(name, search_path=[...])`) raises `CommandNotFound` at construction; a `Path` is only checked when the command runs.
- `exec(wait_completion=False)` returns the running `Popen` without enforcing either.

### How to redirect stdin, stdout and stderr

```python
import logging
from pathlib import Path
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)
Command("make").args("build").stdout(Path("build.log")).stderr(
    strip_redirect(log.warning)
).exec()
```

- `stdout`/`stderr` accept a `Path`, a file object (text or binary, including `StringIO`/`BytesIO`) or a callable receiving each decoded line; unset, output is discarded.
- `stdin` accepts a `str` or `bytes` payload, a file object (including `StringIO`/`BytesIO`) or a queue fed until it yields `None`: a `queue.Queue` with `exec()`, an `asyncio.Queue` with `aexec()`. Unset, the command reads from the null device.
- `inherit()` wires all three to the current process.

### How to run a command with asyncio

```python
await Command("sleep").args("1").aexec()
```

- Every way of running a command has an `asyncio` counterpart prefixed with `a`: `aexec()`, `apipe()` and `apipeline()`.

### How to chain commands into a pipeline

```python
from proclet import Command

Command("tar").args("-c", "data").pipeline(
    Command("zstd").args("-q"),
    Command("tee").args("data.tar.zst"),
)
# or: await Command(...).apipeline(...)
```

- Stages run concurrently, connected with raw OS pipes, so binary payloads aren't decoded on the way.
- Each stage keeps its own stderr, env, cwd, timeout and exit codes.
- Stages are checked in order: the first failure raises and the stages still running are terminated.

### How to set the environment, privileges and priority

```python
from proclet import Command, EnvCmd, PathCmd

env = (
    EnvCmd.inherit(lambda key, _: key.startswith("LC_"))
    .add({"PATH": PathCmd.empty().add("/usr/bin").build()})
    .build()
)

(
    Command("rsync")
    .args("-a", "src/", "dst/")
    .env(env)
    .user("backup")  # requires sufficient privileges, e.g. root
    .group("backup")
    .extra_groups([])  # drop supplementary groups (user/group/extra_groups: POSIX only)
    .nice(19)  # lowest CPU priority, idle IO class on Linux, idle class on Windows
    .exec()
)
```

- Without `env(...)` a command inherits the current environment; once set, it gets exactly that mapping, so build on `EnvCmd.inherit()` to keep it.
- `user`, `group` and `extra_groups` are POSIX only and raise `ValueError` elsewhere.

### How to wrap a binary into a typed API

```python
from proclet import Bind


class Git(Bind):
    def __init__(self) -> None:
        super().__init__("git")

    def clone(self, url: str, dest: str) -> None:
        self._command("clone", "--", url, dest).exec()
```

- Subclass `Bind` and build each operation with `self._command(...)`, a fresh `Command` carrying the bind's context (env, cwd, user, groups, niceness, observers).

### How to observe processes

- `add_observer(...)` registers a `ProcessObserver` notified with `on_spawn(process)` and `on_exit(process)`.
- Hooks run synchronously and must not raise.

## :heart: Contributing

If you find this project useful here's how you can help, please click the :eye: **Watch** button to avoid missing
notifications about new versions, and give it a :star2: **GitHub Star**!

You can also contribute by:

- Sending a [Pull Request](https://github.com/techcode-io/proclet/pulls) with your awesome new features and bug fixed.
- Be part of the community and help resolve [Issues](https://github.com/techcode-io/proclet/issues).

## 🧾 License

The `proclet` project is free and open-source software licensed under the Apache-2.0 license.
