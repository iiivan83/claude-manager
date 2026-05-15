"""Tests for the Claude Code CLI backend adapter."""

import json
import os
import signal
from pathlib import Path

import pytest

from claude_manager.coding_agent_backend import (
    BackendName,
    BackendProtocolError,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    StopStrategy,
    TerminalStatus,
    get_backend,
)
from claude_manager.claude_code_backend import (
    BACKEND_DISPLAY_NAME_CLAUDE,
    CLAUDE_CODE_STOP_STRATEGY,
    EMPTY_RESPONSE_MARKER,
    TERMINATE_TIMEOUT_SECONDS,
    ClaudeCodeBackend,
    _encode_project_path,
)


@pytest.fixture()
def backend() -> ClaudeCodeBackend:
    """Return a fresh stateless Claude backend adapter."""
    return ClaudeCodeBackend()


def write_jsonl_file(file_path: Path, records: list[dict[str, object]]) -> None:
    """Write records to a UTF-8 JSONL file."""
    with file_path.open("w", encoding="utf-8") as file_handle:
        for session_record in records:
            file_handle.write(json.dumps(session_record, ensure_ascii=False) + "\n")


def test_name_and_display_name(backend: ClaudeCodeBackend) -> None:
    """The Claude adapter exposes the stable backend identity."""
    assert backend.name == BackendName.CLAUDE
    assert backend.display_name == BACKEND_DISPLAY_NAME_CLAUDE


