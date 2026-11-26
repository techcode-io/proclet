# Run a subprocess as another user

Your script runs as root (a backup job, a provisioning step, a container entrypoint) and
needs to run a tool as an unprivileged user, with no leftover privileges.

## With `subprocess`

`subprocess` has the knobs (`user=`, `group=`, `extra_groups=`), but the easy mistakes
are left to you:

```python
import os
import subprocess

subprocess.run(
    ["pg_dump", "-f", "/backups/shop.sql", "shop"],
    user="postgres",
    group="postgres",
    extra_groups=[],  # forget this and root's supplementary groups are kept
    env={**os.environ, "HOME": "/var/lib/postgresql", "USER": "postgres"},
    check=True,
)
```

## With `proclet`

```python
from proclet import Command, EnvCmd

(
    Command("pg_dump")
    .args("-f", "/backups/shop.sql", "shop")
    .user("postgres")
    .group("postgres")
    .extra_groups([])
    .env(
        EnvCmd.inherit()
        .add({"HOME": "/var/lib/postgresql", "USER": "postgres"})
        .build()
    )
    .exec()
)
```

- `user(...)`, `group(...)` and `extra_groups(...)` accept names or ids.
- Without `extra_groups(...)`, the current supplementary groups are kept, as with
  `subprocess`. Pass `[]` to drop them all when going from root to a service account.
- Switching user does **not** change the environment: `HOME`, `USER` and the rest are
  inherited from the caller. Set them when the tool reads its configuration from them.
- These settings are POSIX only. On Windows, the setters raise `ValueError` right away,
  before anything runs.

## Give every command the same identity

When several commands run as the same user, hold that context in a
[`Bind`](/guide/reuse-and-wrap#wrap-a-binary-into-a-typed-api), set once in its constructor:

```python
from proclet import Bind, EnvCmd


class Postgres(Bind):
    def __init__(self, binary: str) -> None:
        super().__init__(binary)
        self.user("postgres").group("postgres").extra_groups([]).nice(10)
        self.env(EnvCmd.inherit().add({"HOME": "/var/lib/postgresql"}).build())

    def dump(self, database: str, target: str) -> None:
        self._command("-f", target, database).timeout(3600).exec()


Postgres("pg_dump").dump("shop", "/backups/shop.sql")
```

`nice(10)` here also lowers the CPU priority, so the dump doesn't slow down the server.

See also: [Privileges and priority](/guide/privileges), [`Command.user`](/reference/api#proclet.Command.user).
