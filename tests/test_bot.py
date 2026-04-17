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
    EMPTY_PROJECTS_TEMPLATE,
    EMPTY_RESPONSE_TEXT,
    FILE_CONTENT_HEADER_TEMPLATE,
    IMAGE_EXTENSIONS,
    INVALID_PROJECT_NUMBER_TEMPLATE,
    MONITORING_MODE_MESSAGE,
    NO_RESPONSE_MARKER,
    PROJECT_ALREADY_ACTIVE_TEMPLATE,
    PROJECT_CURRENT_MARKER,
    PROJECT_SWITCH_ERROR_TEMPLATE,
    PROJECT_SWITCH_SUCCESS_TEMPLATE,
    RECEIVED_FILES_DIR,
    RECEIVED_FILES_MAX_AGE_DAYS,
    SECONDS_PER_DAY,
    SEND_RETRY_COUNT,
    _build_file_task,
    _check_access,
    _clean_old_received_files,
    _handle_claude_result,
    _send_to_claude_and_respond,
    _format_clickable_session_number,
    _format_session_header,
    _generate_file_name,
    _is_current_session,
    _process_file_markers,
    _send_binary_file,
    _send_telegram_message,
    _send_text_file,
    handle_all,
    handle_document,
    handle_message,
    handle_new,
    handle_photo,
    handle_projects,
    handle_sessions,
    handle_stop,
    handle_switch_project,
    handle_switch_session,
    post_init,
    send_response,
    send_watcher_message,
    setup_bot,
)
from claude_manager import file_sender
from claude_manager import project_manager
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


# --- Тесты авторизации E2E тестового аккаунта ---


E2E_TEST_USER_ID = 77777


