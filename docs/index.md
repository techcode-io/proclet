---
layout: home

hero:
  name: proclet
  text: Subprocess, without the boilerplate
  tagline: A fluent, typed wrapper around Python's subprocess, with matching sync and async APIs, binary-safe pipelines, privilege dropping and low-priority execution.
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started
    - theme: alt
      text: Recipes
      link: /recipes/
    - theme: alt
      text: Why proclet
      link: /guide/why-proclet
    - theme: alt
      text: View on GitHub
      link: https://github.com/techcode-io/proclet

features:
  - title: Fluent and typed
    details: Configure a command through a chainable API, checked by your type checker.
  - title: Sync and async
    details: Run the same Command with exec() or await aexec().
  - title: Shell-free pipelines
    details: Chain commands through raw OS pipes, safe for binary payloads.
  - title: Output and input anywhere
    details: Stream output to a logger line by line, feed stdin from a string, a file or a queue.
  - title: Privileges and priority
    details: Run as another user or group, lower the CPU and IO priority, Windows included.
  - title: No dependencies
    details: Pure standard library, on Linux, macOS and Windows, Python 3.12 or newer.
---

```python
import logging
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)
to_log = strip_redirect(log.warning)

# Stream a database dump through a compressor, logging both tools' stderr,
# with a timeout and an exit code check.
Command("pg_dump").args("shop").stderr(to_log).timeout(600).pipe(
    Command("zstd").args("-q", "-o", "shop.sql.zst").stderr(to_log).timeout(600)
)
```
