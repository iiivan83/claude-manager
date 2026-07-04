"""Tests for global all-project monitoring."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import all_projects_monitor, config, project_manager, unread_buffer
from claude_manager.coding_agent_backend import (
    CURSOR_ONLY_PARSED_MESSAGE_COUNT,
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


def _recent_row(
    project_path: str,
    backend: BackendName,
    session_id: str,
    file_path: str,
    mtime: float = 1.0,
    preview: str = "preview",
) -> MagicMock:
    """Build a persisted recent-session row test value."""
    row = MagicMock()
    row.project_path = project_path
    row.backend = backend
    row.session_id = session_id
    row.file_path = file_path
    row.last_modified_at = mtime
    row.preview = preview
    return row


def _recent_result(
    rows: list[MagicMock],
    degraded_messages: list[str] | None = None,
) -> MagicMock:
    """Build a recent-session query result."""
    return MagicMock(rows=rows, degraded_messages=degraded_messages or [])


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


def _candidate_with_mtime(
    project_session: all_projects_monitor._ProjectSession,
    mtime: float,
) -> all_projects_monitor._ProjectSession:
    """Return the same candidate with a refreshed file mtime."""
    file_info = project_session.file_info
    return all_projects_monitor._ProjectSession(
        project_number=project_session.project_number,
        project_name=project_session.project_name,
        project_path=project_session.project_path,
        session_number=project_session.session_number,
        file_info=SessionFileInfo(
            session_id=file_info.session_id,
            file_path=file_info.file_path,
            last_modified_at=mtime,
            preview=file_info.preview,
        ),
        backend=project_session.backend,
    )


async def _enable_with_recent(
    rows: list[MagicMock],
    backend: FakeBackend,
    projects: list[project_manager.ProjectInfo],
    degraded_messages: list[str] | None = None,
    chat_id: int = CHAT_ID,
) -> tuple[object, AsyncMock, MagicMock]:
    """Enable all mode with recent-session rows patched in."""
    recent_refresh = MagicMock()
    recent_refresh.ALL_MODE_SESSION_CANDIDATE_LIMIT = 80
    recent_refresh.get_global_recent_sessions = AsyncMock(
        return_value=_recent_result(rows, degraded_messages)
    )
    get_all_backends = MagicMock(return_value=[backend])

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch.object(
        all_projects_monitor,
        "recent_sessions_refresh",
        recent_refresh,
        create=True,
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_backend",
        return_value=backend,
    ), patch(
        "claude_manager.all_projects_monitor.coding_agent_backend.get_all_backends",
        get_all_backends,
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.get_seen_counts_snapshot",
        return_value={},
    ):
        enable_result = await all_projects_monitor.enable_for_chat(chat_id)

    return (
        enable_result,
        recent_refresh.get_global_recent_sessions,
        get_all_backends,
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
async def test_enable_for_chat_uses_recent_sessions_global_query() -> None:
    """All mode builds candidates from recent rows, not backend bulk listing."""
    row = _recent_row(
        "/projects/beta",
        BackendName.CODEX,
        "sess-beta",
        "/sessions/beta.jsonl",
        mtime=20.0,
    )
    backend = FakeBackend(BackendName.CODEX, {})
    backend.snapshots[row.file_path] = _snapshot([_message("user", "task")])
    projects = [_project("beta", "/projects/beta")]

    enable_result, recent_query, get_all_backends = await _enable_with_recent(
        [row], backend, projects,
    )

    assert enable_result.enabled is True
    assert "all" in enable_result.message.lower()
    assert "проект" in enable_result.message.lower()
    recent_query.assert_awaited_once_with(
        ["/projects/beta"],
        limit=80,
        refresh_on_hit=True,
    )
    get_all_backends.assert_not_called()
    assert backend.bulk_project_dir_calls == []
    assert all_projects_monitor.resolve_link(1, 1).session_id == "sess-beta"
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is True


@pytest.mark.asyncio()
async def test_enable_for_chat_returns_disabled_when_recent_index_is_empty() -> None:
    """Empty recent index resumes normal watcher and does not enable all mode."""
    backend = FakeBackend(BackendName.CLAUDE, {})
    projects = [_project("alpha", "/projects/alpha")]
    recent_refresh = MagicMock()
    recent_refresh.ALL_MODE_SESSION_CANDIDATE_LIMIT = 80
    recent_refresh.get_global_recent_sessions = AsyncMock(
        return_value=_recent_result([], ["codex index unavailable"])
    )

    with patch.object(config, "WORKING_DIR", "/projects/alpha"), patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=projects),
    ), patch.object(
        all_projects_monitor,
        "recent_sessions_refresh",
        recent_refresh,
        create=True,
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock, patch(
        "claude_manager.all_projects_monitor.session_watcher.resume_all",
    ) as resume_all_mock:
        enable_result = await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    resume_all_mock.assert_called_once()
    assert enable_result.enabled is False
    assert "сесс" in enable_result.message.lower()
    assert "codex index unavailable" in enable_result.message
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is False


@pytest.mark.asyncio()
async def test_poll_uses_enabled_candidate_snapshot_without_refresh() -> None:
    """Poll loop must not rebuild candidates or refresh recent sessions."""
    row = _recent_row(
        "/projects/beta",
        BackendName.CLAUDE,
        "sess-beta",
        "/sessions/beta.jsonl",
        mtime=20.0,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.list_all_session_files_for_project = AsyncMock(
        side_effect=AssertionError("poll backend discovery is forbidden")
    )
    backend.list_all_session_files_for_projects = AsyncMock(
        side_effect=AssertionError("poll backend bulk discovery is forbidden")
    )
    backend.snapshots[row.file_path] = _snapshot([_message("user", "task")])
    projects = [_project("beta", "/projects/beta")]
    await _enable_with_recent([row], backend, projects)

    callback = AsyncMock()
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=AssertionError("poll discovery is forbidden")),
    ), patch.object(
        all_projects_monitor,
        "recent_sessions_refresh",
        MagicMock(
            get_global_recent_sessions=AsyncMock(
                side_effect=AssertionError("poll refresh is forbidden")
            )
        ),
        create=True,
    ), patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(side_effect=lambda project_session: project_session),
        create=True,
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_not_called()
    backend.list_all_session_files_for_project.assert_not_awaited()
    backend.list_all_session_files_for_projects.assert_not_awaited()


@pytest.mark.asyncio()
async def test_enable_for_chat_reads_baseline_snapshots_concurrently() -> None:
    """All mode baselines session files concurrently instead of one by one."""
    files = [
        _file("sess-alpha", "/sessions/alpha.jsonl", mtime=20.0),
        _file("sess-beta", "/sessions/beta.jsonl", mtime=10.0),
        _file("sess-gamma", "/sessions/gamma.jsonl", mtime=5.0),
    ]
    rows = [
        _recent_row(
            "/projects/alpha",
            BackendName.CLAUDE,
            file_info.session_id,
            file_info.file_path,
            file_info.last_modified_at,
        )
        for file_info in files
    ]
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.read_delay_seconds = 0.01
    for file_info in files:
        backend.snapshots[file_info.file_path] = _snapshot([
            _message("user", "task"),
        ])
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    await _enable_with_recent(rows, backend, projects)

    assert backend.max_active_reads > 1


@pytest.mark.asyncio()
async def test_enable_for_chat_uses_lightweight_cursors_for_baseline() -> None:
    """All mode baselines files through lightweight cursors, not full snapshots."""
    file_info = _file("sess-alpha", "/sessions/alpha.jsonl", mtime=20.0)
    row = _recent_row(
        "/projects/alpha",
        BackendName.CLAUDE,
        file_info.session_id,
        file_info.file_path,
        file_info.last_modified_at,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("alpha", "/projects/alpha", is_current=True)]

    await _enable_with_recent([row], backend, projects)

    assert backend.cursor_count_by_path[file_info.file_path] == 1
    assert backend.read_count_by_path.get(file_info.file_path, 0) == 0


@pytest.mark.asyncio()
async def test_enable_for_chat_returns_disabled_when_recent_query_fails() -> None:
    """A failed recent query must restore watcher and return a degraded result."""
    recent_refresh = MagicMock()
    recent_refresh.ALL_MODE_SESSION_CANDIDATE_LIMIT = 80
    recent_refresh.get_global_recent_sessions = AsyncMock(
        side_effect=RuntimeError("recent store failed")
    )

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(return_value=[_project("alpha", "/projects/alpha")]),
    ), patch.object(
        all_projects_monitor,
        "recent_sessions_refresh",
        recent_refresh,
        create=True,
    ), patch(
        "claude_manager.all_projects_monitor.session_watcher.pause_all",
    ) as pause_all_mock, patch(
        "claude_manager.all_projects_monitor.session_watcher.resume_all",
    ) as resume_all_mock:
        enable_result = await all_projects_monitor.enable_for_chat(CHAT_ID)

    pause_all_mock.assert_called_once()
    resume_all_mock.assert_called_once()
    assert enable_result.enabled is False
    assert "временно недоступен" in enable_result.message
    assert all_projects_monitor.is_enabled_for_chat(CHAT_ID) is False


@pytest.mark.asyncio()
async def test_poll_skips_snapshot_read_when_session_mtime_is_unchanged() -> None:
    """Polling all mode does not reread unchanged session files."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl", mtime=20.0)
    row = _recent_row(
        "/projects/beta",
        BackendName.CLAUDE,
        file_info.session_id,
        file_info.file_path,
        file_info.last_modified_at,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("beta", "/projects/beta")]

    await _enable_with_recent([row], backend, projects)

    callback = AsyncMock()
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=AssertionError("poll discovery is forbidden")),
    ), patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(side_effect=lambda project_session: project_session),
        create=True,
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


