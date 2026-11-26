# Input and output

Redirect stdout and stderr anywhere and feed stdin from any source.

## Capture and stream output

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

## Feed stdin

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
