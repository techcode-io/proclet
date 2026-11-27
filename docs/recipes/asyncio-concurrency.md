# Run many commands concurrently with asyncio

You need to run the same command against many targets (hosts, repositories, files) at
the same time, with a cap on how many run at once, a timeout each, and a report of which
ones failed.

## With `asyncio` alone

`asyncio.create_subprocess_exec` gives you the process, and everything else is yours:
the pipes, the timeout, the cleanup when it fires, the exit code check.

```python
import asyncio


async def uptime(host: str, limit: asyncio.Semaphore) -> str:
    async with limit:
        process = await asyncio.create_subprocess_exec(
            "ssh", host, "uptime",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError(f"{host}: {err.decode().strip()}")
        return out.decode().strip()


async def main(hosts: list[str]) -> None:
    limit = asyncio.Semaphore(8)
    results = await asyncio.gather(
        *(uptime(host, limit) for host in hosts), return_exceptions=True
    )
    for host, result in zip(hosts, results):
        print(host, result)
```

## With `proclet`

```python
import asyncio
from io import StringIO
from proclet import Command


async def uptime(host: str, limit: asyncio.Semaphore) -> str:
    out = StringIO()
    async with limit:
        await Command("ssh").args(host, "uptime").stdout(out).timeout(10).aexec()
    return out.getvalue().strip()


async def main(hosts: list[str]) -> None:
    limit = asyncio.Semaphore(8)
    results = await asyncio.gather(
        *(uptime(host, limit) for host in hosts), return_exceptions=True
    )
    for host, result in zip(hosts, results):
        print(host, result)
```

- `aexec()` takes the same configuration as `exec()`: redirects, timeout, exit codes, env.
- A failure raises `ErrorReturnCode`, a timeout `TimeoutException` (after terminating the
  process), and `gather(..., return_exceptions=True)` collects them per host.
- Cancelling the awaiting task terminates the process too, so an `asyncio.timeout(...)`
  around the whole batch leaves nothing running behind.
- Output can be streamed instead of collected: `.stdout(lambda line: ...)` is called for
  each line as it arrives, from the event loop.

## Feeding stdin from a coroutine

An `asyncio.Queue` streams data to a command's stdin while it runs; `None` closes it:

```python
import asyncio
from proclet import Command


async def main() -> None:
    lines: asyncio.Queue[bytes | str | None] = asyncio.Queue()
    run = asyncio.create_task(Command("sort").stdin(lines).inherit_out().aexec())
    for word in ("pear", "apple", "fig"):
        await lines.put(f"{word}\n")
    await lines.put(None)
    await run


asyncio.run(main())
```

See also: [Async execution](/guide/asyncio), [`Command.aexec`](/reference/api#proclet.Command.aexec).
