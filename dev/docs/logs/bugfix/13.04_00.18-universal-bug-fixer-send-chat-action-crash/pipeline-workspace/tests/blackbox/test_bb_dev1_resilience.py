"""Blackbox-тесты DEV-1: обработчики устойчивы к ошибкам Telegram API.

Проверяют поведение с точки зрения пользователя: при любой ошибке
Telegram API на декоративных вызовах обработчик доставляет сообщение
в Claude. Сейчас (до фикса) тесты КРАСНЫЕ. После фикса — ЗЕЛЁНЫЕ.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import RetryAfter, TimedOut

from claude_manager import session_manager
import claude_manager.bot as bot_module
import claude_manager.config as config_module
from claude_manager.bot import handle_document, handle_message, handle_photo


# --- Константы ---

ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345

# Время ожидания перед повтором при RetryAfter (Telegram throttling)
RETRY_AFTER_SECONDS = 30


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для всех тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_working_dir = config_module.WORKING_DIR
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.WORKING_DIR = "/tmp/test_working_dir"
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.WORKING_DIR = original_working_dir


@pytest.fixture(autouse=True)
def _setup_application():
    """Устанавливает фейковый Application для bot модуля."""
    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    mock_app.bot.send_chat_action = AsyncMock()
    original = bot_module._application
    bot_module._application = mock_app
    yield mock_app
    bot_module._application = original


def _make_update(
    text: str = "test",
    chat_id: int = TEST_CHAT_ID,
    user_id: int = ALLOWED_USER_ID,
) -> MagicMock:
    """Создаёт фейковый Update для тестов."""
    update = MagicMock()
    update.message.text = text
    update.message.chat.id = chat_id
    update.message.chat_id = chat_id
    update.effective_chat.id = chat_id
    update.message.from_user.id = user_id
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    update.message.photo = None
    update.message.document = None
    return update


# --- RetryAfter (Telegram throttling) ---


class TestRetryAfterResilience:
    """Обработчики не падают при Telegram throttling (RetryAfter)."""

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_handle_message_survives_retry_after(
        self,
        _mock_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """handle_message доставляет сообщение в Claude при RetryAfter."""
        update = _make_update(text="Проверь")
        context = MagicMock()
        context.bot = MagicMock()
        context.bot.send_chat_action = AsyncMock(
            side_effect=RetryAfter(RETRY_AFTER_SECONDS)
        )

        await handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Проверь")

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_handle_photo_survives_retry_after(
        self,
        _mock_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """handle_photo доставляет фото в Claude при RetryAfter."""
        mock_download.return_value = "/tmp/received_files/photo.jpg"
        update = _make_update()
        update.message.photo = [MagicMock()]
        context = MagicMock()
        context.bot = MagicMock()
        context.bot.send_chat_action = AsyncMock(
            side_effect=RetryAfter(RETRY_AFTER_SECONDS)
        )

        await handle_photo(update, context)

        mock_send_to_claude.assert_called_once()

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_handle_document_survives_retry_after(
        self,
        _mock_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """handle_document доставляет документ в Claude при RetryAfter."""
        mock_download.return_value = "/tmp/received_files/doc.pdf"
        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "doc.pdf"
        context = MagicMock()
        context.bot = MagicMock()
        context.bot.send_chat_action = AsyncMock(
            side_effect=RetryAfter(RETRY_AFTER_SECONDS)
        )

        await handle_document(update, context)

        mock_send_to_claude.assert_called_once()


# --- Общая ошибка Exception ---


class TestGenericExceptionResilience:
    """Обработчики не падают при произвольном Exception от send_chat_action."""

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_handle_message_survives_generic_exception(
        self,
        _mock_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """handle_message доставляет сообщение при любом Exception."""
        update = _make_update(text="Тест")
        context = MagicMock()
        context.bot = MagicMock()
        context.bot.send_chat_action = AsyncMock(
            side_effect=Exception("Unexpected API error")
        )

        await handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Тест")