class TestE2eTestUserAccess:
    """Тесты авторизации E2E тестового аккаунта."""

    def test_e2e_user_passes_check_access(self) -> None:
        """E2E_TEST_USER_ID проходит _check_access."""
        original = config_module.E2E_TEST_USER_ID
        config_module.E2E_TEST_USER_ID = E2E_TEST_USER_ID
        try:
            update = _make_update(user_id=E2E_TEST_USER_ID)
            assert _check_access(update) is True
        finally:
            config_module.E2E_TEST_USER_ID = original

    def test_e2e_user_denied_when_not_configured(self) -> None:
        """Без E2E_TEST_USER_ID чужой ID отклоняется."""
        original = config_module.E2E_TEST_USER_ID
        config_module.E2E_TEST_USER_ID = None
        try:
            update = _make_update(user_id=E2E_TEST_USER_ID)
            assert _check_access(update) is False
        finally:
            config_module.E2E_TEST_USER_ID = original

    @patch("claude_manager.bot._send_telegram_message", new_callable=AsyncMock)
    @patch("claude_manager.bot._clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_skips_e2e_user(
        self,
        mock_session_mgr: MagicMock,
        mock_clean: AsyncMock,
        mock_send: AsyncMock,
    ) -> None:
        """post_init не шлёт уведомление E2E-пользователю."""
        original_allowed = config_module.ALLOWED_USER_IDS
        original_e2e = config_module.E2E_TEST_USER_ID

        # Оба ID в белом списке, но E2E-пользователь должен быть пропущен
        config_module.ALLOWED_USER_IDS = {111, E2E_TEST_USER_ID}
        config_module.E2E_TEST_USER_ID = E2E_TEST_USER_ID

        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}

        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        try:
            with patch.object(
                daily_session_registry, "is_registry_loaded", return_value=False,
            ):
                await post_init(mock_app)

            # _send_telegram_message вызван только для chat_id=111, не для E2E
            sent_chat_ids = [
                call.args[0] for call in mock_send.call_args_list
            ]
            assert 111 in sent_chat_ids
            assert E2E_TEST_USER_ID not in sent_chat_ids
        finally:
            config_module.ALLOWED_USER_IDS = original_allowed
            config_module.E2E_TEST_USER_ID = original_e2e


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
        """Кликабельный номер содержит команду в жирном формате."""
        result = _format_clickable_session_number(3)
        assert result == "<b>/3</b>"


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
            session_id="_new_abc123def456", day_number=1
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
            TEST_CHAT_ID, "Ответ", TEST_SESSION_ID, 1, is_final=True
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
        """Сообщение из другой сессии — с кликабельной командой."""
        mock_get_bound.return_value = TEST_SESSION_ID_2

        await send_watcher_message(
            TEST_CHAT_ID, "Ответ", TEST_SESSION_ID, 1, is_final=True
        )

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "<b>/1</b>" in sent_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_send_watcher_message_uses_correct_icon(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Финальное сообщение с галочкой, промежуточное — с часами и курсивом."""
        mock_get_bound.return_value = TEST_SESSION_ID

        # Финальное: галочка, без песочных часов
        await send_watcher_message(
            TEST_CHAT_ID, "Готово", TEST_SESSION_ID, 1, is_final=True
        )
        sent = _setup_application.bot.send_message
        final_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "\u2705" in final_text
        assert "\u23f3" not in final_text

        sent.reset_mock()

        # Промежуточное: песочные часы, без галочки, курсив
        await send_watcher_message(
            TEST_CHAT_ID, "Думаю...", TEST_SESSION_ID, 1, is_final=False
        )
        intermediate_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "\u23f3" in intermediate_text
        assert "\u2705" not in intermediate_text
        assert "<i>" in intermediate_text


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
        mock_builder.concurrent_updates.return_value = mock_builder
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


# --- Тесты команды /projects ---


def _make_project_info(
    name: str, path: str = "/tmp/fake", is_current: bool = False
) -> project_manager.ProjectInfo:
    """Вспомогательная функция для создания ProjectInfo в тестах."""
    return project_manager.ProjectInfo(
        name=name,
        absolute_path=path,
        is_current=is_current,
    )


class TestHandleProjects:
    """Тесты обработчика команды /projects."""

    @pytest.mark.asyncio()
    async def test_access_denied_for_unauthorized(self) -> None:
        """Неавторизованный пользователь не получает список проектов."""
        update = _make_update(user_id=DENIED_USER_ID)
        context = MagicMock()

        with patch.object(
            project_manager, "scan_available_projects", new_callable=AsyncMock
        ) as mock_scan:
            await handle_projects(update, context)
            mock_scan.assert_not_called()

    @pytest.mark.asyncio()
    async def test_empty_list_message(self) -> None:
        """Пустой список проектов → отправляется сообщение EMPTY_PROJECTS_TEMPLATE."""
        update = _make_update()
        context = MagicMock()

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=[]),
        ), patch.object(config_module, "PROJECTS_ROOT_DIR", "/fake/root"):
            await handle_projects(update, context)

        sent = bot_module._application.bot.send_message.call_args
        assert "/fake/root" in sent.args[1]

    @pytest.mark.asyncio()
    async def test_shows_all_projects(self) -> None:
        """Список проектов отображается со всеми именами и командами /pN."""
        projects = [
            _make_project_info("alpha"),
            _make_project_info("beta"),
            _make_project_info("gamma"),
        ]
        update = _make_update()
        context = MagicMock()

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ):
            await handle_projects(update, context)

        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        assert "/p1" in sent_text
        assert "alpha" in sent_text
        assert "/p2" in sent_text
        assert "beta" in sent_text
        assert "/p3" in sent_text
        assert "gamma" in sent_text

    @pytest.mark.asyncio()
    async def test_marks_current_project(self) -> None:
        """Текущий проект помечается маркером."""
        projects = [
            _make_project_info("alpha"),
            _make_project_info("beta", is_current=True),
        ]
        update = _make_update()
        context = MagicMock()

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ):
            await handle_projects(update, context)

        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        # Маркер появляется только в строке с beta
        lines = sent_text.split("\n")
        beta_line = next(line for line in lines if "beta" in line)
        alpha_line = next(line for line in lines if "alpha" in line)
        assert PROJECT_CURRENT_MARKER in beta_line
        assert PROJECT_CURRENT_MARKER not in alpha_line

    @pytest.mark.asyncio()
    async def test_sends_as_plain_text(self) -> None:
        """Список отправляется с parse_mode=None для кликабельности команд."""
        projects = [_make_project_info("alpha")]
        update = _make_update()
        context = MagicMock()

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ):
            await handle_projects(update, context)

        call_kwargs = bot_module._application.bot.send_message.call_args.kwargs
        assert call_kwargs.get("parse_mode") is None


# --- Тесты команды /pN ---


class TestHandleSwitchProject:
    """Тесты обработчика команды /pN для переключения проектов."""

    @pytest.mark.asyncio()
    async def test_access_denied_for_unauthorized(self) -> None:
        """Неавторизованный пользователь не может переключить проект."""
        update = _make_update(text="/p1", user_id=DENIED_USER_ID)
        context = MagicMock()

        with patch.object(
            project_manager, "switch_project", new_callable=AsyncMock
        ) as mock_switch:
            await handle_switch_project(update, context)
            mock_switch.assert_not_called()

    @pytest.mark.asyncio()
    async def test_valid_number_calls_switch(self) -> None:
        """Валидный номер вызывает project_manager.switch_project с правильным путём."""
        projects = [_make_project_info("alpha", path="/fake/alpha")]
        update = _make_update(text="/p1")
        context = MagicMock()

        switch_result = project_manager.SwitchResult(
            success=True, already_active=False,
            old_path="/fake/old", new_path="/fake/alpha",
            pending_messages_count=0, pending_messages=[],
            error_message="",
        )

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager, "switch_project",
            new=AsyncMock(return_value=switch_result),
        ) as mock_switch:
            await handle_switch_project(update, context)

        mock_switch.assert_awaited_once_with("/fake/alpha")

    @pytest.mark.asyncio()
    async def test_invalid_number_shows_error(self) -> None:
        """Номер вне диапазона — отправляется INVALID_PROJECT_NUMBER_TEMPLATE."""
        projects = [_make_project_info("alpha")]
        update = _make_update(text="/p99")
        context = MagicMock()

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ):
            await handle_switch_project(update, context)

        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "99" in sent

    @pytest.mark.asyncio()
    async def test_already_active_shows_message(self) -> None:
        """already_active=True → сообщение PROJECT_ALREADY_ACTIVE_TEMPLATE."""
        projects = [_make_project_info("alpha", path="/fake/alpha", is_current=True)]
        update = _make_update(text="/p1")
        context = MagicMock()

        switch_result = project_manager.SwitchResult(
            success=True, already_active=True,
            old_path="/fake/alpha", new_path="/fake/alpha",
            pending_messages_count=0, pending_messages=[],
            error_message="",
        )

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager, "switch_project",
            new=AsyncMock(return_value=switch_result),
        ):
            await handle_switch_project(update, context)

        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "alpha" in sent
        assert "уже" in sent.lower() or "Уже" in sent

    @pytest.mark.asyncio()
    async def test_success_message_includes_name(self) -> None:
        """Успешное переключение → сообщение с именем проекта."""
        projects = [_make_project_info("beta", path="/fake/beta")]
        update = _make_update(text="/p1")
        context = MagicMock()

        switch_result = project_manager.SwitchResult(
            success=True, already_active=False,
            old_path="/fake/alpha", new_path="/fake/beta",
            pending_messages_count=0, pending_messages=[],
            error_message="",
        )

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager, "switch_project",
            new=AsyncMock(return_value=switch_result),
        ):
            await handle_switch_project(update, context)

        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "beta" in sent

    @pytest.mark.asyncio()
    async def test_success_message_includes_pending_count(self) -> None:
        """Если есть непрочитанные сообщения — их количество добавляется в ответ."""
        projects = [_make_project_info("beta", path="/fake/beta")]
        update = _make_update(text="/p1")
        context = MagicMock()

        switch_result = project_manager.SwitchResult(
            success=True, already_active=False,
            old_path="/fake/alpha", new_path="/fake/beta",
            pending_messages_count=3, pending_messages=[],
            error_message="",
        )

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager, "switch_project",
            new=AsyncMock(return_value=switch_result),
        ):
            await handle_switch_project(update, context)

        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "beta" in sent
        assert "3" in sent

    @pytest.mark.asyncio()
    async def test_error_shows_error_message(self) -> None:
        """success=False → сообщение с причиной ошибки."""
        projects = [_make_project_info("beta", path="/fake/beta")]
        update = _make_update(text="/p1")
        context = MagicMock()

        switch_result = project_manager.SwitchResult(
            success=False, already_active=False,
            old_path="/fake/alpha", new_path="/fake/beta",
            pending_messages_count=0, pending_messages=[],
            error_message="Нет прав на чтение папки",
        )

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager, "switch_project",
            new=AsyncMock(return_value=switch_result),
        ):
            await handle_switch_project(update, context)

        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "Нет прав" in sent

    @pytest.mark.asyncio()
    async def test_delivers_pending_messages_after_switch(self) -> None:
        """При наличии pending_messages каждое доставляется через send_response."""
        from claude_manager.unread_buffer import PendingMessage

        projects = [_make_project_info("beta", path="/fake/beta")]
        update = _make_update(text="/p1")
        context = MagicMock()

        pending = [
            PendingMessage(session_id="sess-1", text="Ответ из фона"),
            PendingMessage(session_id="sess-2", text="Второй ответ"),
        ]
        switch_result = project_manager.SwitchResult(
            success=True, already_active=False,
            old_path="/fake/alpha", new_path="/fake/beta",
            pending_messages_count=2, pending_messages=pending,
            error_message="",
        )

        with patch.object(
            project_manager, "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager, "switch_project",
            new=AsyncMock(return_value=switch_result),
        ), patch.object(
            daily_session_registry, "register_session",
            new=AsyncMock(side_effect=[1, 2]),
        ):
            await handle_switch_project(update, context)

        # Должно быть 3 вызова send_message:
        # 1 — результат переключения, 2 и 3 — pending-сообщения
        all_calls = bot_module._application.bot.send_message.call_args_list
        assert len(all_calls) >= 3, (
            f"Ожидалось минимум 3 вызова send_message (1 результат + 2 pending), "
            f"получено {len(all_calls)}"
        )


# --- Тесты send_response с файловыми маркерами ---


class TestSendResponseFileMarkers:
    """Тесты обработки маркеров [SEND_FILE:path] в send_response."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_text_file", new_callable=AsyncMock)
    @patch.object(file_sender, "is_text_file", return_value=True)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/test.md"],
    )
    async def test_send_response_with_text_file_marker(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_send_text_file: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Финальный ответ с маркером текстового файла — файл отправляется."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/test.md]",
            1,
            is_final=True,
        )
        mock_extract.assert_called_once()
        mock_send_text_file.assert_awaited_once_with(
            TEST_CHAT_ID, "/tmp/test.md",
        )

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._process_file_markers", new_callable=AsyncMock)
    async def test_send_response_not_final_skips_file_markers(
        self,
        mock_process: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Промежуточный ответ (is_final=False) — маркеры не обрабатываются."""
        await send_response(
            TEST_CHAT_ID,
            "text [SEND_FILE:/tmp/test.md]",
            1,
            is_final=False,
        )
        mock_process.assert_not_awaited()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_binary_file", new_callable=AsyncMock)
    @patch.object(file_sender, "is_text_file", return_value=False)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/image.png"],
    )
    async def test_send_response_with_binary_file_marker(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_send_binary: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Маркер бинарного файла — отправляется как документ."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/image.png]",
            1,
            is_final=True,
        )
        mock_send_binary.assert_awaited_once_with(
            TEST_CHAT_ID, "/tmp/image.png",
        )

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_text_file", new_callable=AsyncMock)
    @patch.object(file_sender, "is_text_file", return_value=True)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender,
        "extract_file_markers",
        return_value=["/tmp/a.md", "/tmp/b.md"],
    )
    async def test_send_response_multiple_file_markers(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_send_text_file: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Два маркера — оба файла отправлены."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/a.md] [SEND_FILE:/tmp/b.md]",
            1,
            is_final=True,
        )
        assert mock_send_text_file.await_count == 2

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_telegram_message", new_callable=AsyncMock)
    @patch.object(
        file_sender,
        "read_file_content",
        return_value=("", "Файл не найден: /tmp/missing.md"),
    )
    @patch.object(file_sender, "is_text_file", return_value=True)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender,
        "extract_file_markers",
        return_value=["/tmp/missing.md"],
    )
    async def test_send_response_file_not_found_sends_error(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_read: MagicMock,
        mock_send_msg: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Файл не найден — пользователь получает сообщение об ошибке, бот не падает."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/missing.md]",
            1,
            is_final=True,
        )
        # Одно из сообщений содержит ошибку о файле
        error_sent = any(
            "Файл не найден" in str(call)
            for call in mock_send_msg.call_args_list
        )
        assert error_sent


# --- Тесты send_watcher_message с файловыми маркерами ---


class TestSendWatcherMessageFileMarkers:
    """Тесты обработки маркеров [SEND_FILE:path] в send_watcher_message."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._send_text_file", new_callable=AsyncMock)
    @patch.object(file_sender, "is_text_file", return_value=True)
    @patch.object(file_sender, "strip_file_markers", return_value="ответ")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/file.md"],
    )
    @patch.object(session_manager, "get_bound_session", return_value=TEST_SESSION_ID)
    async def test_send_watcher_message_with_file_marker(
        self,
        mock_get_bound: MagicMock,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_send_text_file: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Финальный ответ watcher с маркером — файл отправлен."""
        await send_watcher_message(
            TEST_CHAT_ID, "ответ [SEND_FILE:/tmp/file.md]",
            TEST_SESSION_ID, 1, is_final=True,
        )
        mock_send_text_file.assert_awaited_once_with(
            TEST_CHAT_ID, "/tmp/file.md",
        )

    @pytest.mark.asyncio()
    @patch("claude_manager.bot._process_file_markers", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session", return_value=TEST_SESSION_ID)
    async def test_send_watcher_message_not_final_skips_markers(
        self,
        mock_get_bound: MagicMock,
        mock_process: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Промежуточный ответ watcher — маркеры не обрабатываются."""
        await send_watcher_message(
            TEST_CHAT_ID, "text [SEND_FILE:/tmp/file.md]",
            TEST_SESSION_ID, 1, is_final=False,
        )
        mock_process.assert_not_awaited()


# --- Тесты _process_file_markers напрямую ---


class TestProcessFileMarkers:
    """Тесты функции _process_file_markers."""

    @pytest.mark.asyncio()
    async def test_process_file_markers_no_markers(
        self, _setup_application: MagicMock,
    ) -> None:
        """Текст без маркеров — возвращается без изменений, файлы не отправляются."""
        result = await _process_file_markers(TEST_CHAT_ID, "обычный текст")
        assert result == "обычный текст"
        # send_message не должен быть вызван для отправки файлов
        # (только _send_telegram_message может быть вызвана позже для текста,
        # но _process_file_markers сама файлы не отправляла)
        _setup_application.bot.send_document.assert_not_called()


# --- Тесты _send_text_file (контракт передачи резерва в file_sender) ---


class TestSendTextFile:
    """Тесты контракта между _send_text_file (bot.py) и render_file_for_telegram (file_sender)."""

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities", return_value=[])
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_passes_header_reserve(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """render_file_for_telegram вызывается с first_chunk_reserve, равным UTF-16 длине заголовка."""
        mock_read.return_value = ("file content", None)
        mock_render.return_value = [("rendered text", [])]

        filename = "report.md"
        header = FILE_CONTENT_HEADER_TEMPLATE.format(filename=filename)
        expected_reserve = len(header.encode("utf-16-le")) // 2

        await _send_text_file(TEST_CHAT_ID, f"/path/to/{filename}")

        mock_render.assert_called_once_with(
            "file content", first_chunk_reserve=expected_reserve,
        )

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities", return_value=[])
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_long_filename_passes_correct_reserve(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Длинное имя файла (200 символов) — резерв корректно учитывает все части заголовка."""
        mock_read.return_value = ("file content", None)
        mock_render.return_value = [("rendered text", [])]

        # Имя файла из 200 ASCII-символов
        long_filename = "a" * 200
        header = FILE_CONTENT_HEADER_TEMPLATE.format(filename=long_filename)
        # Заголовок: emoji U+1F4CE (2 UTF-16 units) + пробел (1) + filename (200) + \n\n (2) = 205
        expected_reserve = len(header.encode("utf-16-le")) // 2

        await _send_text_file(TEST_CHAT_ID, f"/path/to/{long_filename}")

        actual_reserve = mock_render.call_args.kwargs["first_chunk_reserve"]
        assert actual_reserve == expected_reserve
        assert actual_reserve == 205


# --- Тесты session_id_callback (раннее обновление привязок) ---


class TestSessionIdCallback:
    """Тесты механизма раннего уведомления о смене session_id через callback."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_send_to_claude_passes_session_id_callback(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """_send_to_claude_and_respond передаёт session_id_callback в process_manager.send_message."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            mock_send.return_value = SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await _send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # Проверяем, что session_id_callback передан и является callable
            call_kwargs = mock_send.call_args
            assert "session_id_callback" in call_kwargs.kwargs
            assert callable(call_kwargs.kwargs["session_id_callback"])

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "update_session_id", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_on_session_id_changed_updates_watcher_and_session_manager(
        self,
        mock_get_bound: MagicMock,
        mock_sm_update: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Callback _on_session_id_changed вызывает update_session_id в watcher и session_manager."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1
        new_session_id = "real-uuid-xyz-789"

        # Мокаем send_message так, чтобы он захватил и вызвал session_id_callback
        async def fake_send_message(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None,
        ):
            # Вызываем callback, имитируя обнаружение нового session_id
            if session_id_callback is not None:
                await session_id_callback(TEST_SESSION_ID, new_session_id)
            return SendResult(
                text="OK", session_id=new_session_id, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_message,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "update_session_id",
        ) as mock_watcher_update, patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await _send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # session_watcher.update_session_id вызван с правильными аргументами
            mock_watcher_update.assert_called_once_with(TEST_SESSION_ID, new_session_id)
            # session_manager.update_session_id вызван с chat_id и обоими ID
            mock_sm_update.assert_awaited_once_with(
                TEST_CHAT_ID, TEST_SESSION_ID, new_session_id,
            )

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "update_session_id", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_resume_uses_updated_session_id_after_callback(
        self,
        mock_get_bound: MagicMock,
        mock_sm_update: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """После callback обновления, finally вызывает resume_session с НОВЫМ session_id."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1
        new_session_id = "real-uuid-resume-test"

        async def fake_send_message(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None,
        ):
            if session_id_callback is not None:
                await session_id_callback(TEST_SESSION_ID, new_session_id)
            return SendResult(
                text="OK", session_id=new_session_id, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_message,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "update_session_id",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume:
            await _send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # resume_session вызван с НОВЫМ session_id, а не со старым
            mock_resume.assert_awaited_once_with(new_session_id)

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_handle_claude_result_no_longer_calls_update_session_id(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """_handle_claude_result не вызывает update_session_id — обновление теперь через callback."""
        mock_register.return_value = 1
        different_session_id = "different-uuid-999"

        # Результат с session_id, отличающимся от переданного
        result = SendResult(
            text="OK", session_id=different_session_id, is_error=False, retries_used=0,
        )

        with patch.object(
            session_manager, "update_session_id", new_callable=AsyncMock,
        ) as mock_sm_update, patch.object(
            session_watcher, "update_session_id",
        ) as mock_watcher_update:
            returned_id = await _handle_claude_result(
                TEST_CHAT_ID, TEST_SESSION_ID, result,
            )

            # update_session_id НЕ вызывается в _handle_claude_result
            mock_sm_update.assert_not_awaited()
            mock_watcher_update.assert_not_called()
            # Возвращает actual_session_id из результата
            assert returned_id == different_session_id
