# Stream subprocess output to a logger

You run a long command (an install, a build, a migration) and want every line it prints
to reach your `logging` setup **as it is produced**, not in one block once it exits.

## With `subprocess`

`subprocess.run(capture_output=True)` only hands the output back at the end. Streaming
means reading the pipes yourself, and reading stdout and stderr one after the other can
deadlock as soon as the child fills the other pipe's buffer: you need a thread per stream.

```python
import logging
import subprocess
import threading

log = logging.getLogger(__name__)


def forward(stream, log_fn):
    for line in iter(stream.readline, b""):
        log_fn(line.decode(errors="replace").rstrip("\n"))
    stream.close()


process = subprocess.Popen(
    ["pip", "install", "-r", "requirements.txt"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
threads = [
    threading.Thread(target=forward, args=(process.stdout, log.info)),
    threading.Thread(target=forward, args=(process.stderr, log.warning)),
]
for thread in threads:
    thread.start()
process.wait()
for thread in threads:
    thread.join()
if process.returncode != 0:
    raise subprocess.CalledProcessError(process.returncode, process.args)
```

## With `proclet`

```python
import logging
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)

(
    Command("pip")
    .args("install", "-r", "requirements.txt")
    .stdout(strip_redirect(log.info))
    .stderr(strip_redirect(log.warning))
    .exec()
)
```

- `stdout(...)` and `stderr(...)` accept any callable taking a `str`: it is called once per
  line, as soon as the line is written. Lines are decoded as UTF-8, invalid bytes replaced.
- Lines keep their trailing newline; `strip_redirect(...)` removes it, which suits loggers.
- A non-zero exit code raises `ErrorReturnCode`, after every line has been forwarded.

## Going further

Tag each record with the tool that produced it, or send it to a dedicated logger:

```python
import logging
from proclet import Command

log = logging.getLogger("deploy")


def tagged(tool: str):
    return lambda line: log.info("[%s] %s", tool, line.rstrip("\n"))


Command("terraform").args("apply", "-auto-approve").stdout(tagged("terraform")).exec()
```

The same redirects work unchanged with `await Command(...).aexec()` and in
[pipelines](/guide/pipelines), where each stage logs its own stderr.

See also: [Input and output](/guide/input-output), [`strip_redirect`](/reference/api#proclet.strip_redirect).
