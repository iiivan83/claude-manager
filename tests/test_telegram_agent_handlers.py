"""Тесты Telegram handlers для выбора CLI-агента."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import (
    current_backend_registry,
    daily_session_registry,
    session_manager,
    telegram_agent_handlers as agent_handlers,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession
import claude_manager.bot as bot_module
import claude_manager.config as config_module


ALLOWED_USER_ID = 12345
DENIED_USER_ID = 99999
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "abc-def-111"


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для agent-handler тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_e2e = config_module.E2E_TEST_USER_ID
    original_current_backend = current_backend_registry._current_backend
    original_current_backend_loaded = current_backend_registry._loaded_from_disk
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.E2E_TEST_USER_ID = None
    current_backend_registry._current_backend = BackendName.CLAUDE
    current_backend_registry._loaded_from_disk = True
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.E2E_TEST_USER_ID = original_e2e
    current_backend_registry._current_backend = original_current_backend
    current_backend_registry._loaded_from_disk = original_current_backend_loaded


@pytest.fixture(autouse=True)
def _setup_application():
    """Устанавливает фейковый Application для agent-handler модуля."""
    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    original = bot_module._application
    bot_module._application = mock_app
    agent_handlers.init_callbacks(
        bot_module._get_application_for_handlers,
        bot_module._has_access_for_handlers,
    )
    yield mock_app
    bot_module._application = original
    bot_module._init_handler_callbacks()


def _make_update(
    text: str = "test",
    chat_id: int = TEST_CHAT_ID,
    user_id: int = ALLOWED_USER_ID,
) -> MagicMock:
    """Создаёт фейковый Update для agent-handler тестов."""
    update = MagicMock()
    update.message.text = text
    update.message.chat.id = chat_id
    update.message.chat_id = chat_id
    update.effective_chat.id = chat_id
    update.message.from_user.id = user_id
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


def _make_context() -> MagicMock:
    """Создаёт фейковый context для handler тестов."""
    context = MagicMock()
    context.bot = MagicMock()
    return context


def _make_callback_update(
    data: str,
    chat_id: int = TEST_CHAT_ID,
    user_id: int = ALLOWED_USER_ID,
) -> MagicMock:
    """Создаёт фейковый callback Update для inline-кнопок."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


class TestHandleAgent:
    """Тесты команды /agent."""

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_shows_current_backend(
        self,
        mock_get_current: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /agent показывает текущий backend."""
        mock_get_current.return_value = BackendName.CLAUDE

        update = _make_update(text="/agent")
        context = _make_context()
        await agent_handlers.handle_agent(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Текущий агент: 🤖 Claude" in sent_text

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_keyboard_marks_current_backend(
        self,
        mock_get_current: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Клавиатура /agent помечает текущий backend галочкой."""
        mock_get_current.return_value = BackendName.CLAUDE

        update = _make_update(text="/agent")
        context = _make_context()
        await agent_handlers.handle_agent(update, context)

        reply_markup = _setup_application.bot.send_message.call_args.kwargs[
            "reply_markup"
        ]
        buttons = [button for row in reply_markup.inline_keyboard for button in row]
        assert buttons[0].text == "✓ 🤖 Claude"
        assert buttons[0].callback_data == "agent:claude"
        assert buttons[1].text == "⚡ Codex"
        assert buttons[1].callback_data == "agent:codex"

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_active_session")
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(current_backend_registry, "set_current")
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_callback_switches_to_codex(
        self,
        mock_get_current: MagicMock,
        mock_set_current: MagicMock,
        mock_register_session: AsyncMock,
        mock_get_active: MagicMock,
    ) -> None:
        """Callback agent:codex переключает текущий backend на Codex."""
        mock_get_current.return_value = BackendName.CLAUDE
        mock_get_active.return_value = ActiveSession(
            TEST_SESSION_ID,
            BackendName.CLAUDE,
        )
        mock_register_session.return_value = 1

        update = _make_callback_update("agent:codex")
        context = _make_context()
        await agent_handlers.handle_agent_callback(update, context)

        mock_set_current.assert_called_once_with(BackendName.CODEX)
        update.callback_query.answer.assert_awaited_once()
        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "через ⚡ Codex" in sent_text
        assert "Текущая сессия #1 остаётся на 🤖 Claude" in sent_text

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "set_current")
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_callback_does_not_switch_when_already_current(
        self,
        mock_get_current: MagicMock,
        mock_set_current: MagicMock,
    ) -> None:
        """Повторный выбор текущего backend-а не пишет файл."""
        mock_get_current.return_value = BackendName.CODEX

        update = _make_callback_update("agent:codex")
        context = _make_context()
        await agent_handlers.handle_agent_callback(update, context)

        mock_set_current.assert_not_called()
        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert sent_text == "Уже выбран: ⚡ Codex."

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_active_session", return_value=None)
    @patch.object(current_backend_registry, "set_current")
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_callback_without_active_session_omits_current_session_line(
        self,
        mock_get_current: MagicMock,
        mock_set_current: MagicMock,
        _mock_get_active: MagicMock,
    ) -> None:
        """Без активной сессии подтверждение не упоминает текущую сессию."""
        mock_get_current.return_value = BackendName.CLAUDE

        update = _make_callback_update("agent:codex")
        context = _make_context()
        await agent_handlers.handle_agent_callback(update, context)

        mock_set_current.assert_called_once_with(BackendName.CODEX)
        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "Текущая сессия" not in sent_text
        assert "Чтобы начать новую сессию, отправьте /new." in sent_text

    @pytest.mark.asyncio()
    @patch.object(
        current_backend_registry,
        "set_current",
        side_effect=RuntimeError("state not loaded"),
    )
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_callback_handles_registry_runtime_error(
        self,
        mock_get_current: MagicMock,
        _mock_set_current: MagicMock,
    ) -> None:
        """RuntimeError из registry показывается пользователю."""
        mock_get_current.return_value = BackendName.CLAUDE

        update = _make_callback_update("agent:codex")
        context = _make_context()
        await agent_handlers.handle_agent_callback(update, context)

        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "Не удалось переключить агента: state not loaded" in sent_text

    @pytest.mark.asyncio()
    @patch.object(
        current_backend_registry,
        "set_current",
        side_effect=OSError("disk full"),
    )
    @patch.object(current_backend_registry, "get_current")
    async def test_handle_agent_callback_handles_oserror(
        self,
        mock_get_current: MagicMock,
        _mock_set_current: MagicMock,
    ) -> None:
        """OSError из registry показывается пользователю."""
        mock_get_current.return_value = BackendName.CLAUDE

        update = _make_callback_update("agent:codex")
        context = _make_context()
        await agent_handlers.handle_agent_callback(update, context)

        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "Не удалось переключить агента: disk full" in sent_text

    @pytest.mark.asyncio()
    async def test_handle_agent_callback_rejects_unknown_backend_value(self) -> None:
        """Неизвестное callback value отклоняется."""
        update = _make_callback_update("agent:gemini")
        context = _make_context()
        await agent_handlers.handle_agent_callback(update, context)

        update.callback_query.answer.assert_awaited_once_with(
            "Неизвестный агент",
            show_alert=True,
        )
        update.callback_query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "get_current")
    async def test_unauthorized_agent_command_ignored(
        self,
        mock_get_current: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Неавторизованный пользователь не получает клавиатуру /agent."""
        update = _make_update(text="/agent", user_id=DENIED_USER_ID)
        context = _make_context()
        await agent_handlers.handle_agent(update, context)

        mock_get_current.assert_not_called()
        _setup_application.bot.send_message.assert_not_called()