def test_compose_args_for_new_session_has_no_resume(
    backend: ClaudeCodeBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A temporary new-session id must not be passed to Claude as --resume."""
    monkeypatch.setattr(
        "claude_manager.claude_code_backend._resolve_claude_binary_path",
        lambda: "/bin/claude",
    )

    command_args = backend.compose_subprocess_command_args(
        "_new_abc123def456",
        "/tmp/project",
        "hello",
        [],
    )

    assert command_args[0] == "/bin/claude"
    assert "-p" in command_args
    assert command_args[command_args.index("--output-format") + 1] == "stream-json"
    assert command_args[command_args.index("--input-format") + 1] == "stream-json"
    assert "--dangerously-skip-permissions" in command_args
    assert "--resume" not in command_args


def test_compose_args_for_resume_session_appends_resume(
    backend: ClaudeCodeBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real session id is appended through --resume."""
    monkeypatch.setattr(
        "claude_manager.claude_code_backend._resolve_claude_binary_path",
        lambda: "/bin/claude",
    )
    session_id = "84748107-a3de-4314-8c72-4c3b1b6e3605"

    command_args = backend.compose_subprocess_command_args(
        session_id,
        "/tmp/project",
        "hello",
        [],
    )

    assert command_args[-2:] == ["--resume", session_id]


def test_compose_args_ignore_prompt_text_and_image_paths(
    backend: ClaudeCodeBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude receives prompt text through stdin, not command arguments."""
    monkeypatch.setattr(
        "claude_manager.claude_code_backend._resolve_claude_binary_path",
        lambda: "/bin/claude",
    )

    first_command_args = backend.compose_subprocess_command_args(
        "_new_abc123def456",
        "/tmp/project",
        "first prompt",
        ["/tmp/one.png"],
    )
    second_command_args = backend.compose_subprocess_command_args(
        "_new_abc123def456",
        "/tmp/project",
        "second prompt",
        ["/tmp/two.png"],
    )

    assert first_command_args == second_command_args


def test_encode_user_message_for_cli_stdin_uses_stream_json(
    backend: ClaudeCodeBackend,
) -> None:
    """Claude stdin uses the stream-json user message shape."""
    encoded_message = backend.encode_user_message_for_cli_stdin("привет", [])

    assert encoded_message.endswith(b"\n")
    assert "привет".encode("utf-8") in encoded_message
    assert b"\\u043f" not in encoded_message

    parsed_message = json.loads(encoded_message.decode("utf-8"))
    assert parsed_message == {
        "type": "user",
        "message": {"role": "user", "content": "привет"},
    }
    assert parsed_message["type"] != "user_message"


def test_parse_stdout_line_into_event(backend: ClaudeCodeBackend) -> None:
    """Valid JSON stdout lines become backend events."""
    event = backend.parse_stdout_line_into_event(
        '{"type":"system","session_id":"abc-123"}'
    )

    assert event == {"type": "system", "session_id": "abc-123"}
    assert backend.parse_stdout_line_into_event("  \n") is None


def test_parse_stdout_line_raises_protocol_error_for_invalid_json(
    backend: ClaudeCodeBackend,
) -> None:
    """Malformed stream-json output is a protocol error with a short preview."""
    omitted_suffix = "TAIL_SHOULD_NOT_APPEAR"
    raw_line = ("x" * 200) + omitted_suffix

    with pytest.raises(BackendProtocolError) as error_info:
        backend.parse_stdout_line_into_event(raw_line)

    error_message = str(error_info.value)
    assert raw_line[:200] in error_message
    assert omitted_suffix not in error_message


def test_stdout_event_extractors(backend: ClaudeCodeBackend) -> None:
    """The adapter reads terminal status, session id, text, and errors."""
    result_event = {
        "type": "result",
        "session_id": "session-1",
        "is_error": False,
        "result": "Готово",
    }
    error_event = {
        "type": "result",
        "is_error": True,
        "result": "max turns exceeded",
    }

    assert backend.is_turn_complete_event(result_event) is True
    assert backend.read_session_id_from_event(result_event) == "session-1"
    assert backend.read_assistant_text_from_event(result_event) == "Готово"
    assert backend.read_assistant_text_from_event({"type": "assistant"}) is None
    assert backend.read_assistant_text_from_event(
        {"type": "result", "result": EMPTY_RESPONSE_MARKER}
    ) == ""
    assert backend.is_error_event(error_event) is True
    assert backend.read_error_text_from_event(error_event) == "max turns exceeded"
    assert backend.read_terminal_status_from_event(result_event) == TerminalStatus.SUCCESS
    assert backend.read_terminal_status_from_event(error_event) == TerminalStatus.FAILED
    assert backend.read_terminal_status_from_event({"type": "assistant"}) is None


def test_read_progress_text_prefers_text_over_thinking(
    backend: ClaudeCodeBackend,
) -> None:
    """Progress text keeps current process_manager semantics: text before thinking."""
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "думаю"},
                {"type": "text", "text": "читаю файл"},
            ]
        },
    }

    assert backend.read_progress_text_from_event(event) == "читаю файл"
    assert backend.read_progress_text_from_event({"type": "result"}) is None


def test_locate_session_files_directory_encodes_project_path(
    backend: ClaudeCodeBackend,
) -> None:
    """Claude session directory names use the existing sanitized path contract."""
    result = backend.locate_session_files_directory_for_project(
        "/Users/ivan/My Project_2",
    )

    assert result.startswith(os.path.expanduser("~"))
    assert result.endswith(".claude/projects/-Users-ivan-My-Project-2")


async def test_list_session_files_for_project_returns_recent_metadata(
    backend: ClaudeCodeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recent session metadata is sorted by file mtime and includes previews."""
    monkeypatch.setattr(os.path, "expanduser", lambda _path: str(tmp_path))
    project_dir = "/tmp/project_a"
    sessions_dir = (
        tmp_path / ".claude" / "projects" / _encode_project_path(project_dir)
    )
    sessions_dir.mkdir(parents=True)

    older_file = sessions_dir / "older.jsonl"
    newer_file = sessions_dir / "newer.jsonl"
    write_jsonl_file(
        older_file,
        [{"type": "user", "message": {"content": "old preview"}}],
    )
    write_jsonl_file(
        newer_file,
        [{"type": "user", "message": {"content": "new preview"}}],
    )
    os.utime(older_file, (1, 1))
    os.utime(newer_file, (2, 2))

    session_file_infos = await backend.list_session_files_for_project(project_dir)

    assert session_file_infos == [
        SessionFileInfo(
            session_id="newer",
            file_path=str(newer_file),
            last_modified_at=2.0,
            preview="new preview",
        ),
        SessionFileInfo(
            session_id="older",
            file_path=str(older_file),
            last_modified_at=1.0,
            preview="old preview",
        ),
    ]


async def test_list_session_files_uses_file_caption_as_preview(
    backend: ClaudeCodeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude session previews show the original file caption, not bot boilerplate."""
    monkeypatch.setattr(os.path, "expanduser", lambda _path: str(tmp_path))
    project_dir = "/tmp/project_a"
    sessions_dir = (
        tmp_path / ".claude" / "projects" / _encode_project_path(project_dir)
    )
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "file-task.jsonl"
    write_jsonl_file(
        session_file,
        [
            {
                "sessionId": "file-task",
                "timestamp": "2026-05-13T01:00:00Z",
                "type": "user",
                "message": {
                    "content": (
                        "Пользователь отправил файл с подписью: "
                        "Добавь понятное превью сессии. "
                        "Файл: /tmp/screenshot.jpg. "
                        "Прочитай файл инструментом Read и выполни задачу из подписи"
                    )
                },
            },
        ],
    )

    session_file_infos = await backend.list_session_files_for_project(project_dir)

    assert session_file_infos[0].preview == "Добавь понятное превью сессии"


async def test_read_messages_from_session_file_parses_user_and_assistant(
    backend: ClaudeCodeBackend,
    tmp_path: Path,
) -> None:
    """Claude JSONL records are converted to backend-neutral messages."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-05-07T01:02:03Z",
                        "message": {"content": "hello"},
                    }
                ),
                "{invalid json",
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": 42,
                        "message": {
                            "content": [{"type": "text", "text": EMPTY_RESPONSE_MARKER}]
                        },
                    }
                ),
                json.dumps({"type": "result", "result": "ignored"}),
            ]
        ),
        encoding="utf-8",
    )

    messages = await backend.read_messages_from_session_file(str(session_file))

    assert messages == [
        SessionMessage(
            role="user",
            text="hello",
            timestamp=1778115723.0,
            is_empty_response=False,
        ),
        SessionMessage(
            role="assistant",
            text=EMPTY_RESPONSE_MARKER,
            timestamp=42.0,
            is_empty_response=True,
        ),
    ]


