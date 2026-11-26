# Why proclet

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

## Compared to other libraries

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