def test_next_state_keeps_raw_cursor_before_held_final_message() -> None:
    """Активный ход: raw-курсор не съедает придержанный финал (P1-2)."""
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=3,
        last_delivered_idx=0,
    )
    snapshot = _snapshot(
        [
            _raw_message("assistant", "старый ответ", raw_record_index=2),
            _raw_message("assistant", "придержанный финал", raw_record_index=5),
        ],
        raw_count=6,
        is_turn_active=True,
    )

    next_state = all_projects_monitor._next_state_from_snapshot(
        previous, snapshot, 50.0,
    )

    # Курсор останавливается ПЕРЕД придержанным сообщением (raw_record_index=5),
    # чтобы на следующем поллинге оно снова стало кандидатом: 5 > 4.
    assert next_state.raw_record_count == 4


def test_held_final_message_is_candidate_again_after_turn_completes() -> None:
    """Финал, придержанный при активном ходе, доставляется после завершения хода."""
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=3,
        last_delivered_idx=0,
    )
    active_snapshot = _snapshot(
        [
            _raw_message("assistant", "старый ответ", raw_record_index=2),
            _raw_message("assistant", "придержанный финал", raw_record_index=5),
        ],
        raw_count=6,
        is_turn_active=True,
    )
    after_active_turn = all_projects_monitor._next_state_from_snapshot(
        previous, active_snapshot, 50.0,
    )
    completed_snapshot = _snapshot(
        [
            _raw_message("assistant", "старый ответ", raw_record_index=2),
            _raw_message("assistant", "придержанный финал", raw_record_index=5),
        ],
        raw_count=7,
        is_turn_active=False,
    )

    candidate_indices = all_projects_monitor._candidate_indices(
        after_active_turn, completed_snapshot,
    )

    assert candidate_indices == [1]


