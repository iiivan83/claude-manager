"""Regression tests for handler-owned final delivery races."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from claude_manager import session_watcher
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession
from tests.test_session_watcher import (
    PROJECT_DIR,
    TEST_CHAT_ID,
    FakeBackend,
    _file,
    _snapshot,
)


@pytest.mark.asyncio
async def test_handler_owned_final_arriving_after_resume_is_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watcher must not duplicate a final answer already sent by the request handler."""
    session_id = "session-final-race"
    file_path = f"/tmp/{session_id}.jsonl"
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    backend = FakeBackend(BackendName.CODEX)
    backend.files = [_file(session_id, file_path)]
    backend.snapshots[file_path] = _snapshot(
        "visible progress",
        raw_count=1,
        is_turn_active=True,
    )
    callback = AsyncMock()
    watcher = session_watcher.SessionWatcher(backend)

    watcher.pause_session(session_id)
    await watcher.resume_session(session_id)

    # The request handler has already sent "FINAL ANSWER" from CLI stdout, but the
    # JSONL session file receives that final message only after handler cleanup.
    backend.snapshots[file_path] = _snapshot(
        "visible progress",
        "FINAL ANSWER",
        raw_count=2,
        is_turn_active=False,
    )

    with (
        patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            session_watcher.daily_session_registry,
            "register_session",
            new=AsyncMock(return_value=47),
        ),
        patch.object(
            session_watcher.session_manager,
            "find_chat_by_session_id",
            new=Mock(return_value=TEST_CHAT_ID),
        ),
    ):
        await watcher.poll_once(
            callback,
            AsyncMock(return_value=ActiveSession("other-session", BackendName.CODEX)),
        )

    callback.assert_not_awaited()
    state = watcher._states[session_id]
    assert state.last_delivered_idx == 1
    assert state.handler_owns_final_delivery is False
