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
  <a href="https://github.com/techcode-io/proclet/blob/main/pyproject.toml">
    <img src="https://img.shields.io/badge/dependencies-none-brightgreen?style=flat-square" alt="no runtime dependencies" style="height: 20px;">
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

## :rocket: Quick start

Requires Python 3.12 or newer, on Linux, macOS or Windows. No runtime dependency.

```bash
pip install proclet
```

```python
import asyncio
from io import StringIO
from proclet import Command

# Run a command and capture its output.
out = StringIO()
Command("git").args("rev-parse", "HEAD").stdout(out).timeout(10).exec()
print(out.getvalue().strip())

# Chain commands through raw OS pipes, no shell involved.
Command("git").args("log", "--format=%an").pipe(
    Command("sort").args("-u").inherit_out()
)

# Run the same configuration with asyncio.
asyncio.run(Command("git").args("fetch", "--all").inherit_out().aexec())
```

- A failing command raises `ErrorReturnCode`, a missing binary `CommandNotFound` and an
  elapsed timeout `TimeoutException`.
- The [usage](#-usage) section covers each feature in detail.

## :sparkles: Features

- **Fluent and typed**: configure a command through a chainable API, checked by your type checker.
- **Sync and async**: run the same `Command` with `exec()` or `await aexec()`.
- **Output anywhere**: redirect to files, file objects, `StringIO`/`BytesIO` or a callable receiving each line.
- **Input from anywhere**: feed stdin from a string, bytes, a file object or a queue.
- **Shell-free pipelines**: chain commands through raw OS pipes, safe for binary payloads.
- **Guard rails**: enforce timeouts and choose the exit codes that count as success.
- **Environment control**: build a command's environment and `PATH` from the current ones.
- **Privilege dropping**: run as another user, group and set of supplementary groups.
- **Low priority**: lower the CPU (and, on Linux, IO) priority of a command, Windows included.
- **Typed wrappers**: turn a binary into a Python API of its own with `Bind`.
- **Observability**: get notified of every process spawn and exit.

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

### Compared to other libraries

| | `subprocess` | `sh` | `plumbum` | `invoke` | `proclet` |
|---|---|---|---|---|---|
| Style | function arguments | commands as functions | `local["git"]` objects and operators | shell command strings | chainable builder |
| Windows | yes | no | yes | yes | yes |
| asyncio | separate API | `_async=True` | separate async commands | no | same `Command` (`aexec()`) |
| Pipelines without a shell | hand-wired `Popen`s | yes | yes | no | yes, raw OS pipes |
| Run as another user | `user=` | `_uid=` | no | no | `user()` |
| Choose the group and supplementary groups | `group=`, `extra_groups=` | no | no | no | `group()`, `extra_groups()` |
| Lower the priority | by hand (`preexec_fn`) | no | no | no | `nice()`, Windows included |

- Checked against `sh` 2.4, `plumbum` 2.0 and `invoke` 3.0. `invoke` is mainly a task
  runner and `plumbum` also covers CLI apps and remote machines: pick them for that.
- Spotted a mistake? Please [open an issue](https://github.com/techcode-io/proclet/issues).

## 📖 Usage

The [documentation site](https://techcode-io.github.io/proclet/) covers every feature with examples:

- [How to run a command](https://techcode-io.github.io/proclet/guide/running-commands#run-a-command)
- [How to handle failures](https://techcode-io.github.io/proclet/guide/running-commands#handle-failures)
- [How to capture and stream output](https://techcode-io.github.io/proclet/guide/input-output#capture-and-stream-output)
- [How to feed stdin](https://techcode-io.github.io/proclet/guide/input-output#feed-stdin)
- [How to run a command in the background](https://techcode-io.github.io/proclet/guide/running-commands#run-a-command-in-the-background)
- [How to run commands with asyncio](https://techcode-io.github.io/proclet/guide/asyncio#run-commands-with-asyncio)
- [How to chain commands into a pipeline](https://techcode-io.github.io/proclet/guide/pipelines#chain-commands-into-a-pipeline)
- [How to set the environment and PATH](https://techcode-io.github.io/proclet/guide/environment#set-the-environment-and-path)
- [How to drop privileges and lower priority](https://techcode-io.github.io/proclet/guide/privileges#drop-privileges-and-lower-priority)
- [How to reuse a configuration](https://techcode-io.github.io/proclet/guide/reuse-and-wrap#reuse-a-configuration)
- [How to wrap a binary into a typed API](https://techcode-io.github.io/proclet/guide/reuse-and-wrap#wrap-a-binary-into-a-typed-api)
- [How to observe processes](https://techcode-io.github.io/proclet/guide/observers#observe-processes)

It also has [recipes](https://techcode-io.github.io/proclet/recipes/) for common problems (streaming output to a logger,
timeouts, binary pipes, running as another user, concurrency with asyncio, wrapping a CLI),
migration guides [from `sh`](https://techcode-io.github.io/proclet/migration/from-sh) and
[from plumbum](https://techcode-io.github.io/proclet/migration/from-plumbum), and the [API reference](https://techcode-io.github.io/proclet/reference/api).

## :heart: Contributing

If you find this project useful here's how you can help, please click the :eye: **Watch** button to avoid missing
notifications about new versions, and give it a :star2: **GitHub Star**!

You can also contribute by:

- Sending a [Pull Request](https://github.com/techcode-io/proclet/pulls) with your awesome new features and bug fixed.
- Be part of the community and help resolve [Issues](https://github.com/techcode-io/proclet/issues).

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) to set up the project and check your changes before opening a pull request.

## 🧾 License

The `proclet` project is free and open-source software licensed under the Apache-2.0 license.
