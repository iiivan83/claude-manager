"""Tests for the Codex CLI backend adapter."""

import json
import os
import signal
from datetime import date, timedelta
from pathlib import Path

import pytest

from claude_manager.coding_agent_backend import (
    BackendBinaryNotFoundError,
    BackendName,
    BackendProtocolError,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    StopStrategy,
    TerminalStatus,
)
from claude_manager.codex_backend import (
    BACKEND_DISPLAY_NAME_CODEX,
    CLI_FLAG_BYPASS_APPROVALS,
    CLI_FLAG_JSON,
    CLI_FLAG_SKIP_GIT_CHECK,
    CODEX_STOP_STRATEGY,
    MAX_RECENT_SESSIONS,
    STOP_SIGINT_TIMEOUT_SECONDS,
    STOP_SIGTERM_TIMEOUT_SECONDS,
    CodexBackend,
)


@pytest.fixture()
def backend() -> CodexBackend:
    """Return a fresh stateless Codex backend adapter."""
    return CodexBackend()


def write_jsonl_file(file_path: Path, records: list[dict[str, object]]) -> None:
    """Write records to a UTF-8 JSONL file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file_handle:
        for session_record in records:
            file_handle.write(json.dumps(session_record, ensure_ascii=False) + "\n")


def make_rollout_file(
    sessions_root: Path,
    session_id: str,
    project_dir: str,
    days_ago: int = 0,
    user_text: str = "hello",
) -> Path:
    """Create one Codex rollout file under YYYY/MM/DD."""
    rollout_date = date.today() - timedelta(days=days_ago)
    file_path = (
        sessions_root
        / f"{rollout_date:%Y}"
        / f"{rollout_date:%m}"
        / f"{rollout_date:%d}"
        / f"rollout-{rollout_date:%Y-%m-%d}T01-02-03-{session_id}.jsonl"
    )
    write_jsonl_file(
        file_path,
        [
            {
                "timestamp": "2026-05-06T01:00:00Z",
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": project_dir},
            },
            {
                "timestamp": "2026-05-06T01:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_text}],
                },
            },
        ],
    )
    return file_path


def patch_codex_home(monkeypatch: pytest.MonkeyPatch, home_dir: Path) -> None:
    """Route Codex session discovery to a temporary home directory."""
    monkeypatch.setattr(os.path, "expanduser", lambda _path: str(home_dir))


def test_name_and_display_name(backend: CodexBackend) -> None:
    """The Codex adapter exposes the stable backend identity."""
    assert backend.name == BackendName.CODEX
    assert backend.display_name == BACKEND_DISPLAY_NAME_CODEX


def test_compose_args_for_new_session_uses_exec_flags_and_cwd(
    backend: CodexBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A temporary new-session id must not be passed to Codex resume."""
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: "/bin/codex",
    )

    command_args = backend.compose_subprocess_command_args(
        "_new_abc123def456",
        "/tmp/my project",
        "привет",
        [],
    )

    assert command_args == [
        "/bin/codex",
        "exec",
        CLI_FLAG_JSON,
        CLI_FLAG_BYPASS_APPROVALS,
        CLI_FLAG_SKIP_GIT_CHECK,
        "-C",
        "/tmp/my project",
        "привет",
    ]
    assert "resume" not in command_args


def test_compose_args_for_resume_session_uses_resume_without_cwd(
    backend: CodexBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real Codex session id is passed through `codex exec resume`."""
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: "/bin/codex",
    )
    session_id = "019dfaeb-7c5b-7ba1-9e56-a33b5e0b512a"

    command_args = backend.compose_subprocess_command_args(
        session_id,
        "/tmp/project",
        "still here?",
        [],
    )

    assert command_args == [
        "/bin/codex",
        "exec",
        "resume",
        session_id,
        CLI_FLAG_JSON,
        CLI_FLAG_BYPASS_APPROVALS,
        CLI_FLAG_SKIP_GIT_CHECK,
        "still here?",
    ]
    assert "-C" not in command_args


def test_compose_args_ignores_image_paths_in_current_version(
    backend: CodexBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex currently receives image paths only as text inside the prompt."""
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: "/bin/codex",
    )

    without_images = backend.compose_subprocess_command_args(
        "_new_abc123def456", "/tmp/project", "see /tmp/x.png", [],
    )
    with_images = backend.compose_subprocess_command_args(
        "_new_abc123def456", "/tmp/project", "see /tmp/x.png", ["/tmp/x.png"],
    )

    assert with_images == without_images
    assert "-i" not in with_images
    assert "--image" not in with_images


