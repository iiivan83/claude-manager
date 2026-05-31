"""Regression tests for fast session watcher reset baselines."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import session_watcher
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    StopStrategy,
    TerminalStatus,
    UnifiedEvent,
)

PROJECT_DIR = "/fake/project"


class CursorOnlyBackend(CodingAgentBackend):
    """Backend double that fails if reset_state reads full snapshots."""

    def __init__(self) -> None:
        self.files = [
            SessionFileInfo(
                session_id="session-1",
                file_path="/tmp/session-1.jsonl",
                last_modified_at=10.0,
                preview="preview",
            )
        ]
        self.cursor_calls: list[str] = []
        self.snapshot_calls: list[str] = []
        self.next_snapshot: SessionFileSnapshot | None = None

    @property
    def name(self) -> BackendName:
        return BackendName.CODEX

    @property
    def display_name(self) -> str:
        return "Codex"

    def compose_subprocess_command_args(
        self,
        session_id: str,
        cwd: str,
        prompt_text: str,
        image_paths: list[str],
    ) -> list[str]:
        return []

    def encode_user_message_for_cli_stdin(
        self,
        prompt_text: str,
        image_paths: list[str],
    ) -> bytes:
        return b""

    def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None:
        return None

    def is_turn_complete_event(self, event: UnifiedEvent) -> bool:
        return False

    def read_session_id_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def locate_session_files_directory_for_project(self, project_dir: str) -> str:
        return project_dir

    async def list_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        return await self.list_all_session_files_for_project(project_dir)

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        del project_dir, lookback_days
        return self.files

    async def session_file_exists_for_project(
        self,
        session_id: str,
        project_dir: str,
    ) -> bool:
        del project_dir
        return any(info.session_id == session_id for info in self.files)

    async def read_messages_from_session_file(
        self,
        file_path: str,
    ) -> list[SessionMessage]:
        del file_path
        return []

    def text_markers_indicating_empty_response(self) -> frozenset[str]:
        return frozenset()

    def event_types_meaning_cli_is_busy(self) -> frozenset[str]:
        return frozenset()

    def is_turn_terminal_session_record(self, record: dict[str, object]) -> bool:
        del record
        return False

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        self.snapshot_calls.append(file_path)
        if self.next_snapshot is not None:
            return self.next_snapshot
        raise AssertionError("reset_state should not read full snapshots")

    async def read_session_file_cursor(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        self.cursor_calls.append(file_path)
        return SessionFileSnapshot(
            messages=[],
            raw_record_count=7,
            last_record=None,
            is_turn_active=True,
        )

    def is_error_event(self, event: UnifiedEvent) -> bool:
        return False

    def read_error_text_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def read_terminal_status_from_event(
        self,
        event: UnifiedEvent,
    ) -> TerminalStatus | None:
        return None

    def get_stop_strategy(self) -> StopStrategy:
        return StopStrategy(steps=())


def _message(text: str, raw_record_index: int) -> SessionMessage:
    return SessionMessage(
        role="assistant",
        text=text,
        timestamp=None,
        is_empty_response=False,
        raw_record_index=raw_record_index,
    )


@pytest.mark.asyncio()
async def test_reset_state_uses_lightweight_cursors_for_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project switching should not parse historical messages for watcher baseline."""
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    backend = CursorOnlyBackend()
    watcher = session_watcher.SessionWatcher(backend)

    with patch.object(
        session_watcher.daily_session_registry,
        "get_all_today_sessions",
        new=AsyncMock(return_value={}),
    ):
        await watcher.reset_state()

    assert backend.cursor_calls == ["/tmp/session-1.jsonl"]
    assert backend.snapshot_calls == []
    state = watcher._states["session-1"]
    assert state.raw_count == 7
    assert state.last_delivered_idx == -1
    assert state.cli_process_is_currently_writing_session_file is True


@pytest.mark.asyncio()
async def test_poll_after_cursor_baseline_delivers_only_new_raw_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor baselines must not redeliver old messages on the next poll."""
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    monkeypatch.setattr(session_watcher.config, "ALLOWED_USER_IDS", [123])
    backend = CursorOnlyBackend()
    watcher = session_watcher.SessionWatcher(backend)
    callback = AsyncMock()

    with patch.object(
        session_watcher.daily_session_registry,
        "get_all_today_sessions",
        new=AsyncMock(return_value={}),
    ):
        await watcher.reset_state()

    backend.files[0] = SessionFileInfo(
        session_id="session-1",
        file_path="/tmp/session-1.jsonl",
        last_modified_at=11.0,
        preview="preview",
    )
    backend.next_snapshot = SessionFileSnapshot(
        messages=[
            _message("old answer", raw_record_index=2),
            _message("new answer", raw_record_index=9),
        ],
        raw_record_count=9,
        last_record=None,
        is_turn_active=False,
    )

    with patch.object(
        session_watcher.daily_session_registry,
        "get_all_today_sessions",
        new=AsyncMock(return_value={}),
    ), patch.object(
        session_watcher.daily_session_registry,
        "register_session",
        new=AsyncMock(return_value=1),
    ):
        await watcher.poll_once(callback, AsyncMock(return_value=None))

    callback.assert_awaited_once_with(
        123,
        "session-1",
        BackendName.CODEX,
        1,
        "new answer",
        False,
        True,
    )
