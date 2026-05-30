"""Tests for clearing Telegram reply anchors on /stop."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    bot as bot_module,
    config as config_module,
    process_manager,
    reply_anchor_registry,
    session_manager,
)
from claude_manager.bot import handle_stop
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession


ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "session-current"
TEST_PROJECT_PATH = "/tmp/reply-anchor-project"


def _make_update() -> MagicMock:
    """Create a fake /stop Telegram update."""
    update = MagicMock()
    update.effective_chat.id = TEST_CHAT_ID
    update.effective_user.id = ALLOWED_USER_ID
    update.message.text = "/stop"
    return update


def _make_context() -> MagicMock:
    """Create a fake Telegram handler context."""
    return MagicMock()


@pytest.fixture(autouse=True)
def _setup_bot() -> MagicMock:
    """Install fake bot application and config."""
    fake_application = MagicMock()
    fake_application.bot = MagicMock()
    fake_application.bot.send_message = AsyncMock()
    original_application = bot_module._application
    original_allowed = config_module.ALLOWED_USER_IDS
    original_working_dir = config_module.WORKING_DIR
    bot_module._application = fake_application
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.WORKING_DIR = TEST_PROJECT_PATH
    reply_anchor_registry.clear_all()
    yield fake_application
    reply_anchor_registry.clear_all()
    bot_module._application = original_application
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.WORKING_DIR = original_working_dir


@pytest.mark.asyncio()
async def test_handle_stop_clears_reply_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping a turn removes the active reply anchor for that session."""
    reply_anchor_registry.set_anchor(
        TEST_PROJECT_PATH,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
        123,
    )
    monkeypatch.setattr(
        session_manager,
        "get_active_session",
        lambda _chat_id: ActiveSession(TEST_SESSION_ID, BackendName.CLAUDE),
    )
    monkeypatch.setattr(process_manager, "has_process", lambda *_args: True)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: True)
    monkeypatch.setattr(process_manager, "stop_process", AsyncMock())

    await handle_stop(_make_update(), _make_context())

    assert (
        reply_anchor_registry.get_anchor(
            TEST_PROJECT_PATH,
            BackendName.CLAUDE,
            TEST_SESSION_ID,
        )
        is None
    )
