"""Tests for Telegram handler routing edge cases."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User

from claude_manager import (
    bot as bot_module,
    telegram_input_handlers,
    telegram_project_handlers,
    telegram_session_handlers,
)


def _make_text_update(text: str, command_length: int) -> Update:
    """Build a real Telegram text update with a leading bot command entity."""
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=12345, type="private"),
        from_user=User(id=12345, first_name="Ivan", is_bot=False),
        text=text,
        entities=[
            MessageEntity(
                type=MessageEntity.BOT_COMMAND,
                offset=0,
                length=command_length,
            )
        ],
    )
    message.set_bot(MagicMock(username="ClaudeDialogForMeIAmIvan_bot"))
    return Update(update_id=1, message=message)


def _registered_handlers() -> list[object]:
    """Return handlers registered by the production setup function."""
    application = MagicMock()
    application.add_handler = MagicMock()
    bot_module._register_handlers(application)
    return [call.args[0] for call in application.add_handler.call_args_list]


def _first_matching_callback(update: Update) -> object | None:
    """Return the callback that would handle this update in handler order."""
    for handler in _registered_handlers():
        check_result = handler.check_update(update)
        if check_result:
            return handler.callback
    return None


@pytest.mark.parametrize(
    ("text", "command_length"),
    [
        ("/8 ⚡ Codex ✅ Поиск инфоповодов", 2),
        ("/3s12 budget ⚡ Codex ✅ Готово", 5),
    ],
)
def test_copied_session_header_text_routes_to_message_handler(
    text: str,
    command_length: int,
) -> None:
    """Copied response headers with extra text are prompts, not switch commands."""
    update = _make_text_update(text, command_length)

    callback = _first_matching_callback(update)

    assert callback is telegram_input_handlers.handle_message


@pytest.mark.parametrize(
    ("text", "command_length", "expected_callback"),
    [
        ("/8", 2, telegram_session_handlers.handle_switch_session),
        ("/3s12", 5, telegram_project_handlers.handle_switch_project_session),
    ],
)
def test_exact_session_commands_keep_switch_routing(
    text: str,
    command_length: int,
    expected_callback: object,
) -> None:
    """Exact numeric session commands still switch sessions."""
    update = _make_text_update(text, command_length)

    callback = _first_matching_callback(update)

    assert callback is expected_callback
