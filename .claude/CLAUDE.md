# proclet

Public Python 3.12+ library (`uv` + `poe` tasks), published on PyPI as `proclet`.
Code in `src/proclet/`: `process.py` (`Command`, process types, `ProcessObserver`),
`bind.py` (`Bind`), `env.py` (`EnvCmd`, `PathCmd`), `errors.py` (exceptions),
`redirect.py` (redirect types, `strip_redirect`). Private modules (`_io.py`, `_platform.py`,
`_streams.py`) use plain names, no leading underscore. `__init__.py` re-exports the public
API with `__all__` - `proclet` is the only import path users should rely on. `py.typed` ships.
No runtime dependencies - keep it that way.

## Commands
- `uv run poe test` - run full test suite (`pytest tests`, `asyncio_mode = "auto"`)
- `uv run poe lint` - `ruff check src/proclet`
- `uv run poe fmt` - `ruff check --select I --fix` + `ruff format`
- `uv run poe ci` - lint + test (what CI runs)
- `uv run poe env:configure` - install pre-commit hooks (ruff, LF line endings, gitlint)
- Lint only covers `src/proclet`; ruff run over `scripts/` or `tests/` reports a few
  warnings that CI ignores.
- `ruff format` also formats Python code blocks inside `README.md`.

## Tests
- Tests must run on POSIX and Windows: spawn `tests/portable.py` helpers (`py(...)`, `cat()`,
  `echo()`, `sleep()`...), never POSIX tools (`sh`, `cat`, `head`...).
- Mark POSIX-only behaviour (`user`/`group`/niceness) with `posix_only` / `skipif(win32)`.
- Unit tests of platform branches (`_platform.*`) monkeypatch `sys.platform` either way
  (`"win32"` or `"linux"`), never rely on the host OS.
- Env tests: a child Python with an empty env still sees `LC_CTYPE`/`__CF_USER_TEXT_ENCODING`
  on macOS, so assert on a sentinel var and spawn with `MINIMAL_ENV` (Windows needs `SYSTEMROOT`).

## Environment
- `uv run ...` fails with "Operation not permitted" under the default Claude sandbox (it
  needs `~/.cache/uv`) - rerun with the sandbox disabled.

## Commits
- gitlint enforces a title prefix: `build|ci|docs|feat|fix|perf|refactor|test|chore|release`,
  5-80 chars, body optional.
- This is a public repo: describe the library on its own terms.

## CI and release
- `.github/workflows/ci.yml`: push to `main` + PRs, matrix ubuntu/macos/windows x Python 3.12/3.13,
  `uv sync --locked`, `uv run poe ci`, `uv build`. Keep `uv.lock` committed and in sync.
- Actions are pinned by commit SHA with the version in a trailing comment
  (`uses: actions/checkout@<sha> # v7.0.1`); bump both together.
- Cut a release from a clean `main` with:
  `RELEASE_VERSION=X.Y.Z NEXT_VERSION=X.Y.Z-dev uv run poe release`
  (`scripts/release.py`, plain `subprocess` + `git`, both env vars required, versions picked
  by the caller). `pyproject.toml`'s `version` is `X.Y.Z-dev` between releases;
  `RELEASE_VERSION` is that with `-dev` stripped, `NEXT_VERSION` the next patch + `-dev`.
- The script sets the version, `git add -A`, signed-off `--no-verify` commit
  `release: proclet <version>`, tags `<version>`, sets `NEXT_VERSION`, commits
  `chore: prepare for next iteration`, then pushes `main` and the tag to `origin` - no
  dry-run or confirmation step.
- The tag push runs `.github/workflows/release.yml`: fails if the tag != `uv version --short`,
  runs CI, builds, then publishes via PyPI trusted publishing (OIDC, GitHub environment
  `pypi`, no API token). No CHANGELOG is generated.

## Design
- Framework-agnostic: no logging setup, no CLI framework imports, no exits. Errors are
  exceptions (`CommandNotFound` at construction or spawn, `ErrorReturnCode`,
  `TimeoutException`); callers decide whether they're fatal.
- `Bind` is a thin base for typed per-binary wrappers: subclasses call
  `super().__init__("<binary>")` and drive `self._cmd`. Subclasses that need extra env vars
  should merge them onto `self.env()`, not replace it, so a caller's env survives.
- `user`/`group`/`extra_groups` pass straight to `subprocess.Popen`/
  `asyncio.create_subprocess_exec`. `extra_groups`: `None` keeps the parent's groups, `[]`
  drops them; never default it to `[]` - `setgroups` fails for non-root. They are POSIX
  only: their setters raise `ValueError` (via `_platform.require_os`) where `os` lacks the call.
- `search_path` restricts binary lookup to given directories instead of `PATH` (ignored
  when `cmd_path` is a `Path`).
- `nice(...)` uses a `preexec_fn` built by `_platform.low_priority_hook`: everything is resolved
  before the fork so the hook only makes plain syscalls; the Linux idle IO class is best
  effort (skipped off Linux / unknown machines). Windows has no `preexec_fn`: there
  `_platform.priority_creationflags` maps the niceness to a priority class via `creationflags`.
- `pipeline`/`apipeline` connect stages with raw OS pipes (binary safe, no decoding).
  Thread/task forwarding is only used for Python-facing ends (callables, `StringIO`,
  queues, strings).

## Code style
- `Command` pairs sync/async implementations (`exec`/`aexec`, `pipe`/`apipe`,
  `pipeline`/`apipeline`) in one class, sharing small private static helpers between the
  two paths - follow this pattern for new dual-mode features.
- The asyncio counterpart of a sync method takes the same name with an `a` prefix
  (`_finish`/`_afinish`, `_start_io`/`_astart_io`), never a `_threads`/`_tasks` suffix.
  The prefix means "asyncio side", not "coroutine": `_ahandle_stdin` and
  `_areap_in_background` are plain `def`s returning tasks - don't turn them into
  `async def`. Helpers named `_start_*`/`_handle_*` start their thread/task.
- Private functions/methods use a single leading underscore (`_foo`), not double
  (`__foo`). Existing instance attributes still use `self.__x`; don't spread that to new
  methods.
- Google-style docstrings (`Args:` / `Returns:` / `Raises:`).