def test_next_state_advances_raw_cursor_fully_when_turn_completed() -> None:
    """Завершённый ход продвигает raw-курсор до полного счётчика файла."""
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=3,
        last_delivered_idx=0,
    )
    snapshot = _snapshot(
        [_raw_message("assistant", "финал", raw_record_index=5)],
        raw_count=6,
        is_turn_active=False,
    )

    next_state = all_projects_monitor._next_state_from_snapshot(
        previous, snapshot, 50.0,
    )

    assert next_state.raw_record_count == 6


@pytest.mark.asyncio()
async def test_unread_snapshot_saved_even_when_held_final_blocks_delivery() -> None:
    """Если все кандидаты придержаны, pending-снапшот всё равно фиксируется (P1-2)."""
    unread_buffer._snapshots.clear()
    backend = FakeBackend(BackendName.CLAUDE, {})
    project_session = all_projects_monitor._ProjectSession(
        project_number=1,
        project_name="alpha",
        project_path="/fake/alpha",
        session_number=1,
        file_info=_file("sess-held", "/sessions/held.jsonl", mtime=30.0),
        backend=backend,
    )
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=3,
        last_delivered_idx=0,
    )
    snapshot = _snapshot(
        [_raw_message("assistant", "придержанный финал", raw_record_index=5)],
        raw_count=6,
        is_turn_active=True,
    )
    callback = AsyncMock()

    await all_projects_monitor._deliver_project_session_delta(
        [CHAT_ID], project_session, snapshot, previous, callback,
    )

    callback.assert_not_called()
    stored = unread_buffer.restore_snapshot("sess-held", BackendName.CLAUDE)
    assert stored is not None
    assert stored.raw_record_count == 3
    assert stored.last_delivered_idx == 0
    unread_buffer._snapshots.clear()


