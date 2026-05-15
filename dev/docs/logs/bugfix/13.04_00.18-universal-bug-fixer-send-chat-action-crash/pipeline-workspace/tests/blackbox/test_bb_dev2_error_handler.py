"""Blackbox-тесты DEV-2: глобальный error handler зарегистрирован.

Проверяют, что Application имеет error handler, и что он корректно
обрабатывает ошибки (логирует, уведомляет пользователя).
Сейчас (до фикса) тесты КРАСНЫЕ. После фикса — ЗЕЛЁНЫЕ.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager.bot import setup_bot


class TestErrorHandlerRegistered:
    """Глобальный error handler должен быть зарегистрирован при setup_bot."""

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_error_handler_is_async_function(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """Error handler должен быть асинхронной функцией (coroutine function).

        python-telegram-bot вызывает error handler через await,
        поэтому он обязан быть async def.
        """
        import asyncio

        mock_app = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_app.add_error_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.post_init.return_value = mock_builder
        mock_builder.concurrent_updates.return_value = mock_builder
        mock_builder.build.return_value = mock_app
        mock_builder_class.return_value = mock_builder

        setup_bot()

        # Error handler должен быть зарегистрирован
        mock_app.add_error_handler.assert_called()
        handler_func = mock_app.add_error_handler.call_args[0][0]
        # И это должна быть coroutine function
        assert asyncio.iscoroutinefunction(handler_func), (
            "Error handler должен быть async def, чтобы python-telegram-bot "
            "мог вызвать его через await"
        )
