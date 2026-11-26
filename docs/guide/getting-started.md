# Getting started

## Install

`proclet` requires Python 3.12 or newer, on Linux, macOS or Windows, and has no runtime dependency.

::: code-group

```bash [pip]
pip install proclet
```

```bash [uv]
uv add proclet
```

:::

## First commands

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
- The next pages of this guide cover each feature in detail.
