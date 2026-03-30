"""Тесты модуля bot — транспортный слой Telegram-бота."""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from claude_manager import (
    daily_session_registry,
    process_manager,
    session_manager,
    session_reader,
    session_watcher,
)
from claude_manager.bot import (
    BOT_COMMANDS,
    EMPTY_RESPONSE_TEXT,
    IMAGE_EXTENSIONS,
    MONITORING_MODE_MESSAGE,
    NO_RESPONSE_MARKER,
    RECEIVED_FILES_DIR,
    RECEIVED_FILES_MAX_AGE_DAYS,
    SECONDS_PER_DAY,
    SEND_RETRY_COUNT,
    _build_file_task,
    _check_access,
    _clean_old_received_files,
    _format_clickable_session_number,
    _format_session_header,
    _generate_file_name,
    _is_current_session,
    _send_telegram_message,
    handle_all,
    handle_document,
    handle_message,
    handle_new,
    handle_photo,
    handle_sessions,
    handle_stop,
    handle_switch_session,
    post_init,
    send_response,
    send_watcher_message,
    setup_bot,
)
import claude_manager.bot as bot_module
import claude_manager.config as config_module
from claude_manager.process_manager import (
    ProcessManagerError,
    ProcessNotFoundError,
    ProcessStoppedError,
    SendResult,
    StopResult,
)
from claude_manager.session_manager import NewSessionResult, SwitchResult
from claude_manager.session_reader import SessionInfo


# --- Фикстуры ---


ALLOWED_USER_ID = 12345
DENIED_USER_ID = 99999
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "abc-def-111"
TEST_SESSION_ID_2 = "abc-def-222"


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


# --- Тесты доступа ---


class TestCheckAccess:
    """Тесты проверки доступа пользователей."""

    def test_check_access_allowed_user(self) -> None:
        """Разрешённый пользователь проходит проверку."""
        update = _make_update(user_id=ALLOWED_USER_ID)
        assert _check_access(update) is True

    def test_check_access_denied_user(self) -> None:
        """Неразрешённый пользователь отклоняется."""
        update = _make_update(user_id=DENIED_USER_ID)
        assert _check_access(update) is False


# --- Тесты форматирования ---


class TestFormatSessionHeader:
    """Тесты формата заголовков сессий."""

    def test_format_session_header_final(self) -> None:
        """Финальный ответ получает галочку."""
        result = _format_session_header(3, is_final=True)
        assert result == "#3 \u2705 "

    def test_format_session_header_intermediate(self) -> None:
        """Промежуточное обновление получает песочные часы."""
        result = _format_session_header(5, is_final=False)
        assert result == "#5 \u23f3 "


class TestFormatClickableSessionNumber:
    """Тесты формата кликабельных номеров сессий."""

    def test_format_clickable_session_number(self) -> None:
        """Кликабельный номер содержит HTML-ссылку."""
        result = _format_clickable_session_number(3)
        assert "<a href=" in result
        assert "/3" in result
        assert "#3" in result


# --- Тесты задания для Claude ---


class TestBuildFileTask:
    """Тесты формирования текстовых заданий для Claude."""

    def test_build_file_task_with_caption(self) -> None:
        """Задание с подписью включает текст подписи."""
        result = _build_file_task(
            "/tmp/received_files/file_20260328_143022_abc123.jpg",
            "Что здесь не так?",
            is_image=True,
        )
        assert "подписью" in result
        assert "Что здесь не так?" in result
        assert "/tmp/received_files/file_20260328_143022_abc123.jpg" in result
        assert "Прочитай файл" in result

    def test_build_file_task_image_no_caption(self) -> None:
        """Задание для фото без подписи предлагает описать содержимое."""
        result = _build_file_task("/tmp/photo.jpg", None, is_image=True)
        assert "фотографию без подписи" in result
        assert "/tmp/photo.jpg" in result
        assert "опиши, что на фотографии" in result

    def test_build_file_task_document_no_caption(self) -> None:
        """Задание для документа без подписи предлагает описать содержимое."""
        result = _build_file_task("/tmp/report.pdf", None, is_image=False)
        assert "файл без подписи" in result
        assert "/tmp/report.pdf" in result
        assert "опиши его содержимое" in result


