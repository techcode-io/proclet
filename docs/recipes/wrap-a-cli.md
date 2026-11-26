# Wrap a CLI in a typed Python API

Your code calls the same tool (`kubectl`, `git`, `docker`, `ffmpeg`...) from many places.
Command lines are copy-pasted, flags drift, and every caller parses the output its own way.
You want one Python class that knows the tool, with typed methods.

## The wrapper

Subclass [`Bind`](/reference/api#proclet.Bind): it holds the context shared by every
operation (env, cwd, user, priority, observers), and `self._command(...)` builds a fresh
`Command` from it for each operation.

```python
import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from proclet import Bind, Command


@dataclass(frozen=True)
class Pod:
    name: str
    phase: str


class Kubectl(Bind):
    def __init__(self, context: str, namespace: str) -> None:
        super().__init__("kubectl")
        self._scope = ("--context", context, "--namespace", namespace)

    def _kubectl(self, *args: str) -> Command:
        """Every operation targets the same cluster and namespace, within 60s."""
        return self._command(*self._scope, *args).timeout(60)

    def pods(self) -> list[Pod]:
        """Run the command and return a parsed result."""
        out = StringIO()
        self._kubectl("get", "pods", "-o", "json").stdout(out).exec()
        items = json.loads(out.getvalue())["items"]
        return [Pod(item["metadata"]["name"], item["status"]["phase"]) for item in items]

    def apply(self, manifest: Path) -> None:
        self._kubectl("apply", "-f", str(manifest)).exec()

    def logs(self, pod: str) -> Command:
        """Return the command: the caller decides where the output goes."""
        return self._kubectl("logs", pod)
```

## Using it

```python
import logging
from pathlib import Path
from proclet import strip_redirect

log = logging.getLogger(__name__)
kubectl = Kubectl(context="staging", namespace="shop")

kubectl.apply(Path("deploy.yaml"))
for pod in kubectl.pods():
    print(pod.name, pod.phase)
kubectl.logs("shop-api-0").stdout(strip_redirect(log.info)).exec()
```

- **Two kinds of operations.** `pods()` and `apply()` run the command and return a
  result. `logs()` returns the `Command` itself, so the caller can redirect it, give it
  another timeout, run it with `aexec()` or pipe it into another command.
- **Nothing leaks between operations.** Each `_command(...)` call is a fresh `Command`:
  a redirect or timeout set on one operation never reaches the next.
- **Shared defaults go in a private helper** (`_kubectl` here), per-operation settings
  on the returned command.
- **The binary is checked once.** A missing `kubectl` raises `CommandNotFound` when the
  wrapper is built, not halfway through a deployment.

## Context for every operation

The context setters of `Bind` apply to every operation, and can be set by the caller:

```python
from proclet import EnvCmd

kubectl = Kubectl(context="prod", namespace="shop").nice(10)
kubectl.env(EnvCmd.inherit().add({"KUBECONFIG": "/etc/deploy/kubeconfig"}).build())
```

Add an [observer](/guide/observers) with `kubectl.add_observer(...)` to time or audit every
call the wrapper makes.

See also: [Reusing and wrapping commands](/guide/reuse-and-wrap), [`Bind`](/reference/api#proclet.Bind).
