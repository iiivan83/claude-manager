"""Whitebox-тесты для трёх отклонений: DEV-1, DEV-2, DEV-3.

DEV-1: send_chat_action без try/except роняет обработчики при TimedOut.
DEV-2: Нет глобального error handler (add_error_handler не вызывается).
DEV-3: session_watcher спамит error.log — нет валидации файлов, warning вместо debug.
"""

import asyncio
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ChatAction
from telegram.error import NetworkError, RetryAfter, TimedOut

from claude_manager import (
    daily_session_registry,
    session_manager,
    session_reader,
    session_watcher,
)
from claude_manager.bot import (
    handle_document,
    handle_message,
    handle_photo,
    setup_bot,
)
import claude_manager.bot as bot_module
import claude_manager.config as config_module


# --- Константы ---

ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "abc-def-111"


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
    mock_app.bot.get_file = AsyncMock()
    mock_app.bot.send_chat_action = AsyncMock()
    mock_app.bot.set_my_commands = AsyncMock()
    original = bot_module._application
    bot_module._application = mock_app
    yield mock_app
    bot_module._application = original


@pytest.fixture(autouse=True)
def _reset_watcher_state():
    """Сбрасывает внутреннее состояние watcher перед каждым тестом."""
    session_watcher._seen_message_counts = {}
    session_watcher._paused_sessions = set()
    yield
    session_watcher._seen_message_counts = {}
    session_watcher._paused_sessions = set()


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


def _make_context() -> MagicMock:
    """Создаёт фейковый context для обработчиков."""
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


# =============================================================================
# DEV-1: send_chat_action без try/except роняет обработчики
# =============================================================================