# --- Тесты генерации имён файлов ---


class TestGenerateFileName:
    """Тесты генерации уникальных имён файлов."""

    def test_generate_file_name_format(self) -> None:
        """Имя файла соответствует формату file_YYYYMMDD_HHMMSS_XXXXXX.ext."""
        result = _generate_file_name("photo.jpg", "jpg")
        assert result.startswith("file_")
        assert result.endswith(".jpg")
        # Формат: file_YYYYMMDD_HHMMSS_XXXXXX.jpg — длина фиксирована
        parts = result.removesuffix(".jpg").split("_")
        # ["file", "YYYYMMDD", "HHMMSS", "XXXXXX"]
        assert len(parts) == 4
        assert len(parts[3]) == 6  # случайный суффикс

    def test_generate_file_name_unique(self) -> None:
        """Два вызова генерируют разные имена (случайный суффикс)."""
        name_first = _generate_file_name("test.txt", "txt")
        name_second = _generate_file_name("test.txt", "txt")
        assert name_first != name_second


# --- Тесты определения текущей сессии ---


class TestIsCurrentSession:
    """Тесты определения текущей сессии чата."""

    @patch.object(session_manager, "get_bound_session")
    def test_is_current_session_true(self, mock_get: MagicMock) -> None:
        """Возвращает True когда сессия совпадает с привязанной."""
        mock_get.return_value = TEST_SESSION_ID
        assert _is_current_session(TEST_CHAT_ID, TEST_SESSION_ID) is True

    @patch.object(session_manager, "get_bound_session")
    def test_is_current_session_false(self, mock_get: MagicMock) -> None:
        """Возвращает False когда сессия не совпадает."""
        mock_get.return_value = TEST_SESSION_ID
        assert _is_current_session(TEST_CHAT_ID, TEST_SESSION_ID_2) is False

    @patch.object(session_manager, "get_bound_session")
    def test_is_current_session_no_binding(self, mock_get: MagicMock) -> None:
        """Возвращает False когда нет привязки."""
        mock_get.return_value = None
        assert _is_current_session(TEST_CHAT_ID, TEST_SESSION_ID) is False


# --- Тесты обработчиков команд ---


