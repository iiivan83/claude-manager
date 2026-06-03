"""Tests for Telegram audio handler registration."""

from unittest.mock import MagicMock, patch

from claude_manager import bot as bot_module
from claude_manager import telegram_input_handlers
from claude_manager.bot import setup_bot


def _mock_builder(mock_app: MagicMock) -> MagicMock:
    builder = MagicMock()
    builder.token.return_value = builder
    builder.post_init.return_value = builder
    builder.concurrent_updates.return_value = builder
    builder.connect_timeout.return_value = builder
    builder.read_timeout.return_value = builder
    builder.write_timeout.return_value = builder
    builder.pool_timeout.return_value = builder
    builder.connection_pool_size.return_value = builder
    builder.build.return_value = mock_app
    return builder


def test_bot_reexports_voice_handler() -> None:
    """Old imports from claude_manager.bot expose the voice handler."""
    assert bot_module.handle_voice is telegram_input_handlers.handle_voice


@patch("claude_manager.bot.ApplicationBuilder")
def test_setup_bot_registers_voice_handler(mock_builder_class: MagicMock) -> None:
    """setup_bot registers a handler that calls handle_voice."""
    mock_app = MagicMock()
    mock_app.add_handler = MagicMock()
    mock_builder_class.return_value = _mock_builder(mock_app)

    setup_bot()

    callbacks = [
        getattr(call.args[0], "callback", None)
        for call in mock_app.add_handler.call_args_list
    ]
    assert bot_module.handle_voice in callbacks
