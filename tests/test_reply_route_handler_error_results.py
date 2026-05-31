"""Regression tests for reply-route SendResult errors."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    all_projects_monitor,
    process_manager,
    reply_route_handler,
    reply_route_registry,
)
from claude_manager.coding_agent_backend import BackendName, PermanentErrorKind
from claude_manager.process_manager import SendResult


TEST_CHAT_ID = 12345
BOT_MESSAGE_ID = 8001
CURRENT_PROJECT = "/tmp/current-project"


@pytest.fixture(autouse=True)
def _reset_routes() -> None:
    """Reset routes around each test."""
    reply_route_registry.clear_all()
    reply_route_handler._inflight_route_sends.clear()
    yield
    for task in list(reply_route_handler._background_tasks):
        task.cancel()
    reply_route_registry.clear_all()
    reply_route_handler._inflight_route_sends.clear()


async def _drain_background_tasks() -> None:
    """Wait for routed background sends started by the handler."""
    tasks = list(reply_route_handler._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


@pytest.fixture
def _bot() -> MagicMock:
    """Build a bot double used by telegram_sender."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    return bot


def _target() -> reply_route_registry.ReplyRouteTarget:
    """Build a route target for tests."""
    return reply_route_registry.ReplyRouteTarget(
        project_path=CURRENT_PROJECT,
        session_id="session-route",
        backend=BackendName.CODEX,
        session_number=12,
        project_number=3,
        project_name="budget",
    )


def _update(text: str = "ответ") -> MagicMock:
    """Build a Telegram update that replies to a bot message."""
    update = MagicMock()
    update.effective_chat.id = TEST_CHAT_ID
    update.message.text = text
    update.message.reply_to_message.message_id = BOT_MESSAGE_ID
    update.message.media_group_id = None
    return update


async def _handle_error_result(
    monkeypatch: pytest.MonkeyPatch,
    bot: MagicMock,
    result: SendResult,
) -> str:
    """Send a routed reply and return the user-visible Telegram text."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
    monkeypatch.setattr(process_manager, "send_message", AsyncMock(return_value=result))
    monkeypatch.setattr(
        reply_route_handler,
        "_target_project_is_available",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        reply_route_handler,
        "_target_session_is_available",
        AsyncMock(return_value=True),
    )

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=bot),
    )
    await _drain_background_tasks()

    assert handled is True
    return bot.send_message.await_args.args[1]


@pytest.mark.asyncio()
async def test_context_overflow_error_result_suggests_new_without_raw_backend_error(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Permanent context overflow keeps route semantics and hides raw backend text."""
    sent_text = await _handle_error_result(
        monkeypatch,
        _bot,
        SendResult(
            text="",
            session_id="session-route",
            is_error=True,
            retries_used=0,
            backend=BackendName.CODEX,
            error_text="Prompt is too long",
            permanent_error_kind=PermanentErrorKind.CONTEXT_OVERFLOW,
        ),
    )

    assert sent_text.startswith("Не передал в /3s12: ")
    assert "/new" in sent_text
    assert "Prompt is too long" not in sent_text
    assert "сессия недоступна" not in sent_text


@pytest.mark.asyncio()
async def test_generic_error_result_uses_backend_error_text(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Non-permanent SendResult errors show backend details instead of stale route text."""
    sent_text = await _handle_error_result(
        monkeypatch,
        _bot,
        SendResult(
            text="",
            session_id="session-route",
            is_error=True,
            retries_used=0,
            backend=BackendName.CODEX,
            error_text="backend exploded",
        ),
    )

    assert sent_text == "Не передал в /3s12: backend exploded"
    assert "сессия недоступна" not in sent_text
