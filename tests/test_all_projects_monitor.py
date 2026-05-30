"""Tests for global all-project monitoring."""

import asyncio
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

    def __init__(
        self,
        name: BackendName,
        files_by_project: dict[str, list[SessionFileInfo]],
    ) -> None:
        self.name = name
        self.display_name = name.value
        self.files_by_project = files_by_project
        self.snapshots: dict[str, SessionFileSnapshot] = {}
        self.individual_project_dir_calls: list[str] = []
        self.bulk_project_dir_calls: list[list[str]] = []
        self.read_count_by_path: dict[str, int] = {}
        self.cursor_count_by_path: dict[str, int] = {}
        self.active_reads = 0
        self.max_active_reads = 0
        self.read_delay_seconds = 0.0

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        del lookback_days
        self.individual_project_dir_calls.append(project_dir)
        return self.files_by_project.get(project_dir, [])

    async def list_all_session_files_for_projects(
        self,
        project_dirs: list[str],
    ) -> dict[str, list[SessionFileInfo]]:
        self.bulk_project_dir_calls.append(list(project_dirs))
        return {
            project_dir: self.files_by_project.get(project_dir, [])
            for project_dir in project_dirs
        }

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        self.read_count_by_path[file_path] = (
            self.read_count_by_path.get(file_path, 0) + 1
        )
        self.active_reads += 1
        self.max_active_reads = max(self.max_active_reads, self.active_reads)
        if self.read_delay_seconds:
            await asyncio.sleep(self.read_delay_seconds)
        try:
            return self.snapshots[file_path]
        finally:
            self.active_reads -= 1

    async def read_session_file_cursor(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        self.cursor_count_by_path[file_path] = (
            self.cursor_count_by_path.get(file_path, 0) + 1
        )
        self.active_reads += 1
        self.max_active_reads = max(self.max_active_reads, self.active_reads)
        if self.read_delay_seconds:
            await asyncio.sleep(self.read_delay_seconds)
        try:
            return self.snapshots[file_path]
        finally:
            self.active_reads -= 1


class FailingBackend:
    """Backend double that fails while listing project session files."""

    name = BackendName.CODEX
    display_name = "Codex"

    async def list_all_session_files_for_project(
        self,
        _project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        del lookback_days
        raise OSError("backend unavailable")


def _project(
    name: str,
    path: str,
    is_current: bool = False,
) -> project_manager.ProjectInfo:
    """Build a project info test value."""
    return project_manager.ProjectInfo(
        name=name,
        absolute_path=path,
        is_current=is_current,
    )


def _file(
    session_id: str,
    file_path: str,
    mtime: float = 1.0,
) -> SessionFileInfo:
    """Build session file metadata."""
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
    """Build a backend-neutral session snapshot."""
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_count if raw_count is not None else len(messages),
        last_record={},
        is_turn_active=is_turn_active,
    )


def _message(role: str, text: str) -> SessionMessage:
    """Build a session message."""
    return SessionMessage(
        role=role,
        text=text,
        timestamp=None,
        is_empty_response=False,
    )


def _raw_message(role: str, text: str, raw_record_index: int) -> SessionMessage:
    """Build a session message tied to a source JSONL record."""
    return SessionMessage(
        role=role,
        text=text,
        timestamp=None,
        is_empty_response=False,
        raw_record_index=raw_record_index,
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
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/alpha": []})
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock:
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is True


@pytest.mark.asyncio()
async def test_collect_project_sessions_uses_bulk_backend_listing_once() -> None:
    """All mode asks each backend for all project files in one bulk listing."""
    alpha_file = _file("sess-alpha", "/sessions/alpha.jsonl", mtime=20.0)
    beta_file = _file("sess-beta", "/sessions/beta.jsonl", mtime=10.0)
    backend = FakeBackend(
        BackendName.CLAUDE,
        {
            "/projects/alpha": [alpha_file],
            "/projects/beta": [beta_file],
        },
    )
    backend.snapshots[alpha_file.file_path] = _snapshot([])
    backend.snapshots[beta_file.file_path] = _snapshot([])
    projects = [
        _project("alpha", "/projects/alpha"),
        _project("beta", "/projects/beta"),
    ]

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ):
        project_sessions = await all_projects_monitor._collect_project_sessions()

    assert backend.bulk_project_dir_calls == [[
        "/projects/alpha",
        "/projects/beta",
    ]]
    assert backend.individual_project_dir_calls == []
    assert [session.file_info.session_id for session in project_sessions] == [
        "sess-alpha",
        "sess-beta",
    ]


@pytest.mark.asyncio()
async def test_enable_for_chat_reads_baseline_snapshots_concurrently() -> None:
    """All mode baselines session files concurrently instead of one by one."""
    files = [
        _file("sess-alpha", "/sessions/alpha.jsonl", mtime=20.0),
        _file("sess-beta", "/sessions/beta.jsonl", mtime=10.0),
        _file("sess-gamma", "/sessions/gamma.jsonl", mtime=5.0),
    ]
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/alpha": files})
    backend.read_delay_seconds = 0.01
    for file_info in files:
        backend.snapshots[file_info.file_path] = _snapshot([
            _message("user", "task"),
        ])
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    assert backend.max_active_reads > 1


@pytest.mark.asyncio()
async def test_enable_for_chat_uses_lightweight_cursors_for_baseline() -> None:
    """All mode baselines files through lightweight cursors, not full snapshots."""
    file_info = _file("sess-alpha", "/sessions/alpha.jsonl", mtime=20.0)
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/alpha": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    assert backend.cursor_count_by_path[file_info.file_path] == 1
    assert backend.read_count_by_path.get(file_info.file_path, 0) == 0


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
async def test_poll_skips_snapshot_read_when_session_mtime_is_unchanged() -> None:
    """Polling all mode does not reread unchanged session files."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl", mtime=20.0)
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    callback = AsyncMock()
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ):
        await all_projects_monitor.poll_once(callback)

    assert backend.cursor_count_by_path[file_info.file_path] == 1
    assert backend.read_count_by_path.get(file_info.file_path, 0) == 0
    callback.assert_not_called()


def test_candidate_indices_use_raw_record_cursor_when_available() -> None:
    """All mode only delivers messages appended after the baseline raw cursor."""
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=3,
        last_delivered_idx=-1,
    )
    snapshot = _snapshot([
        _raw_message("assistant", "old answer", raw_record_index=2),
        _raw_message("assistant", "new answer", raw_record_index=4),
    ])

    assert all_projects_monitor._candidate_indices(previous, snapshot) == [1]


def test_candidate_indices_do_not_fall_back_when_raw_cursor_has_no_new_messages() -> None:
    """A service-only append must not redeliver old assistant messages."""
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=3,
        last_delivered_idx=-1,
    )
    snapshot = _snapshot([
        _raw_message("assistant", "old answer", raw_record_index=2),
    ])

    assert all_projects_monitor._candidate_indices(previous, snapshot) == []


@pytest.mark.asyncio()
async def test_poll_delivers_all_project_message_and_keeps_unread_snapshot() -> None:
    """All monitor delivers a new assistant message and leaves it unread for project switch."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl", mtime=20.0)
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
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
    backend.files_by_project["/projects/beta"] = [
        _file("sess-beta", "/sessions/beta.jsonl", mtime=30.0)
    ]
    callback = AsyncMock()

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
    call = callback.call_args.args
    assert call[0] == CHAT_ID
    assert call[1] == 1
    assert call[2] == 1
    assert call[3] == "beta"
    assert call[4] == "/projects/beta"
    assert call[5] == "sess-beta"
    assert call[6] == BackendName.CLAUDE
    assert call[7] == "answer"

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
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "old"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    assert unread_buffer.restore_snapshot(
        "sess-beta",
        BackendName.CLAUDE,
    ) == SessionUnreadState(
        raw_record_count=3,
        last_delivered_idx=2,
    )


