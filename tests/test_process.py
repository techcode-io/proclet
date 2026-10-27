from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from asyncio import Queue as AsyncQueue
from io import BytesIO, StringIO
from pathlib import Path
from queue import Queue
from typing import Callable

import pytest

from proclet import (
    Bind,
    Command,
    CommandNotFound,
    EnvCmd,
    ErrorReturnCode,
    PathCmd,
    Process,
    TimeoutException,
)
from proclet._platform import low_priority_hook, priority_creationflags
from tests.portable import (
    COPY_STDIN,
    DRAIN_STDIN,
    MINIMAL_ENV,
    PYTHON,
    PYTHON_DIR,
    PYTHON_NAME,
    cat,
    count_bytes,
    echo,
    false,
    head_bytes,
    hexdump,
    printenv,
    pwd,
    py,
    random_bytes,
    rev,
    sleep,
    true,
    upper,
    write_stdin_to,
    yes,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only option")


class _RecordingObserver:
    """Test ProcessObserver that records every spawn/exit notification it receives."""

    def __init__(self) -> None:
        self.spawned: list[Process] = []
        self.exited: list[Process] = []

    def on_spawn(self, process: Process) -> None:
        self.spawned.append(process)

    def on_exit(self, process: Process) -> None:
        self.exited.append(process)


class _FdWriter:
    """File-like object exposing a real fd without subclassing IOBase."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("wb")

    def fileno(self) -> int:
        return self._file.fileno()

    def close(self) -> None:
        self._file.close()


class TestEnvCmd:
    def test_empty(self) -> None:
        env = EnvCmd.empty()
        assert env.build() == {}

    def test_inherit_copies_os_environ(self) -> None:
        env = EnvCmd.inherit()
        result = env.build()
        assert result == dict(os.environ)
        result["__SENTINEL__"] = "x"
        assert "__SENTINEL__" not in env.build()

    def test_inherit_with_predicate(self) -> None:
        os.environ["_TEST_VAR_KEEP"] = "yes"
        os.environ["_TEST_VAR_DROP"] = "no"
        try:
            env = EnvCmd.inherit(predicate=lambda k, _: k == "_TEST_VAR_KEEP")
            result = env.build()
            assert "_TEST_VAR_KEEP" in result
            assert "_TEST_VAR_DROP" not in result
        finally:
            del os.environ["_TEST_VAR_KEEP"]
            del os.environ["_TEST_VAR_DROP"]

    def test_add_updates_vars(self) -> None:
        env = EnvCmd.empty()
        returned = env.add({"FOO": "bar", "BAZ": "qux"})
        assert returned is env
        assert env.build() == {"FOO": "bar", "BAZ": "qux"}

    def test_add_chaining(self) -> None:
        env = EnvCmd.empty().add({"A": "1"}).add({"B": "2"})
        assert env.build() == {"A": "1", "B": "2"}

    def test_add_overwrites_existing(self) -> None:
        env = EnvCmd.empty().add({"KEY": "old"}).add({"KEY": "new"})
        assert env.build()["KEY"] == "new"


class TestPathCmd:
    def test_empty(self) -> None:
        path = PathCmd.empty()
        assert path.build() == ""

    def test_inherit(self) -> None:
        path = PathCmd.inherit()
        assert path.build() == os.environ.get("PATH", "")

    def test_add_prepends(self) -> None:
        path = PathCmd.empty().add("/usr/local/bin").add("/opt/bin")
        parts = path.build().split(os.pathsep)
        assert parts[0] == "/opt/bin"
        assert parts[1] == "/usr/local/bin"

    def test_inherit_without_path_has_no_empty_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PATH")
        assert PathCmd.inherit().add("/usr/bin").build() == "/usr/bin"

    def test_inherit_drops_empty_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", os.pathsep.join(["/a", "", "/b", ""]))
        assert PathCmd.inherit().build() == os.pathsep.join(["/a", "/b"])

    def test_add_returns_self(self) -> None:
        path = PathCmd.empty()
        returned = path.add("/bin")
        assert returned is path

    def test_inherit_then_prepend(self) -> None:
        path = PathCmd.inherit().add("/custom/bin")
        parts = path.build().split(os.pathsep)
        assert parts[0] == "/custom/bin"


class TestCommand:
    def test_not_found_raises(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("__no_such_binary_exists__")
        assert exc_info.value.command == "__no_such_binary_exists__"
        assert "__no_such_binary_exists__" in str(exc_info.value)

    def test_binary_path(self) -> None:
        cmd = Command(PYTHON_NAME, search_path=[PYTHON_DIR])
        assert cmd.binary_path().is_absolute()
        assert cmd.binary_path().samefile(PYTHON)

    def test_args_getter_returns_empty_tuple_by_default(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.args() == ()

    def test_args_setter_and_getter(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.args("hello", "world")
        assert returned is cmd
        assert cmd.args() == ("hello", "world")

    def test_env_getter_returns_empty_dict_by_default(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.env() == {}

    def test_env_setter_and_getter(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.env({"FOO": "bar"})
        assert returned is cmd
        assert cmd.env() == {"FOO": "bar"}
        cmd.env()["EXTRA"] = "x"
        assert "EXTRA" not in cmd.env()

    def test_cwd_getter_returns_cwd(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.cwd() == Path.cwd()

    def test_cwd_setter_and_getter(self) -> None:
        cmd = Command(PYTHON)
        new_cwd = Path("/tmp")
        returned = cmd.cwd(new_cwd)
        assert returned is cmd
        assert cmd.cwd() == new_cwd

    def test_stdin_getter_returns_none_by_default(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.stdin() is None

    def test_stdin_setter(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.stdin("some input")
        assert returned is cmd
        assert cmd.stdin() == "some input"

    def test_stdout_getter_returns_none_by_default(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.stdout() is None

    def test_stdout_setter_callable(self) -> None:
        cmd = Command(PYTHON)
        callback: Callable[[str], None] = lambda _: None  # noqa: E731
        cmd.stdout(callback)
        assert cmd.stdout() is callback

    def test_stdout_setter_path_is_absolutized(self) -> None:
        cmd = Command(PYTHON)
        relative = Path("output.txt")
        cmd.stdout(relative)
        assert cmd.stdout() == relative.absolute()

    def test_stderr_getter_returns_none_by_default(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.stderr() is None

    def test_stderr_setter_path_is_absolutized(self) -> None:
        cmd = Command(PYTHON)
        relative = Path("error.txt")
        cmd.stderr(relative)
        assert cmd.stderr() == relative.absolute()

    def test_inherit_sets_all_streams(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.inherit()
        assert returned is cmd
        assert cmd.stdin() is sys.stdin
        assert cmd.stdout() is sys.stdout
        assert cmd.stderr() is sys.stderr

    def test_inherit_out_sets_stdout_stderr(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.inherit_out()
        assert returned is cmd
        assert cmd.stdout() is sys.stdout
        assert cmd.stderr() is sys.stderr

    def test_timeout_setter(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.timeout(30)
        assert returned is cmd

    def test_user_getter_returns_none_by_default(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.user() is None

    @posix_only
    def test_user_setter_and_getter(self) -> None:
        cmd = Command(PYTHON)
        returned = cmd.user(os.getuid())
        assert returned is cmd
        assert cmd.user() == os.getuid()

    def test_group_and_extra_groups_default_to_none(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.group() is None
        assert cmd.extra_groups() is None

    @posix_only
    def test_group_and_extra_groups_setters(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.group(os.getgid()) is cmd
        assert cmd.extra_groups([]) is cmd
        assert cmd.group() == os.getgid()
        assert cmd.extra_groups() == []

    @posix_only
    def test_exec_with_group(self) -> None:
        capture = StringIO()
        py("print(os.getgid())").group(os.getgid()).stdout(capture).exec()
        assert capture.getvalue().strip() == str(os.getgid())

    def test_search_path_restricts_lookup(self, tmp_path: Path) -> None:
        with pytest.raises(CommandNotFound):
            Command(PYTHON_NAME, search_path=[str(tmp_path)])

    @posix_only
    def test_copy_from_another_command(self) -> None:
        src = Command(PYTHON)
        src.group(os.getgid()).extra_groups([])
        src.args("hello").env({"X": "1"}).cwd(Path("/tmp")).timeout(10).user(
            os.getuid()
        )
        dst = Command(PYTHON)
        returned = dst.copy(src)
        assert returned is dst
        assert dst.args() == ("hello",)
        assert dst.env() == {"X": "1"}
        assert dst.cwd() == Path("/tmp")
        assert dst.user() == os.getuid()
        assert dst.group() == os.getgid()
        assert dst.extra_groups() == []

    def test_exec_success(self) -> None:
        cmd = echo("hello")
        process = cmd.exec()
        assert process.returncode == 0

    def test_exec_raises_error_return_code(self) -> None:
        cmd = false()
        with pytest.raises(ErrorReturnCode) as exc_info:
            cmd.exec()
        assert exc_info.value.exit_code != 0
        assert PYTHON in exc_info.value.cmd

    def test_error_return_code_properties(self) -> None:
        err = ErrorReturnCode(cmd="mycmd", exit_code=42)
        assert err.cmd == "mycmd"
        assert err.exit_code == 42
        assert "mycmd" in str(err)
        assert "42" in str(err)

    def test_exec_raises_timeout_exception(self) -> None:
        cmd = sleep(10).timeout(1)
        with pytest.raises(TimeoutException) as exc_info:
            cmd.exec()
        assert exc_info.value.timeout == 1
        assert "sleep" in exc_info.value.cmd

    def test_exec_accepts_fractional_timeout(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            sleep(10).timeout(0.2).exec()
        assert exc_info.value.timeout == 0.2

    def test_timeout_exception_properties(self) -> None:
        err = TimeoutException(cmd="mycmd", timeout=5.0)
        assert err.cmd == "mycmd"
        assert err.timeout == 5.0
        assert "mycmd" in str(err)
        assert "5.0" in str(err)

    def test_exec_no_wait_returns_running_process(self) -> None:
        cmd = sleep(5)
        process = cmd.exec(wait_completion=False)
        assert process.returncode is None
        process.terminate()
        process.wait()

    def test_exec_stdout_to_callable(self) -> None:
        lines: list[str] = []
        cmd = echo("hello world")
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("hello world" in line for line in lines)

    def test_exec_stderr_to_callable(self) -> None:
        lines: list[str] = []
        cmd = py("print('error', file=sys.stderr)")
        cmd.stderr(lines.append)
        cmd.exec()
        assert any("error" in line for line in lines)

    def test_exec_stdout_to_stringio(self) -> None:
        buf = StringIO()
        cmd = echo("captured")
        cmd.stdout(buf)
        cmd.exec()
        assert "captured" in buf.getvalue()

    @posix_only
    def test_exec_with_user(self) -> None:
        # Dropping to your own uid needs no elevated privilege, so this runs
        # unprivileged in CI while still exercising the subprocess.Popen(user=)
        # wiring end to end.
        buf = StringIO()
        cmd = py("print(os.getuid())").user(os.getuid())
        cmd.stdout(buf)
        cmd.exec()
        assert buf.getvalue().strip() == str(os.getuid())

    def test_exec_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out.txt"
        cmd = echo("file output")
        cmd.stdout(out_file)
        cmd.exec()
        assert "file output" in out_file.read_text()

    def test_exec_stderr_to_file(self, tmp_path: Path) -> None:
        err_file = tmp_path / "err.txt"
        cmd = py("print('err_content', file=sys.stderr)")
        cmd.stderr(err_file)
        cmd.exec()
        assert "err_content" in err_file.read_text()

    def test_exec_stdout_to_non_iobase_file_object(self, tmp_path: Path) -> None:
        writer = _FdWriter(tmp_path / "out")
        echo("duck").stdout(writer).exec()
        writer.close()
        assert (tmp_path / "out").read_text() == "duck\n"

    def test_exec_stdout_to_bytesio(self) -> None:
        payload = bytes(range(256)) * 512
        out = BytesIO()
        cat().stdin(payload).stdout(out).exec()
        assert out.getvalue() == payload

    def test_exec_stdout_to_binary_file_object(self, tmp_path: Path) -> None:
        # A real fd-backed binary file object (e.g. Path.open("wb")) must be used
        # directly, not silently dropped into an undrained pipe.
        out_file = tmp_path / "binary_out.txt"
        with out_file.open("wb") as fh:
            echo("binary file object output").stdout(fh).exec()
        assert "binary file object output" in out_file.read_text()

    def test_exec_stdin_from_binary_file(self, tmp_path: Path) -> None:
        in_file = tmp_path / "input.bin"
        in_file.write_text("hello from binary file\n")
        lines: list[str] = []
        with in_file.open("rb") as fh:
            cat().stdin(fh).stdout(lines.append).exec()
        assert any("hello from binary file" in line for line in lines)

    def test_exec_stdin_from_string(self) -> None:
        lines: list[str] = []
        cmd = cat()
        cmd.stdin("hello from stdin\n")
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("hello from stdin" in line for line in lines)

    @pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
    def test_exec_stdin_string_tolerates_child_not_reading(self) -> None:
        true().stdin("x" * (1 << 20)).exec()

    @pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
    def test_exec_stdin_queue_tolerates_child_not_reading(self) -> None:
        q: Queue[bytes | str | None] = Queue()
        q.put("x" * (1 << 20))
        q.put(None)
        true().stdin(q).exec()

    def test_exec_large_stdin_string_does_not_deadlock(self) -> None:
        # Larger than any pipe buffer: stdout must be drained while stdin is fed.
        payload = "x" * (1 << 20)
        out = StringIO()
        cat().stdin(payload).stdout(out).timeout(10).exec()
        assert out.getvalue() == payload

    def test_exec_stdin_from_stringio(self) -> None:
        out = StringIO()
        cat().stdin(StringIO("hello from StringIO\n")).stdout(out).exec()
        assert out.getvalue() == "hello from StringIO\n"

    def test_exec_stdin_from_bytesio(self, tmp_path: Path) -> None:
        payload = bytes(range(256)) * 512
        out = tmp_path / "out.bin"
        cat().stdin(BytesIO(payload)).stdout(out).exec()
        assert out.read_bytes() == payload

    def test_exec_stdin_from_bytes(self) -> None:
        payload = bytes(range(256))
        out = StringIO()
        hexdump().stdin(payload).stdout(out).exec()
        assert out.getvalue().split() == [f"{byte:02x}" for byte in payload]

    def test_exec_stdin_from_queue(self) -> None:
        lines: list[str] = []
        q: Queue[bytes | str | None] = Queue()
        q.put("line1\n")
        q.put("line2\n")
        q.put(None)
        cmd = cat()
        cmd.stdin(q)
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("line1" in line for line in lines)
        assert any("line2" in line for line in lines)

    def test_exec_rejects_async_queue_stdin(self) -> None:
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        with pytest.raises(TypeError, match="asyncio.Queue"):
            cat().stdin(q).exec()

    def test_pipeline_rejects_async_queue_stdin(self) -> None:
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        with pytest.raises(TypeError, match="asyncio.Queue"):
            cat().stdin(q).pipeline(cat())

    def test_exec_with_env(self) -> None:
        lines: list[str] = []
        env = EnvCmd.inherit().add({"MY_TEST_VAR": "my_value"}).build()
        cmd = py("print(os.environ['MY_TEST_VAR'])")
        cmd.env(env)
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("my_value" in line for line in lines)

    def test_exec_without_env_inherits_parent_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROCLET_SENTINEL", "inherited")
        out = StringIO()
        printenv().stdout(out).exec()
        assert "PROCLET_SENTINEL=inherited" in out.getvalue().splitlines()

    def test_exec_with_empty_env_runs_without_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The child interpreter may set a few vars of its own (e.g. LC_CTYPE), so
        # check that nothing from this process leaks through.
        monkeypatch.setenv("PROCLET_SENTINEL", "inherited")
        out = StringIO()
        printenv().env(EnvCmd.empty().add(MINIMAL_ENV).build()).stdout(out).exec()
        assert "PROCLET_SENTINEL" not in out.getvalue()

    def test_env_setter_copies_value(self) -> None:
        env = {"FOO": "bar"}
        cmd = Command(PYTHON).env(env)
        env["LATE"] = "x"
        assert cmd.env() == {"FOO": "bar"}

    def test_exec_with_cwd(self, tmp_path: Path) -> None:
        lines: list[str] = []
        cmd = pwd()
        cmd.cwd(tmp_path)
        cmd.stdout(lines.append)
        cmd.exec()
        assert Path("".join(lines).strip()).samefile(tmp_path)

    def test_exec_command_not_found_from_path(self) -> None:
        with pytest.raises(CommandNotFound):
            Command(Path("/no/such/binary")).exec()

    async def test_aexec_success(self) -> None:
        cmd = echo("async hello")
        process = await cmd.aexec()
        assert process.returncode == 0

    async def test_aexec_raises_error_return_code(self) -> None:
        cmd = false()
        with pytest.raises(ErrorReturnCode):
            await cmd.aexec()

    async def test_aexec_raises_timeout_exception(self) -> None:
        cmd = sleep(10).timeout(1)
        with pytest.raises(TimeoutException):
            await cmd.aexec()

    async def test_aexec_no_wait_returns_running_process(self) -> None:
        cmd = sleep(5)
        process = await cmd.aexec(wait_completion=False)
        assert process.returncode is None
        process.terminate()
        await process.wait()

    async def test_aexec_stdout_to_callable(self) -> None:
        lines: list[str] = []
        cmd = echo("async output")
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async output" in line for line in lines)

    async def test_aexec_stderr_to_callable(self) -> None:
        lines: list[str] = []
        cmd = py("print('async_err', file=sys.stderr)")
        cmd.stderr(lines.append)
        await cmd.aexec()
        assert any("async_err" in line for line in lines)

    async def test_aexec_stdout_to_stringio(self) -> None:
        buf = StringIO()
        cmd = echo("async captured")
        cmd.stdout(buf)
        await cmd.aexec()
        result = buf.getvalue()
        assert "async captured" in result

    async def test_aexec_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "async_out.txt"
        cmd = echo("async file output")
        cmd.stdout(out_file)
        await cmd.aexec()
        assert "async file output" in out_file.read_text()

    async def test_aexec_stdout_to_non_iobase_file_object(self, tmp_path: Path) -> None:
        writer = _FdWriter(tmp_path / "out")
        await echo("duck").stdout(writer).aexec()
        writer.close()
        assert (tmp_path / "out").read_text() == "duck\n"

    async def test_aexec_stdout_to_bytesio(self) -> None:
        payload = bytes(range(256)) * 512
        out = BytesIO()
        await cat().stdin(payload).stdout(out).aexec()
        assert out.getvalue() == payload

    async def test_aexec_stdout_to_binary_file_object(self, tmp_path: Path) -> None:
        # A real fd-backed binary file object used to fall through to DEVNULL in
        # the async path, silently discarding the child's output.
        out_file = tmp_path / "async_binary_out.txt"
        with out_file.open("wb") as fh:
            await echo("async binary file output").stdout(fh).aexec()
        assert "async binary file output" in out_file.read_text()

    async def test_aexec_stdin_from_binary_file(self, tmp_path: Path) -> None:
        in_file = tmp_path / "async_input.bin"
        in_file.write_text("async hello from binary file\n")
        lines: list[str] = []
        with in_file.open("rb") as fh:
            await cat().stdin(fh).stdout(lines.append).aexec()
        assert any("async hello from binary file" in line for line in lines)

    async def test_aexec_stdin_from_string(self) -> None:
        lines: list[str] = []
        cmd = cat()
        cmd.stdin("async stdin\n")
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async stdin" in line for line in lines)

    async def test_aexec_stdin_string_tolerates_child_not_reading(self) -> None:
        await true().stdin("x" * (1 << 20)).aexec()

    async def test_aexec_stdin_queue_tolerates_child_not_reading(self) -> None:
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        q.put_nowait("x" * (1 << 20))
        q.put_nowait(None)
        await true().stdin(q).aexec()

    async def test_aexec_stdout_line_longer_than_stream_limit(self) -> None:
        payload = "x" * (1 << 20)
        out = StringIO()
        await cat().stdin(payload).stdout(out).timeout(10).aexec()
        assert out.getvalue() == payload

    async def test_aexec_large_stdin_string_does_not_deadlock(self) -> None:
        payload = ("x" * 1023 + "\n") * 1024
        out = StringIO()
        await cat().stdin(payload).stdout(out).timeout(10).aexec()
        assert out.getvalue() == payload

    async def test_aexec_stdin_from_stringio(self) -> None:
        out = StringIO()
        await cat().stdin(StringIO("async StringIO\n")).stdout(out).aexec()
        assert out.getvalue() == "async StringIO\n"

    async def test_aexec_stdin_from_bytes(self) -> None:
        payload = bytes(range(256))
        out = StringIO()
        await hexdump().stdin(payload).stdout(out).aexec()
        assert out.getvalue().split() == [f"{byte:02x}" for byte in payload]

    async def test_aexec_stdin_from_queue(self) -> None:
        lines: list[str] = []
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        q.put_nowait("async_line1\n")
        q.put_nowait("async_line2\n")
        q.put_nowait(None)
        cmd = cat()
        cmd.stdin(q)
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async_line1" in line for line in lines)
        assert any("async_line2" in line for line in lines)

    async def test_aexec_rejects_sync_queue_stdin(self) -> None:
        q: Queue[bytes | str | None] = Queue()
        with pytest.raises(TypeError, match="queue.Queue"):
            await cat().stdin(q).aexec()

    async def test_apipeline_rejects_sync_queue_stdin(self) -> None:
        q: Queue[bytes | str | None] = Queue()
        with pytest.raises(TypeError, match="queue.Queue"):
            await cat().stdin(q).apipeline(cat())

    async def test_aexec_timeout_cancels_pending_io_tasks(self) -> None:
        # The queue never gets its None sentinel, so the feeder would wait forever.
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        with pytest.raises(TimeoutException):
            await cat().stdin(q).timeout(1).aexec()
        assert asyncio.all_tasks() == {asyncio.current_task()}

    async def test_aexec_with_env(self) -> None:
        lines: list[str] = []
        env = EnvCmd.inherit().add({"ASYNC_VAR": "async_val"}).build()
        cmd = py("print(os.environ['ASYNC_VAR'])")
        cmd.env(env)
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async_val" in line for line in lines)

    async def test_aexec_with_empty_env_runs_without_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROCLET_SENTINEL", "inherited")
        out = StringIO()
        await printenv().env(dict(MINIMAL_ENV)).stdout(out).aexec()
        assert "PROCLET_SENTINEL" not in out.getvalue()

    async def test_aexec_with_cwd(self, tmp_path: Path) -> None:
        lines: list[str] = []
        cmd = pwd()
        cmd.cwd(tmp_path)
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert Path("".join(lines).strip()).samefile(tmp_path)


class TestCommandPipe:
    def test_pipe_success_captures_downstream_stdout(self) -> None:
        buf = StringIO()
        downstream = echo("hello world").pipe(cat().stdout(buf))
        assert downstream.returncode == 0
        assert "hello world" in buf.getvalue()

    def test_pipe_upstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        py("print('out')", "print('err', file=sys.stderr)").stderr(
            err_lines.append
        ).pipe(cat())
        assert any("err" in line for line in err_lines)

    def test_pipe_upstream_error_return_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            false().pipe(cat())
        assert PYTHON in exc_info.value.cmd

    def test_pipe_downstream_error_return_code(self) -> None:
        # The failing downstream must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            echo("hi").pipe(py(DRAIN_STDIN, "sys.exit(1)"))
        assert exc_info.value.exit_code == 1

    def test_pipe_upstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("__no_such_binary_exists__").pipe(cat())
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipe_downstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            echo("hi").pipe(Command("__no_such_binary_exists__"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipe_upstream_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            sleep(10).timeout(1).pipe(cat())
        assert exc_info.value.timeout == 1

    def test_pipe_downstream_timeout_raises(self) -> None:
        # Upstream must finish on its own (echo writes a few bytes and exits
        # regardless of whether downstream ever reads them) so we actually reach
        # downstream's own wait/timeout instead of blocking on upstream first.
        with pytest.raises(TimeoutException) as exc_info:
            echo("hi").pipe(sleep(10).timeout(1))
        assert exc_info.value.timeout == 1

    def test_pipe_ignores_upstream_stdout_redirect(self) -> None:
        # self's own .stdout() is ignored: its stdout always becomes the pipe.
        ignored = StringIO()
        buf = StringIO()
        echo("via pipe").stdout(ignored).pipe(cat().stdout(buf))
        assert ignored.getvalue() == ""
        assert "via pipe" in buf.getvalue()

    def test_pipe_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = random_bytes(65536).pipe(count_bytes().stdout(buf))
        assert downstream.returncode == 0
        assert buf.getvalue().split() == ["65536"]

    def test_pipe_honors_downstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        echo("x").pipe(pwd().cwd(tmp_path).stdout(buf))
        assert Path(buf.getvalue().strip()).samefile(tmp_path)

    def test_pipe_no_deadlock_when_downstream_exits_early(self) -> None:
        result: dict[str, object] = {}

        def run() -> None:
            try:
                process = yes().pipe(head_bytes(100))
                result["returncode"] = process.returncode
            except ErrorReturnCode as err:
                result["error"] = err

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), "pipe() deadlocked when downstream exited early"
        assert "error" in result or "returncode" in result

    def test_pipe_upstream_stdin_from_queue(self) -> None:
        buf = StringIO()
        q: Queue[bytes | str | None] = Queue()
        q.put("line1\n")
        q.put("line2\n")
        q.put(None)
        cat().stdin(q).pipe(cat().stdout(buf))
        assert "line1" in buf.getvalue()
        assert "line2" in buf.getvalue()

    def test_pipe_downstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        echo("hi").pipe(
            py(DRAIN_STDIN, "print('down_err', file=sys.stderr)").stderr(
                err_lines.append
            )
        )
        assert any("down_err" in line for line in err_lines)

    def test_pipe_honors_env_on_both_sides(self) -> None:
        buf = StringIO()
        up_env = EnvCmd.inherit().add({"PIPE_UP_VAR": "up_value"}).build()
        down_env = EnvCmd.inherit().add({"PIPE_DOWN_VAR": "down_value"}).build()
        py("print(os.environ['PIPE_UP_VAR'])").env(up_env).pipe(
            py(COPY_STDIN, "print(os.environ['PIPE_DOWN_VAR'])")
            .env(down_env)
            .stdout(buf)
        )
        assert "up_value" in buf.getvalue()
        assert "down_value" in buf.getvalue()

    def test_pipe_honors_upstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        pwd().cwd(tmp_path).pipe(cat().stdout(buf))
        assert Path(buf.getvalue().strip()).samefile(tmp_path)

    def test_pipe_notifies_observers_on_both_sides(self) -> None:
        up_observer = _RecordingObserver()
        down_observer = _RecordingObserver()
        echo("hi").add_observer(up_observer).pipe(cat().add_observer(down_observer))
        assert len(up_observer.spawned) == 1
        assert len(up_observer.exited) == 1
        assert len(down_observer.spawned) == 1
        assert len(down_observer.exited) == 1

    def test_pipe_downstream_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "pipe_out.txt"
        echo("piped to file").pipe(cat().stdout(out_file))
        assert "piped to file" in out_file.read_text()

    def test_pipe_downstream_stdout_to_binary_file_object(self, tmp_path: Path) -> None:
        out_file = tmp_path / "pipe_binary_out.txt"
        with out_file.open("wb") as fh:
            echo("binary pipe output").pipe(cat().stdout(fh))
        assert "binary pipe output" in out_file.read_text()

    def test_pipe_upstream_stderr_to_binary_file_object(self, tmp_path: Path) -> None:
        err_file = tmp_path / "pipe_binary_err.txt"
        with err_file.open("wb") as fh:
            py("print('out')", "print('up_err', file=sys.stderr)").stderr(fh).pipe(
                cat()
            )
        assert "up_err" in err_file.read_text()

    def test_pipe_ignores_downstream_stdin(self) -> None:
        # other's own .stdin() is ignored: its stdin always becomes the upstream pipe.
        buf = StringIO()
        echo("via pipe stdin").pipe(cat().stdin("ignored stdin content\n").stdout(buf))
        assert "via pipe stdin" in buf.getvalue()
        assert "ignored stdin content" not in buf.getvalue()


class TestCommandPipeline:
    def test_pipeline_requires_at_least_one_downstream_command(self) -> None:
        with pytest.raises(ValueError):
            Command(PYTHON).pipeline()

    def test_pipeline_success_captures_final_stdout(self) -> None:
        buf = StringIO()
        downstream = echo("hello world").pipeline(cat(), cat(), cat().stdout(buf))
        assert downstream.returncode == 0
        assert "hello world" in buf.getvalue()

    def test_pipeline_transforms_through_every_stage(self) -> None:
        buf = StringIO()
        echo("abc").pipeline(
            upper(),
            rev().stdout(buf),
        )
        assert buf.getvalue().strip() == "CBA"

    def test_pipeline_equivalent_to_pipe_for_two_stages(self) -> None:
        buf_pipe = StringIO()
        buf_pipeline = StringIO()
        echo("same result").pipe(cat().stdout(buf_pipe))
        echo("same result").pipeline(cat().stdout(buf_pipeline))
        assert buf_pipe.getvalue() == buf_pipeline.getvalue()

    def test_pipeline_ignores_intermediate_stdout_redirect(self) -> None:
        ignored = StringIO()
        buf = StringIO()
        echo("via pipeline").pipeline(
            cat().stdout(ignored),
            cat().stdout(buf),
        )
        assert ignored.getvalue() == ""
        assert "via pipeline" in buf.getvalue()

    def test_pipeline_ignores_intermediate_stdin(self) -> None:
        buf = StringIO()
        echo("via pipeline stdin").pipeline(
            cat().stdin("ignored stdin content\n"),
            cat().stdout(buf),
        )
        assert "via pipeline stdin" in buf.getvalue()
        assert "ignored stdin content" not in buf.getvalue()

    def test_pipeline_honors_first_stage_stdin(self) -> None:
        buf = StringIO()
        q: Queue[bytes | str | None] = Queue()
        q.put("line1\n")
        q.put(None)
        cat().stdin(q).pipeline(cat(), cat().stdout(buf))
        assert "line1" in buf.getvalue()

    def test_pipeline_forwards_stderr_from_every_stage(self) -> None:
        err_lines: list[str] = []
        py("print('out')", "print('first_err', file=sys.stderr)").stderr(
            err_lines.append
        ).pipeline(
            py(DRAIN_STDIN, "print('middle_err', file=sys.stderr)").stderr(
                err_lines.append
            ),
            py(DRAIN_STDIN, "print('last_err', file=sys.stderr)").stderr(
                err_lines.append
            ),
        )
        joined = "".join(err_lines)
        assert "first_err" in joined
        assert "middle_err" in joined
        assert "last_err" in joined

    def test_pipeline_first_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("__no_such_binary_exists__").pipeline(cat())
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipeline_middle_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            echo("hi").pipeline(Command("__no_such_binary_exists__"), cat())
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipeline_last_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            echo("hi").pipeline(cat(), Command("__no_such_binary_exists__"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipeline_middle_stage_error_return_code(self) -> None:
        # The failing stage must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream stage before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            echo("hi").pipeline(py(DRAIN_STDIN, "sys.exit(1)"), cat())
        assert exc_info.value.exit_code == 1

    def test_pipeline_cleans_up_stages_exited_after_earlier_failure(
        self, tmp_path: Path
    ) -> None:
        # The last stage exits right away; the first fails later, so _finish()
        # never runs for the last one.
        observer = _RecordingObserver()
        last = true().stdout(tmp_path / "out").add_observer(observer)
        with pytest.raises(ErrorReturnCode):
            py("time.sleep(0.3)", "sys.exit(1)").pipeline(last)
        assert len(observer.exited) == 1

    def test_pipeline_last_stage_error_return_code(self) -> None:
        # Same reasoning as above: the failing last stage must drain stdin first.
        with pytest.raises(ErrorReturnCode) as exc_info:
            echo("hi").pipeline(cat(), py(DRAIN_STDIN, "sys.exit(1)"))
        assert exc_info.value.exit_code == 1

    def test_pipeline_middle_stage_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            echo("hi").pipeline(sleep(10).timeout(1), cat())
        assert exc_info.value.timeout == 1

    def test_pipeline_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = random_bytes(65536).pipeline(cat(), count_bytes().stdout(buf))
        assert downstream.returncode == 0
        assert buf.getvalue().split() == ["65536"]

    def test_pipeline_no_deadlock_when_middle_stage_exits_early(self) -> None:
        result: dict[str, object] = {}

        def run() -> None:
            try:
                process = yes().pipeline(head_bytes(100), count_bytes())
                result["returncode"] = process.returncode
            except ErrorReturnCode as err:
                result["error"] = err

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), (
            "pipeline() deadlocked when a middle stage exited early"
        )
        assert "error" in result or "returncode" in result

    def test_pipeline_honors_env_per_stage(self) -> None:
        buf = StringIO()
        first_env = EnvCmd.inherit().add({"PIPELINE_FIRST_VAR": "first_value"}).build()
        last_env = EnvCmd.inherit().add({"PIPELINE_LAST_VAR": "last_value"}).build()
        py("print(os.environ['PIPELINE_FIRST_VAR'])").env(first_env).pipeline(
            cat(),
            py(COPY_STDIN, "print(os.environ['PIPELINE_LAST_VAR'])")
            .env(last_env)
            .stdout(buf),
        )
        assert "first_value" in buf.getvalue()
        assert "last_value" in buf.getvalue()

    def test_pipeline_honors_last_stage_cwd(self, tmp_path: Path) -> None:
        # The last stage must actually drain stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the middle stage before it finishes writing.
        buf = StringIO()
        echo("x").pipeline(
            cat(),
            py(DRAIN_STDIN, "print(os.getcwd())").cwd(tmp_path).stdout(buf),
        )
        assert Path(buf.getvalue().strip()).samefile(tmp_path)

    def test_pipeline_notifies_observers_for_every_stage(self) -> None:
        observers = [_RecordingObserver() for _ in range(3)]
        echo("hi").add_observer(observers[0]).pipeline(
            cat().add_observer(observers[1]),
            cat().add_observer(observers[2]),
        )
        for observer in observers:
            assert len(observer.spawned) == 1
            assert len(observer.exited) == 1


class TestCommandApipe:
    async def test_apipe_success_captures_downstream_stdout(self) -> None:
        buf = StringIO()
        downstream = await echo("hello world").apipe(cat().stdout(buf))
        returncode, output = downstream.returncode, buf.getvalue()
        assert returncode == 0
        assert "hello world" in output

    async def test_apipe_upstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        await (
            py("print('out')", "print('err', file=sys.stderr)")
            .stderr(err_lines.append)
            .apipe(cat())
        )
        assert any("err" in line for line in err_lines)

    async def test_apipe_upstream_error_return_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            await false().apipe(cat())
        assert PYTHON in exc_info.value.cmd

    async def test_apipe_downstream_error_return_code(self) -> None:
        # The failing downstream must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            await echo("hi").apipe(py(DRAIN_STDIN, "sys.exit(1)"))
        assert exc_info.value.exit_code == 1

    async def test_apipe_upstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await Command("__no_such_binary_exists__").apipe(cat())
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipe_downstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await echo("hi").apipe(Command("__no_such_binary_exists__"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipe_upstream_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            await sleep(10).timeout(1).apipe(cat())
        assert exc_info.value.timeout == 1

    async def test_apipe_downstream_timeout_raises(self) -> None:
        # Upstream must finish on its own so we actually reach downstream's
        # own wait/timeout instead of blocking on upstream first.
        with pytest.raises(TimeoutException) as exc_info:
            await echo("hi").apipe(sleep(10).timeout(1))
        assert exc_info.value.timeout == 1

    async def test_apipe_ignores_upstream_stdout_redirect(self) -> None:
        # self's own .stdout() is ignored: its stdout always becomes the pipe.
        ignored = StringIO()
        buf = StringIO()
        await echo("via pipe").stdout(ignored).apipe(cat().stdout(buf))
        ignored_value, buf_value = ignored.getvalue(), buf.getvalue()
        assert ignored_value == ""
        assert "via pipe" in buf_value

    async def test_apipe_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = await random_bytes(65536).apipe(count_bytes().stdout(buf))
        returncode, output = downstream.returncode, buf.getvalue()
        assert returncode == 0
        assert output.split() == ["65536"]

    async def test_apipe_honors_downstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        await echo("x").apipe(pwd().cwd(tmp_path).stdout(buf))
        output = buf.getvalue()
        assert Path(output.strip()).samefile(tmp_path)

    async def test_apipe_no_deadlock_when_downstream_exits_early(self) -> None:
        async def run() -> str:
            try:
                process = await yes().apipe(head_bytes(100))
                return f"returncode={process.returncode}"
            except ErrorReturnCode as err:
                return f"error={err}"

        result = await asyncio.wait_for(run(), timeout=10)
        assert result.startswith("returncode=") or result.startswith("error=")

    async def test_apipe_upstream_stdin_from_queue(self) -> None:
        buf = StringIO()
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        q.put_nowait("async_line1\n")
        q.put_nowait("async_line2\n")
        q.put_nowait(None)
        await cat().stdin(q).apipe(cat().stdout(buf))
        output = buf.getvalue()
        assert "async_line1" in output
        assert "async_line2" in output

    async def test_apipe_downstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        await echo("hi").apipe(
            py(DRAIN_STDIN, "print('async_down_err', file=sys.stderr)").stderr(
                err_lines.append
            )
        )
        assert any("async_down_err" in line for line in err_lines)

    async def test_apipe_honors_env_on_both_sides(self) -> None:
        buf = StringIO()
        up_env = EnvCmd.inherit().add({"APIPE_UP_VAR": "async_up_value"}).build()
        down_env = EnvCmd.inherit().add({"APIPE_DOWN_VAR": "async_down_value"}).build()
        await (
            py("print(os.environ['APIPE_UP_VAR'])")
            .env(up_env)
            .apipe(
                py(COPY_STDIN, "print(os.environ['APIPE_DOWN_VAR'])")
                .env(down_env)
                .stdout(buf)
            )
        )
        output = buf.getvalue()
        assert "async_up_value" in output
        assert "async_down_value" in output

    async def test_apipe_honors_upstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        await pwd().cwd(tmp_path).apipe(cat().stdout(buf))
        output = buf.getvalue()
        assert Path(output.strip()).samefile(tmp_path)

    async def test_apipe_notifies_observers_on_both_sides(self) -> None:
        up_observer = _RecordingObserver()
        down_observer = _RecordingObserver()
        await (
            echo("hi")
            .add_observer(up_observer)
            .apipe(cat().add_observer(down_observer))
        )
        assert len(up_observer.spawned) == 1
        assert len(up_observer.exited) == 1
        assert len(down_observer.spawned) == 1
        assert len(down_observer.exited) == 1

    async def test_apipe_downstream_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "async_pipe_out.txt"
        await echo("async piped to file").apipe(cat().stdout(out_file))
        assert "async piped to file" in out_file.read_text()

    async def test_apipe_downstream_stdout_to_binary_file_object(
        self, tmp_path: Path
    ) -> None:
        out_file = tmp_path / "async_pipe_binary_out.txt"
        with out_file.open("wb") as fh:
            await echo("async binary pipe output").apipe(cat().stdout(fh))
        assert "async binary pipe output" in out_file.read_text()

    async def test_apipe_upstream_stderr_to_binary_file_object(
        self, tmp_path: Path
    ) -> None:
        err_file = tmp_path / "async_pipe_binary_err.txt"
        with err_file.open("wb") as fh:
            await (
                py("print('out')", "print('async_up_err', file=sys.stderr)")
                .stderr(fh)
                .apipe(cat())
            )
        assert "async_up_err" in err_file.read_text()

    async def test_apipe_ignores_downstream_stdin(self) -> None:
        buf = StringIO()
        await echo("via async pipe stdin").apipe(
            cat().stdin("ignored async stdin content\n").stdout(buf)
        )
        output = buf.getvalue()
        assert "via async pipe stdin" in output
        assert "ignored async stdin content" not in output


class TestCommandApipeline:
    async def test_apipeline_requires_at_least_one_downstream_command(self) -> None:
        with pytest.raises(ValueError):
            await Command(PYTHON).apipeline()

    async def test_apipeline_success_captures_final_stdout(self) -> None:
        buf = StringIO()
        downstream = await echo("hello world").apipeline(
            cat(), cat(), cat().stdout(buf)
        )
        assert downstream.returncode == 0
        assert "hello world" in buf.getvalue()

    async def test_apipeline_transforms_through_every_stage(self) -> None:
        buf = StringIO()
        await echo("abc").apipeline(
            upper(),
            rev().stdout(buf),
        )
        assert buf.getvalue().strip() == "CBA"

    async def test_apipeline_equivalent_to_apipe_for_two_stages(self) -> None:
        buf_pipe = StringIO()
        buf_pipeline = StringIO()
        await echo("same result").apipe(cat().stdout(buf_pipe))
        await echo("same result").apipeline(cat().stdout(buf_pipeline))
        assert buf_pipe.getvalue() == buf_pipeline.getvalue()

    async def test_apipeline_ignores_intermediate_stdout_redirect(self) -> None:
        ignored = StringIO()
        buf = StringIO()
        await echo("via apipeline").apipeline(
            cat().stdout(ignored),
            cat().stdout(buf),
        )
        assert ignored.getvalue() == ""
        assert "via apipeline" in buf.getvalue()

    async def test_apipeline_ignores_intermediate_stdin(self) -> None:
        buf = StringIO()
        await echo("via apipeline stdin").apipeline(
            cat().stdin("ignored stdin content\n"),
            cat().stdout(buf),
        )
        assert "via apipeline stdin" in buf.getvalue()
        assert "ignored stdin content" not in buf.getvalue()

    async def test_apipeline_honors_first_stage_stdin(self) -> None:
        buf = StringIO()
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        await q.put("line1\n")
        await q.put(None)
        await cat().stdin(q).apipeline(cat(), cat().stdout(buf))
        assert "line1" in buf.getvalue()

    async def test_apipeline_forwards_stderr_from_every_stage(self) -> None:
        err_lines: list[str] = []
        await (
            py("print('out')", "print('first_err', file=sys.stderr)")
            .stderr(err_lines.append)
            .apipeline(
                py(DRAIN_STDIN, "print('middle_err', file=sys.stderr)").stderr(
                    err_lines.append
                ),
                py(DRAIN_STDIN, "print('last_err', file=sys.stderr)").stderr(
                    err_lines.append
                ),
            )
        )
        joined = "".join(err_lines)
        assert "first_err" in joined
        assert "middle_err" in joined
        assert "last_err" in joined

    async def test_apipeline_first_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await Command("__no_such_binary_exists__").apipeline(cat())
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipeline_middle_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await echo("hi").apipeline(Command("__no_such_binary_exists__"), cat())
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipeline_last_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await echo("hi").apipeline(cat(), Command("__no_such_binary_exists__"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipeline_middle_stage_error_return_code(self) -> None:
        # The failing stage must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream stage before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            await echo("hi").apipeline(py(DRAIN_STDIN, "sys.exit(1)"), cat())
        assert exc_info.value.exit_code == 1

    async def test_apipeline_cleans_up_stages_exited_after_earlier_failure(
        self, tmp_path: Path
    ) -> None:
        observer = _RecordingObserver()
        last = true().stdout(tmp_path / "out").add_observer(observer)
        with pytest.raises(ErrorReturnCode):
            await py("time.sleep(0.3)", "sys.exit(1)").apipeline(last)
        assert len(observer.exited) == 1

    async def test_apipeline_last_stage_error_return_code(self) -> None:
        # Same reasoning as above: the failing last stage must drain stdin first.
        with pytest.raises(ErrorReturnCode) as exc_info:
            await echo("hi").apipeline(cat(), py(DRAIN_STDIN, "sys.exit(1)"))
        assert exc_info.value.exit_code == 1

    async def test_apipeline_middle_stage_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            await echo("hi").apipeline(sleep(10).timeout(1), cat())
        assert exc_info.value.timeout == 1

    async def test_apipeline_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = await random_bytes(65536).apipeline(
            cat(), count_bytes().stdout(buf)
        )
        assert downstream.returncode == 0
        assert buf.getvalue().split() == ["65536"]

    async def test_apipeline_no_deadlock_when_middle_stage_exits_early(self) -> None:
        try:
            process = await asyncio.wait_for(
                yes().apipeline(head_bytes(100), count_bytes()),
                timeout=10,
            )
            assert process.returncode is not None
        except ErrorReturnCode:
            pass

    async def test_apipeline_honors_env_per_stage(self) -> None:
        buf = StringIO()
        first_env = EnvCmd.inherit().add({"PIPELINE_FIRST_VAR": "first_value"}).build()
        last_env = EnvCmd.inherit().add({"PIPELINE_LAST_VAR": "last_value"}).build()
        await (
            py("print(os.environ['PIPELINE_FIRST_VAR'])")
            .env(first_env)
            .apipeline(
                cat(),
                py(COPY_STDIN, "print(os.environ['PIPELINE_LAST_VAR'])")
                .env(last_env)
                .stdout(buf),
            )
        )
        assert "first_value" in buf.getvalue()
        assert "last_value" in buf.getvalue()

    async def test_apipeline_honors_last_stage_cwd(self, tmp_path: Path) -> None:
        # The last stage must actually drain stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the middle stage before it finishes writing.
        buf = StringIO()
        await echo("x").apipeline(
            cat(),
            py(DRAIN_STDIN, "print(os.getcwd())").cwd(tmp_path).stdout(buf),
        )
        assert Path(buf.getvalue().strip()).samefile(tmp_path)

    async def test_apipeline_notifies_observers_for_every_stage(self) -> None:
        observers = [_RecordingObserver() for _ in range(3)]
        await (
            echo("hi")
            .add_observer(observers[0])
            .apipeline(
                cat().add_observer(observers[1]),
                cat().add_observer(observers[2]),
            )
        )
        for observer in observers:
            assert len(observer.spawned) == 1
            assert len(observer.exited) == 1


class _Echo(Bind):
    """Bind printing the given words, written like a real per-binary wrapper."""

    def __init__(self, *words: str) -> None:
        super().__init__(PYTHON)
        self._cmd.copy(echo(*words))


class _Cat(Bind):
    """Bind copying stdin to stdout."""

    def __init__(self) -> None:
        super().__init__(PYTHON)
        self._cmd.copy(cat())


class TestBind:
    def _make_bind(self) -> Bind:
        return Bind(PYTHON)

    def test_command_not_found(self) -> None:
        with pytest.raises(CommandNotFound):
            Bind("__no_such_binary_exists__")

    def test_binary_path(self) -> None:
        b = self._make_bind()
        assert b.binary_path().samefile(PYTHON)

    def test_env_getter(self) -> None:
        b = self._make_bind()
        assert b.env() == {}

    def test_env_setter_chaining(self) -> None:
        b = self._make_bind()
        returned = b.env({"K": "V"})
        assert returned is b
        assert b.env() == {"K": "V"}

    def test_cwd_getter(self) -> None:
        b = self._make_bind()
        assert b.cwd() == Path.cwd()

    def test_cwd_setter_chaining(self) -> None:
        b = self._make_bind()
        returned = b.cwd(Path("/tmp"))
        assert returned is b
        assert b.cwd() == Path("/tmp")

    def test_user_getter(self) -> None:
        b = self._make_bind()
        assert b.user() is None

    @posix_only
    def test_user_setter_chaining(self) -> None:
        b = self._make_bind()
        returned = b.user(os.getuid())
        assert returned is b
        assert b.user() == os.getuid()

    def test_stdin_chaining(self) -> None:
        b = self._make_bind()
        returned = b.stdin("data")
        assert returned is b

    def test_stdout_chaining(self) -> None:
        b = self._make_bind()
        returned = b.stdout(lambda _: None)
        assert returned is b

    def test_stderr_chaining(self) -> None:
        b = self._make_bind()
        returned = b.stderr(lambda _: None)
        assert returned is b

    def test_inherit_chaining(self) -> None:
        b = self._make_bind()
        returned = b.inherit()
        assert returned is b

    def test_inherit_out_chaining(self) -> None:
        b = self._make_bind()
        returned = b.inherit_out()
        assert returned is b

    def test_timeout_chaining(self) -> None:
        b = self._make_bind()
        returned = b.timeout(30)
        assert returned is b

    def test_copy_chaining(self) -> None:
        src_cmd = Command(PYTHON)
        src_cmd.args("hi").env({"X": "1"})
        b = self._make_bind()
        returned = b.copy(src_cmd)
        assert returned is b

    def test_create_returns_command(self) -> None:
        b = self._make_bind()
        cmd = b.create(PYTHON)
        assert isinstance(cmd, Command)

    def test_pipe_into_command(self) -> None:
        buf = StringIO()
        b = _Echo("via-bind")
        b.pipe(cat().stdout(buf))
        assert "via-bind" in buf.getvalue()

    def test_pipe_into_bind(self) -> None:
        buf = StringIO()
        upstream = _Echo("piped")
        downstream = _Cat()
        downstream._cmd.stdout(buf)
        upstream.pipe(downstream)
        assert "piped" in buf.getvalue()

    async def test_apipe_into_command(self) -> None:
        buf = StringIO()
        b = _Echo("via-bind-async")
        await b.apipe(cat().stdout(buf))
        assert "via-bind-async" in buf.getvalue()

    async def test_apipe_into_bind(self) -> None:
        buf = StringIO()
        upstream = _Echo("piped-async")
        downstream = _Cat()
        downstream._cmd.stdout(buf)
        await upstream.apipe(downstream)
        assert "piped-async" in buf.getvalue()

    def test_pipeline_into_command(self) -> None:
        buf = StringIO()
        b = _Echo("via-bind-pipeline")
        b.pipeline(cat(), cat().stdout(buf))
        assert "via-bind-pipeline" in buf.getvalue()

    def test_pipeline_into_bind(self) -> None:
        buf = StringIO()
        upstream = _Echo("piped-pipeline")
        middle = _Cat()
        downstream = _Cat()
        downstream._cmd.stdout(buf)
        upstream.pipeline(middle, downstream)
        assert "piped-pipeline" in buf.getvalue()

    async def test_apipeline_into_command(self) -> None:
        buf = StringIO()
        b = _Echo("via-bind-apipeline")
        await b.apipeline(cat(), cat().stdout(buf))
        assert "via-bind-apipeline" in buf.getvalue()

    async def test_apipeline_into_bind(self) -> None:
        buf = StringIO()
        upstream = _Echo("piped-apipeline")
        middle = _Cat()
        downstream = _Cat()
        downstream._cmd.stdout(buf)
        await upstream.apipeline(middle, downstream)
        assert "piped-apipeline" in buf.getvalue()


class TestOkExitCodes:
    def test_requires_at_least_one_code(self) -> None:
        with pytest.raises(ValueError):
            true().ok_exit_codes()

    def test_exec_default_rejects_non_zero(self) -> None:
        with pytest.raises(ErrorReturnCode):
            py("sys.exit(1)").exec()

    def test_exec_accepts_listed_code(self) -> None:
        py("sys.exit(1)").ok_exit_codes(0, 1).exec()

    def test_exec_rejects_unlisted_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            py("sys.exit(2)").ok_exit_codes(0, 1).exec()
        assert exc_info.value.exit_code == 2

    def test_copy_keeps_ok_exit_codes(self) -> None:
        source = py("sys.exit(1)").ok_exit_codes(0, 1)
        Command(PYTHON).copy(source).exec()

    def test_pipe_accepted_upstream_code_still_feeds_downstream(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        py("print('data')", "sys.exit(1)").ok_exit_codes(0, 1).pipe(write_stdin_to(out))
        assert out.read_text() == "data\n"

    def test_pipe_rejects_unlisted_upstream_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            py("sys.exit(2)").ok_exit_codes(0, 1).pipe(cat())
        assert exc_info.value.exit_code == 2

    def test_pipeline_accepted_middle_code(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        echo("data").pipeline(
            py(COPY_STDIN, "sys.exit(1)").ok_exit_codes(0, 1),
            write_stdin_to(out),
        )
        assert out.read_text() == "data\n"

    async def test_aexec_accepts_listed_code(self) -> None:
        await py("sys.exit(1)").ok_exit_codes(0, 1).aexec()

    async def test_aexec_rejects_unlisted_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            await py("sys.exit(2)").ok_exit_codes(0, 1).aexec()
        assert exc_info.value.exit_code == 2

    async def test_apipe_accepted_upstream_code(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        await (
            py("print('data')", "sys.exit(1)")
            .ok_exit_codes(0, 1)
            .apipe(write_stdin_to(out))
        )
        assert out.read_text() == "data\n"

    async def test_apipeline_accepted_middle_code(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        await echo("data").apipeline(
            py(COPY_STDIN, "sys.exit(1)").ok_exit_codes(0, 1),
            write_stdin_to(out),
        )
        assert out.read_text() == "data\n"


class TestNice:
    _SHOW_NICE = "print(os.getpriority(os.PRIO_PROCESS, 0))"

    def test_getter_defaults_to_none(self) -> None:
        assert Command(PYTHON).nice() is None

    def test_setter_returns_self(self) -> None:
        cmd = Command(PYTHON)
        assert cmd.nice(5) is cmd
        assert cmd.nice() == 5

    def test_copy_carries_nice(self) -> None:
        assert Command(PYTHON).copy(Command(PYTHON).nice(7)).nice() == 7

    def test_bind_passthrough(self) -> None:
        bind = Bind(PYTHON)
        assert bind.nice() is None
        assert bind.nice(3) is bind
        assert bind.nice() == 3

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
    def test_exec_applies_nice(self) -> None:
        buf = StringIO()
        base = os.getpriority(os.PRIO_PROCESS, 0)
        py(self._SHOW_NICE).nice(base + 5).stdout(buf).exec()
        assert buf.getvalue().strip() == str(base + 5)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
    def test_exec_without_nice_keeps_priority(self) -> None:
        buf = StringIO()
        py(self._SHOW_NICE).stdout(buf).exec()
        assert buf.getvalue().strip() == str(os.getpriority(os.PRIO_PROCESS, 0))

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
    async def test_aexec_applies_nice(self) -> None:
        buf = StringIO()
        base = os.getpriority(os.PRIO_PROCESS, 0)
        cmd = py(self._SHOW_NICE).nice(base + 5)
        await cmd.stdout(buf).aexec()
        assert buf.getvalue().strip() == str(base + 5)

    def test_no_creationflags_off_windows(self) -> None:
        assert priority_creationflags(19) == 0
        assert priority_creationflags(None) == 0

    @pytest.mark.parametrize(
        ("nice", "expected"),
        [
            (None, 0),
            (0, 0),
            (19, 1),
            (15, 1),
            (14, 2),
            (1, 2),
            (-1, 3),
            (-14, 3),
            (-15, 4),
            (-20, 4),
        ],
    )
    def test_windows_priority_class(
        self, monkeypatch: pytest.MonkeyPatch, nice: int | None, expected: int
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        for value, name in enumerate(
            [
                "IDLE_PRIORITY_CLASS",
                "BELOW_NORMAL_PRIORITY_CLASS",
                "ABOVE_NORMAL_PRIORITY_CLASS",
                "HIGH_PRIORITY_CLASS",
            ],
            start=1,
        ):
            monkeypatch.setattr(subprocess, name, value, raising=False)
        assert priority_creationflags(nice) == expected

    def test_no_preexec_hook_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert low_priority_hook(5) is None


class TestPosixOnlyOptions:
    @pytest.mark.parametrize(
        ("option", "func", "value"),
        [
            ("user", "setreuid", "nobody"),
            ("group", "setregid", "nogroup"),
            ("extra_groups", "setgroups", []),
        ],
    )
    def test_setter_fails_fast_without_os_support(
        self, monkeypatch: pytest.MonkeyPatch, option: str, func: str, value: object
    ) -> None:
        monkeypatch.delattr(os, func, raising=False)
        with pytest.raises(ValueError, match=option):
            getattr(Command(PYTHON), option)(value)
        with pytest.raises(ValueError, match=option):
            getattr(Bind(PYTHON), option)(value)
        assert getattr(Command(PYTHON), option)() is None
