"""Якорные тесты: описания команд в BOT_COMMANDS и ответ handle_all.

Проверяют, что после фикса DEV-1/DEV-2 строковые константы содержат
корректные, не вводящие в заблуждение тексты.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager.bot import BOT_COMMANDS, handle_all


# --- Вспомогательные функции ---

def _get_description(command_name: str) -> str | None:
    """Возвращает описание команды из BOT_COMMANDS по имени."""
    for name, description in BOT_COMMANDS:
        if name == command_name:
            return description
    return None


# --- DEV-1: описание /all ---

class TestAllCommandDescription:
    """Описание команды /all должно точно отражать её действие (отключение от сессии)."""

    def test_all_description_not_misleading(self) -> None:
        """Описание /all НЕ должно быть «Мониторинг всех сессий» — это вводит в заблуждение."""
        description = _get_description("all")
        assert description is not None, "Команда 'all' отсутствует в BOT_COMMANDS"
        assert description != "Мониторинг всех сессий", (
            "Описание /all всё ещё «Мониторинг всех сессий» — "
            "мониторинг всегда активен, а /all отключает от сессии"
        )

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.session_manager")
    @patch("claude_manager.bot._send_telegram_message", new_callable=AsyncMock)
    async def test_handle_all_response_accurate(
        self,
        mock_send: AsyncMock,
        mock_session_mgr: MagicMock,
    ) -> None:
        """Ответ handle_all не должен быть «Режим мониторинга всех сессий»."""
        mock_session_mgr.unbind_session = AsyncMock()
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_chat.id = 12345
        context = MagicMock()

        # Патчим проверку доступа, чтобы не зависеть от config
        with patch("claude_manager.bot._check_access", return_value=True):
            await handle_all(update, context)

        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][1]
        assert sent_text != "Режим мониторинга всех сессий", (
            "Ответ handle_all всё ещё «Режим мониторинга всех сессий» — нужно обновить"
        )


# --- DEV-2: описание /projects ---

class TestProjectsCommandDescription:
    """Описание команды /projects должно быть коротким."""

    def test_projects_description_not_verbose(self) -> None:
        """Описание /projects НЕ должно быть длинным «Список проектов для переключения»."""
        description = _get_description("projects")
        assert description is not None, "Команда 'projects' отсутствует в BOT_COMMANDS"
        assert description != "Список проектов для переключения", (
            "Описание /projects всё ещё многословное — нужно сократить"
        )

    def test_projects_description_reasonable_length(self) -> None:
        """Описание /projects не длиннее 25 символов."""
        max_description_length = 25
        description = _get_description("projects")
        assert description is not None, "Команда 'projects' отсутствует в BOT_COMMANDS"
        assert len(description) <= max_description_length, (
            f"Описание /projects слишком длинное ({len(description)} символов): «{description}»"
        )
