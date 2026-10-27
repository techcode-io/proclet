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
`timeout(...)` elapses. A missing binary raises `CommandNotFound` at construction.

### Redirects

`stdout`/`stderr` accept a `Path`, a file object, a `StringIO` or a callable
receiving each decoded line; `stdin` accepts a string, a file object or a
`queue.Queue`/`asyncio.Queue` fed until `None`. `inherit()` wires all three to the
current process.

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

Stages are connected with raw OS pipes, so binary payloads aren't decoded on the way.

```python
from proclet import Command

Command("tar").args("-c", "data").pipeline(
    Command("zstd").args("-q"),
    Command("tee").args("data.tar.zst"),
)
# or: await Command(...).apipeline(...)
```

### Environment, privileges and priority

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
    .extra_groups([])  # drop supplementary groups
    .nice(19)  # lowest CPU priority, idle IO class on Linux
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
and `on_exit(process)`.

## Development

```sh
uv sync
uv run poe env:configure   # pre-commit hooks
uv run poe ci              # lint + tests
```

## License

Apache-2.0, see [LICENSE](LICENSE).
