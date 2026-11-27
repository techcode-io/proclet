# Reusing and wrapping commands

Share a configuration between commands and turn a binary into a typed Python API.

## Reuse a configuration

```python
from pathlib import Path
from proclet import Command

base = Command("git").cwd(Path("repo")).timeout(30)
base.copy().args("fetch", "--all").exec()
base.copy().args("gc").exec()
```

- `copy()` returns a new command with the same binary and every setting (args, env, cwd, redirects, timeout, exit codes, privileges, niceness, observers), without looking the binary up again. Changing the copy leaves `base` untouched.
- `copy(other)` takes every setting of `other` into an existing command while keeping its own binary.

## Wrap a binary into a typed API

```python
from io import StringIO
from pathlib import Path
from proclet import Bind, Command


class Git(Bind):
    def __init__(self, repo: Path) -> None:
        super().__init__("git")
        self.cwd(repo)

    def log(self, *refs: str) -> Command:
        return self._command("log", "--oneline", *refs)

    def current_branch(self) -> str:
        out = StringIO()
        self._command("branch", "--show-current").stdout(out).exec()
        return out.getvalue().strip()


git = Git(Path("repo")).nice(10)
print(git.current_branch())
git.log("main").inherit_out().timeout(30).exec()
git.log("main", "--", "README.md").pipe(Command("wc").args("-l").inherit_out())
```

- A `Bind` holds the context shared by every operation on its binary: `env`, `cwd`, `user`, `group`, `extra_groups`, `nice` and observers.
- Each operation calls `self._command(...)`, which returns a fresh `Command` carrying that context, then either returns it, for the caller to redirect, run or pipe (`log`), or runs it and returns a result (`current_branch`).
- Settings made on one operation's command never leak into another one.
- Set default context in the constructor, and share per-operation defaults through a private helper:

```python
from proclet import Bind, Command, EnvCmd


class Terraform(Bind):
    def __init__(self) -> None:
        super().__init__("terraform")
        self.env(EnvCmd.inherit().add({"TF_IN_AUTOMATION": "1"}).build())

    def _run(self, *args: str) -> Command:
        return self._command(*args).timeout(600)

    def plan(self) -> bool:
        """Whether changes are pending."""
        plan = self._run("plan", "-detailed-exitcode").ok_exit_codes(0, 2).exec()
        return plan.returncode == 2
```

- A caller's `env(...)` replaces the bind's env: merge onto `bind.env()` to keep its defaults.
