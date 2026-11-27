# Async execution

Run the same commands from `asyncio` code.

## Run commands with asyncio

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
