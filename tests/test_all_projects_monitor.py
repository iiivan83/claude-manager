"""Tests for global all-project monitoring."""

from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import all_projects_monitor, config, project_manager, unread_buffer
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    SessionUnreadState,
)


CHAT_ID = 12345


class FakeBackend:
    """Small backend double for all-project monitor tests."""

    def __init__(self, files_by_project: dict[str, list[SessionFileInfo]]) -> None:
        self.name = BackendName.CLAUDE
        self.display_name = "Claude"
        self.files_by_project = files_by_project
        self.snapshots: dict[str, SessionFileSnapshot] = {}

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        return self.files_by_project.get(project_dir, [])

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        return self.snapshots[file_path]


def _project(name: str, path: str, is_current: bool = False) -> project_manager.ProjectInfo:
    return project_manager.ProjectInfo(
        name=name,
        absolute_path=path,
        is_current=is_current,
    )


def _file(session_id: str, file_path: str, mtime: float = 1.0) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=file_path,
        last_modified_at=mtime,
        preview="preview",
    )


def _snapshot(
    messages: list[SessionMessage],
    raw_count: int | None = None,
    is_turn_active: bool = False,
) -> SessionFileSnapshot:
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_count if raw_count is not None else len(messages),
        last_record={},
        is_turn_active=is_turn_active,
    )


def _message(role: str, text: str) -> SessionMessage:
    return SessionMessage(
        role=role,
        text=text,
        timestamp=None,
        is_empty_response=False,
    )


@pytest.fixture(autouse=True)
def _reset_monitor_state():
    """Keep module globals isolated between tests."""
    all_projects_monitor.reset_state()
    unread_buffer._snapshots.clear()
    yield
    all_projects_monitor.reset_state()
    unread_buffer._snapshots.clear()


@pytest.mark.asyncio()
async def test_enable_for_chat_pauses_current_watcher_and_marks_mode() -> None:
    """Enabling all mode pauses normal watcher so it cannot mark messages as read."""
    backend = FakeBackend({"/projects/alpha": []})
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch("claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends", return_value=[backend]), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock:
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is True


@pytest.mark.asyncio()
async def test_enable_for_chat_resumes_watcher_when_baseline_fails() -> None:
    """A failed all-mode entry must not leave the normal watcher globally paused."""
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=RuntimeError("scan failed")),
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock, patch(
        "claude_manager.all_projects_monitor.session_watcher.resume_all",
    ) as resume_all_mock:
        with pytest.raises(RuntimeError, match="scan failed"):
            await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    resume_all_mock.assert_called_once()
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is False


@pytest.mark.asyncio()
async def test_poll_delivers_all_project_message_and_keeps_unread_snapshot() -> None:
    """All monitor delivers a new assistant message and leaves it unread for project switch."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl", mtime=20.0)
    backend = FakeBackend({"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch("claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends", return_value=[backend]), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "answer"),
    ])
    callback = AsyncMock()

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch("claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends", return_value=[backend]):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
    call = callback.call_args.args
    assert call[0] == CHAT_ID
    assert call[1] == 1
    assert call[2] == 1
    assert call[3] == "beta"
    assert call[4] == "sess-beta"
    assert call[6] == "answer"

    unread_state = unread_buffer.restore_snapshot("sess-beta", BackendName.CLAUDE)
    assert unread_state == SessionUnreadState(
        raw_record_count=1,
        last_delivered_idx=0,
    )


@pytest.mark.asyncio()
async def test_existing_unread_snapshot_is_not_overwritten() -> None:
    """All mode must not replace older unread cursors captured by project switching."""
    unread_buffer.save_snapshot(
        "sess-beta",
        BackendName.CLAUDE,
        raw_record_count=3,
        last_delivered_idx=2,
    )
    file_info = _file("sess-beta", "/sessions/beta.jsonl")
    backend = FakeBackend({"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "old"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch("claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends", return_value=[backend]), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    assert unread_buffer.restore_snapshot("sess-beta", BackendName.CLAUDE) == SessionUnreadState(
        raw_record_count=3,
        last_delivered_idx=2,
    )


@pytest.mark.asyncio()
async def test_resolve_link_returns_project_and_session_target() -> None:
    """The displayed /<project>s<session> command resolves back to the exact session."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl")
    backend = FakeBackend({"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch("claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends", return_value=[backend]), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    target = all_projects_monitor.resolve_link(project_number=1, session_number=1)
    assert target == all_projects_monitor.AllProjectSessionLink(
        project_number=1,
        session_number=1,
        project_name="beta",
        project_path="/projects/beta",
        session_id="sess-beta",
        backend=BackendName.CLAUDE,
    )
