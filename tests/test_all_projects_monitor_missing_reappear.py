"""Regression tests for disappeared all-mode candidate files."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import all_projects_monitor, config, project_manager, unread_buffer
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)


CHAT_ID = 12345


class FakeBackend:
    """Backend double for one all-mode candidate."""

    name = BackendName.CLAUDE
    display_name = "claude"

    def __init__(self) -> None:
        self.snapshots: dict[str, SessionFileSnapshot] = {}

    async def read_session_file_cursor(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        return self.snapshots[file_path]

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        return self.snapshots[file_path]


def _recent_row(file_info: SessionFileInfo) -> MagicMock:
    row = MagicMock()
    row.project_path = "/projects/alpha"
    row.backend = BackendName.CLAUDE
    row.session_id = file_info.session_id
    row.file_path = file_info.file_path
    row.last_modified_at = file_info.last_modified_at
    row.preview = file_info.preview
    return row


def _snapshot(messages: list[SessionMessage]) -> SessionFileSnapshot:
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=len(messages),
        last_record={},
        is_turn_active=False,
    )


def _message(role: str, text: str) -> SessionMessage:
    return SessionMessage(
        role=role,
        text=text,
        timestamp=None,
        is_empty_response=False,
    )


def _candidate_with_mtime(
    project_session: all_projects_monitor._ProjectSession,
    mtime: float,
) -> all_projects_monitor._ProjectSession:
    return all_projects_monitor._ProjectSession(
        project_number=project_session.project_number,
        project_name=project_session.project_name,
        project_path=project_session.project_path,
        session_number=project_session.session_number,
        file_info=SessionFileInfo(
            session_id=project_session.file_info.session_id,
            file_path=project_session.file_info.file_path,
            last_modified_at=mtime,
            preview=project_session.file_info.preview,
        ),
        backend=project_session.backend,
    )


@pytest.fixture(autouse=True)
def _reset_monitor_state():
    all_projects_monitor.reset_state()
    unread_buffer._snapshots.clear()
    yield
    all_projects_monitor.reset_state()
    unread_buffer._snapshots.clear()


@pytest.mark.asyncio()
async def test_missing_candidate_is_not_polled_after_reappearing_without_reenable() -> None:
    file_info = SessionFileInfo(
        session_id="sess-alpha",
        file_path="/sessions/alpha.jsonl",
        last_modified_at=1.0,
        preview="preview",
    )
    backend = FakeBackend()
    backend.snapshots[file_info.file_path] = _snapshot([_message("user", "task")])
    recent_refresh = MagicMock()
    recent_refresh.ALL_MODE_SESSION_CANDIDATE_LIMIT = 80
    recent_refresh.get_global_recent_sessions = AsyncMock(
        return_value=MagicMock(rows=[_recent_row(file_info)], degraded_messages=[])
    )

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(
            return_value=[
                project_manager.ProjectInfo(
                    name="alpha",
                    absolute_path="/projects/alpha",
                    is_current=True,
                )
            ]
        ),
    ), patch.object(
        all_projects_monitor,
        "recent_sessions_refresh",
        recent_refresh,
        create=True,
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_backend",
        return_value=backend,
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        enable_result = await all_projects_monitor.enable_for_chat(CHAT_ID)

    assert enable_result.enabled is True
    callback = AsyncMock()
    with patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(return_value=None),
    ):
        await all_projects_monitor.poll_once(callback)

    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "must not be delivered"),
    ])
    with patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(
            side_effect=lambda project_session: _candidate_with_mtime(
                project_session,
                2.0,
            )
        ),
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_not_called()
