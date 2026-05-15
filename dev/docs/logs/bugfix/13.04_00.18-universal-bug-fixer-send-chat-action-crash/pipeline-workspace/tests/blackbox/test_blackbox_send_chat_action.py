"""Blackbox-тесты для трёх отклонений: DEV-1, DEV-2, DEV-3.

Эти тесты скрыты от исполнителя — он не видит их содержимого на этапе 8.
Тесты проверяют поведение «снаружи», без знания внутренней реализации фикса.
"""

import asyncio
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

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
# DEV-1: Устойчивость обработчиков к сбоям API (blackbox)
# =============================================================================


class TestDev1MessageDeliveryResilience:
    """Сообщение доходит до Claude даже при сбое декоративных вызовов API."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_text_message_delivered_despite_api_timeout(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Пользователь отправил текст, API вернул TimedOut — ответ всё равно приходит."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Ну как ты?")
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(side_effect=TimedOut())

        await handle_message(update, context)

        mock_send_to_claude.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_photo_delivered_despite_api_timeout(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Пользователь отправил фото, API вернул TimedOut — фото уходит в Claude."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/screenshot.png"

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(side_effect=TimedOut())

        await handle_photo(update, context)

        mock_send_to_claude.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_document_delivered_despite_api_timeout(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Пользователь отправил файл, API вернул TimedOut — файл уходит в Claude."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/report.pdf"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(side_effect=TimedOut())

        await handle_document(update, context)

        mock_send_to_claude.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_no_exception_propagated_on_throttle(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """RetryAfter (throttling) не пробрасывается наружу обработчика."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Привет")
        context = _make_context()
        context.bot.send_chat_action = AsyncMock(side_effect=RetryAfter(30))

        # Не должно бросить исключение — обработчик ловит ошибку
        await handle_message(update, context)

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_multiple_error_types_all_handled(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Все типы ошибок Telegram API обрабатываются одинаково — сообщение доставляется."""
        mock_is_monitoring.return_value = False

        telegram_errors = [
            TimedOut(),
            NetworkError("Connection reset by peer"),
            RetryAfter(10),
        ]

        for error in telegram_errors:
            mock_send_to_claude.reset_mock()

            update = _make_update(text="Сообщение")
            context = _make_context()
            context.bot.send_chat_action = AsyncMock(side_effect=error)

            await handle_message(update, context)

            mock_send_to_claude.assert_called_once(), (
                f"Сообщение не доставлено при ошибке {type(error).__name__}"
            )


class TestDev1NormalFlow:
    """Обычный поток работы не сломан фиксом."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_chat_action_still_called_on_success(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Индикатор 'печатает...' по-прежнему вызывается при нормальной работе."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Обычное сообщение")
        context = _make_context()

        await handle_message(update, context)

        # send_chat_action по-прежнему вызывается
        context.bot.send_chat_action.assert_called_once_with(
            TEST_CHAT_ID, ChatAction.TYPING
        )
        # Сообщение уходит в Claude
        mock_send_to_claude.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_monitoring_mode_still_blocks_messages(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Режим мониторинга по-прежнему блокирует отправку сообщений."""
        mock_is_monitoring.return_value = True

        update = _make_update(text="Сообщение в режиме мониторинга")
        context = _make_context()

        await handle_message(update, context)

        mock_send_to_claude.assert_not_called()


# =============================================================================
# DEV-2: Глобальный error handler (blackbox)
# =============================================================================


class TestDev2GlobalErrorHandler:
    """Бот имеет глобальный error handler как последний рубеж."""

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_error_handler_registered(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """setup_bot регистрирует error handler через add_error_handler."""
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

        assert mock_app.add_error_handler.called, (
            "setup_bot не зарегистрировал глобальный error handler"
        )

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_error_handler_is_async_callable(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """Error handler — вызываемая функция (async)."""
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

        if mock_app.add_error_handler.called:
            handler = mock_app.add_error_handler.call_args[0][0]
            assert callable(handler), (
                f"Error handler не callable: {type(handler)}"
            )
            # Проверяем, что handler — корутина (async функция)
            assert asyncio.iscoroutinefunction(handler), (
                "Error handler должен быть async функцией"
            )


# =============================================================================
# DEV-3: Спам в логах из-за несуществующих сессий (blackbox)
# =============================================================================


class TestDev3LogSpamPrevention:
    """Watcher не спамит логами при обращении к удалённым сессиям."""

    @pytest.mark.asyncio()
    async def test_missing_session_file_no_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Чтение несуществующей сессии не порождает WARNING-записи в логе.

        Когда JSONL-файла нет — это ожидаемая ситуация для watcher (сессия
        удалена), а не ошибка. Запись должна быть на уровне DEBUG.
        """
        with caplog.at_level(logging.DEBUG, logger="claude_manager.session_reader"):
            await session_reader.get_session_messages(
                "deleted-session-70ca7205", str(tmp_path)
            )

        # Не должно быть WARNING-записей с текстом про отсутствие файла
        warning_about_missing = [
            record for record in caplog.records
            if record.levelno >= logging.WARNING
            and ("не найден" in record.message.lower() or "not found" in record.message.lower())
        ]

        assert len(warning_about_missing) == 0, (
            f"Обнаружено {len(warning_about_missing)} WARNING о несуществующем файле — "
            "должен быть DEBUG. Это вызывает ~1 МБ мусора в error.log "
            "при 3 удалённых сессиях и цикле опроса каждые 2 секунды."
        )

    @pytest.mark.asyncio()
    async def test_missing_session_file_returns_empty_list(
        self, tmp_path: Path
    ) -> None:
        """Чтение несуществующей сессии возвращает пустой список (без исключения)."""
        result = await session_reader.get_session_messages(
            "nonexistent-session", str(tmp_path)
        )
        assert result == []

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "get_all_today_sessions", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    async def test_watcher_filters_nonexistent_sessions(
        self,
        mock_recent: AsyncMock,
        mock_today: AsyncMock,
    ) -> None:
        """_get_sessions_to_monitor фильтрует сессии, чьих файлов нет на диске.

        Если реестр содержит session_id удалённой сессии, он не должен попадать
        в список мониторинга — иначе каждый цикл watcher будет безуспешно
        пытаться прочитать файл.
        """
        real_session = MagicMock()
        real_session.session_id = "real-session"
        mock_recent.return_value = [real_session]

        # Удалённые сессии в реестре — файлов на диске нет
        mock_today.return_value = {
            1: "deleted-70ca7205",
            2: "deleted-669ddf67",
            3: "deleted-d2c6a9c3",
        }

        from claude_manager.session_watcher import _get_sessions_to_monitor
        result = await _get_sessions_to_monitor()

        # Реальная сессия должна быть в списке
        assert "real-session" in result

        # После фикса: удалённые сессии не должны попадать в список.
        # Если они там есть — watcher будет спамить логами.
        deleted_in_result = [
            sid for sid in result
            if sid.startswith("deleted-")
        ]
        assert len(deleted_in_result) == 0, (
            f"_get_sessions_to_monitor вернул {len(deleted_in_result)} удалённых сессий "
            f"({deleted_in_result}). Это вызовет спам при каждом цикле watcher."
        )