def test_encode_user_message_returns_empty_bytes(backend: CodexBackend) -> None:
    """Codex receives prompt text as an argv item, not stdin bytes."""
    assert backend.encode_user_message_for_cli_stdin("Привет", ["/tmp/x.png"]) == b""


def test_parse_stdout_line_and_extractors(backend: CodexBackend) -> None:
    """Codex stdout JSON events expose session id, text, errors, and status."""
    thread_event = backend.parse_stdout_line_into_event(
        '{"type":"thread.started","thread_id":"abc"}'
    )
    assistant_event = {
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "Готово"},
    }
    reasoning_event = {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "reasoning", "text": "Думаю"},
    }
    failed_event = {"type": "turn.failed", "error": {"message": "rate limit"}}
    completed_event = {"type": "turn.completed", "usage": {}}

    assert thread_event == {"type": "thread.started", "thread_id": "abc"}
    assert backend.parse_stdout_line_into_event(" \n") is None
    assert backend.read_session_id_from_event(thread_event) == "abc"
    assert backend.read_session_id_from_event(completed_event) is None
    assert backend.read_assistant_text_from_event(assistant_event) == "Готово"
    assert backend.read_assistant_text_from_event(reasoning_event) is None
    assert backend.read_progress_text_from_event(reasoning_event) == "Думаю"
    assert backend.read_progress_text_from_event(assistant_event) is None
    assert backend.is_turn_complete_event(completed_event) is True
    assert backend.is_turn_complete_event(failed_event) is True
    assert backend.is_error_event(failed_event) is True
    assert backend.read_error_text_from_event(failed_event) == "rate limit"
    assert backend.read_terminal_status_from_event(completed_event) == TerminalStatus.SUCCESS
    assert backend.read_terminal_status_from_event(failed_event) == TerminalStatus.FAILED
    assert backend.read_terminal_status_from_event(assistant_event) is None


def test_parse_stdout_line_raises_protocol_error_for_invalid_json(
    backend: CodexBackend,
) -> None:
    """Malformed Codex --json output is a protocol error with a short preview."""
    omitted_suffix = "TAIL_SHOULD_NOT_APPEAR"
    raw_line = ("x" * 200) + omitted_suffix

    with pytest.raises(BackendProtocolError) as error_info:
        backend.parse_stdout_line_into_event(raw_line)

    error_message = str(error_info.value)
    assert "Codex" in error_message
    assert raw_line[:200] in error_message
    assert omitted_suffix not in error_message


