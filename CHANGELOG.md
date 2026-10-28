# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

First public release.

### Added

- `Command`: a chainable, fully typed builder to configure and run an external command.
- `exec()`/`aexec()` to run a command synchronously or with `asyncio`, waiting for it or
  leaving it in the background.
- `pipe()`/`pipeline()` and their `apipe()`/`apipeline()` counterparts, chaining commands
  with raw OS pipes, safe for binary payloads.
- Output redirects to a `Path`, a file object (`StringIO`/`BytesIO` included) or a
  callable receiving each line, with `strip_redirect()` to feed loggers.
- Input from a `str` or `bytes` payload, a file object, a `queue.Queue` or an
  `asyncio.Queue`.
- `timeout()` and `ok_exit_codes()`, enforced with `TimeoutException` and
  `ErrorReturnCode`; `CommandNotFound` for a missing binary.
- `EnvCmd` and `PathCmd` to build a command's environment and `PATH` from the current
  ones, and `search_path` to look a binary up in given directories.
- `user()`, `group()` and `extra_groups()` to drop privileges (POSIX only).
- `nice()` to lower the CPU priority, with the idle IO class on Linux and a priority
  class on Windows.
- `Bind`, a base for typed per-binary wrappers whose operations each run in an
  isolated `Command`.
- `ProcessObserver` to be notified of every process spawn and exit.
- Support for Python 3.12 and 3.13 on Linux, macOS and Windows, with no runtime
  dependency.

[Unreleased]: https://github.com/techcode-io/proclet/commits/main
