---
layout: home

hero:
  name: proclet
  text: Subprocess, without the boilerplate
  tagline: Run external commands from Python through one fluent, typed API. The same command runs with exec() or await aexec(), chains into shell-free pipelines, and streams its output wherever you need it.
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started
    - theme: alt
      text: Browse recipes
      link: /recipes/
    - theme: alt
      text: View on GitHub
      link: https://github.com/techcode-io/proclet
---

<div class="pc-section">

## The same job, twice

<p class="pc-lead">Stream a database dump through a compressor, log both tools' stderr line by line, enforce a timeout and check every exit code.</p>

<div class="pc-compare">
<div class="pc-compare-side pc-compare-before">
<h3>With subprocess <span class="pc-count">35 lines</span></h3>

```python
import logging
import subprocess
import threading

log = logging.getLogger(__name__)

dump = subprocess.Popen(
    ["pg_dump", "shop"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
pack = subprocess.Popen(
    ["zstd", "-q", "-o", "shop.sql.zst"], stdin=dump.stdout, stderr=subprocess.PIPE
)
dump.stdout.close()  # let pg_dump see a broken pipe if zstd dies


def forward(stream):
    for line in stream:
        log.warning(line.decode().rstrip("\n"))


threads = [threading.Thread(target=forward, args=(p.stderr,)) for p in (dump, pack)]
for thread in threads:
    thread.start()
try:
    dump.wait(timeout=600)
    pack.wait(timeout=600)
finally:
    for process in (dump, pack):
        if process.poll() is None:
            process.kill()
    for thread in threads:
        thread.join()
for process in (dump, pack):
    if process.returncode != 0:
        raise RuntimeError(f"{process.args} exited with {process.returncode}")
```

</div>
<div class="pc-compare-side pc-compare-after">
<h3>With proclet <span class="pc-count">9 lines</span></h3>

```python
import logging
from proclet import Command, strip_redirect

log = logging.getLogger(__name__)
to_log = strip_redirect(log.warning)

Command("pg_dump").args("shop").stderr(to_log).timeout(600).pipe(
    Command("zstd").args("-q", "-o", "shop.sql.zst").stderr(to_log).timeout(600)
)
```

</div>
</div>
</div>

<div class="pc-section">

## What it handles for you

<dl class="pc-capabilities">
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">⚡</span>Sync and async</dt>
    <dd>One <code>Command</code>, run with <code>exec()</code> or <code>await aexec()</code>. <a href="./guide/asyncio">asyncio guide</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">🚰</span>Pipelines without a shell</dt>
    <dd>Stages joined by raw OS pipes: no quoting, no injection, binary data untouched. <a href="./recipes/binary-pipes">Binary pipes</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">📤</span>Output where you want it</dt>
    <dd>Send each line to a logger as it is written, or to a file, a <code>StringIO</code> or a <code>BytesIO</code>. <a href="./recipes/stream-output-to-logger">Stream to a logger</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">📥</span>Input from anything</dt>
    <dd>Feed stdin from a string, bytes, a file or a queue that streams while the command runs. <a href="./guide/input-output">Input and output</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">⏱️</span>Timeouts that clean up</dt>
    <dd>Terminate, wait, then kill, and raise <code>TimeoutException</code>, in pipelines too. <a href="./recipes/timeout">Timeouts</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">🔐</span>Privileges and priority</dt>
    <dd>Run as another user and group, drop supplementary groups, lower the CPU and IO priority. <a href="./guide/privileges">Privileges and priority</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">🧩</span>Typed wrappers for your tools</dt>
    <dd>Turn <code>git</code>, <code>kubectl</code> or <code>ffmpeg</code> into a Python class with <code>Bind</code>. <a href="./recipes/wrap-a-cli">Wrap a CLI</a></dd>
  </div>
  <div class="pc-capability">
    <dt><span class="pc-emoji" aria-hidden="true">📦</span>Nothing else to install</dt>
    <dd>Standard library only, on Linux, macOS and Windows, Python 3.12 or newer. <a href="./guide/getting-started">Getting started</a></dd>
  </div>
</dl>
</div>

<div class="pc-section pc-next">

## Already using sh or plumbum?

The migration guides map what you know to proclet, side by side: [coming from `sh`](/migration/from-sh) and [coming from plumbum](/migration/from-plumbum).

</div>
