# Contributing

Thanks for helping improve `proclet`! Bug reports, ideas and pull requests are welcome on
[GitHub](https://github.com/techcode-io/proclet).

## Setup

You need [Python](https://www.python.org/downloads/) 3.12 or newer and [uv](https://docs.astral.sh/uv/).

1. Clone the repository:
   ```shell
   git clone https://github.com/techcode-io/proclet
   cd proclet
   ```
2. Install the dependencies and the pre-commit hooks:
   ```shell
   uv sync
   uv run poe env:configure
   ```

## Workflow

| Task | Command | What it does |
|---|---|---|
| Lint | `uv run poe lint` | Lints the project code with `ruff`. |
| Format | `uv run poe fmt` | Formats the project code with `ruff`, including the Python examples of the README. |
| Test | `uv run poe test` | Runs the test suite with `pytest`. |

## Documentation

The documentation site lives in `docs/` and is built with [VitePress](https://vitepress.dev/), which
needs [Node.js](https://nodejs.org/). The API reference page is generated from the docstrings.

| Task | Command | What it does |
|---|---|---|
| Install | `uv run poe docs:install` | Installs the site dependencies with `npm ci`. |
| Serve | `uv run poe docs:dev` | Generates the API reference and serves the site with live reload. |
| Build | `uv run poe docs:build` | Generates the API reference and builds the site into `docs/.vitepress/dist`. |

## Before opening a pull request

- Run `uv run poe lint` and `uv run poe test`: the CI runs the tests on Linux, macOS and Windows.
- Write tests with the helpers of `tests/portable.py` (`py(...)`, `cat()`, `echo()`...) rather than POSIX tools such as `sh` or `cat`, so they run on every platform.
- Keep `proclet` free of runtime dependencies.
- Start commit titles with `build`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `test`, `chore` or `release`, followed by a colon: `gitlint` checks it.
- Add user-facing changes to the `## [Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md).
