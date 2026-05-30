"""Tests for direct Telegram reply-anchor delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    claude_interaction as ci_module,
    config as config_module,
    daily_session_registry,
    process_manager,
    reply_anchor_registry,
    session_manager,
    session_watcher,
    telegram_response_delivery as delivery_module,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.process_manager import SendResult
from claude_manager.session_manager import ActiveSession


TEST_CHAT_ID = 12345
TEST_SESSION_ID = "session-current"
TEST_PROJECT_PATH = "/tmp/reply-anchor-project"


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


@pytest.mark.asyncio()
async def test_send_response_replies_only_first_chunk(
    monkeypatch: pytest.MonkeyPatch,
    _send_mock: AsyncMock,
) -> None:
    """Long response uses reply only for the first Telegram chunk."""
    monkeypatch.setattr(
        delivery_module.message_splitter,
        "prepare_message",
        lambda _text: ["one", "two"],
    )

    await delivery_module.send_response(
        TEST_CHAT_ID,
        "long",
        3,
        BackendName.CLAUDE,
        is_final=False,
        reply_to_message_id=555,
    )

    assert _send_mock.await_args_list[0].kwargs["reply_to_message_id"] == 555
    assert _send_mock.await_args_list[1].kwargs.get("reply_to_message_id") is None


@pytest.mark.asyncio()
async def test_send_to_claude_sets_anchor_for_accepted_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted user message becomes reply anchor for the final response."""
    original_working_dir = config_module.WORKING_DIR
    original_send_response_ref = ci_module._send_response_ref
    original_send_telegram_message_ref = ci_module._send_telegram_message_ref
    mock_response = AsyncMock()
    callback_module = SimpleNamespace(
        send_response=mock_response,
        send_telegram_message=AsyncMock(),
    )
    reply_anchor_registry.clear_all()
    config_module.WORKING_DIR = TEST_PROJECT_PATH
    ci_module.init_callbacks(
        send_response_module=callback_module,
        send_response_attr="send_response",
        send_telegram_message_module=callback_module,
        send_telegram_message_attr="send_telegram_message",
    )
    monkeypatch.setattr(
        session_manager,
        "get_active_session",
        lambda _chat_id: ActiveSession(TEST_SESSION_ID, BackendName.CLAUDE),
    )
    monkeypatch.setattr(
        daily_session_registry,
        "register_session",
        AsyncMock(return_value=7),
    )
    monkeypatch.setattr(
        process_manager,
        "send_message",
        AsyncMock(
            return_value=SendResult(
                text="done",
                session_id=TEST_SESSION_ID,
                is_error=False,
                retries_used=0,
                backend=BackendName.CLAUDE,
            ),
        ),
    )
    monkeypatch.setattr(session_watcher, "pause_session", MagicMock())
    monkeypatch.setattr(session_watcher, "resume_session", AsyncMock())
    monkeypatch.setattr(session_watcher, "clear_handler_owns_final_delivery", MagicMock())
    monkeypatch.setattr(ci_module, "start_agent_silence_watchdog", MagicMock())
    monkeypatch.setattr(ci_module, "cancel_agent_silence_watchdog", MagicMock())

    try:
        await ci_module.send_to_claude_and_respond(
            TEST_CHAT_ID,
            "hello",
            reply_to_message_id=444,
        )
    finally:
        config_module.WORKING_DIR = original_working_dir
        ci_module._send_response_ref = original_send_response_ref
        ci_module._send_telegram_message_ref = original_send_telegram_message_ref

    assert (
        reply_anchor_registry.get_anchor(
            TEST_PROJECT_PATH,
            BackendName.CLAUDE,
            TEST_SESSION_ID,
        )
        == 444
    )
    assert mock_response.await_args.kwargs["reply_to_message_id"] == 444


@pytest.mark.asyncio()
async def test_session_id_change_moves_anchor_even_after_project_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reply anchor follows temp->real session id even if delivery is suppressed."""
    original_working_dir = config_module.WORKING_DIR
    original_send_response_ref = ci_module._send_response_ref
    original_send_telegram_message_ref = ci_module._send_telegram_message_ref
    callback_module = SimpleNamespace(
        send_response=AsyncMock(),
        send_telegram_message=AsyncMock(),
    )
    temp_session_id = "_new_123"
    real_session_id = "real-session"
    reply_anchor_registry.clear_all()
    config_module.WORKING_DIR = TEST_PROJECT_PATH
    ci_module.init_callbacks(
        send_response_module=callback_module,
        send_response_attr="send_response",
        send_telegram_message_module=callback_module,
        send_telegram_message_attr="send_telegram_message",
    )
    monkeypatch.setattr(
        session_manager,
        "get_active_session",
        lambda _chat_id: ActiveSession(temp_session_id, BackendName.CODEX),
    )

    async def fake_send_message(
        _session_id: str,
        _text: str,
        *,
        session_id_callback=None,
        **_kwargs,
    ) -> SendResult:
        config_module.WORKING_DIR = "/tmp/other-project"
        await session_id_callback(temp_session_id, real_session_id, BackendName.CODEX)
        return SendResult(
            text="done",
            session_id=real_session_id,
            is_error=False,
            retries_used=0,
            backend=BackendName.CODEX,
        )

    monkeypatch.setattr(process_manager, "send_message", fake_send_message)
    monkeypatch.setattr(session_watcher, "pause_session", MagicMock())
    monkeypatch.setattr(session_watcher, "resume_session", AsyncMock())
    monkeypatch.setattr(session_watcher, "clear_handler_owns_final_delivery", MagicMock())
    monkeypatch.setattr(ci_module, "start_agent_silence_watchdog", MagicMock())
    monkeypatch.setattr(ci_module, "cancel_agent_silence_watchdog", MagicMock())

    try:
        await ci_module.send_to_claude_and_respond(
            TEST_CHAT_ID,
            "hello",
            reply_to_message_id=888,
        )
    finally:
        config_module.WORKING_DIR = original_working_dir
        ci_module._send_response_ref = original_send_response_ref
        ci_module._send_telegram_message_ref = original_send_telegram_message_ref

    assert (
        reply_anchor_registry.get_anchor(
            TEST_PROJECT_PATH,
            BackendName.CODEX,
            temp_session_id,
        )
        is None
    )
    assert (
        reply_anchor_registry.get_anchor(
            TEST_PROJECT_PATH,
            BackendName.CODEX,
            real_session_id,
        )
        == 888
    )
