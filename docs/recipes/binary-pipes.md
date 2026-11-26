# Pipe binary data between processes without a shell

You want the equivalent of `tar -c data | zstd -q -o data.tar.zst`, or to send bytes
through a tool and get bytes back, without a shell and without corrupting the payload.

## With `subprocess`

The quick way goes through a shell, which brings quoting problems and command injection
as soon as a value comes from outside:

```python
import subprocess

subprocess.run(f"tar -c {folder} | zstd -q -o {archive}", shell=True, check=True)
```

Without a shell, every `Popen` has to be wired by hand, and each one checked:

```python
import subprocess

tar = subprocess.Popen(["tar", "-c", folder], stdout=subprocess.PIPE)
zstd = subprocess.Popen(["zstd", "-q", "-o", archive], stdin=tar.stdout)
tar.stdout.close()  # let tar see a broken pipe if zstd dies
zstd.wait()
tar.wait()
for process in (tar, zstd):
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, process.args)
```

## With `proclet`

```python
from proclet import Command

Command("tar").args("-c", folder).pipe(Command("zstd").args("-q", "-o", archive))
```

- Arguments are passed as they are: no shell, no quoting, no injection.
- Stages are connected with raw OS pipes. Data flows from process to process without
  going through Python, so binary payloads are never decoded and large ones cost nothing.
- Every stage is checked: the first failure raises `ErrorReturnCode` and the stages still
  running are terminated.
- `pipeline(...)` takes any number of stages:

```python
from proclet import Command

Command("tar").args("-c", folder).pipeline(
    Command("zstd").args("-q"),
    Command("age").args("-r", recipient, "-o", f"{archive}.age"),
)
```

## Bytes in, bytes out

Feed `bytes` to the first stage and collect the last stage's output in a `BytesIO`:

```python
from io import BytesIO
from pathlib import Path
from proclet import Command

thumbnail = BytesIO()
(
    Command("magick")
    .args("-", "-resize", "256x256", "png:-")
    .stdin(Path("photo.jpg").read_bytes())
    .stdout(thumbnail)
    .exec()
)
Path("thumbnail.png").write_bytes(thumbnail.getvalue())
```

A file opened in binary mode works too, both ways: `stdin(open("photo.jpg", "rb"))` hands
the file to the process directly.

## Good to know

- There is no shell, so no globbing or `~` expansion: expand them in Python
  (`Path.glob(...)`, `Path.expanduser()`) and pass the results as arguments.
- Each stage keeps its own stderr, env, cwd, timeout and exit codes.

See also: [Pipelines](/guide/pipelines), [`Command.pipeline`](/reference/api#proclet.Command.pipeline).
