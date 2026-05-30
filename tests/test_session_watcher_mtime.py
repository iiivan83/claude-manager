"""Tests for watcher last-modified cursor state."""

from unittest.mock import AsyncMock, patch

from claude_manager import session_watcher
from claude_manager.coding_agent_backend import BackendName, SessionFileInfo
from tests.test_session_watcher import FakeBackend, PROJECT_DIR, _snapshot


def _file(session_id: str, mtime: float) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=f"/tmp/{session_id}.jsonl",
        last_modified_at=mtime,
        preview="",
    )


async def test_reset_state_preserves_file_mtime_in_unread_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    backend = FakeBackend(BackendName.CODEX)
    backend.files = [_file("session-1", 42.0)]
    backend.snapshots["/tmp/session-1.jsonl"] = _snapshot("old", raw_count=1)
    watcher = session_watcher.SessionWatcher(backend)

    with patch.object(
        session_watcher.daily_session_registry,
        "get_all_today_sessions",
        new=AsyncMock(return_value={}),
    ):
        await watcher.reset_state()

    snapshot = watcher.get_seen_counts_snapshot()

    assert snapshot["session-1"].last_modified_at == 42.0


async def test_poll_once_preserves_file_mtime_after_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    backend = FakeBackend(BackendName.CODEX)
    backend.files = [_file("session-1", 43.0)]
    backend.snapshots["/tmp/session-1.jsonl"] = _snapshot("new", raw_count=1)
    watcher = session_watcher.SessionWatcher(backend)

    with patch.object(
        session_watcher.daily_session_registry,
        "get_all_today_sessions",
        new=AsyncMock(return_value={}),
    ):
        await watcher.poll_once(AsyncMock(), AsyncMock(return_value=None))

    assert watcher.get_seen_counts_snapshot()["session-1"].last_modified_at == 43.0


async def test_resume_session_preserves_file_mtime(monkeypatch) -> None:
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    backend = FakeBackend(BackendName.CODEX)
    backend.files = [_file("session-1", 44.0)]
    backend.snapshots["/tmp/session-1.jsonl"] = _snapshot("new", raw_count=1)
    watcher = session_watcher.SessionWatcher(backend)
    watcher.pause_session("session-1")

    await watcher.resume_session("session-1")

    assert watcher.get_seen_counts_snapshot()["session-1"].last_modified_at == 44.0