def test_baseline_from_cursor_only_read_uses_parsed_count_sentinel() -> None:
    """Baseline из cursor-чтения не выдаёт пустой messages за parsed-историю (P1-1)."""
    backend = FakeBackend(BackendName.CLAUDE, {})
    project_session = all_projects_monitor._ProjectSession(
        project_number=1,
        project_name="alpha",
        project_path="/fake/alpha",
        session_number=1,
        file_info=_file("sess-baseline", "/sessions/baseline.jsonl", mtime=10.0),
        backend=backend,
    )
    cursor_snapshot = _snapshot([], raw_count=42)

    state = all_projects_monitor._baseline_state_from_snapshot(
        project_session, cursor_snapshot, None, None,
    )

    assert state.parsed_message_count == CURSOR_ONLY_PARSED_MESSAGE_COUNT
    assert state.raw_record_count == 42
    assert state.last_delivered_idx == -1


def test_baseline_inherits_parsed_count_from_watcher_state() -> None:
    """Baseline, унаследованный от watcher'а, сохраняет его parsed-число."""
    backend = FakeBackend(BackendName.CLAUDE, {})
    project_session = all_projects_monitor._ProjectSession(
        project_number=1,
        project_name="alpha",
        project_path="/fake/alpha",
        session_number=1,
        file_info=_file("sess-watcher", "/sessions/watcher.jsonl", mtime=10.0),
        backend=backend,
    )
    watcher_state = SessionUnreadState(
        raw_record_count=5,
        last_delivered_idx=4,
        parsed_message_count=5,
    )
    cursor_snapshot = _snapshot([], raw_count=7)

    state = all_projects_monitor._baseline_state_from_snapshot(
        project_session, cursor_snapshot, watcher_state, None,
    )

    assert state.parsed_message_count == 5
    assert state.last_delivered_idx == 4


@pytest.mark.asyncio()
async def test_ensure_unread_snapshot_passes_parsed_message_count() -> None:
    """Unread-снапшот из /all несёт parsed-число состояния, а не default (P1-1)."""
    unread_buffer._snapshots.clear()
    previous = all_projects_monitor._AllMonitorState(
        raw_record_count=6,
        parsed_message_count=3,
        last_delivered_idx=2,
    )

    all_projects_monitor._ensure_unread_snapshot(
        "sess-parsed", BackendName.CLAUDE, previous,
    )

    stored = unread_buffer.restore_snapshot("sess-parsed", BackendName.CLAUDE)
    assert stored is not None
    assert stored.parsed_message_count == 3
    unread_buffer._snapshots.clear()


@pytest.mark.asyncio()
async def test_poll_delivers_all_project_message_and_keeps_unread_snapshot() -> None:
    """All monitor delivers a new assistant message and leaves it unread for project switch."""
    file_info = _file("sess-beta", "/sessions/beta.jsonl", mtime=20.0)
    row = _recent_row(
        "/projects/beta",
        BackendName.CLAUDE,
        file_info.session_id,
        file_info.file_path,
        file_info.last_modified_at,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("beta", "/projects/beta")]

    await _enable_with_recent([row], backend, projects)

    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "answer"),
    ])
    callback = AsyncMock()

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=AssertionError("poll discovery is forbidden")),
    ), patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(
            side_effect=lambda project_session: _candidate_with_mtime(
                project_session, 30.0,
            )
        ),
        create=True,
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
        # Baseline построен из cursor-снапшота с одним сообщением ("task"),
        # поэтому /all переносит parsed-число 1, а не sentinel.
        parsed_message_count=1,
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
    row = _recent_row(
        "/projects/beta",
        BackendName.CLAUDE,
        file_info.session_id,
        file_info.file_path,
        file_info.last_modified_at,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "old"),
        _message("assistant", "old answer"),
    ])
    projects = [_project("beta", "/projects/beta")]

    await _enable_with_recent([row], backend, projects)

    assert unread_buffer.restore_snapshot(
        "sess-beta",
        BackendName.CLAUDE,
    ) == SessionUnreadState(
        raw_record_count=3,
        last_delivered_idx=2,
    )


@pytest.mark.asyncio()
async def test_enable_baselines_other_project_from_unread_cursor() -> None:
    """All mode shows a backlog answer that already waited in another project."""
    unread_buffer.save_snapshot(
        "sess-video",
        BackendName.CLAUDE,
        raw_record_count=1,
        last_delivered_idx=0,
        last_modified_at=10.0,
    )
    file_info = _file("sess-video", "/sessions/video.jsonl", mtime=20.0)
    row = _recent_row(
        "/projects/video",
        BackendName.CLAUDE,
        file_info.session_id,
        file_info.file_path,
        file_info.last_modified_at,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "waiting answer"),
    ])
    projects = [_project("video", "/projects/video")]

    await _enable_with_recent([row], backend, projects)

    callback = AsyncMock()
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=AssertionError("poll discovery is forbidden")),
    ), patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(
            side_effect=lambda project_session: _candidate_with_mtime(
                project_session, 20.0,
            )
        ),
        create=True,
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
    assert callback.call_args.args[7] == "waiting answer"

    # The /all preview must not consume the unread cursor: entering the project
    # later through /pN must still re-deliver the same backlog message.
    assert unread_buffer.restore_snapshot(
        "sess-video",
        BackendName.CLAUDE,
    ) == SessionUnreadState(
        raw_record_count=1,
        last_delivered_idx=0,
        last_modified_at=10.0,
    )


