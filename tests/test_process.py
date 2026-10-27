from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from asyncio import Queue as AsyncQueue
from io import StringIO
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
from proclet.process import _low_priority_hook, _priority_creationflags


class _RecordingObserver:
    """Test ProcessObserver that records every spawn/exit notification it receives."""

    def __init__(self) -> None:
        self.spawned: list[Process] = []
        self.exited: list[Process] = []

    def on_spawn(self, process: Process) -> None:
        self.spawned.append(process)

    def on_exit(self, process: Process) -> None:
        self.exited.append(process)


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
        cmd = Command("echo")
        assert cmd.binary_path().is_absolute()
        assert cmd.binary_path().name == "echo"

    def test_args_getter_returns_empty_tuple_by_default(self) -> None:
        cmd = Command("echo")
        assert cmd.args() == ()

    def test_args_setter_and_getter(self) -> None:
        cmd = Command("echo")
        returned = cmd.args("hello", "world")
        assert returned is cmd
        assert cmd.args() == ("hello", "world")

    def test_env_getter_returns_empty_dict_by_default(self) -> None:
        cmd = Command("echo")
        assert cmd.env() == {}

    def test_env_setter_and_getter(self) -> None:
        cmd = Command("echo")
        returned = cmd.env({"FOO": "bar"})
        assert returned is cmd
        assert cmd.env() == {"FOO": "bar"}
        cmd.env()["EXTRA"] = "x"
        assert "EXTRA" not in cmd.env()

    def test_cwd_getter_returns_cwd(self) -> None:
        cmd = Command("echo")
        assert cmd.cwd() == Path.cwd()

    def test_cwd_setter_and_getter(self) -> None:
        cmd = Command("echo")
        new_cwd = Path("/tmp")
        returned = cmd.cwd(new_cwd)
        assert returned is cmd
        assert cmd.cwd() == new_cwd

    def test_stdin_getter_returns_none_by_default(self) -> None:
        cmd = Command("echo")
        assert cmd.stdin() is None

    def test_stdin_setter(self) -> None:
        cmd = Command("echo")
        returned = cmd.stdin("some input")
        assert returned is cmd
        assert cmd.stdin() == "some input"

    def test_stdout_getter_returns_none_by_default(self) -> None:
        cmd = Command("echo")
        assert cmd.stdout() is None

    def test_stdout_setter_callable(self) -> None:
        cmd = Command("echo")
        callback: Callable[[str], None] = lambda _: None  # noqa: E731
        cmd.stdout(callback)
        assert cmd.stdout() is callback

    def test_stdout_setter_path_is_absolutized(self) -> None:
        cmd = Command("echo")
        relative = Path("output.txt")
        cmd.stdout(relative)
        assert cmd.stdout() == relative.absolute()

    def test_stderr_getter_returns_none_by_default(self) -> None:
        cmd = Command("echo")
        assert cmd.stderr() is None

    def test_stderr_setter_path_is_absolutized(self) -> None:
        cmd = Command("echo")
        relative = Path("error.txt")
        cmd.stderr(relative)
        assert cmd.stderr() == relative.absolute()

    def test_inherit_sets_all_streams(self) -> None:
        cmd = Command("echo")
        returned = cmd.inherit()
        assert returned is cmd
        assert cmd.stdin() is sys.stdin
        assert cmd.stdout() is sys.stdout
        assert cmd.stderr() is sys.stderr

    def test_inherit_out_sets_stdout_stderr(self) -> None:
        cmd = Command("echo")
        returned = cmd.inherit_out()
        assert returned is cmd
        assert cmd.stdout() is sys.stdout
        assert cmd.stderr() is sys.stderr

    def test_timeout_setter(self) -> None:
        cmd = Command("echo")
        returned = cmd.timeout(30)
        assert returned is cmd

    def test_user_getter_returns_none_by_default(self) -> None:
        cmd = Command("echo")
        assert cmd.user() is None

    def test_user_setter_and_getter(self) -> None:
        cmd = Command("echo")
        returned = cmd.user(os.getuid())
        assert returned is cmd
        assert cmd.user() == os.getuid()

    def test_group_and_extra_groups_default_to_none(self) -> None:
        cmd = Command("echo")
        assert cmd.group() is None
        assert cmd.extra_groups() is None

    def test_group_and_extra_groups_setters(self) -> None:
        cmd = Command("echo")
        assert cmd.group(os.getgid()) is cmd
        assert cmd.extra_groups([]) is cmd
        assert cmd.group() == os.getgid()
        assert cmd.extra_groups() == []

    def test_exec_with_group(self) -> None:
        capture = StringIO()
        Command("id").args("-g").group(os.getgid()).stdout(capture).exec()
        assert capture.getvalue().strip() == str(os.getgid())

    def test_search_path_restricts_lookup(self) -> None:
        with pytest.raises(CommandNotFound):
            Command("sh", search_path=["/nonexistent"])

    def test_copy_from_another_command(self) -> None:
        src = Command("echo")
        src.group(os.getgid()).extra_groups([])
        src.args("hello").env({"X": "1"}).cwd(Path("/tmp")).timeout(10).user(
            os.getuid()
        )
        dst = Command("echo")
        returned = dst.copy(src)
        assert returned is dst
        assert dst.args() == ("hello",)
        assert dst.env() == {"X": "1"}
        assert dst.cwd() == Path("/tmp")
        assert dst.user() == os.getuid()
        assert dst.group() == os.getgid()
        assert dst.extra_groups() == []

    def test_exec_success(self) -> None:
        cmd = Command("echo").args("hello")
        process = cmd.exec()
        assert process.returncode == 0

    def test_exec_raises_error_return_code(self) -> None:
        cmd = Command("false")
        with pytest.raises(ErrorReturnCode) as exc_info:
            cmd.exec()
        assert exc_info.value.exit_code != 0
        assert "false" in exc_info.value.cmd

    def test_error_return_code_properties(self) -> None:
        err = ErrorReturnCode(cmd="mycmd", exit_code=42)
        assert err.cmd == "mycmd"
        assert err.exit_code == 42
        assert "mycmd" in str(err)
        assert "42" in str(err)

    def test_exec_raises_timeout_exception(self) -> None:
        cmd = Command("sleep").args("10").timeout(1)
        with pytest.raises(TimeoutException) as exc_info:
            cmd.exec()
        assert exc_info.value.timeout == 1
        assert "sleep" in exc_info.value.cmd

    def test_timeout_exception_properties(self) -> None:
        err = TimeoutException(cmd="mycmd", timeout=5.0)
        assert err.cmd == "mycmd"
        assert err.timeout == 5.0
        assert "mycmd" in str(err)
        assert "5.0" in str(err)

    def test_exec_no_wait_returns_running_process(self) -> None:
        cmd = Command("sleep").args("5")
        process = cmd.exec(wait_completion=False)
        assert process.returncode is None
        process.terminate()
        process.wait()

    def test_exec_stdout_to_callable(self) -> None:
        lines: list[str] = []
        cmd = Command("echo").args("hello world")
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("hello world" in line for line in lines)

    def test_exec_stderr_to_callable(self) -> None:
        lines: list[str] = []
        cmd = Command("sh").args("-c", "echo error >&2")
        cmd.stderr(lines.append)
        cmd.exec()
        assert any("error" in line for line in lines)

    def test_exec_stdout_to_stringio(self) -> None:
        buf = StringIO()
        cmd = Command("echo").args("captured")
        cmd.stdout(buf)
        cmd.exec()
        assert "captured" in buf.getvalue()

    def test_exec_with_user(self) -> None:
        # Dropping to your own uid needs no elevated privilege, so this runs
        # unprivileged in CI while still exercising the subprocess.Popen(user=)
        # wiring end to end.
        buf = StringIO()
        cmd = Command("id").args("-u").user(os.getuid())
        cmd.stdout(buf)
        cmd.exec()
        assert buf.getvalue().strip() == str(os.getuid())

    def test_exec_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "out.txt"
        cmd = Command("echo").args("file output")
        cmd.stdout(out_file)
        cmd.exec()
        assert "file output" in out_file.read_text()

    def test_exec_stderr_to_file(self, tmp_path: Path) -> None:
        err_file = tmp_path / "err.txt"
        cmd = Command("sh").args("-c", "echo err_content >&2")
        cmd.stderr(err_file)
        cmd.exec()
        assert "err_content" in err_file.read_text()

    def test_exec_stdout_to_binary_file_object(self, tmp_path: Path) -> None:
        # A real fd-backed binary file object (e.g. Path.open("wb")) must be used
        # directly, not silently dropped into an undrained pipe.
        out_file = tmp_path / "binary_out.txt"
        with out_file.open("wb") as fh:
            Command("echo").args("binary file object output").stdout(fh).exec()
        assert "binary file object output" in out_file.read_text()

    def test_exec_stdin_from_binary_file(self, tmp_path: Path) -> None:
        in_file = tmp_path / "input.bin"
        in_file.write_text("hello from binary file\n")
        lines: list[str] = []
        with in_file.open("rb") as fh:
            Command("cat").stdin(fh).stdout(lines.append).exec()
        assert any("hello from binary file" in line for line in lines)

    def test_exec_stdin_from_string(self) -> None:
        lines: list[str] = []
        cmd = Command("cat")
        cmd.stdin("hello from stdin\n")
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("hello from stdin" in line for line in lines)

    def test_exec_stdin_from_queue(self) -> None:
        lines: list[str] = []
        q: Queue[bytes | str | None] = Queue()
        q.put("line1\n")
        q.put("line2\n")
        q.put(None)
        cmd = Command("cat")
        cmd.stdin(q)
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("line1" in line for line in lines)
        assert any("line2" in line for line in lines)

    def test_exec_with_env(self) -> None:
        lines: list[str] = []
        env = EnvCmd.inherit().add({"MY_TEST_VAR": "my_value"}).build()
        cmd = Command("sh").args("-c", "echo $MY_TEST_VAR")
        cmd.env(env)
        cmd.stdout(lines.append)
        cmd.exec()
        assert any("my_value" in line for line in lines)

    def test_exec_with_cwd(self, tmp_path: Path) -> None:
        lines: list[str] = []
        cmd = Command("pwd")
        cmd.cwd(tmp_path)
        cmd.stdout(lines.append)
        cmd.exec()
        assert any(str(tmp_path) in line for line in lines)

    def test_exec_command_not_found_from_path(self) -> None:
        with pytest.raises(CommandNotFound):
            Command(Path("/no/such/binary")).exec()

    async def test_aexec_success(self) -> None:
        cmd = Command("echo").args("async hello")
        process = await cmd.aexec()
        assert process.returncode == 0

    async def test_aexec_raises_error_return_code(self) -> None:
        cmd = Command("false")
        with pytest.raises(ErrorReturnCode):
            await cmd.aexec()

    async def test_aexec_raises_timeout_exception(self) -> None:
        cmd = Command("sleep").args("10").timeout(1)
        with pytest.raises(TimeoutException):
            await cmd.aexec()

    async def test_aexec_no_wait_returns_running_process(self) -> None:
        cmd = Command("sleep").args("5")
        process = await cmd.aexec(wait_completion=False)
        assert process.returncode is None
        process.terminate()
        await process.wait()

    async def test_aexec_stdout_to_callable(self) -> None:
        lines: list[str] = []
        cmd = Command("echo").args("async output")
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async output" in line for line in lines)

    async def test_aexec_stderr_to_callable(self) -> None:
        lines: list[str] = []
        cmd = Command("sh").args("-c", "echo async_err >&2")
        cmd.stderr(lines.append)
        await cmd.aexec()
        assert any("async_err" in line for line in lines)

    async def test_aexec_stdout_to_stringio(self) -> None:
        buf = StringIO()
        cmd = Command("echo").args("async captured")
        cmd.stdout(buf)
        await cmd.aexec()
        result = buf.getvalue()
        assert "async captured" in result

    async def test_aexec_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "async_out.txt"
        cmd = Command("echo").args("async file output")
        cmd.stdout(out_file)
        await cmd.aexec()
        assert "async file output" in out_file.read_text()

    async def test_aexec_stdout_to_binary_file_object(self, tmp_path: Path) -> None:
        # A real fd-backed binary file object used to fall through to DEVNULL in
        # the async path, silently discarding the child's output.
        out_file = tmp_path / "async_binary_out.txt"
        with out_file.open("wb") as fh:
            await Command("echo").args("async binary file output").stdout(fh).aexec()
        assert "async binary file output" in out_file.read_text()

    async def test_aexec_stdin_from_binary_file(self, tmp_path: Path) -> None:
        in_file = tmp_path / "async_input.bin"
        in_file.write_text("async hello from binary file\n")
        lines: list[str] = []
        with in_file.open("rb") as fh:
            await Command("cat").stdin(fh).stdout(lines.append).aexec()
        assert any("async hello from binary file" in line for line in lines)

    async def test_aexec_stdin_from_string(self) -> None:
        lines: list[str] = []
        cmd = Command("cat")
        cmd.stdin("async stdin\n")
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async stdin" in line for line in lines)

    async def test_aexec_stdin_from_queue(self) -> None:
        lines: list[str] = []
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        q.put_nowait("async_line1\n")
        q.put_nowait("async_line2\n")
        q.put_nowait(None)
        cmd = Command("cat")
        cmd.stdin(q)
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async_line1" in line for line in lines)
        assert any("async_line2" in line for line in lines)

    async def test_aexec_with_env(self) -> None:
        lines: list[str] = []
        env = EnvCmd.inherit().add({"ASYNC_VAR": "async_val"}).build()
        cmd = Command("sh").args("-c", "echo $ASYNC_VAR")
        cmd.env(env)
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any("async_val" in line for line in lines)

    async def test_aexec_with_cwd(self, tmp_path: Path) -> None:
        lines: list[str] = []
        cmd = Command("pwd")
        cmd.cwd(tmp_path)
        cmd.stdout(lines.append)
        await cmd.aexec()
        assert any(str(tmp_path) in line for line in lines)


