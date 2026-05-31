"""Tests for incoming Telegram reply-route handling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    all_projects_monitor,
    config as config_module,
    process_manager,
    reply_route_handler,
    reply_route_registry,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.process_manager import SendResult


TEST_CHAT_ID = 12345
BOT_MESSAGE_ID = 8001
CURRENT_PROJECT = "/tmp/current-project"
OTHER_PROJECT = "/tmp/other-project"


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


def _target(
    *,
    project_path: str = CURRENT_PROJECT,
    project_number: int | None = 3,
    session_number: int = 12,
) -> reply_route_registry.ReplyRouteTarget:
    """Build a route target for tests."""
    return reply_route_registry.ReplyRouteTarget(
        project_path=project_path,
        session_id="session-route",
        backend=BackendName.CODEX,
        session_number=session_number,
        project_number=project_number,
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


@pytest.mark.asyncio()
async def test_text_reply_from_all_routes_to_target_without_disabling_all(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Text reply in /all sends to route target and confirms with /PsS."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    send_message = AsyncMock(
        return_value=SendResult(
            text="accepted",
            session_id="session-route",
            is_error=False,
            retries_used=0,
            backend=BackendName.CODEX,
        )
    )
    disable_for_chat = MagicMock()
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(all_projects_monitor, "disable_for_chat", disable_for_chat)
    monkeypatch.setattr(process_manager, "send_message", send_message)
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

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )
    await _drain_background_tasks()

    assert handled is True
    send_message.assert_awaited_once_with(
        "session-route",
        "ответ",
        backend=BackendName.CODEX,
        cwd=CURRENT_PROJECT,
    )
    assert _bot.send_message.await_args.args[1] == "Передал в /3s12"
    disable_for_chat.assert_not_called()
    assert all_projects_monitor.is_enabled_for_chat(TEST_CHAT_ID) is True


@pytest.mark.asyncio()
async def test_text_reply_inside_same_project_uses_local_link(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Same-project reply confirms with /N and does not change active session."""
    reply_route_registry.register_route(
        TEST_CHAT_ID,
        BOT_MESSAGE_ID,
        _target(project_path=CURRENT_PROJECT, session_number=12),
    )
    original_working_dir = config_module.WORKING_DIR
    config_module.WORKING_DIR = CURRENT_PROJECT
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: False)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
    monkeypatch.setattr(
        process_manager,
        "send_message",
        AsyncMock(
            return_value=SendResult(
                text="accepted",
                session_id="session-route",
                is_error=False,
                retries_used=0,
                backend=BackendName.CODEX,
            )
        ),
    )
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

    try:
        handled = await reply_route_handler.try_handle_text_reply(
            _update(),
            SimpleNamespace(bot=_bot),
        )
    finally:
        config_module.WORKING_DIR = original_working_dir

    assert handled is True
    assert _bot.send_message.await_args.args[1] == "Передал в /12"


