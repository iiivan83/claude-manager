"""Tests for reply anchors in watcher, all-mode, and pending delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    all_projects_monitor,
    config as config_module,
    daily_session_registry,
    project_pending_delivery,
    reply_anchor_registry,
    telegram_response_delivery as delivery_module,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)


TEST_CHAT_ID = 12345
TEST_SESSION_ID = "session-current"
TEST_PROJECT_PATH = "/tmp/reply-anchor-project"


@pytest.fixture(autouse=True)
def _setup_reply_registry() -> None:
    """Reset reply anchors around each test."""
    reply_anchor_registry.clear_all()
    yield
    reply_anchor_registry.clear_all()


@pytest.fixture
def _send_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Install fake Telegram delivery dependencies."""
    fake_application = SimpleNamespace(bot=MagicMock(name="telegram_bot"))
    send_mock = AsyncMock()
    monkeypatch.setattr(delivery_module, "_application", fake_application)
    monkeypatch.setattr(
        delivery_module.telegram_sender,
        "send_telegram_message",
        send_mock,
    )
    monkeypatch.setattr(
        delivery_module.silence_mode_registry,
        "is_enabled",
        lambda: False,
    )
    return send_mock


@pytest.fixture
def _working_dir() -> None:
    """Pin config.WORKING_DIR for current-project delivery."""
    original_working_dir = config_module.WORKING_DIR
    config_module.WORKING_DIR = TEST_PROJECT_PATH
    yield
    config_module.WORKING_DIR = original_working_dir


@pytest.mark.asyncio()
async def test_send_watcher_message_uses_existing_reply_anchor(
    _working_dir: None,
    _send_mock: AsyncMock,
) -> None:
    """Watcher delivery reads anchor for project/backend/session."""
    reply_anchor_registry.set_anchor(
        TEST_PROJECT_PATH,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
        909,
    )

    await delivery_module.send_watcher_message(
        TEST_CHAT_ID,
        "watcher text",
        TEST_SESSION_ID,
        BackendName.CLAUDE,
        session_number=4,
        is_final=False,
    )

    assert _send_mock.await_args.kwargs["reply_to_message_id"] == 909


@pytest.mark.asyncio()
async def test_all_projects_delta_callback_receives_project_path() -> None:
    """All-mode callback receives source project_path for reply lookup."""
    callback = AsyncMock()
    backend = SimpleNamespace(name=BackendName.CODEX)
    project_session = SimpleNamespace(
        project_number=3,
        session_number=12,
        project_name="budget",
        project_path=TEST_PROJECT_PATH,
        file_info=SessionFileInfo(
            session_id=TEST_SESSION_ID,
            file_path="/tmp/session.jsonl",
            last_modified_at=1.0,
            preview="preview",
        ),
        backend=backend,
    )
    snapshot = SessionFileSnapshot(
        messages=[
            SessionMessage(
                role="assistant",
                text="hello",
                timestamp=None,
                is_empty_response=False,
            ),
        ],
        raw_record_count=1,
        last_record=None,
        is_turn_active=False,
    )

    await all_projects_monitor._deliver_project_session_delta(
        [TEST_CHAT_ID],
        project_session,
        snapshot,
        all_projects_monitor._AllMonitorState(),
        callback,
    )

    callback.assert_awaited_once_with(
        TEST_CHAT_ID,
        3,
        12,
        "budget",
        TEST_PROJECT_PATH,
        TEST_SESSION_ID,
        BackendName.CODEX,
        "hello",
        True,
    )


@pytest.mark.asyncio()
async def test_send_all_projects_watcher_message_uses_source_project_anchor(
    _send_mock: AsyncMock,
) -> None:
    """All-mode delivery reads anchor by source project/backend/session."""
    reply_anchor_registry.set_anchor(
        TEST_PROJECT_PATH,
        BackendName.CODEX,
        TEST_SESSION_ID,
        808,
    )

    await delivery_module.send_all_projects_watcher_message(
        TEST_CHAT_ID,
        project_number=3,
        session_number=12,
        project_name="budget",
        project_path=TEST_PROJECT_PATH,
        session_id=TEST_SESSION_ID,
        backend=BackendName.CODEX,
        text="all text",
        is_final=True,
    )

    assert _send_mock.await_args.kwargs["reply_to_message_id"] == 808


@pytest.mark.asyncio()
async def test_pending_delivery_uses_existing_reply_anchor(
    monkeypatch: pytest.MonkeyPatch,
    _working_dir: None,
) -> None:
    """Pending delivery reads anchor for the returned project/session."""
    send_response = AsyncMock()
    reply_anchor_registry.set_anchor(
        TEST_PROJECT_PATH,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
        707,
    )
    monkeypatch.setattr(
        daily_session_registry,
        "register_session",
        AsyncMock(return_value=5),
    )
    monkeypatch.setattr(delivery_module, "send_response", send_response)

    await project_pending_delivery.deliver_pending_messages(
        TEST_CHAT_ID,
        [
            project_pending_delivery.PendingDeliveryItem(
                session_id=TEST_SESSION_ID,
                backend=BackendName.CLAUDE,
                text="pending text",
                is_final=True,
            )
        ],
    )

    send_response.assert_awaited_once_with(
        TEST_CHAT_ID,
        "pending text",
        5,
        BackendName.CLAUDE,
        is_final=True,
        reply_to_message_id=707,
    )