class TestCommandPipe:
    def test_pipe_success_captures_downstream_stdout(self) -> None:
        buf = StringIO()
        downstream = (
            Command("echo").args("hello world").pipe(Command("cat").stdout(buf))
        )
        assert downstream.returncode == 0
        assert "hello world" in buf.getvalue()

    def test_pipe_upstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        Command("sh").args("-c", "echo out; echo err >&2").stderr(
            err_lines.append
        ).pipe(Command("cat"))
        assert any("err" in line for line in err_lines)

    def test_pipe_upstream_error_return_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            Command("false").pipe(Command("cat"))
        assert "false" in exc_info.value.cmd

    def test_pipe_downstream_error_return_code(self) -> None:
        # The failing downstream must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            Command("echo").args("hi").pipe(
                Command("sh").args("-c", "cat >/dev/null; exit 1")
            )
        assert exc_info.value.exit_code == 1

    def test_pipe_upstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("__no_such_binary_exists__").pipe(Command("cat"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipe_downstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("echo").args("hi").pipe(Command("__no_such_binary_exists__"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipe_upstream_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            Command("sleep").args("10").timeout(1).pipe(Command("cat"))
        assert exc_info.value.timeout == 1

    def test_pipe_downstream_timeout_raises(self) -> None:
        # Upstream must finish on its own (echo writes a few bytes and exits
        # regardless of whether downstream ever reads them) so we actually reach
        # downstream's own wait/timeout instead of blocking on upstream first.
        with pytest.raises(TimeoutException) as exc_info:
            Command("echo").args("hi").pipe(Command("sleep").args("10").timeout(1))
        assert exc_info.value.timeout == 1

    def test_pipe_ignores_upstream_stdout_redirect(self) -> None:
        # self's own .stdout() is ignored: its stdout always becomes the pipe.
        ignored = StringIO()
        buf = StringIO()
        Command("echo").args("via pipe").stdout(ignored).pipe(
            Command("cat").stdout(buf)
        )
        assert ignored.getvalue() == ""
        assert "via pipe" in buf.getvalue()

    def test_pipe_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = (
            Command("head")
            .args("-c", "65536", "/dev/urandom")
            .pipe(Command("wc").args("-c").stdout(buf))
        )
        assert downstream.returncode == 0
        assert buf.getvalue().split() == ["65536"]

    def test_pipe_honors_downstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        Command("echo").args("x").pipe(Command("pwd").cwd(tmp_path).stdout(buf))
        assert str(tmp_path) in buf.getvalue()

    def test_pipe_no_deadlock_when_downstream_exits_early(self) -> None:
        result: dict[str, object] = {}

        def run() -> None:
            try:
                process = Command("yes").pipe(Command("head").args("-c", "100"))
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
        Command("cat").stdin(q).pipe(Command("cat").stdout(buf))
        assert "line1" in buf.getvalue()
        assert "line2" in buf.getvalue()

    def test_pipe_downstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        Command("echo").args("hi").pipe(
            Command("sh")
            .args("-c", "cat >/dev/null; echo down_err >&2")
            .stderr(err_lines.append)
        )
        assert any("down_err" in line for line in err_lines)

    def test_pipe_honors_env_on_both_sides(self) -> None:
        buf = StringIO()
        up_env = EnvCmd.inherit().add({"PIPE_UP_VAR": "up_value"}).build()
        down_env = EnvCmd.inherit().add({"PIPE_DOWN_VAR": "down_value"}).build()
        Command("sh").args("-c", "echo $PIPE_UP_VAR").env(up_env).pipe(
            Command("sh")
            .args("-c", "cat; echo $PIPE_DOWN_VAR")
            .env(down_env)
            .stdout(buf)
        )
        assert "up_value" in buf.getvalue()
        assert "down_value" in buf.getvalue()

    def test_pipe_honors_upstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        Command("pwd").cwd(tmp_path).pipe(Command("cat").stdout(buf))
        assert str(tmp_path) in buf.getvalue()

    def test_pipe_notifies_observers_on_both_sides(self) -> None:
        up_observer = _RecordingObserver()
        down_observer = _RecordingObserver()
        Command("echo").args("hi").add_observer(up_observer).pipe(
            Command("cat").add_observer(down_observer)
        )
        assert len(up_observer.spawned) == 1
        assert len(up_observer.exited) == 1
        assert len(down_observer.spawned) == 1
        assert len(down_observer.exited) == 1

    def test_pipe_downstream_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "pipe_out.txt"
        Command("echo").args("piped to file").pipe(Command("cat").stdout(out_file))
        assert "piped to file" in out_file.read_text()

    def test_pipe_downstream_stdout_to_binary_file_object(self, tmp_path: Path) -> None:
        out_file = tmp_path / "pipe_binary_out.txt"
        with out_file.open("wb") as fh:
            Command("echo").args("binary pipe output").pipe(Command("cat").stdout(fh))
        assert "binary pipe output" in out_file.read_text()

    def test_pipe_upstream_stderr_to_binary_file_object(self, tmp_path: Path) -> None:
        err_file = tmp_path / "pipe_binary_err.txt"
        with err_file.open("wb") as fh:
            Command("sh").args("-c", "echo out; echo up_err >&2").stderr(fh).pipe(
                Command("cat")
            )
        assert "up_err" in err_file.read_text()

    def test_pipe_ignores_downstream_stdin(self) -> None:
        # other's own .stdin() is ignored: its stdin always becomes the upstream pipe.
        buf = StringIO()
        Command("echo").args("via pipe stdin").pipe(
            Command("cat").stdin("ignored stdin content\n").stdout(buf)
        )
        assert "via pipe stdin" in buf.getvalue()
        assert "ignored stdin content" not in buf.getvalue()


class TestCommandPipeline:
    def test_pipeline_requires_at_least_one_downstream_command(self) -> None:
        with pytest.raises(ValueError):
            Command("echo").pipeline()

    def test_pipeline_success_captures_final_stdout(self) -> None:
        buf = StringIO()
        downstream = (
            Command("echo")
            .args("hello world")
            .pipeline(Command("cat"), Command("cat"), Command("cat").stdout(buf))
        )
        assert downstream.returncode == 0
        assert "hello world" in buf.getvalue()

    def test_pipeline_transforms_through_every_stage(self) -> None:
        buf = StringIO()
        Command("echo").args("abc").pipeline(
            Command("tr").args("a-z", "A-Z"),
            Command("rev").stdout(buf),
        )
        assert buf.getvalue().strip() == "CBA"

    def test_pipeline_equivalent_to_pipe_for_two_stages(self) -> None:
        buf_pipe = StringIO()
        buf_pipeline = StringIO()
        Command("echo").args("same result").pipe(Command("cat").stdout(buf_pipe))
        Command("echo").args("same result").pipeline(
            Command("cat").stdout(buf_pipeline)
        )
        assert buf_pipe.getvalue() == buf_pipeline.getvalue()

    def test_pipeline_ignores_intermediate_stdout_redirect(self) -> None:
        ignored = StringIO()
        buf = StringIO()
        Command("echo").args("via pipeline").pipeline(
            Command("cat").stdout(ignored),
            Command("cat").stdout(buf),
        )
        assert ignored.getvalue() == ""
        assert "via pipeline" in buf.getvalue()

    def test_pipeline_ignores_intermediate_stdin(self) -> None:
        buf = StringIO()
        Command("echo").args("via pipeline stdin").pipeline(
            Command("cat").stdin("ignored stdin content\n"),
            Command("cat").stdout(buf),
        )
        assert "via pipeline stdin" in buf.getvalue()
        assert "ignored stdin content" not in buf.getvalue()

    def test_pipeline_honors_first_stage_stdin(self) -> None:
        buf = StringIO()
        q: Queue[bytes | str | None] = Queue()
        q.put("line1\n")
        q.put(None)
        Command("cat").stdin(q).pipeline(Command("cat"), Command("cat").stdout(buf))
        assert "line1" in buf.getvalue()

    def test_pipeline_forwards_stderr_from_every_stage(self) -> None:
        err_lines: list[str] = []
        Command("sh").args("-c", "echo out; echo first_err >&2").stderr(
            err_lines.append
        ).pipeline(
            Command("sh")
            .args("-c", "cat >/dev/null; echo middle_err >&2")
            .stderr(err_lines.append),
            Command("sh")
            .args("-c", "cat >/dev/null; echo last_err >&2")
            .stderr(err_lines.append),
        )
        joined = "".join(err_lines)
        assert "first_err" in joined
        assert "middle_err" in joined
        assert "last_err" in joined

    def test_pipeline_first_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("__no_such_binary_exists__").pipeline(Command("cat"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipeline_middle_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("echo").args("hi").pipeline(
                Command("__no_such_binary_exists__"), Command("cat")
            )
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipeline_last_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            Command("echo").args("hi").pipeline(
                Command("cat"), Command("__no_such_binary_exists__")
            )
        assert exc_info.value.command == "__no_such_binary_exists__"

    def test_pipeline_middle_stage_error_return_code(self) -> None:
        # The failing stage must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream stage before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            Command("echo").args("hi").pipeline(
                Command("sh").args("-c", "cat >/dev/null; exit 1"), Command("cat")
            )
        assert exc_info.value.exit_code == 1

    def test_pipeline_last_stage_error_return_code(self) -> None:
        # Same reasoning as above: the failing last stage must drain stdin first.
        with pytest.raises(ErrorReturnCode) as exc_info:
            Command("echo").args("hi").pipeline(
                Command("cat"), Command("sh").args("-c", "cat >/dev/null; exit 1")
            )
        assert exc_info.value.exit_code == 1

    def test_pipeline_middle_stage_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            Command("echo").args("hi").pipeline(
                Command("sleep").args("10").timeout(1), Command("cat")
            )
        assert exc_info.value.timeout == 1

    def test_pipeline_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = (
            Command("head")
            .args("-c", "65536", "/dev/urandom")
            .pipeline(Command("cat"), Command("wc").args("-c").stdout(buf))
        )
        assert downstream.returncode == 0
        assert buf.getvalue().split() == ["65536"]

    def test_pipeline_no_deadlock_when_middle_stage_exits_early(self) -> None:
        result: dict[str, object] = {}

        def run() -> None:
            try:
                process = Command("yes").pipeline(
                    Command("head").args("-c", "100"), Command("wc").args("-c")
                )
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
        Command("sh").args("-c", "echo $PIPELINE_FIRST_VAR").env(first_env).pipeline(
            Command("cat"),
            Command("sh")
            .args("-c", "cat; echo $PIPELINE_LAST_VAR")
            .env(last_env)
            .stdout(buf),
        )
        assert "first_value" in buf.getvalue()
        assert "last_value" in buf.getvalue()

    def test_pipeline_honors_last_stage_cwd(self, tmp_path: Path) -> None:
        # The last stage must actually drain stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the middle stage before it finishes writing.
        buf = StringIO()
        Command("echo").args("x").pipeline(
            Command("cat"),
            Command("sh").args("-c", "cat >/dev/null; pwd").cwd(tmp_path).stdout(buf),
        )
        assert str(tmp_path) in buf.getvalue()

    def test_pipeline_notifies_observers_for_every_stage(self) -> None:
        observers = [_RecordingObserver() for _ in range(3)]
        Command("echo").args("hi").add_observer(observers[0]).pipeline(
            Command("cat").add_observer(observers[1]),
            Command("cat").add_observer(observers[2]),
        )
        for observer in observers:
            assert len(observer.spawned) == 1
            assert len(observer.exited) == 1


class TestCommandApipe:
    async def test_apipe_success_captures_downstream_stdout(self) -> None:
        buf = StringIO()
        downstream = (
            await Command("echo").args("hello world").apipe(Command("cat").stdout(buf))
        )
        returncode, output = downstream.returncode, buf.getvalue()
        assert returncode == 0
        assert "hello world" in output

    async def test_apipe_upstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        await (
            Command("sh")
            .args("-c", "echo out; echo err >&2")
            .stderr(err_lines.append)
            .apipe(Command("cat"))
        )
        assert any("err" in line for line in err_lines)

    async def test_apipe_upstream_error_return_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            await Command("false").apipe(Command("cat"))
        assert "false" in exc_info.value.cmd

    async def test_apipe_downstream_error_return_code(self) -> None:
        # The failing downstream must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            await (
                Command("echo")
                .args("hi")
                .apipe(Command("sh").args("-c", "cat >/dev/null; exit 1"))
            )
        assert exc_info.value.exit_code == 1

    async def test_apipe_upstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await Command("__no_such_binary_exists__").apipe(Command("cat"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipe_downstream_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await Command("echo").args("hi").apipe(Command("__no_such_binary_exists__"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipe_upstream_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            await Command("sleep").args("10").timeout(1).apipe(Command("cat"))
        assert exc_info.value.timeout == 1

    async def test_apipe_downstream_timeout_raises(self) -> None:
        # Upstream must finish on its own so we actually reach downstream's
        # own wait/timeout instead of blocking on upstream first.
        with pytest.raises(TimeoutException) as exc_info:
            await (
                Command("echo").args("hi").apipe(Command("sleep").args("10").timeout(1))
            )
        assert exc_info.value.timeout == 1

    async def test_apipe_ignores_upstream_stdout_redirect(self) -> None:
        # self's own .stdout() is ignored: its stdout always becomes the pipe.
        ignored = StringIO()
        buf = StringIO()
        await (
            Command("echo")
            .args("via pipe")
            .stdout(ignored)
            .apipe(Command("cat").stdout(buf))
        )
        ignored_value, buf_value = ignored.getvalue(), buf.getvalue()
        assert ignored_value == ""
        assert "via pipe" in buf_value

    async def test_apipe_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = await (
            Command("head")
            .args("-c", "65536", "/dev/urandom")
            .apipe(Command("wc").args("-c").stdout(buf))
        )
        returncode, output = downstream.returncode, buf.getvalue()
        assert returncode == 0
        assert output.split() == ["65536"]

    async def test_apipe_honors_downstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        await Command("echo").args("x").apipe(Command("pwd").cwd(tmp_path).stdout(buf))
        output = buf.getvalue()
        assert str(tmp_path) in output

    async def test_apipe_no_deadlock_when_downstream_exits_early(self) -> None:
        async def run() -> str:
            try:
                process = await Command("yes").apipe(Command("head").args("-c", "100"))
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
        await Command("cat").stdin(q).apipe(Command("cat").stdout(buf))
        output = buf.getvalue()
        assert "async_line1" in output
        assert "async_line2" in output

    async def test_apipe_downstream_stderr_is_forwarded(self) -> None:
        err_lines: list[str] = []
        await (
            Command("echo")
            .args("hi")
            .apipe(
                Command("sh")
                .args("-c", "cat >/dev/null; echo async_down_err >&2")
                .stderr(err_lines.append)
            )
        )
        assert any("async_down_err" in line for line in err_lines)

    async def test_apipe_honors_env_on_both_sides(self) -> None:
        buf = StringIO()
        up_env = EnvCmd.inherit().add({"APIPE_UP_VAR": "async_up_value"}).build()
        down_env = EnvCmd.inherit().add({"APIPE_DOWN_VAR": "async_down_value"}).build()
        await (
            Command("sh")
            .args("-c", "echo $APIPE_UP_VAR")
            .env(up_env)
            .apipe(
                Command("sh")
                .args("-c", "cat; echo $APIPE_DOWN_VAR")
                .env(down_env)
                .stdout(buf)
            )
        )
        output = buf.getvalue()
        assert "async_up_value" in output
        assert "async_down_value" in output

    async def test_apipe_honors_upstream_cwd(self, tmp_path: Path) -> None:
        buf = StringIO()
        await Command("pwd").cwd(tmp_path).apipe(Command("cat").stdout(buf))
        output = buf.getvalue()
        assert str(tmp_path) in output

    async def test_apipe_notifies_observers_on_both_sides(self) -> None:
        up_observer = _RecordingObserver()
        down_observer = _RecordingObserver()
        await (
            Command("echo")
            .args("hi")
            .add_observer(up_observer)
            .apipe(Command("cat").add_observer(down_observer))
        )
        assert len(up_observer.spawned) == 1
        assert len(up_observer.exited) == 1
        assert len(down_observer.spawned) == 1
        assert len(down_observer.exited) == 1

    async def test_apipe_downstream_stdout_to_file(self, tmp_path: Path) -> None:
        out_file = tmp_path / "async_pipe_out.txt"
        await (
            Command("echo")
            .args("async piped to file")
            .apipe(Command("cat").stdout(out_file))
        )
        assert "async piped to file" in out_file.read_text()

    async def test_apipe_downstream_stdout_to_binary_file_object(
        self, tmp_path: Path
    ) -> None:
        out_file = tmp_path / "async_pipe_binary_out.txt"
        with out_file.open("wb") as fh:
            await (
                Command("echo")
                .args("async binary pipe output")
                .apipe(Command("cat").stdout(fh))
            )
        assert "async binary pipe output" in out_file.read_text()

    async def test_apipe_upstream_stderr_to_binary_file_object(
        self, tmp_path: Path
    ) -> None:
        err_file = tmp_path / "async_pipe_binary_err.txt"
        with err_file.open("wb") as fh:
            await (
                Command("sh")
                .args("-c", "echo out; echo async_up_err >&2")
                .stderr(fh)
                .apipe(Command("cat"))
            )
        assert "async_up_err" in err_file.read_text()

    async def test_apipe_ignores_downstream_stdin(self) -> None:
        buf = StringIO()
        await (
            Command("echo")
            .args("via async pipe stdin")
            .apipe(Command("cat").stdin("ignored async stdin content\n").stdout(buf))
        )
        output = buf.getvalue()
        assert "via async pipe stdin" in output
        assert "ignored async stdin content" not in output


class TestCommandApipeline:
    async def test_apipeline_requires_at_least_one_downstream_command(self) -> None:
        with pytest.raises(ValueError):
            await Command("echo").apipeline()

    async def test_apipeline_success_captures_final_stdout(self) -> None:
        buf = StringIO()
        downstream = await (
            Command("echo")
            .args("hello world")
            .apipeline(Command("cat"), Command("cat"), Command("cat").stdout(buf))
        )
        assert downstream.returncode == 0
        assert "hello world" in buf.getvalue()

    async def test_apipeline_transforms_through_every_stage(self) -> None:
        buf = StringIO()
        await (
            Command("echo")
            .args("abc")
            .apipeline(
                Command("tr").args("a-z", "A-Z"),
                Command("rev").stdout(buf),
            )
        )
        assert buf.getvalue().strip() == "CBA"

    async def test_apipeline_equivalent_to_apipe_for_two_stages(self) -> None:
        buf_pipe = StringIO()
        buf_pipeline = StringIO()
        await Command("echo").args("same result").apipe(Command("cat").stdout(buf_pipe))
        await (
            Command("echo")
            .args("same result")
            .apipeline(Command("cat").stdout(buf_pipeline))
        )
        assert buf_pipe.getvalue() == buf_pipeline.getvalue()

    async def test_apipeline_ignores_intermediate_stdout_redirect(self) -> None:
        ignored = StringIO()
        buf = StringIO()
        await (
            Command("echo")
            .args("via apipeline")
            .apipeline(
                Command("cat").stdout(ignored),
                Command("cat").stdout(buf),
            )
        )
        assert ignored.getvalue() == ""
        assert "via apipeline" in buf.getvalue()

    async def test_apipeline_ignores_intermediate_stdin(self) -> None:
        buf = StringIO()
        await (
            Command("echo")
            .args("via apipeline stdin")
            .apipeline(
                Command("cat").stdin("ignored stdin content\n"),
                Command("cat").stdout(buf),
            )
        )
        assert "via apipeline stdin" in buf.getvalue()
        assert "ignored stdin content" not in buf.getvalue()

    async def test_apipeline_honors_first_stage_stdin(self) -> None:
        buf = StringIO()
        q: AsyncQueue[bytes | str | None] = AsyncQueue()
        await q.put("line1\n")
        await q.put(None)
        await (
            Command("cat")
            .stdin(q)
            .apipeline(Command("cat"), Command("cat").stdout(buf))
        )
        assert "line1" in buf.getvalue()

    async def test_apipeline_forwards_stderr_from_every_stage(self) -> None:
        err_lines: list[str] = []
        await (
            Command("sh")
            .args("-c", "echo out; echo first_err >&2")
            .stderr(err_lines.append)
            .apipeline(
                Command("sh")
                .args("-c", "cat >/dev/null; echo middle_err >&2")
                .stderr(err_lines.append),
                Command("sh")
                .args("-c", "cat >/dev/null; echo last_err >&2")
                .stderr(err_lines.append),
            )
        )
        joined = "".join(err_lines)
        assert "first_err" in joined
        assert "middle_err" in joined
        assert "last_err" in joined

    async def test_apipeline_first_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await Command("__no_such_binary_exists__").apipeline(Command("cat"))
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipeline_middle_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await (
                Command("echo")
                .args("hi")
                .apipeline(Command("__no_such_binary_exists__"), Command("cat"))
            )
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipeline_last_stage_not_found(self) -> None:
        with pytest.raises(CommandNotFound) as exc_info:
            await (
                Command("echo")
                .args("hi")
                .apipeline(Command("cat"), Command("__no_such_binary_exists__"))
            )
        assert exc_info.value.command == "__no_such_binary_exists__"

    async def test_apipeline_middle_stage_error_return_code(self) -> None:
        # The failing stage must drain its stdin before exiting, or an unlucky
        # scheduling can SIGPIPE the upstream stage before it finishes writing.
        with pytest.raises(ErrorReturnCode) as exc_info:
            await (
                Command("echo")
                .args("hi")
                .apipeline(
                    Command("sh").args("-c", "cat >/dev/null; exit 1"), Command("cat")
                )
            )
        assert exc_info.value.exit_code == 1

    async def test_apipeline_last_stage_error_return_code(self) -> None:
        # Same reasoning as above: the failing last stage must drain stdin first.
        with pytest.raises(ErrorReturnCode) as exc_info:
            await (
                Command("echo")
                .args("hi")
                .apipeline(
                    Command("cat"), Command("sh").args("-c", "cat >/dev/null; exit 1")
                )
            )
        assert exc_info.value.exit_code == 1

    async def test_apipeline_middle_stage_timeout_raises(self) -> None:
        with pytest.raises(TimeoutException) as exc_info:
            await (
                Command("echo")
                .args("hi")
                .apipeline(Command("sleep").args("10").timeout(1), Command("cat"))
            )
        assert exc_info.value.timeout == 1

    async def test_apipeline_preserves_binary_payload_byte_for_byte(self) -> None:
        buf = StringIO()
        downstream = await (
            Command("head")
            .args("-c", "65536", "/dev/urandom")
            .apipeline(Command("cat"), Command("wc").args("-c").stdout(buf))
        )
        assert downstream.returncode == 0
        assert buf.getvalue().split() == ["65536"]

    async def test_apipeline_no_deadlock_when_middle_stage_exits_early(self) -> None:
        try:
            process = await asyncio.wait_for(
                Command("yes").apipeline(
                    Command("head").args("-c", "100"), Command("wc").args("-c")
                ),
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
            Command("sh")
            .args("-c", "echo $PIPELINE_FIRST_VAR")
            .env(first_env)
            .apipeline(
                Command("cat"),
                Command("sh")
                .args("-c", "cat; echo $PIPELINE_LAST_VAR")
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
        await (
            Command("echo")
            .args("x")
            .apipeline(
                Command("cat"),
                Command("sh")
                .args("-c", "cat >/dev/null; pwd")
                .cwd(tmp_path)
                .stdout(buf),
            )
        )
        assert str(tmp_path) in buf.getvalue()

    async def test_apipeline_notifies_observers_for_every_stage(self) -> None:
        observers = [_RecordingObserver() for _ in range(3)]
        await (
            Command("echo")
            .args("hi")
            .add_observer(observers[0])
            .apipeline(
                Command("cat").add_observer(observers[1]),
                Command("cat").add_observer(observers[2]),
            )
        )
        for observer in observers:
            assert len(observer.spawned) == 1
            assert len(observer.exited) == 1


class TestBind:
    def _make_bind(self) -> Bind:
        return Bind("echo")

    def test_command_not_found(self) -> None:
        with pytest.raises(CommandNotFound):
            Bind("__no_such_binary_exists__")

    def test_binary_path(self) -> None:
        b = self._make_bind()
        assert b.binary_path().name == "echo"

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
        src_cmd = Command("echo")
        src_cmd.args("hi").env({"X": "1"})
        b = self._make_bind()
        returned = b.copy(src_cmd)
        assert returned is b

    def test_create_returns_command(self) -> None:
        b = self._make_bind()
        cmd = b.create("echo")
        assert isinstance(cmd, Command)

    def test_pipe_into_command(self) -> None:
        buf = StringIO()
        b = self._make_bind()
        b._cmd.args("via-bind")
        b.pipe(Command("cat").stdout(buf))
        assert "via-bind" in buf.getvalue()

    def test_pipe_into_bind(self) -> None:
        buf = StringIO()
        upstream = Bind("echo")
        upstream._cmd.args("piped")
        downstream = Bind("cat")
        downstream._cmd.stdout(buf)
        upstream.pipe(downstream)
        assert "piped" in buf.getvalue()

    async def test_apipe_into_command(self) -> None:
        buf = StringIO()
        b = self._make_bind()
        b._cmd.args("via-bind-async")
        await b.apipe(Command("cat").stdout(buf))
        assert "via-bind-async" in buf.getvalue()

    async def test_apipe_into_bind(self) -> None:
        buf = StringIO()
        upstream = Bind("echo")
        upstream._cmd.args("piped-async")
        downstream = Bind("cat")
        downstream._cmd.stdout(buf)
        await upstream.apipe(downstream)
        assert "piped-async" in buf.getvalue()

    def test_pipeline_into_command(self) -> None:
        buf = StringIO()
        b = self._make_bind()
        b._cmd.args("via-bind-pipeline")
        b.pipeline(Command("cat"), Command("cat").stdout(buf))
        assert "via-bind-pipeline" in buf.getvalue()

    def test_pipeline_into_bind(self) -> None:
        buf = StringIO()
        upstream = Bind("echo")
        upstream._cmd.args("piped-pipeline")
        middle = Bind("cat")
        downstream = Bind("cat")
        downstream._cmd.stdout(buf)
        upstream.pipeline(middle, downstream)
        assert "piped-pipeline" in buf.getvalue()

    async def test_apipeline_into_command(self) -> None:
        buf = StringIO()
        b = self._make_bind()
        b._cmd.args("via-bind-apipeline")
        await b.apipeline(Command("cat"), Command("cat").stdout(buf))
        assert "via-bind-apipeline" in buf.getvalue()

    async def test_apipeline_into_bind(self) -> None:
        buf = StringIO()
        upstream = Bind("echo")
        upstream._cmd.args("piped-apipeline")
        middle = Bind("cat")
        downstream = Bind("cat")
        downstream._cmd.stdout(buf)
        await upstream.apipeline(middle, downstream)
        assert "piped-apipeline" in buf.getvalue()


class TestOkExitCodes:
    def test_exec_default_rejects_non_zero(self) -> None:
        with pytest.raises(ErrorReturnCode):
            Command("sh").args("-c", "exit 1").exec()

    def test_exec_accepts_listed_code(self) -> None:
        Command("sh").args("-c", "exit 1").ok_exit_codes(0, 1).exec()

    def test_exec_rejects_unlisted_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            Command("sh").args("-c", "exit 2").ok_exit_codes(0, 1).exec()
        assert exc_info.value.exit_code == 2

    def test_copy_keeps_ok_exit_codes(self) -> None:
        source = Command("sh").args("-c", "exit 1").ok_exit_codes(0, 1)
        Command("sh").copy(source).exec()

    def test_pipe_accepted_upstream_code_still_feeds_downstream(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        Command("sh").args("-c", "echo data; exit 1").ok_exit_codes(0, 1).pipe(
            Command("sh").args("-c", f"cat >{out}")
        )
        assert out.read_text() == "data\n"

    def test_pipe_rejects_unlisted_upstream_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            Command("sh").args("-c", "exit 2").ok_exit_codes(0, 1).pipe(Command("cat"))
        assert exc_info.value.exit_code == 2

    def test_pipeline_accepted_middle_code(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        Command("echo").args("data").pipeline(
            Command("sh").args("-c", "cat; exit 1").ok_exit_codes(0, 1),
            Command("sh").args("-c", f"cat >{out}"),
        )
        assert out.read_text() == "data\n"

    async def test_aexec_accepts_listed_code(self) -> None:
        await Command("sh").args("-c", "exit 1").ok_exit_codes(0, 1).aexec()

    async def test_aexec_rejects_unlisted_code(self) -> None:
        with pytest.raises(ErrorReturnCode) as exc_info:
            await Command("sh").args("-c", "exit 2").ok_exit_codes(0, 1).aexec()
        assert exc_info.value.exit_code == 2

    async def test_apipe_accepted_upstream_code(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        await (
            Command("sh")
            .args("-c", "echo data; exit 1")
            .ok_exit_codes(0, 1)
            .apipe(Command("sh").args("-c", f"cat >{out}"))
        )
        assert out.read_text() == "data\n"

    async def test_apipeline_accepted_middle_code(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        await (
            Command("echo")
            .args("data")
            .apipeline(
                Command("sh").args("-c", "cat; exit 1").ok_exit_codes(0, 1),
                Command("sh").args("-c", f"cat >{out}"),
            )
        )
        assert out.read_text() == "data\n"


class TestNice:
    _SHOW_NICE = "import os; print(os.getpriority(os.PRIO_PROCESS, 0))"

    def test_getter_defaults_to_none(self) -> None:
        assert Command("echo").nice() is None

    def test_setter_returns_self(self) -> None:
        cmd = Command("echo")
        assert cmd.nice(5) is cmd
        assert cmd.nice() == 5

    def test_copy_carries_nice(self) -> None:
        assert Command("echo").copy(Command("echo").nice(7)).nice() == 7

    def test_bind_passthrough(self) -> None:
        bind = Bind("echo")
        assert bind.nice() is None
        assert bind.nice(3) is bind
        assert bind.nice() == 3

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
    def test_exec_applies_nice(self) -> None:
        buf = StringIO()
        base = os.getpriority(os.PRIO_PROCESS, 0)
        Command(sys.executable).args("-c", self._SHOW_NICE).nice(base + 5).stdout(
            buf
        ).exec()
        assert buf.getvalue().strip() == str(base + 5)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
    def test_exec_without_nice_keeps_priority(self) -> None:
        buf = StringIO()
        Command(sys.executable).args("-c", self._SHOW_NICE).stdout(buf).exec()
        assert buf.getvalue().strip() == str(os.getpriority(os.PRIO_PROCESS, 0))

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX niceness")
    async def test_aexec_applies_nice(self) -> None:
        buf = StringIO()
        base = os.getpriority(os.PRIO_PROCESS, 0)
        cmd = Command(sys.executable).args("-c", self._SHOW_NICE).nice(base + 5)
        await cmd.stdout(buf).aexec()
        assert buf.getvalue().strip() == str(base + 5)

    def test_no_creationflags_off_windows(self) -> None:
        assert _priority_creationflags(19) == 0
        assert _priority_creationflags(None) == 0

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
        assert _priority_creationflags(nice) == expected

    def test_no_preexec_hook_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert _low_priority_hook(5) is None


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
            getattr(Command("echo"), option)(value)
        with pytest.raises(ValueError, match=option):
            getattr(Bind("echo"), option)(value)
        assert getattr(Command("echo"), option)() is None