class TestDev1HandleMessageCrash:
    """DEV-1: handle_message падает при TimedOut в send_chat_action."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_crashes_on_timed_out(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """TimedOut при send_chat_action прерывает handle_message.

        Ожидаемый результат после фикса: _send_to_claude_and_respond
        вызывается несмотря на ошибку в send_chat_action.
        """
        mock_is_monitoring.return_value = False

        update = _make_update(text="Ну как ты?")
        context = _make_context()
        # send_chat_action бросает TimedOut — имитация перегруженного API
        context.bot.send_chat_action = AsyncMock(side_effect=TimedOut())

        # До фикса: handle_message упадёт, _send_to_claude_and_respond не вызовется.
        # После фикса: ошибка ловится, _send_to_claude_and_respond вызывается.
        try:
            await handle_message(update, context)
        except TimedOut:
            # Если исключение пробросилось наружу — баг ещё не исправлен
            pytest.fail(
                "handle_message не поймал TimedOut от send_chat_action — "
                "_send_to_claude_and_respond не вызвана, пользователь не получит ответа"
            )

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Ну как ты?")

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_crashes_on_network_error(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """NetworkError при send_chat_action тоже прерывает обработчик."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Привет")
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(
            side_effect=NetworkError("Connection reset")
        )

        try:
            await handle_message(update, context)
        except NetworkError:
            pytest.fail(
                "handle_message не поймал NetworkError от send_chat_action"
            )

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Привет")

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_crashes_on_retry_after(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """RetryAfter (throttling) при send_chat_action тоже роняет обработчик."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Тест")
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(
            side_effect=RetryAfter(30)
        )

        try:
            await handle_message(update, context)
        except RetryAfter:
            pytest.fail(
                "handle_message не поймал RetryAfter от send_chat_action"
            )

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Тест")


class TestDev1HandlePhotoCrash:
    """DEV-1: handle_photo падает при TimedOut в send_chat_action."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_crashes_on_timed_out(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """TimedOut при send_chat_action прерывает handle_photo.

        Фото уже скачано, но ответ не отправится в Claude.
        """
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/photo.jpg"

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(side_effect=TimedOut())

        try:
            await handle_photo(update, context)
        except TimedOut:
            pytest.fail(
                "handle_photo не поймал TimedOut от send_chat_action — "
                "фото скачано, но не отправлено в Claude"
            )

        mock_download.assert_called_once()
        mock_send_to_claude.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_crashes_on_network_error(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """NetworkError при send_chat_action прерывает handle_photo."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/photo.jpg"

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(
            side_effect=NetworkError("Connection reset")
        )

        try:
            await handle_photo(update, context)
        except NetworkError:
            pytest.fail(
                "handle_photo не поймал NetworkError от send_chat_action"
            )

        mock_send_to_claude.assert_called_once()


class TestDev1HandleDocumentCrash:
    """DEV-1: handle_document падает при TimedOut в send_chat_action."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_crashes_on_timed_out(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """TimedOut при send_chat_action прерывает handle_document."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/report.pdf"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(side_effect=TimedOut())

        try:
            await handle_document(update, context)
        except TimedOut:
            pytest.fail(
                "handle_document не поймал TimedOut от send_chat_action — "
                "документ скачан, но не отправлен в Claude"
            )

        mock_download.assert_called_once()
        mock_send_to_claude.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_crashes_on_retry_after(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """RetryAfter при send_chat_action прерывает handle_document."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/data.csv"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "data.csv"
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(
            side_effect=RetryAfter(60)
        )

        try:
            await handle_document(update, context)
        except RetryAfter:
            pytest.fail(
                "handle_document не поймал RetryAfter от send_chat_action"
            )

        mock_send_to_claude.assert_called_once()


class TestDev1SuccessfulSendChatAction:
    """DEV-1: при успешном send_chat_action логика не ломается."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_works_when_chat_action_succeeds(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Обычный сценарий: send_chat_action проходит, сообщение уходит в Claude."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Всё работает")
        context = _make_context()

        await handle_message(update, context)

        context.bot.send_chat_action.assert_called_once_with(
            TEST_CHAT_ID, ChatAction.TYPING
        )
        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "Всё работает")


# =============================================================================
# DEV-2: Нет глобального error handler
# =============================================================================


class TestDev2NoErrorHandler:
    """DEV-2: setup_bot не регистрирует add_error_handler."""

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_setup_bot_has_no_error_handler(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """setup_bot не вызывает add_error_handler — нет safety net.

        После фикса: add_error_handler должен быть вызван хотя бы один раз.
        """
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

        # После фикса add_error_handler должен быть вызван
        assert mock_app.add_error_handler.call_count >= 1, (
            "setup_bot() не вызывает add_error_handler — необработанные "
            "исключения в любом обработчике будут проглочены молча"
        )

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_error_handler_receives_callable(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """Глобальный error handler должен быть callable (async функция).

        Проверяет, что add_error_handler вызван с вызываемым аргументом,
        а не с None или строкой.
        """
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

        if mock_app.add_error_handler.call_count >= 1:
            handler_arg = mock_app.add_error_handler.call_args[0][0]
            assert callable(handler_arg), (
                "add_error_handler вызван с не-callable аргументом: "
                f"{type(handler_arg)}"
            )


# =============================================================================
# DEV-3: session_watcher спамит error.log
# =============================================================================


class TestDev3WatcherLogSpam:
    """DEV-3: _get_sessions_to_monitor не валидирует файлы, session_reader спамит warning."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "get_all_today_sessions", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    async def test_get_sessions_includes_nonexistent_session_files(
        self,
        mock_recent: AsyncMock,
        mock_today: AsyncMock,
    ) -> None:
        """_get_sessions_to_monitor включает ID из реестра без проверки файлов.

        Реестр содержит session_id удалённой сессии — функция возвращает его
        в списке, хотя JSONL-файла на диске нет.
        """
        # Реальная сессия на диске
        real_session = MagicMock()
        real_session.session_id = "real-session-on-disk"
        mock_recent.return_value = [real_session]

        # Реестр содержит удалённую сессию (файла нет на диске)
        mock_today.return_value = {1: "deleted-session-no-file"}

        from claude_manager.session_watcher import _get_sessions_to_monitor
        result = await _get_sessions_to_monitor()

        # Оба ID включены — _get_sessions_to_monitor не фильтрует
        assert "real-session-on-disk" in result
        assert "deleted-session-no-file" in result

    @pytest.mark.asyncio()
    async def test_session_reader_logs_warning_for_missing_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """session_reader.get_session_messages логирует warning для несуществующего файла.

        После фикса: должен логировать debug вместо warning (для watcher это
        ожидаемая ситуация, а не проблема).
        """
        with caplog.at_level(logging.DEBUG, logger="claude_manager.session_reader"):
            messages = await session_reader.get_session_messages(
                "nonexistent-session-id", str(tmp_path)
            )

        assert messages == []

        # Проверяем, что предупреждение было залогировано
        warning_records = [
            record for record in caplog.records
            if "не найден" in record.message.lower() or "not found" in record.message.lower()
        ]

        # После фикса: warning должен стать debug
        # Сейчас записывается как WARNING — это и есть проблема спама
        if warning_records:
            has_warning_level = any(
                record.levelno == logging.WARNING for record in warning_records
            )
            assert not has_warning_level, (
                "get_session_messages логирует WARNING для отсутствующего файла — "
                "это вызывает спам в error.log при каждом цикле watcher (каждые 2 сек). "
                "Должен быть DEBUG."
            )

    @pytest.mark.asyncio()
    @patch.object(session_reader, "get_session_messages", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "get_all_today_sessions", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    async def test_check_session_calls_reader_for_deleted_session(
        self,
        mock_recent: AsyncMock,
        mock_today: AsyncMock,
        mock_get_messages: AsyncMock,
    ) -> None:
        """_check_session вызывает session_reader для каждого ID, включая удалённые.

        Это корень проблемы спама: watcher не фильтрует несуществующие файлы
        перед вызовом session_reader.
        """
        deleted_session_id = "deleted-70ca7205"
        mock_get_messages.return_value = []

        # Инициализируем _seen_message_counts, чтобы _check_session мог работать
        session_watcher._seen_message_counts[deleted_session_id] = 0

        from claude_manager.session_watcher import _check_session
        await _check_session(deleted_session_id)

        # session_reader.get_session_messages вызывается для удалённой сессии
        mock_get_messages.assert_called_once_with(
            deleted_session_id, config_module.WORKING_DIR
        )
