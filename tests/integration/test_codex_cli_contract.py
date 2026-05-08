"""Integration tests for the real Codex CLI contract."""

import asyncio
import base64
import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from claude_manager.codex_backend import (
    STOP_SIGINT_TIMEOUT_SECONDS,
    CodexBackend,
)

CODEX_CLI_TIMEOUT_SECONDS = 180
CODEX_OPTIONAL_IMAGE_TIMEOUT_SECONDS = 45
EXPECTED_CODEX_VERSION = "0.128.0"
MINIMAL_PROMPT = "say hi in one word"


def _find_codex_binary() -> Path | None:
    """Find the real Codex binary used by contract tests."""
    env_path = os.environ.get("CODEX_REAL_BIN")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    path_from_env = shutil.which("codex")
    return Path(path_from_env) if path_from_env else None


def _read_codex_version(binary_path: Path | None) -> str | None:
    """Return `codex --version` output when the binary is runnable."""
    if binary_path is None:
        return None
    completed = subprocess.run(
        [str(binary_path), "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


CODEX_BINARY = _find_codex_binary()
CODEX_VERSION = _read_codex_version(CODEX_BINARY)
CODEX_CONTRACT_SKIP_REASON = None
if CODEX_BINARY is None:
    CODEX_CONTRACT_SKIP_REASON = "Реальный Codex CLI не найден — контрактные тесты пропускаются"
elif CODEX_VERSION is None or EXPECTED_CODEX_VERSION not in CODEX_VERSION:
    CODEX_CONTRACT_SKIP_REASON = (
        "Нужен Codex CLI 0.128.0 для контрактных тестов; "
        f"найдена версия: {CODEX_VERSION!r}"
    )

pytestmark = pytest.mark.skipif(
    CODEX_CONTRACT_SKIP_REASON is not None,
    reason=CODEX_CONTRACT_SKIP_REASON or "",
)


@dataclass(frozen=True)
class CodexRun:
    """Captured result of one real Codex CLI run."""

    completed: subprocess.CompletedProcess[bytes]
    events: list[dict[str, object]]
    thread_id: str
    session_file: Path


def _child_env() -> dict[str, str]:
    """Return a subprocess environment for a real Codex run."""
    child_env = os.environ.copy()
    if CODEX_BINARY is not None:
        child_env["CODEX_REAL_BIN"] = str(CODEX_BINARY)
    return child_env


def _parse_stdout_events(
    backend: CodexBackend,
    stdout_bytes: bytes,
) -> list[dict[str, object]]:
    """Parse Codex --json stdout into event dictionaries."""
    events: list[dict[str, object]] = []
    for raw_line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        parsed_event = backend.parse_stdout_line_into_event(raw_line)
        if parsed_event is not None:
            events.append(parsed_event)
    return events


def _wait_for_rollout_file(
    backend: CodexBackend,
    project_dir: Path,
    thread_id: str,
    timeout_seconds: float = 10.0,
) -> Path:
    """Find the rollout file created by a real Codex run."""
    sessions_root = Path(
        backend.locate_session_files_directory_for_project(str(project_dir))
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        matches = list(sessions_root.glob(f"**/rollout-*{thread_id}.jsonl"))
        if matches:
            return max(matches, key=lambda path: path.stat().st_mtime)
        time.sleep(0.2)
    pytest.fail(f"Codex rollout file for thread {thread_id} was not created")


def _read_jsonl_records(file_path: Path) -> list[dict[str, object]]:
    """Read valid JSONL records from one rollout file."""
    records: list[dict[str, object]] = []
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        parsed_value = json.loads(raw_line)
        if isinstance(parsed_value, dict):
            records.append(parsed_value)
    return records


def _remove_rollout_file(file_path: Path | None) -> None:
    """Remove only the rollout file created by this test."""
    if file_path is None:
        return
    file_path.unlink(missing_ok=True)


def _run_codex_turn(
    backend: CodexBackend,
    project_dir: Path,
    prompt_text: str,
    session_id: str = "_new_contract123",
    timeout_seconds: int = CODEX_CLI_TIMEOUT_SECONDS,
) -> CodexRun:
    """Run one real Codex turn through backend-generated argv."""
    command = backend.compose_subprocess_command_args(
        session_id,
        str(project_dir),
        prompt_text,
        [],
    )
    completed = subprocess.run(
        command,
        cwd=str(project_dir),
        input=backend.encode_user_message_for_cli_stdin(prompt_text, []),
        capture_output=True,
        timeout=timeout_seconds,
        env=_child_env(),
        check=False,
    )
    events = _parse_stdout_events(backend, completed.stdout)
    thread_id = ""
    for event in events:
        thread_id = backend.read_session_id_from_event(event) or thread_id
    assert thread_id, (
        "Codex CLI did not emit thread.started.\n"
        f"  stdout: {completed.stdout[:1000]!r}\n"
        f"  stderr: {completed.stderr[:1000]!r}"
    )
    session_file = _wait_for_rollout_file(backend, project_dir, thread_id)
    return CodexRun(
        completed=completed,
        events=events,
        thread_id=thread_id,
        session_file=session_file,
    )


async def test_codex_exec_json_and_session_file_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real `codex exec --json` creates stdout events and a rollout JSONL file."""
    assert CODEX_BINARY is not None
    backend = CodexBackend()
    project_dir = tmp_path / "codex_exec_contract"
    project_dir.mkdir()
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: str(CODEX_BINARY),
    )
    codex_run: CodexRun | None = None

    try:
        codex_run = _run_codex_turn(backend, project_dir, MINIMAL_PROMPT)

        assert codex_run.completed.returncode == 0, (
            "Codex CLI contract run failed.\n"
            f"  stdout: {codex_run.completed.stdout[:1000]!r}\n"
            f"  stderr: {codex_run.completed.stderr[:1000]!r}"
        )
        assert codex_run.events[0].get("type") == "thread.started"
        assert any(backend.is_turn_complete_event(event) for event in codex_run.events)
        assistant_texts = [
            text
            for event in codex_run.events
            if (text := backend.read_assistant_text_from_event(event))
        ]
        assert assistant_texts

        records = _read_jsonl_records(codex_run.session_file)
        assert records[0]["type"] == "session_meta"
        assert records[0]["payload"]["cwd"] == str(project_dir)

        snapshot = await backend.read_session_file_snapshot(str(codex_run.session_file))
        assert snapshot.raw_record_count >= 5
        assert snapshot.messages
        assert snapshot.last_record is not None
        assert backend.is_turn_terminal_session_record(snapshot.last_record)
        assert snapshot.is_turn_active is False
    finally:
        _remove_rollout_file(codex_run.session_file if codex_run else None)


def test_codex_resume_command_args_accept_real_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real `codex exec resume <thread_id>` accepts backend-generated argv."""
    assert CODEX_BINARY is not None
    backend = CodexBackend()
    project_dir = tmp_path / "codex_resume_contract"
    project_dir.mkdir()
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: str(CODEX_BINARY),
    )
    first_run: CodexRun | None = None
    resume_run: CodexRun | None = None

    try:
        first_run = _run_codex_turn(backend, project_dir, MINIMAL_PROMPT)
        resume_run = _run_codex_turn(
            backend,
            project_dir,
            "say ok in one word",
            session_id=first_run.thread_id,
        )

        assert resume_run.completed.returncode != 2, (
            "`codex exec resume` rejected the generated argv.\n"
            f"  stderr: {resume_run.completed.stderr[:1000]!r}"
        )
        assert any(backend.is_turn_complete_event(event) for event in resume_run.events)
    finally:
        _remove_rollout_file(first_run.session_file if first_run else None)
        _remove_rollout_file(resume_run.session_file if resume_run else None)


def test_codex_view_image_path_in_prompt_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prompt-text image path can make Codex call the built-in view_image tool."""
    assert CODEX_BINARY is not None
    backend = CodexBackend()
    project_dir = tmp_path / "codex_image_contract"
    project_dir.mkdir()
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: str(CODEX_BINARY),
    )
    image_path = project_dir / "test_red.png"
    image_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
    )
    codex_run: CodexRun | None = None

    try:
        prompt_text = (
            f"Use view_image to inspect {image_path} and answer with the dominant "
            "color only."
        )
        try:
            codex_run = _run_codex_turn(
                backend,
                project_dir,
                prompt_text,
                timeout_seconds=CODEX_OPTIONAL_IMAGE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            pytest.skip(
                "Codex model did not finish the optional view_image prompt quickly"
            )
        records = _read_jsonl_records(codex_run.session_file)
        saw_view_image = any(
            record.get("type") == "event_msg"
            and isinstance(record.get("payload"), dict)
            and record["payload"].get("type") == "view_image_tool_call"
            for record in records
        )
        if not saw_view_image:
            pytest.skip(
                "Codex model did not call view_image for a prompt-text path in this run"
            )
    finally:
        _remove_rollout_file(codex_run.session_file if codex_run else None)


async def test_codex_sigint_records_terminal_marker_in_session_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT lets Codex write a terminal interruption marker to the rollout file."""
    assert CODEX_BINARY is not None
    backend = CodexBackend()
    project_dir = tmp_path / "codex_sigint_contract"
    project_dir.mkdir()
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: str(CODEX_BINARY),
    )
    big_file = project_dir / "big.txt"
    big_file.write_text(("0123456789 abcdefghij\n" * 100000), encoding="utf-8")
    prompt_text = (
        "Run this exact shell command first: sleep 30. After it finishes, read "
        "big.txt and write a long summary."
    )
    command = backend.compose_subprocess_command_args(
        "_new_sigint123",
        str(project_dir),
        prompt_text,
        [],
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(project_dir),
        stdin=subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_env(),
        start_new_session=True,
    )
    thread_id = ""
    session_file: Path | None = None

    try:
        assert process.stdout is not None
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            try:
                raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
            except asyncio.TimeoutError:
                break
            if not raw_line:
                break
            event = backend.parse_stdout_line_into_event(
                raw_line.decode("utf-8", errors="replace")
            )
            if event is None:
                continue
            thread_id = backend.read_session_id_from_event(event) or thread_id
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "command_execution":
                break
            if thread_id and event.get("type") == "turn.started":
                break

        if not thread_id:
            stderr_text = ""
            if process.stderr is not None:
                stderr_bytes = await process.stderr.read(1000)
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            pytest.skip(
                "Codex CLI did not emit thread.started before SIGINT; "
                f"stderr={stderr_text[:500]!r}"
            )
        if process.returncode is not None:
            pytest.skip("Codex turn completed before SIGINT could be sent")

        os.killpg(process.pid, signal.SIGINT)
        returncode = await asyncio.wait_for(
            process.wait(),
            timeout=STOP_SIGINT_TIMEOUT_SECONDS + 20,
        )
        assert returncode not in {137, -signal.SIGKILL}

        session_file = _wait_for_rollout_file(backend, project_dir, thread_id)
        snapshot = await backend.read_session_file_snapshot(str(session_file))
        assert snapshot.last_record is not None
        assert backend.is_turn_terminal_session_record(snapshot.last_record)
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        _remove_rollout_file(session_file)
