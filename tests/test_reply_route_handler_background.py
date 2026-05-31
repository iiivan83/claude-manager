"""Tests for background dispatch of routed Telegram replies."""

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
from claude_manager.coding_agent_backend import BackendName
from claude_manager.process_manager import SendResult


TEST_CHAT_ID = 12345
BOT_MESSAGE_ID = 8001
CURRENT_PROJECT = "/tmp/current-project"


@pytest.fixture(autouse=True)
def _reset_routes() -> None:
    """Reset routes and background tasks around each test."""
    reply_route_registry.clear_all()
    reply_route_handler._inflight_route_sends.clear()
    yield
    for task in list(reply_route_handler._background_tasks):
        task.cancel()
    reply_route_registry.clear_all()
    reply_route_handler._inflight_route_sends.clear()


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


async def _drain_background_tasks() -> None:
    """Wait for routed background sends started by the handler."""
    tasks = list(reply_route_handler._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


def _patch_target_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the target project and session look available."""
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
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


@pytest.mark.asyncio()
async def test_text_reply_confirms_before_backend_result(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Routed reply confirms immediately instead of waiting for agent completion."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    backend_can_finish = asyncio.Event()
    backend_started = asyncio.Event()

    async def slow_send_message(*_args, **_kwargs) -> SendResult:
        backend_started.set()
        await backend_can_finish.wait()
        return SendResult(
            text="accepted",
            session_id="session-route",
            is_error=False,
            retries_used=0,
            backend=BackendName.CODEX,
        )

    _patch_target_checks(monkeypatch)
    monkeypatch.setattr(process_manager, "send_message", slow_send_message)

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    assert _bot.send_message.await_args.args[1] == "Передал в /3s12"
    assert backend_started.is_set() is False

    await asyncio.sleep(0)
    assert backend_started.is_set() is True

    backend_can_finish.set()
    await _drain_background_tasks()


@pytest.mark.asyncio()
async def test_second_fast_reply_sees_inflight_target_as_busy(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """A routed send waiting in the background still blocks a second fast reply."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    backend_can_finish = asyncio.Event()
    send_message = AsyncMock()

    async def slow_send_message(*_args, **_kwargs) -> SendResult:
        await backend_can_finish.wait()
        return SendResult(
            text="accepted",
            session_id="session-route",
            is_error=False,
            retries_used=0,
            backend=BackendName.CODEX,
        )

    send_message.side_effect = slow_send_message
    _patch_target_checks(monkeypatch)
    monkeypatch.setattr(process_manager, "send_message", send_message)

    first_handled = await reply_route_handler.try_handle_text_reply(
        _update("первый"),
        SimpleNamespace(bot=_bot),
    )
    second_handled = await reply_route_handler.try_handle_text_reply(
        _update("второй"),
        SimpleNamespace(bot=_bot),
    )

    assert first_handled is True
    assert second_handled is True
    assert _bot.send_message.await_args.args[1] == (
        "Не передал в /3s12: сессия занята. Подождите или /stop"
    )
    assert send_message.await_count <= 1

    backend_can_finish.set()
    await _drain_background_tasks()
