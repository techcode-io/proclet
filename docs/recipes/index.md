# Recipes

Each recipe solves one common problem, first with the standard library, then with `proclet`.

| Problem | Recipe |
|---|---|
| Get a command's output into `logging`, line by line, while it runs | [Stream subprocess output to a logger](./stream-output-to-logger) |
| Give a command a deadline and stop it gracefully | [Run a command with a timeout and stop it cleanly](./timeout) |
| Chain tools like a shell pipe, safely, with binary data | [Pipe binary data between processes without a shell](./binary-pipes) |
| Drop root privileges for one tool | [Run a subprocess as another user](./run-as-another-user) |
| Run a command against many targets at once | [Run many commands concurrently with asyncio](./asyncio-concurrency) |
| Turn `kubectl`, `git` or `docker` into a Python class | [Wrap a CLI in a typed Python API](./wrap-a-cli) |