async def _poll_with_mtime(callback: AsyncMock, mtime: float) -> None:
    """Run a single all-mode poll with every candidate file at one mtime."""
    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=AssertionError("poll discovery is forbidden")),
    ), patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(
            side_effect=lambda project_session: _candidate_with_mtime(
                project_session, mtime,
            )
        ),
        create=True,
    ):
        await all_projects_monitor.poll_once(callback)


@pytest.mark.asyncio()
async def test_reentering_all_mode_does_not_redeliver_shown_backlog() -> None:
    """Re-entering /all keeps its own cursor and skips an already-shown answer."""
    unread_buffer.save_snapshot(
        "sess-video",
        BackendName.CLAUDE,
        raw_record_count=1,
        last_delivered_idx=0,
        last_modified_at=10.0,
    )
    file_info = _file("sess-video", "/sessions/video.jsonl", mtime=20.0)
    row = _recent_row(
        "/projects/video",
        BackendName.CLAUDE,
        file_info.session_id,
        file_info.file_path,
        file_info.last_modified_at,
    )
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[file_info.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "waiting answer"),
    ])
    projects = [_project("video", "/projects/video")]

    # First /all entry: the backlog answer is shown once.
    await _enable_with_recent([row], backend, projects)
    first_callback = AsyncMock()
    await _poll_with_mtime(first_callback, 20.0)
    first_callback.assert_awaited_once()
    assert first_callback.call_args.args[7] == "waiting answer"

    all_projects_monitor.disable_for_chat(CHAT_ID)

    # Second /all entry without new file activity must not repeat the answer.
    await _enable_with_recent([row], backend, projects)
    second_callback = AsyncMock()
    await _poll_with_mtime(second_callback, 20.0)
    second_callback.assert_not_awaited()

    # The unread snapshot stays intact for the real-project pending path.
    assert unread_buffer.restore_snapshot(
        "sess-video",
        BackendName.CLAUDE,
    ) == SessionUnreadState(
        raw_record_count=1,
        last_delivered_idx=0,
        last_modified_at=10.0,
    )


@pytest.mark.asyncio()
async def test_missing_candidate_file_is_non_fatal_without_discovery() -> None:
    """A disappeared candidate is skipped while other known files are delivered."""
    missing_file = _file("sess-missing", "/sessions/missing.jsonl")
    valid_file = _file("sess-alpha", "/sessions/alpha.jsonl")
    rows = [
        _recent_row(
            "/projects/alpha",
            BackendName.CLAUDE,
            missing_file.session_id,
            missing_file.file_path,
            missing_file.last_modified_at,
        ),
        _recent_row(
            "/projects/alpha",
            BackendName.CLAUDE,
            valid_file.session_id,
            valid_file.file_path,
            valid_file.last_modified_at,
        ),
    ]
    backend = FakeBackend(BackendName.CLAUDE, {})
    backend.snapshots[missing_file.file_path] = _snapshot([
        _message("user", "missing"),
    ])
    backend.snapshots[valid_file.file_path] = _snapshot([
        _message("user", "task"),
    ])
    projects = [_project("alpha", "/projects/alpha")]

    await _enable_with_recent(rows, backend, projects)

    backend.snapshots[valid_file.file_path] = _snapshot([
        _message("user", "task"),
        _message("assistant", "answer"),
    ])
    callback = AsyncMock()

    def refresh_candidate(
        project_session: all_projects_monitor._ProjectSession,
    ) -> all_projects_monitor._ProjectSession | None:
        if project_session.file_info.session_id == "sess-missing":
            return None
        return _candidate_with_mtime(project_session, 2.0)

    with patch.object(
        project_manager,
        "scan_available_projects",
        new=AsyncMock(side_effect=AssertionError("poll discovery is forbidden")),
    ), patch.object(
        all_projects_monitor,
        "_with_current_file_mtime",
        new=AsyncMock(side_effect=refresh_candidate),
        create=True,
    ):
        await all_projects_monitor.poll_once(callback)

    callback.assert_awaited_once()
