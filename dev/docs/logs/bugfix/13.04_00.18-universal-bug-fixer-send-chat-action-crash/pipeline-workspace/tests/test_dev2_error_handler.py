"""Whitebox-тесты DEV-2: отсутствие глобального error handler в setup_bot.

Проверяют, что setup_bot регистрирует глобальный error handler
через Application.add_error_handler. Сейчас (до фикса) тест КРАСНЫЙ —
add_error_handler не вызывается. После фикса тест станет ЗЕЛЁНЫМ.
"""

from unittest.mock import MagicMock, patch

from claude_manager.bot import setup_bot


class TestGlobalErrorHandler:
    """setup_bot должен регистрировать глобальный error handler."""

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_setup_bot_registers_error_handler(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """setup_bot вызывает add_error_handler на объекте Application."""
        # Настраиваем цепочку builder (стандартный паттерн из test_bot.py)
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

        # add_error_handler должен быть вызван хотя бы один раз
        mock_app.add_error_handler.assert_called()
        # Переданный аргумент должен быть callable (функция-обработчик ошибок)
        handler_func = mock_app.add_error_handler.call_args[0][0]
        assert callable(handler_func)