@pytest.mark.asyncio()
async def test_text_reply_inside_other_project_uses_full_link(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Cross-project reply from project mode confirms with /PsS."""
    reply_route_registry.register_route(
        TEST_CHAT_ID,
        BOT_MESSAGE_ID,
        _target(project_path=OTHER_PROJECT, project_number=3, session_number=12),
    )
    original_working_dir = config_module.WORKING_DIR
    config_module.WORKING_DIR = CURRENT_PROJECT
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: False)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
    monkeypatch.setattr(
        process_manager,
        "send_message",
        AsyncMock(
            return_value=SendResult(
                text="accepted",
                session_id="session-route",
                is_error=False,
                retries_used=0,
                backend=BackendName.CODEX,
            )
        ),
    )
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

    try:
        handled = await reply_route_handler.try_handle_text_reply(
            _update(),
            SimpleNamespace(bot=_bot),
        )
    finally:
        config_module.WORKING_DIR = original_working_dir

    assert handled is True
    assert _bot.send_message.await_args.args[1] == "Передал в /3s12"


@pytest.mark.asyncio()
async def test_unknown_route_in_all_mode_shows_unknown_message(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Unknown reply target in /all is not guessed from active session."""
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    assert _bot.send_message.await_args.args[1] == (
        "Не понял, куда передать ответ. "
        "Нажми ссылку на нужную сессию и отправь сообщение там"
    )


@pytest.mark.asyncio()
async def test_unknown_bot_reply_inside_project_shows_unknown_message(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Unknown reply to a bot message is not guessed from the active session."""
    _bot.id = 777
    update = _update()
    update.message.reply_to_message.from_user.id = 777
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: False)

    handled = await reply_route_handler.try_handle_text_reply(
        update,
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    assert _bot.send_message.await_args.args[1] == (
        "Не понял, куда передать ответ. "
        "Нажми ссылку на нужную сессию и отправь сообщение там"
    )


@pytest.mark.asyncio()
async def test_busy_target_shows_busy_message(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Busy target is rejected with the short route link."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: True)

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )
    await _drain_background_tasks()

    assert handled is True
    assert _bot.send_message.await_args.args[1] == (
        "Не передал в /3s12: сессия занята. Подождите или /stop"
    )


@pytest.mark.asyncio()
async def test_unavailable_project_shows_project_error(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Route target with unavailable project is not sent to backend."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    send_message = AsyncMock()
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
    monkeypatch.setattr(process_manager, "send_message", send_message)
    monkeypatch.setattr(
        reply_route_handler,
        "_target_project_is_available",
        AsyncMock(return_value=False),
    )

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    send_message.assert_not_awaited()
    assert _bot.send_message.await_args.args[1] == (
        "Не передал в /3s12: проект недоступен"
    )


@pytest.mark.asyncio()
async def test_unavailable_session_shows_session_error(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Route target with unavailable session is not sent to backend."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    send_message = AsyncMock()
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
    monkeypatch.setattr(process_manager, "send_message", send_message)
    monkeypatch.setattr(
        reply_route_handler,
        "_target_project_is_available",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        reply_route_handler,
        "_target_session_is_available",
        AsyncMock(return_value=False),
    )

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    send_message.assert_not_awaited()
    assert _bot.send_message.await_args.args[1] == (
        "Не передал в /3s12: сессия недоступна"
    )


@pytest.mark.asyncio()
async def test_busy_error_from_send_message_becomes_busy_reply(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """If another reply wins the busy lock, this reply gets busy message."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
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
    monkeypatch.setattr(
        process_manager,
        "send_message",
        AsyncMock(side_effect=process_manager.ProcessManagerError("already busy")),
    )

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )
    await _drain_background_tasks()

    assert handled is True
    assert _bot.send_message.await_args.args[1] == (
        "Не передал в /3s12: сессия занята. Подождите или /stop"
    )


@pytest.mark.asyncio()
async def test_all_mode_reply_does_not_switch_project_or_session(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Reply-routing does not call project switching APIs."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    switch_project = AsyncMock()
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
    monkeypatch.setattr(
        process_manager,
        "send_message",
        AsyncMock(
            return_value=SendResult(
                text="accepted",
                session_id="session-route",
                is_error=False,
                retries_used=0,
                backend=BackendName.CODEX,
            )
        ),
    )
    monkeypatch.setattr(reply_route_handler.project_manager, "switch_project", switch_project)

    handled = await reply_route_handler.try_handle_text_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    switch_project.assert_not_awaited()


@pytest.mark.asyncio()
async def test_attachment_reply_with_route_is_rejected_without_download(
    monkeypatch: pytest.MonkeyPatch,
    _bot: MagicMock,
) -> None:
    """Unsupported attachment reply is handled before download or aggregation."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    send_message = AsyncMock()
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat_id: True)
    monkeypatch.setattr(process_manager, "send_message", send_message)

    handled = await reply_route_handler.try_handle_unsupported_attachment_reply(
        _update(),
        SimpleNamespace(bot=_bot),
    )

    assert handled is True
    assert _bot.send_message.await_args.args[1] == (
        "Не передал в /3s12: ответы с вложениями пока не работают, "
        "этот функционал ещё не сделали"
    )
    send_message.assert_not_awaited()