async def test_read_session_file_snapshot_counts_records_and_activity(
    backend: ClaudeCodeBackend,
    tmp_path: Path,
) -> None:
    """Session snapshots count raw records and expose the last parsed record."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "hello"}}),
                "{invalid json",
                json.dumps({"type": "assistant", "message": {"content": "working"}}),
                json.dumps({"type": "result", "result": "done"}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = await backend.read_session_file_snapshot(str(session_file))

    assert isinstance(snapshot, SessionFileSnapshot)
    assert snapshot.raw_record_count == 4
    assert snapshot.last_record == {"type": "result", "result": "done"}
    assert snapshot.is_turn_active is False
    assert snapshot.messages == await backend.read_messages_from_session_file(
        str(session_file)
    )


async def test_read_session_file_snapshot_marks_assistant_last_as_active(
    backend: ClaudeCodeBackend,
    tmp_path: Path,
) -> None:
    """An assistant session-file record means the Claude turn may still be active."""
    session_file = tmp_path / "active.jsonl"
    write_jsonl_file(session_file, [{"type": "assistant", "message": {"content": ""}}])

    snapshot = await backend.read_session_file_snapshot(str(session_file))

    assert snapshot.is_turn_active is True


async def test_session_file_exists_for_project(
    backend: ClaudeCodeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact session-file ownership checks do not use the recent-session limit."""
    monkeypatch.setattr(os.path, "expanduser", lambda _path: str(tmp_path))
    project_dir = "/tmp/project_a"
    sessions_dir = (
        tmp_path / ".claude" / "projects" / _encode_project_path(project_dir)
    )
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "session-1.jsonl").write_text("", encoding="utf-8")

    assert await backend.session_file_exists_for_project("session-1", project_dir)
    assert not await backend.session_file_exists_for_project("missing", project_dir)


def test_get_stop_strategy_returns_sigterm_then_sigkill(
    backend: ClaudeCodeBackend,
) -> None:
    """Claude uses SIGTERM first and SIGKILL as the final fallback."""
    stop_strategy = backend.get_stop_strategy()

    assert isinstance(stop_strategy, StopStrategy)
    assert stop_strategy is CLAUDE_CODE_STOP_STRATEGY
    assert stop_strategy.steps[0].signal_to_send == signal.SIGTERM
    assert stop_strategy.steps[0].wait_seconds_before_next == TERMINATE_TIMEOUT_SECONDS
    assert stop_strategy.steps[1].signal_to_send == signal.SIGKILL
    assert stop_strategy.steps[1].wait_seconds_before_next == 0.0
    assert signal.SIGINT not in {step.signal_to_send for step in stop_strategy.steps}


def test_get_backend_returns_claude_singleton() -> None:
    """The common factory lazily returns the Claude adapter singleton."""
    first_backend = get_backend(BackendName.CLAUDE)
    second_backend = get_backend("claude")

    assert isinstance(first_backend, ClaudeCodeBackend)
    assert first_backend is second_backend
