# Privileges and priority

Run a command as another user, or with a lower priority.

## Drop privileges and lower priority

```python
from proclet import Command

(
    Command("rsync")
    .args("-a", "src/", "dst/")
    .user("backup")  # requires sufficient privileges, e.g. root
    .group("backup")
    .extra_groups([])  # drop supplementary groups
    .nice(19)  # lowest priority
    .exec()
)
```

- `user`, `group` and `extra_groups` accept names or ids. They are POSIX only and raise `ValueError` elsewhere.
- `extra_groups` unset keeps the current supplementary groups; `[]` drops them all.
- `nice(...)` lowers the CPU priority; on Linux, `nice(19)` also puts the process in the idle IO class. On Windows the niceness maps to a priority class.
