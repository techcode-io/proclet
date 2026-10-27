# proclet

Python 3.12+ library (`uv` + `poe` tasks). Code in `src/proclet/`, no runtime dependencies.

## Commands
- `uv run poe test` - `pytest tests`
- `uv run poe lint` - `ruff check src/proclet`
- `uv run poe fmt` - `ruff check --select I --fix` + `ruff format`
- `uv run poe ci` - lint + test (what GitHub Actions runs)

## Release
- `RELEASE_VERSION=X.Y.Z NEXT_VERSION=X.Y.Z-dev uv run poe release` from a clean `main`:
  sets the version, signed-off `--no-verify` commit `release: proclet <version>`, tag,
  bumps to `NEXT_VERSION`, commit `chore: prepare for next iteration`, pushes `main` and
  the tag.
- The tag push runs `.github/workflows/release.yml`: checks tag == project version, runs
  CI, builds, publishes to PyPI via trusted publishing (environment `pypi`, no token).
- Actions are pinned by commit SHA with the version in a trailing comment.

## Code style
- `Command` pairs sync/async implementations (`exec`/`aexec`, `pipeline`/`apipeline`) in one
  class, sharing small private static helpers between the two paths - follow this pattern
  for new dual-mode features.
- Private functions/methods use a single leading underscore (`_foo`), not double (`__foo`).
- Keep the library framework-agnostic: no logging setup, no CLI framework imports.
