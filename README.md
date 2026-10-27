# proclet

Fluent, typed wrapper around `subprocess` for Python 3.12+, with matching sync and
async APIs, binary-safe pipelines, privilege dropping and low-priority execution.
No runtime dependencies.

```sh
pip install proclet
```

## Usage

### Run a command

```python
from io import StringIO
from proclet import Command

out = StringIO()
Command("git").args("status", "--short").stdout(out).exec()
print(out.getvalue())
```

`exec()` waits for completion and raises `ErrorReturnCode` on a non-zero exit
(`ok_exit_codes(...)` widens what counts as success) and `TimeoutException` once
`timeout(...)` elapses. A binary name missing from `PATH` (or from
`Command(name, search_path=[...])`) raises `CommandNotFound` at construction; a
`Path` is only checked when the command runs. `exec(wait_completion=False)`
returns the running `Popen` without enforcing either.

### Redirects

`stdout`/`stderr` accept a `Path`, a file object (text or binary, including
`StringIO`/`BytesIO`) or a callable receiving each decoded line; unset, output is
discarded. `stdin` accepts a `str` or `bytes` payload, a file object (including
`StringIO`/`BytesIO`) or a queue fed until it yields `None`: a `queue.Queue` with
`exec()`, an `asyncio.Queue` with `aexec()`. Unset, it reads from /dev/null.
`inherit()` wires all three to the current process.

```python
import logging
from pathlib import Path
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)
Command("make").args("build").stdout(Path("build.log")).stderr(
    strip_redirect(log.warning)
).exec()
```

### Async

```python
await Command("sleep").args("1").aexec()
```

### Pipelines

Stages run concurrently, connected with raw OS pipes, so binary payloads aren't
decoded on the way. Each stage keeps its own stderr, env, cwd, timeout and exit
codes. Stages are checked in order: the first failure raises and the stages
still running are terminated.

```python
from proclet import Command

Command("tar").args("-c", "data").pipeline(
    Command("zstd").args("-q"),
    Command("tee").args("data.tar.zst"),
)
# or: await Command(...).apipeline(...)
```

### Environment, privileges and priority

Without `env(...)` a command inherits the current environment; once set, it gets
exactly that mapping, so build on `EnvCmd.inherit()` to keep it.

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

### Binds

Subclass `Bind` to give a binary a typed API of its own:

```python
from proclet import Bind


class Git(Bind):
    def __init__(self) -> None:
        super().__init__("git")

    def clone(self, url: str, dest: str) -> None:
        self._cmd.args("clone", "--", url, dest).exec()
```

### Observers

`add_observer(...)` registers a `ProcessObserver` notified with `on_spawn(process)`
and `on_exit(process)`. Hooks run synchronously and must not raise.

## Development

```sh
uv sync
uv run poe env:configure   # pre-commit hooks
uv run poe ci              # lint + tests
```

## License

Apache-2.0, see [LICENSE](LICENSE).
