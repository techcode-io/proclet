# Environment and PATH

Control the environment variables, working directory and binary lookup of a command.

## Set the environment and PATH

```python
from pathlib import Path
from proclet import Command, EnvCmd, PathCmd

env = (
    EnvCmd.inherit(lambda key, _: key.startswith("LC_"))
    .add({"PATH": PathCmd.inherit().add("/opt/tools/bin").build()})
    .build()
)
Command("deploy").env(env).cwd(Path("infra")).exec()

Command("python", search_path=["/opt/python3.13/bin"]).args("--version").exec()
```

- Without `env(...)` a command inherits the current environment; once set, it gets exactly that mapping, so build on `EnvCmd.inherit()` to keep it.
- `EnvCmd.inherit(predicate)` keeps only the variables `predicate(key, value)` accepts; `EnvCmd.empty()` starts from nothing.
- `PathCmd.add(...)` prepends a directory, so it takes precedence over the inherited `PATH`.
- `search_path` looks the binary up in the given directories instead of `PATH`.
