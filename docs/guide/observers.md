# Observing processes

Get notified of every process spawn and exit.

## Observe processes

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