class TestHandleNew:
    """Тесты команды /new."""

    @pytest.mark.asyncio()
    @patch.object(session_manager, "create_new_session", new_callable=AsyncMock)
    async def test_handle_new_creates_session(
        self,
        mock_create_session: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /new создаёт сессию и отправляет подтверждение."""
        mock_create_session.return_value = NewSessionResult(
            session_id="_new_0001", day_number=1
        )

        update = _make_update(text="/new")
        context = _make_context()
        await handle_new(update, context)

        mock_create_session.assert_called_once_with(TEST_CHAT_ID)

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1] if len(sent.call_args[0]) > 1 else "")
        assert "#1" in sent_text

    @pytest.mark.asyncio()
    async def test_handle_new_denied_user(
        self, _setup_application: MagicMock
    ) -> None:
        """Неавторизованный пользователь не может создать сессию."""
        update = _make_update(text="/new", user_id=DENIED_USER_ID)
        context = _make_context()
        await handle_new(update, context)
        _setup_application.bot.send_message.assert_not_called()


class TestHandleSessions:
    """Тесты команды /sessions."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    async def test_handle_sessions_shows_list(
        self,
        mock_get_sessions: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions показывает список сессий."""
        mock_get_sessions.return_value = [
            SessionInfo("id-1", "2026-03-30T10:00:00", "Первая сессия"),
            SessionInfo("id-2", "2026-03-30T11:00:00", "Вторая сессия"),
            SessionInfo("id-3", "2026-03-30T12:00:00", "Третья сессия"),
        ]
        # Каждый вызов register_session возвращает номер по порядку
        mock_register.side_effect = [1, 2, 3]

        update = _make_update(text="/sessions")
        context = _make_context()
        await handle_sessions(update, context)

        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "/1" in sent_text
        assert "/2" in sent_text
        assert "/3" in sent_text
        # parse_mode=None чтобы /1 были кликабельными
        assert sent.call_args[1].get("parse_mode") is None

    @pytest.mark.asyncio()
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    async def test_handle_sessions_empty(
        self,
        mock_get_sessions: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Пустой список сессий."""
        mock_get_sessions.return_value = []

        update = _make_update(text="/sessions")
        context = _make_context()
        await handle_sessions(update, context)

        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Нет сессий" in sent_text


class TestHandleStop:
    """Тесты команды /stop."""

    @pytest.mark.asyncio()
    @patch.object(process_manager, "stop_process", new_callable=AsyncMock)
    @patch.object(process_manager, "has_process")
    @patch.object(session_manager, "get_bound_session")
    async def test_handle_stop_stops_process(
        self,
        mock_get_bound: MagicMock,
        mock_has_process: MagicMock,
        mock_stop: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /stop останавливает Claude."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_has_process.return_value = True
        mock_stop.return_value = StopResult(was_running=True, was_retrying=False)

        update = _make_update(text="/stop")
        context = _make_context()
        await handle_stop(update, context)

        mock_stop.assert_called_once_with(TEST_SESSION_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "остановлен" in sent_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_handle_stop_in_all_mode(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /stop в режиме /all — предупреждение."""
        mock_get_bound.return_value = None

        update = _make_update(text="/stop")
        context = _make_context()
        await handle_stop(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "только внутри сессии" in sent_text

    @pytest.mark.asyncio()
    @patch.object(process_manager, "has_process")
    @patch.object(session_manager, "get_bound_session")
    async def test_handle_stop_claude_not_running(
        self,
        mock_get_bound: MagicMock,
        mock_has_process: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /stop когда Claude не работает."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_has_process.return_value = False

        update = _make_update(text="/stop")
        context = _make_context()
        await handle_stop(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "не работает" in sent_text


class TestHandleAll:
    """Тесты команды /all."""

    @pytest.mark.asyncio()
    @patch.object(session_manager, "unbind_session", new_callable=AsyncMock)
    async def test_handle_all_switches_to_monitoring(
        self,
        mock_unbind: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /all переводит в режим мониторинга."""
        update = _make_update(text="/all")
        context = _make_context()
        await handle_all(update, context)

        mock_unbind.assert_called_once_with(TEST_CHAT_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()


class TestHandleSwitchSession:
    """Тесты переключения на сессию по номеру."""

    @pytest.mark.asyncio()
    @patch.object(session_manager, "switch_to_session", new_callable=AsyncMock)
    async def test_handle_switch_session_connects(
        self,
        mock_switch: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Переключение /3 подключает к сессии."""
        mock_switch.return_value = SwitchResult(
            found=True,
            session_id=TEST_SESSION_ID,
            day_number=3,
            preview="Первая сессия",
        )

        update = _make_update(text="/3")
        context = _make_context()
        await handle_switch_session(update, context)

        mock_switch.assert_called_once_with(TEST_CHAT_ID, 3)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "#3" in sent_text
        assert "Подключён" in sent_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "switch_to_session", new_callable=AsyncMock)
    async def test_handle_switch_session_not_found(
        self,
        mock_switch: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Переключение на несуществующую сессию."""
        mock_switch.return_value = SwitchResult(
            found=False, session_id="", day_number=99, preview=""
        )

        update = _make_update(text="/99")
        context = _make_context()
        await handle_switch_session(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "#99" in sent_text
        assert "не найдена" in sent_text


class TestHandleMessage:
    """Тесты обработки текстовых сообщений."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_sends_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Текстовое сообщение отправляется в Claude."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Посмотри файл main.py")
        context = _make_context()
        await handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(
            TEST_CHAT_ID, "Посмотри файл main.py"
        )

    @pytest.mark.asyncio()
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_in_all_mode(
        self,
        mock_is_monitoring: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Сообщение в режиме /all — предупреждение."""
        mock_is_monitoring.return_value = True

        update = _make_update(text="Привет")
        context = _make_context()
        await handle_message(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_typing_indicator_shown(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Индикатор 'печатает...' включается перед отправкой в Claude."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="Тест")
        context = _make_context()
        await handle_message(update, context)

        context.bot.send_chat_action.assert_called_once_with(
            TEST_CHAT_ID, ChatAction.TYPING
        )

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_text_commands_sent_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Текстовые слова 'стоп' отправляются как обычные сообщения."""
        mock_is_monitoring.return_value = False

        update = _make_update(text="стоп")
        context = _make_context()
        await handle_message(update, context)

        mock_send_to_claude.assert_called_once_with(TEST_CHAT_ID, "стоп")


# --- Тесты обработки фото и документов ---


class TestHandlePhoto:
    """Тесты обработки фотографий."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_sends_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Фото скачивается и отправляется в Claude."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/photo.jpg"

        update = _make_update()
        update.message.photo = [MagicMock()]  # Хотя бы один PhotoSize
        context = _make_context()
        await handle_photo(update, context)

        mock_download.assert_called_once()
        mock_send_to_claude.assert_called_once()
        task_text = mock_send_to_claude.call_args[0][1]
        assert "/tmp/received_files/photo.jpg" in task_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_in_all_mode(
        self,
        mock_is_monitoring: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Фото в режиме /all — предупреждение."""
        mock_is_monitoring.return_value = True

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        await handle_photo(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()


class TestHandleDocument:
    """Тесты обработки документов."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_sends_to_claude(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Документ скачивается и отправляется в Claude."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/report.pdf"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "report.pdf"
        context = _make_context()
        await handle_document(update, context)

        mock_download.assert_called_once()
        mock_send_to_claude.assert_called_once()
        task_text = mock_send_to_claude.call_args[0][1]
        assert "/tmp/received_files/report.pdf" in task_text

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot._download_and_save_file", new_callable=AsyncMock)
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_image_by_extension(
        self,
        mock_is_monitoring: MagicMock,
        mock_download: AsyncMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Документ с расширением изображения определяется как изображение."""
        mock_is_monitoring.return_value = False
        mock_download.return_value = "/tmp/received_files/screenshot.png"

        update = _make_update()
        update.message.document = MagicMock()
        update.message.document.file_name = "screenshot.png"
        context = _make_context()
        await handle_document(update, context)

        mock_send_to_claude.assert_called_once()
        task_text = mock_send_to_claude.call_args[0][1]
        # Для изображения должен быть текст про фотографию
        assert "фотографию" in task_text or "подписью" in task_text or "изображени" in task_text


# --- Тесты send_response ---


class TestSendResponse:
    """Тесты форматирования и отправки ответов."""

    @pytest.mark.asyncio()
    async def test_send_response_formats_html(
        self, _setup_application: MagicMock
    ) -> None:
        """Ответ конвертируется в HTML и отправляется."""
        await send_response(TEST_CHAT_ID, "**Ответ** Claude", 3, is_final=True)

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "#3" in sent_text

    @pytest.mark.asyncio()
    async def test_send_response_empty_text(
        self, _setup_application: MagicMock
    ) -> None:
        """Пустой текст заменяется на информативное сообщение."""
        await send_response(TEST_CHAT_ID, "", 1, is_final=True)

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert EMPTY_RESPONSE_TEXT in sent_text

    @pytest.mark.asyncio()
    async def test_send_response_no_response_marker(
        self, _setup_application: MagicMock
    ) -> None:
        """Служебный маркер заменяется на информативное сообщение."""
        await send_response(
            TEST_CHAT_ID, NO_RESPONSE_MARKER, 1, is_final=True
        )

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert EMPTY_RESPONSE_TEXT in sent_text


# --- Тесты send_watcher_message ---


class TestSendWatcherMessage:
    """Тесты отправки сообщений от watcher."""

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_send_watcher_message_current_session(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Сообщение из текущей сессии — без кликабельной ссылки."""
        mock_get_bound.return_value = TEST_SESSION_ID

        await send_watcher_message(
            TEST_CHAT_ID, "Ответ", TEST_SESSION_ID, 1
        )

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        # Заголовок без <a href=> (обычный формат)
        assert "tg://msg" not in sent_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_send_watcher_message_other_session(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Сообщение из другой сессии — с кликабельной ссылкой."""
        mock_get_bound.return_value = TEST_SESSION_ID_2

        await send_watcher_message(
            TEST_CHAT_ID, "Ответ", TEST_SESSION_ID, 1
        )

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "tg://msg" in sent_text


# --- Тесты _send_telegram_message ---


class TestSendTelegramMessage:
    """Тесты низкоуровневой отправки в Telegram."""

    @pytest.mark.asyncio()
    async def test_send_telegram_message_html_fallback(
        self, _setup_application: MagicMock
    ) -> None:
        """При ошибке HTML переключается на plain text."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and kwargs.get("parse_mode") == ParseMode.HTML:
                raise BadRequest("Can't parse entities")
            return MagicMock()

        _setup_application.bot.send_message = AsyncMock(side_effect=mock_send)

        await _send_telegram_message(TEST_CHAT_ID, "<b>Тест</b>")

        assert call_count == 2
        # Второй вызов — без HTML
        second_call = _setup_application.bot.send_message.call_args_list[1]
        assert second_call[1].get("parse_mode") is None

    @pytest.mark.asyncio()
    async def test_send_telegram_message_retry_after(
        self, _setup_application: MagicMock
    ) -> None:
        """Ожидание при RetryAfter, затем повторная отправка."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RetryAfter(retry_after=1)
            return MagicMock()

        _setup_application.bot.send_message = AsyncMock(side_effect=mock_send)

        await _send_telegram_message(TEST_CHAT_ID, "Тест")

        assert call_count == 2

    @pytest.mark.asyncio()
    async def test_send_telegram_message_network_retry(
        self, _setup_application: MagicMock
    ) -> None:
        """Повторные попытки при сетевой ошибке."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise TimedOut()
            return MagicMock()

        _setup_application.bot.send_message = AsyncMock(side_effect=mock_send)

        await _send_telegram_message(TEST_CHAT_ID, "Тест")

        assert call_count == 3

    @pytest.mark.asyncio()
    async def test_send_telegram_message_all_retries_failed(
        self, _setup_application: MagicMock
    ) -> None:
        """Все попытки отправки исчерпаны при сетевой ошибке."""
        _setup_application.bot.send_message = AsyncMock(
            side_effect=NetworkError("Connection error")
        )

        # Функция не должна выбрасывать исключение — только логирует
        await _send_telegram_message(TEST_CHAT_ID, "Тест")

        assert _setup_application.bot.send_message.call_count == SEND_RETRY_COUNT


# --- Тесты автоочистки файлов ---


class TestCleanOldReceivedFiles:
    """Тесты удаления старых файлов из received_files/."""

    @pytest.mark.asyncio()
    async def test_clean_old_received_files_deletes_old(
        self, tmp_path: Path
    ) -> None:
        """Файлы старше 7 дней удаляются."""
        files_dir = tmp_path / RECEIVED_FILES_DIR
        files_dir.mkdir()

        # Старый файл (10 дней назад)
        old_file = files_dir / "old_file.jpg"
        old_file.write_text("old")
        old_mtime = time.time() - 10 * SECONDS_PER_DAY
        os.utime(old_file, (old_mtime, old_mtime))

        # Свежий файл (3 дня назад)
        fresh_file = files_dir / "fresh_file.jpg"
        fresh_file.write_text("fresh")
        fresh_mtime = time.time() - 3 * SECONDS_PER_DAY
        os.utime(fresh_file, (fresh_mtime, fresh_mtime))

        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            await _clean_old_received_files()

        assert not old_file.exists()
        assert fresh_file.exists()

    @pytest.mark.asyncio()
    async def test_clean_old_received_files_no_directory(
        self, tmp_path: Path
    ) -> None:
        """Очистка когда папки не существует — без ошибок."""
        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            await _clean_old_received_files()
        # Не должно быть исключений

    @pytest.mark.asyncio()
    async def test_clean_old_received_files_all_fresh(
        self, tmp_path: Path
    ) -> None:
        """Все файлы свежие — ничего не удаляется."""
        files_dir = tmp_path / RECEIVED_FILES_DIR
        files_dir.mkdir()

        fresh_file = files_dir / "fresh.txt"
        fresh_file.write_text("fresh")

        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            await _clean_old_received_files()

        assert fresh_file.exists()


# --- Тесты setup_bot ---


class TestSetupBot:
    """Тесты настройки бота."""

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_setup_bot_registers_handlers(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """setup_bot регистрирует все обработчики."""
        # Настраиваем цепочку builder
        mock_app = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.post_init.return_value = mock_builder
        mock_builder.build.return_value = mock_app
        mock_builder_class.return_value = mock_builder

        result = setup_bot()

        assert result is mock_app
        # Должно быть минимум 8 обработчиков
        # (new, sessions, stop, all, /N, text, photo, document)
        assert mock_app.add_handler.call_count >= 8


# --- Тесты post_init ---


class TestPostInit:
    """Тесты инициализации бота (очистка файлов, восстановление состояния, меню)."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_sets_commands(
        self, mock_session_mgr: MagicMock, mock_clean: AsyncMock,
    ) -> None:
        """post_init устанавливает меню команд."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await post_init(mock_app)

        mock_app.bot.set_my_commands.assert_called_once()
        commands = mock_app.bot.set_my_commands.call_args[0][0]
        assert len(commands) == len(BOT_COMMANDS)

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_restores_bindings(
        self, mock_session_mgr: MagicMock, mock_clean: AsyncMock,
    ) -> None:
        """post_init восстанавливает привязки сессий."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {12345: "session-abc"}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await post_init(mock_app)

        mock_session_mgr.load_bindings.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_continues_on_restore_error(
        self, mock_session_mgr: MagicMock, mock_clean: AsyncMock,
    ) -> None:
        """post_init не падает при ошибке восстановления состояния."""
        mock_session_mgr.load_bindings = AsyncMock(
            side_effect=OSError("disk error")
        )
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        # Не должно выбросить исключение
        await post_init(mock_app)


# --- Тесты _find_session_by_number ---


class TestFindSessionByNumber:
    """Тесты поиска сессии по дневному номеру."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "get_session_id_by_number", new_callable=AsyncMock)
    async def test_find_in_registry(
        self, mock_get: AsyncMock
    ) -> None:
        """Сессия найдена в дневном реестре."""
        mock_get.return_value = TEST_SESSION_ID

        from claude_manager.bot import _find_session_by_number

        result = await _find_session_by_number(3)
        assert result == TEST_SESSION_ID

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "get_session_id_by_number", new_callable=AsyncMock)
    async def test_find_session_by_number_in_visible_sessions(
        self,
        mock_get_by_number: AsyncMock,
        mock_get_sessions: AsyncMock,
        mock_register: AsyncMock,
    ) -> None:
        """Сессия не в реестре, но среди видимых сессий."""
        # Первый вызов — не найдено, второй — найдено (после регистрации)
        mock_get_by_number.side_effect = [None, TEST_SESSION_ID]
        mock_get_sessions.return_value = [
            SessionInfo(TEST_SESSION_ID, "2026-03-30T10:00:00", "Тест"),
        ]
        mock_register.return_value = 5

        from claude_manager.bot import _find_session_by_number

        result = await _find_session_by_number(5)
        assert result == TEST_SESSION_ID
        mock_register.assert_called()