def test_locate_session_files_directory_returns_codex_root(
    backend: CodexBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex stores session files globally under ~/.codex/sessions."""
    patch_codex_home(monkeypatch, tmp_path)

    first = backend.locate_session_files_directory_for_project("/project/a")
    second = backend.locate_session_files_directory_for_project("/project/b")

    assert first == str(tmp_path / ".codex" / "sessions")
    assert second == first


def test_empty_response_markers_and_busy_types(backend: CodexBackend) -> None:
    """Codex has no synthetic empty-response marker and four busy record types."""
    assert backend.text_markers_indicating_empty_response() == frozenset()
    assert backend.event_types_meaning_cli_is_busy() == frozenset(
        {"event_msg", "response_item", "turn_context", "compacted"}
    )


def test_is_turn_terminal_session_record(backend: CodexBackend) -> None:
    """Codex terminal session records are event_msg task_complete or failures."""
    assert backend.is_turn_terminal_session_record(
        {"type": "event_msg", "payload": {"type": "task_complete"}}
    )
    assert backend.is_turn_terminal_session_record(
        {"type": "event_msg", "payload": {"type": "turn_aborted"}}
    )
    assert backend.is_turn_terminal_session_record(
        {"type": "event_msg", "payload": {"type": "error"}}
    )
    assert not backend.is_turn_terminal_session_record(
        {"type": "event_msg", "payload": {"type": "token_count"}}
    )
    assert not backend.is_turn_terminal_session_record(
        {"type": "response_item", "payload": {}}
    )


async def test_read_messages_from_session_file_extracts_response_items(
    backend: CodexBackend, tmp_path: Path,
) -> None:
    """Codex session history uses response_item records as canonical messages."""
    session_file = tmp_path / "session.jsonl"
    write_jsonl_file(
        session_file,
        [
            {
                "timestamp": "2026-05-06T01:35:14.505Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Привет"},
                        {"type": "input_image", "image_url": "data:image/png;base64,x"},
                    ],
                },
            },
            {"timestamp": "bad", "type": "event_msg", "payload": {"type": "agent_message"}},
            {
                "timestamp": "2026-05-06T01:35:16Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Готово"}],
                    "phase": "final_answer",
                },
            },
            {
                "timestamp": "2026-05-06T01:35:17Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "system"}],
                },
            },
            {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
        ],
    )

    messages = await backend.read_messages_from_session_file(str(session_file))

    assert messages == [
        SessionMessage(
            role="user",
            text="Привет",
            timestamp=1778031314.505,
            is_empty_response=False,
        ),
        SessionMessage(
            role="assistant",
            text="Готово",
            timestamp=1778031316.0,
            is_empty_response=False,
        ),
    ]


async def test_list_session_files_filters_by_cwd_and_limits_recent(
    backend: CodexBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex session listing filters rollout files by session_meta.payload.cwd."""
    patch_codex_home(monkeypatch, tmp_path)
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/tmp/project-a"
    other_project_dir = "/tmp/project-b"

    included_paths = []
    for index in range(20):
        session_id = f"019dfaeb-7c5b-7ba1-9e56-a33b5e0b5{index:03d}"
        file_path = make_rollout_file(
            sessions_root,
            session_id,
            project_dir,
            days_ago=index,
            user_text=f"question {index}",
        )
        os.utime(file_path, (1000 + index, 1000 + index))
        included_paths.append(file_path)
    make_rollout_file(
        sessions_root,
        "019dfaeb-7c5b-7ba1-9e56-a33b5e09999",
        other_project_dir,
        user_text="wrong project",
    )

    session_file_infos = await backend.list_session_files_for_project(project_dir)

    assert len(session_file_infos) == MAX_RECENT_SESSIONS
    assert session_file_infos[0] == SessionFileInfo(
        session_id="019dfaeb-7c5b-7ba1-9e56-a33b5e0b5019",
        file_path=str(included_paths[19]),
        last_modified_at=1019.0,
        preview="question 19",
    )
    assert all(info.preview != "wrong project" for info in session_file_infos)
    assert session_file_infos == sorted(
        session_file_infos,
        key=lambda info: info.last_modified_at,
        reverse=True,
    )


async def test_session_file_exists_for_project_scans_all_history(
    backend: CodexBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact ownership checks are not limited to the recent UI listing window."""
    patch_codex_home(monkeypatch, tmp_path)
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/tmp/project-a"
    old_session_id = "019dfaeb-7c5b-7ba1-9e56-a33b5e0abcde"
    wrong_project_session_id = "019dfaeb-7c5b-7ba1-9e56-a33b5e0abcdf"
    make_rollout_file(sessions_root, old_session_id, project_dir, days_ago=60)
    make_rollout_file(sessions_root, wrong_project_session_id, "/tmp/project-b")

    assert await backend.session_file_exists_for_project(old_session_id, project_dir)
    assert not await backend.session_file_exists_for_project(
        wrong_project_session_id, project_dir,
    )
    assert not await backend.session_file_exists_for_project("missing", project_dir)


async def test_list_all_session_files_ignores_recent_and_lookback_limits(
    backend: CodexBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operational scans return all matching Codex project sessions."""
    patch_codex_home(monkeypatch, tmp_path)
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/tmp/project-a"
    for index in range(20):
        make_rollout_file(
            sessions_root,
            f"019dfaeb-7c5b-7ba1-9e56-a33b5e0c{index:03d}",
            project_dir,
            days_ago=index,
        )
    make_rollout_file(
        sessions_root,
        "019dfaeb-7c5b-7ba1-9e56-a33b5e0c999",
        project_dir,
        days_ago=60,
    )

    all_infos = await backend.list_all_session_files_for_project(project_dir)
    recent_infos = await backend.list_session_files_for_project(project_dir)

    assert len(all_infos) == 21
    assert len(recent_infos) == MAX_RECENT_SESSIONS


async def test_read_session_file_snapshot_counts_records_and_activity(
    backend: CodexBackend, tmp_path: Path,
) -> None:
    """Codex snapshots expose raw JSONL count, last record, and active state."""
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {}}),
                json.dumps(
                    {
                        "timestamp": "2026-05-06T01:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "вопрос"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                "{invalid json",
                json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
                json.dumps(
                    {
                        "timestamp": "2026-05-06T01:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ответ"}],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    snapshot = await backend.read_session_file_snapshot(str(session_file))

    assert isinstance(snapshot, SessionFileSnapshot)
    assert snapshot.raw_record_count == 5
    assert snapshot.last_record is not None
    assert snapshot.last_record["type"] == "response_item"
    assert snapshot.is_turn_active is True
    assert snapshot.messages == await backend.read_messages_from_session_file(
        str(session_file)
    )


async def test_read_session_file_snapshot_marks_terminal_records_inactive(
    backend: CodexBackend, tmp_path: Path,
) -> None:
    """Task-complete, turn-aborted, and error event_msg records end a Codex turn."""
    for terminal_subtype in ("task_complete", "turn_aborted", "error"):
        session_file = tmp_path / f"{terminal_subtype}.jsonl"
        write_jsonl_file(
            session_file,
            [
                {"type": "event_msg", "payload": {"type": "task_started"}},
                {"type": "event_msg", "payload": {"type": terminal_subtype}},
            ],
        )

        snapshot = await backend.read_session_file_snapshot(str(session_file))

        assert snapshot.is_turn_active is False
        assert snapshot.last_record == {
            "type": "event_msg",
            "payload": {"type": terminal_subtype},
        }


async def test_read_session_file_snapshot_keeps_token_count_active(
    backend: CodexBackend, tmp_path: Path,
) -> None:
    """Codex token_count is telemetry, not a turn-complete marker."""
    session_file = tmp_path / "token_count.jsonl"
    write_jsonl_file(
        session_file,
        [
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant"}},
            {"type": "event_msg", "payload": {"type": "token_count"}},
        ],
    )

    snapshot = await backend.read_session_file_snapshot(str(session_file))

    assert snapshot.is_turn_active is True


async def test_read_session_file_snapshot_returns_empty_when_missing(
    backend: CodexBackend, tmp_path: Path,
) -> None:
    """Missing Codex rollout files return an inactive empty snapshot."""
    snapshot = await backend.read_session_file_snapshot(str(tmp_path / "missing.jsonl"))

    assert snapshot == SessionFileSnapshot(
        messages=[],
        raw_record_count=0,
        last_record=None,
        is_turn_active=False,
    )


def test_compose_args_raises_when_binary_not_found(
    backend: CodexBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex binary lookup is lazy and reports a backend-specific error."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(os.path, "exists", lambda _path: False)

    with pytest.raises(BackendBinaryNotFoundError) as error_info:
        backend.compose_subprocess_command_args("_new_abc123def456", "/tmp", "x", [])

    assert "Codex CLI" in str(error_info.value)


def test_get_stop_strategy_returns_sigint_sigterm_sigkill(
    backend: CodexBackend,
) -> None:
    """Codex uses SIGINT first so the CLI can write a TurnInterrupt marker."""
    stop_strategy = backend.get_stop_strategy()

    assert isinstance(stop_strategy, StopStrategy)
    assert stop_strategy is CODEX_STOP_STRATEGY
    assert [step.signal_to_send for step in stop_strategy.steps] == [
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGKILL,
    ]
    assert stop_strategy.steps[0].wait_seconds_before_next == STOP_SIGINT_TIMEOUT_SECONDS
    assert stop_strategy.steps[1].wait_seconds_before_next == STOP_SIGTERM_TIMEOUT_SECONDS
    assert stop_strategy.steps[2].wait_seconds_before_next == 0.0
