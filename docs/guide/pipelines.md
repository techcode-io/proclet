# Pipelines

Chain commands through raw OS pipes, without a shell.

## Chain commands into a pipeline

```python
from proclet import Command

Command("tar").args("-c", "data").pipeline(
    Command("zstd").args("-q"),
    Command("tee").args("data.tar.zst"),
)

Command("git").args("log", "--format=%an").pipe(
    Command("sort").args("-u").inherit_out()
)
# or: await Command(...).apipeline(...) / .apipe(...)
```

- `pipe(other)` connects two commands, `pipeline(*others)` any number of them; all stages run concurrently.
- Stages are connected with raw OS pipes, so binary payloads aren't decoded on the way.
- The first stage keeps its stdin, the last one its stdout; each stage keeps its own stderr, env, cwd, timeout and exit codes.
- Stages are checked in order: the first failure raises and the stages still running are terminated. The last stage's process is returned.
