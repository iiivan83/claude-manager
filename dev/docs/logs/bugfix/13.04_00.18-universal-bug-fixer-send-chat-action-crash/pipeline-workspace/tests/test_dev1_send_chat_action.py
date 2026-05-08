"""Whitebox-тесты DEV-1: send_chat_action без try/except в обработчиках.

Проверяют, что при ошибке send_chat_action (TimedOut, NetworkError)
обработчик НЕ падает и _send_to_claude_and_respond вызывается.
Сейчас (до фикса) тесты КРАСНЫЕ — обработчик пробрасывает исключение.
После фикса тесты станут ЗЕЛЁНЫМИ.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.constants import ChatAction
from telegram.error import NetworkError, TimedOut

from claude_manager import session_manager
import claude_manager.bot as bot_module
import claude_manager.config as config_module
from claude_manager.bot import handle_document, handle_message, handle_photo


# --- Константы ---

ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


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


def _make_context_with_failing_chat_action(
    error: Exception,
) -> MagicMock:
    """Создаёт context, чей send_chat_action бросает указанную ошибку."""
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock(side_effect=error)
    return context


# --- handle_message: send_chat_action(TimedOut) ---


class TestHandleMessageChatActionResilience:
    """handle_message должен продолжать работу при ошибке send_chat_action."""

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_timed_out_does_not_block_claude_call(
        self,
        _mock_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """TimedOut на send_chat_action не должен мешать вызову Claude."""
        update = _make_update(text="Привет")
        context = _make_context_with_failing_chat_action(TimedOut())

        await handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Привет")

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_network_error_does_not_block_claude_call(
        self,
        _mock_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """NetworkError на send_chat_action не должен мешать вызову Claude."""
        update = _make_update(text="Тест")
        context = _make_context_with_failing_chat_action(
            NetworkError("Connection reset")
        )

        await handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Тест")


# --- handle_photo: send_chat_action(TimedOut) ---


class TestHandlePhotoChatActionResilience:
    """handle_photo должен продолжать работу при ошибке send_chat_action."""

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_timed_out_does_not_block_photo_processing(
        self,
        _mock_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """TimedOut на send_chat_action не должен мешать обработке фото."""
        mock_download.return_value = "/tmp/received_files/photo.jpg"
        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context_with_failing_chat_action(TimedOut())

        await handle_photo(update, context)

        mock_send_to_claude.assert_called_once()

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_network_error_does_not_block_photo_processing(
        self,
        _mock_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """NetworkError на send_chat_action не должен мешать обработке фото."""
        mock_download.return_value = "/tmp/received_files/photo.jpg"
        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context_with_failing_chat_action(
            NetworkError("Connection reset")
        )

        await handle_photo(update, context)

        mock_send_to_claude.assert_called_once()


# --- handle_document: send_chat_action(TimedOut) ---


class TestHandleDocumentChatActionResilience:
    """handle_document должен продолжать работу при ошибке send_chat_action."""

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_timed_out_does_not_block_document_processing(
        self,
        _mock_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """TimedOut на send_chat_action не должен мешать обработке документа."""
        mock_download.return_value = "/tmp/received_files/report.pdf"
        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context_with_failing_chat_action(TimedOut())

        await handle_document(update, context)

        mock_send_to_claude.assert_called_once()

    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode", return_value=False)
    async def test_network_error_does_not_block_document_processing(
        self,
        _mock_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """NetworkError на send_chat_action не должен мешать обработке документа."""
        mock_download.return_value = "/tmp/received_files/report.pdf"
        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context_with_failing_chat_action(
            NetworkError("Connection reset")
        )

        await handle_document(update, context)

        mock_send_to_claude.assert_called_once()