@pytest.mark.asyncio()
async def test_resolve_link_returns_project_and_session_target() -> None:
    """The displayed /<project>s<session> command resolves back to the exact session."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl")
    backend = FakeBackend(BackendName.CLAUDE, {"/projects/beta": [file_info]})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    target = all_projects_monitor.resolve_link(
        project_number=1,
        session_number=1,
    )
    assert target == all_projects_monitor.AllProjectSessionLink(
        project_number=1,
        session_number=1,
        project_name="beta",
        project_path="/projects/beta",
        session_id="sess-beta",
        backend=BackendName.CLAUDE,
    )


@pytest.mark.asyncio()
async def test_poll_continues_when_one_backend_scan_fails() -> None:
    """A failing backend does not prevent other backends from delivering messages."""
    file_info = _file("sess-alpha", "/sessions/alpha.jsonl")
    working_backend = FakeBackend(BackendName.CLAUDE, {"/projects/alpha": [file_info]})
    working_backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("alpha", "/projects/alpha")]

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[FailingBackend(), working_backend],
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        await all_projects_monitor.enable_for_chat(CHAT_ID)

    working_backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "answer"),
    ])
    working_backend.files_by_project["/projects/alpha"] = [
        _file("sess-alpha", "/sessions/alpha.jsonl", mtime=2.0)
    ]
    callback = AsyncMock()

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        return_value=[FailingBackend(), working_backend],
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
