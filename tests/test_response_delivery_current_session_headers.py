"""Tests for current-session aware direct response headers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import telegram_response_delivery as delivery_module
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession


TEST_CHAT_ID = 12345
TEST_SESSION_ID = "session-current"
OTHER_SESSION_ID = "session-other"


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


@pytest.mark.asyncio
async def test_direct_response_header_is_clickable_after_session_switch(
    monkeypatch: pytest.MonkeyPatch,
    _send_mock: AsyncMock,
) -> None:
    """A direct response should be linkable if the user moved to another session."""
    monkeypatch.setattr(
        delivery_module.session_manager,
        "get_active_session",
        lambda _chat_id: ActiveSession(OTHER_SESSION_ID, BackendName.CODEX),
    )

    await delivery_module.send_response(
        TEST_CHAT_ID,
        "done",
        49,
        BackendName.CODEX,
        is_final=True,
        session_id=TEST_SESSION_ID,
    )

    assert _send_mock.await_args.args[2] == "<b>/49</b> ⚡ Codex ✅ done"
