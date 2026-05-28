"""Тесты модуля bot — транспортный слой Telegram-бота."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ChatAction, ParseMode

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    current_backend_registry,
    daily_session_registry,
    process_manager,
    session_manager,
    session_reader,
    session_watcher,
    silence_mode_registry,
)
from claude_manager.coding_agent_backend import BackendName, SessionFileInfo

from claude_manager.bot import (
    ALL_PROJECTS_MODE_INPUT_WARNING,
    ALL_PROJECTS_MODE_LINE,
    BOT_COMMANDS,
    EMPTY_PROJECTS_TEMPLATE,
    INVALID_PROJECT_NUMBER_TEMPLATE,
    PROJECT_ALREADY_ACTIVE_TEMPLATE,
    PROJECT_CURRENT_MARKER,
    PROJECT_SWITCH_ERROR_TEMPLATE,
    PROJECT_SWITCH_SUCCESS_TEMPLATE,
    _check_access,
    _format_clickable_session_number,
    _format_session_header,
    _is_current_session,
    handle_all,
    handle_document,
    handle_message,
    handle_new,
    handle_photo,
    handle_projects,
    handle_restart,
    handle_sessions,
    handle_stop,
    handle_switch_project,
    handle_switch_project_session,
    handle_switch_session,
    post_init,
    send_all_projects_watcher_message,
    send_response,
    send_watcher_message,
    setup_bot,
)
from claude_manager.claude_interaction import (
    EMPTY_RESPONSE_TEXT,
    MONITORING_MODE_MESSAGE,
    NO_RESPONSE_MARKER,
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
from claude_manager.session_manager import ActiveSession, NewSessionResult, SwitchResult
from claude_manager.session_reader import SessionInfo


def _discard_background_coro(coro):
    """Close a background coroutine that is intentionally not run in unit tests."""
    coro.close()
    return MagicMock()


# --- Фикстуры ---


ALLOWED_USER_ID = 12345
DENIED_USER_ID = 99999
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "abc-def-111"
TEST_SESSION_ID_2 = "abc-def-222"


class FakeBackendForSessionList:
    """Минимальный backend для теста объединённого списка /sessions."""

    def __init__(
        self,
        name: BackendName,
        display_name: str,
        session_files: list[SessionFileInfo],
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.session_files = session_files

    async def list_session_files_for_project(
        self,
        _project_dir: str,
    ) -> list[SessionFileInfo]:
        """Возвращает заранее заданные файлы сессий."""
        return self.session_files


def _session_file(
    session_id: str,
    last_modified_at: float,
    preview: str,
) -> SessionFileInfo:
    """Создаёт session metadata для списка /sessions."""
    return SessionFileInfo(
        session_id=session_id,
        file_path=f"/tmp/{session_id}.jsonl",
        last_modified_at=last_modified_at,
        preview=preview,
    )


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для всех тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_working_dir = config_module.WORKING_DIR
    original_silence_enabled = silence_mode_registry._silence_enabled
    original_silence_loaded = silence_mode_registry._loaded_from_disk
    original_current_backend = current_backend_registry._current_backend
    original_current_backend_loaded = current_backend_registry._loaded_from_disk
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.WORKING_DIR = "/tmp/test_working_dir"
    silence_mode_registry._silence_enabled = False
    silence_mode_registry._loaded_from_disk = True
    current_backend_registry._current_backend = BackendName.CLAUDE
    current_backend_registry._loaded_from_disk = True
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.WORKING_DIR = original_working_dir
    silence_mode_registry._silence_enabled = original_silence_enabled
    silence_mode_registry._loaded_from_disk = original_silence_loaded
    current_backend_registry._current_backend = original_current_backend
    current_backend_registry._loaded_from_disk = original_current_backend_loaded


@pytest.fixture(autouse=True)
def _cleanup_watchdog_tasks():
    """Очищает словарь watchdog-тасков между тестами, чтобы тесты были изолированы."""
    from claude_manager.claude_interaction import watchdog_tasks
    original_tasks = watchdog_tasks.copy()
    watchdog_tasks.clear()
    yield
    for task in watchdog_tasks.values():
        if not task.done():
            task.cancel()
    watchdog_tasks.clear()
    watchdog_tasks.update(original_tasks)


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
    update.message.media_group_id = None
    return update


def _make_context() -> MagicMock:
    """Создаёт фейковый context для обработчиков."""
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
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

    @patch("claude_manager.bot.telegram_sender.send_telegram_message", new_callable=AsyncMock)
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
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
            with patch(
                "claude_manager.bot.asyncio.create_task",
                side_effect=_discard_background_coro,
            ), patch.object(
                daily_session_registry, "is_registry_loaded", return_value=False,
            ):
                await post_init(mock_app)

            # telegram_sender.send_telegram_message вызван только для chat_id=111, не для E2E
            # Первый аргумент — bot, второй — chat_id
            sent_chat_ids = [
                call.args[1] for call in mock_send.call_args_list
            ]
            assert 111 in sent_chat_ids
            assert E2E_TEST_USER_ID not in sent_chat_ids
        finally:
            config_module.ALLOWED_USER_IDS = original_allowed
            config_module.E2E_TEST_USER_ID = original_e2e

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "load_state")
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_loads_current_backend_registry(
        self,
        mock_session_mgr: MagicMock,
        _mock_clean: AsyncMock,
        mock_load_backend_state: MagicMock,
    ) -> None:
        """post_init загружает текущий backend до старта watcher-а."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        with patch(
            "claude_manager.bot.asyncio.create_task",
            side_effect=_discard_background_coro,
        ), patch.object(
            daily_session_registry,
            "is_registry_loaded",
            return_value=True,
        ):
            await post_init(mock_app)

        mock_load_backend_state.assert_called_once()


# --- Тесты форматирования ---


class TestFormatSessionHeader:
    """Тесты формата заголовков сессий."""

    def test_format_session_header_final(self) -> None:
        """Финальный ответ получает галочку."""
        result = _format_session_header(3, is_final=True)
        assert result == "#3 🤖 Claude \u2705 "

    def test_format_session_header_intermediate(self) -> None:
        """Промежуточное обновление получает песочные часы."""
        result = _format_session_header(5, is_final=False)
        assert result == "#5 🤖 Claude \u23f3 "


class TestFormatClickableSessionNumber:
    """Тесты формата кликабельных номеров сессий."""

    def test_format_clickable_session_number(self) -> None:
        """Кликабельный номер содержит команду в жирном формате."""
        result = _format_clickable_session_number(3)
        assert result == "<b>/3</b>"
# --- Тесты определения текущей сессии ---


class TestIsCurrentSession:
    """Тесты определения текущей сессии чата."""

    @patch.object(session_manager, "get_active_session")
    def test_is_current_session_requires_backend_match(
        self,
        mock_get_active: MagicMock,
    ) -> None:
        """Одинаковый session_id другого backend-а не считается текущей сессией."""
        mock_get_active.return_value = ActiveSession(
            TEST_SESSION_ID,
            BackendName.CLAUDE,
        )

        assert _is_current_session(
            TEST_CHAT_ID,
            TEST_SESSION_ID,
            BackendName.CODEX,
        ) is False

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
    @patch.object(current_backend_registry, "get_current")
    @patch.object(session_manager, "create_new_session", new_callable=AsyncMock)
    async def test_handle_new_uses_current_backend(
        self,
        mock_create_session: AsyncMock,
        mock_get_current: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /new создаёт новую сессию в текущем backend-е."""
        mock_get_current.return_value = BackendName.CODEX
        mock_create_session.return_value = NewSessionResult(
            session_id="_new_codex123", day_number=7, backend=BackendName.CODEX
        )

        update = _make_update(text="/new")
        context = _make_context()
        await handle_new(update, context)

        mock_create_session.assert_called_once_with(TEST_CHAT_ID, BackendName.CODEX)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "#7" in sent_text
        assert "Codex" in sent_text

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

        mock_create_session.assert_called_once_with(TEST_CHAT_ID, BackendName.CLAUDE)

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1] if len(sent.call_args[0]) > 1 else "")
        assert "#1" in sent_text

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "create_new_session", new_callable=AsyncMock)
    async def test_handle_new_blocked_in_all_projects_mode(
        self,
        mock_create_session: AsyncMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """In global all mode /new must not create a hidden session."""
        mock_is_all_projects.return_value = True

        update = _make_update(text="/new")
        context = _make_context()
        await handle_new(update, context)

        mock_create_session.assert_not_awaited()
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()

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
    async def test_handle_sessions_merges_backends_before_limiting(
        self,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions объединяет Claude и Codex перед ограничением списка."""
        claude_backend = FakeBackendForSessionList(
            BackendName.CLAUDE,
            "🤖 Claude",
            [_session_file("claude-old", 10.0, "Claude old")],
        )
        codex_backend = FakeBackendForSessionList(
            BackendName.CODEX,
            "⚡ Codex",
            [_session_file("codex-new", 20.0, "Codex new")],
        )
        mock_register.side_effect = [1, 2]

        update = _make_update(text="/sessions")
        context = _make_context()
        with patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[claude_backend, codex_backend],
        ):
            await handle_sessions(update, context)

        assert mock_register.call_args_list[0].args == (
            "codex-new",
            BackendName.CODEX,
        )
        assert mock_register.call_args_list[1].args == (
            "claude-old",
            BackendName.CLAUDE,
        )
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text.splitlines()[0].startswith("/1 ⚡ Codex")
        assert sent_text.splitlines()[1].startswith("/2 🤖 Claude")

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(
        daily_session_registry,
        "get_session_summary",
        new_callable=AsyncMock,
        create=True,
    )
    async def test_handle_sessions_prefers_daily_registry_summary(
        self,
        mock_get_summary: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions показывает сохранённую краткую суть вместо длинного preview."""
        claude_backend = FakeBackendForSessionList(
            BackendName.CLAUDE,
            "🤖 Claude",
            [
                _session_file(
                    "id-1",
                    10.0,
                    "Давай доработаем инициализирующий скрипт загрузки данных по отзывам",
                ),
            ],
        )
        mock_register.return_value = 1
        mock_get_summary.return_value = "Загрузка отзывов за период"

        update = _make_update(text="/sessions")
        context = _make_context()
        with patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[claude_backend],
        ):
            await handle_sessions(update, context)

        mock_get_summary.assert_awaited_once_with("id-1", BackendName.CLAUDE)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "/1 🤖 Claude Загрузка отзывов за период" in sent_text
        assert "инициализирующий скрипт" not in sent_text

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    async def test_handle_sessions_shows_list(
        self,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions показывает список сессий."""
        claude_backend = FakeBackendForSessionList(
            BackendName.CLAUDE,
            "🤖 Claude",
            [
                _session_file("id-1", 10.0, "Первая сессия"),
                _session_file("id-2", 11.0, "Вторая сессия"),
                _session_file("id-3", 12.0, "Третья сессия"),
            ],
        )
        # Каждый вызов register_session возвращает номер по порядку
        mock_register.side_effect = [1, 2, 3]

        update = _make_update(text="/sessions")
        context = _make_context()
        with patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[claude_backend],
        ):
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
    async def test_handle_sessions_empty(
        self,
        _setup_application: MagicMock,
    ) -> None:
        """Пустой список сессий."""
        claude_backend = FakeBackendForSessionList(
            BackendName.CLAUDE,
            "🤖 Claude",
            [],
        )

        update = _make_update(text="/sessions")
        context = _make_context()
        with patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[claude_backend],
        ):
            await handle_sessions(update, context)

        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Нет сессий" in sent_text


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
        await bot_module.handle_agent(update, context)

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
        await bot_module.handle_agent(update, context)

        reply_markup = _setup_application.bot.send_message.call_args.kwargs["reply_markup"]
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
        await bot_module.handle_agent_callback(update, context)

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
        await bot_module.handle_agent_callback(update, context)

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
        await bot_module.handle_agent_callback(update, context)

        mock_set_current.assert_called_once_with(BackendName.CODEX)
        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "Текущая сессия" not in sent_text
        assert "Чтобы начать новую сессию, отправьте /new." in sent_text

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "set_current", side_effect=RuntimeError("state not loaded"))
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
        await bot_module.handle_agent_callback(update, context)

        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "Не удалось переключить агента: state not loaded" in sent_text

    @pytest.mark.asyncio()
    @patch.object(current_backend_registry, "set_current", side_effect=OSError("disk full"))
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
        await bot_module.handle_agent_callback(update, context)

        sent_text = update.callback_query.edit_message_text.call_args.kwargs["text"]
        assert "Не удалось переключить агента: disk full" in sent_text

    @pytest.mark.asyncio()
    async def test_handle_agent_callback_rejects_unknown_backend_value(self) -> None:
        """Неизвестное callback value отклоняется."""
        update = _make_callback_update("agent:gemini")
        context = _make_context()
        await bot_module.handle_agent_callback(update, context)

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
        await bot_module.handle_agent(update, context)

        mock_get_current.assert_not_called()
        _setup_application.bot.send_message.assert_not_called()


class TestHandleStop:
    """Тесты команды /stop."""

    @pytest.mark.asyncio()
    @patch.object(process_manager, "stop_process", new_callable=AsyncMock)
    @patch.object(process_manager, "is_busy")
    @patch.object(process_manager, "has_process")
    @patch.object(session_manager, "get_active_session")
    async def test_handle_stop_passes_active_backend(
        self,
        mock_get_active: MagicMock,
        mock_has_process: MagicMock,
        mock_is_busy: MagicMock,
        mock_stop: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /stop передаёт backend активной сессии."""
        mock_get_active.return_value = ActiveSession(
            TEST_SESSION_ID,
            BackendName.CODEX,
        )
        mock_has_process.return_value = True
        mock_is_busy.return_value = False
        mock_stop.return_value = StopResult(
            was_running=True,
            was_retrying=False,
            backend=BackendName.CODEX,
        )

        update = _make_update(text="/stop")
        context = _make_context()
        await handle_stop(update, context)

        mock_has_process.assert_called_once_with(TEST_SESSION_ID, BackendName.CODEX)
        mock_stop.assert_called_once_with(TEST_SESSION_ID, BackendName.CODEX)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Codex" in sent_text

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

        mock_stop.assert_called_once_with(TEST_SESSION_ID, BackendName.CLAUDE)
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
    @patch.object(all_projects_monitor, "enable_for_chat", new_callable=AsyncMock)
    @patch.object(session_manager, "unbind_session", new_callable=AsyncMock)
    async def test_handle_all_switches_to_monitoring(
        self,
        mock_unbind: AsyncMock,
        mock_enable_all_projects: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Command /all enables global monitoring across projects."""
        update = _make_update(text="/all")
        context = _make_context()
        await handle_all(update, context)

        mock_unbind.assert_called_once_with(TEST_CHAT_ID)
        mock_enable_all_projects.assert_awaited_once_with(TEST_CHAT_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "all" in sent_text.lower()
        assert "проект" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "enable_for_chat", new_callable=AsyncMock)
    @patch.object(session_manager, "unbind_session", new_callable=AsyncMock)
    async def test_handle_all_projects_alias_switches_to_monitoring(
        self,
        mock_unbind: AsyncMock,
        mock_enable_all_projects: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Command /all_projects enables global monitoring across projects."""
        update = _make_update(text="/all_projects")
        context = _make_context()
        await handle_all(update, context)

        mock_unbind.assert_called_once_with(TEST_CHAT_ID)
        mock_enable_all_projects.assert_awaited_once_with(TEST_CHAT_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "all" in sent_text.lower()
        assert "проект" in sent_text.lower()


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
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
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
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_message_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """All-project mode text is blocked with a project/session warning."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update(text="запрос")
        context = _make_context()
        await handle_message(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
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
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
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

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    async def test_silence_on_command_not_sent_to_claude(
        self,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """'Silence on' перехватывается — enable() вызван, подтверждение отправлено, в Claude не ушло."""
        update = _make_update(text="Silence on")
        context = _make_context()
        await handle_message(update, context)

        mock_silence.enable.assert_called_once()
        mock_send_to_claude.assert_not_called()
        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "включён" in sent_text

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    async def test_silence_off_command_not_sent_to_claude(
        self,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """'Silence off' перехватывается — disable() вызван, в Claude не ушло."""
        update = _make_update(text="Silence off")
        context = _make_context()
        await handle_message(update, context)

        mock_silence.disable.assert_called_once()
        mock_send_to_claude.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    async def test_silence_command_case_insensitive(
        self,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Команды silence нечувствительны к регистру и пробелам."""
        context = _make_context()
        for text in ["silence ON", "SILENCE on", " Silence On "]:
            mock_silence.reset_mock()
            mock_send_to_claude.reset_mock()
            update = _make_update(text=text)
            await handle_message(update, context)
            mock_silence.enable.assert_called_once()
            mock_send_to_claude.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_silence_command_works_in_monitoring_mode(
        self,
        mock_is_monitoring: MagicMock,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Silence on перехватывается ДО проверки monitoring mode."""
        mock_is_monitoring.return_value = True
        update = _make_update(text="Silence on")
        context = _make_context()
        await handle_message(update, context)

        mock_silence.enable.assert_called_once()
        mock_send_to_claude.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_regular_text_not_intercepted_as_silence(
        self,
        mock_is_monitoring: MagicMock,
        mock_silence: MagicMock,
        mock_send_to_claude: AsyncMock,
    ) -> None:
        """Похожий текст (не точное совпадение) НЕ перехватывается."""
        mock_is_monitoring.return_value = False
        context = _make_context()
        for text in ["Silence", "silence on please", "Turn silence on"]:
            mock_silence.reset_mock()
            mock_send_to_claude.reset_mock()
            update = _make_update(text=text)
            await handle_message(update, context)
            mock_silence.enable.assert_not_called()
            mock_silence.disable.assert_not_called()


# --- Тесты обработки фото и документов ---


class TestHandlePhoto:
    """Тесты обработки фотографий."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
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

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_photo_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Photo input is blocked in global all mode."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update()
        update.message.photo = [MagicMock()]
        context = _make_context()
        await handle_photo(update, context)

        sent_text = _setup_application.bot.send_message.call_args.args[1]
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()


class TestHandleDocument:
    """Тесты обработки документов."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
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
    @patch("claude_manager.bot.claude_interaction.send_to_claude_and_respond", new_callable=AsyncMock)
    @patch("claude_manager.bot.telegram_file_downloader.download_and_save_file", new_callable=AsyncMock)
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

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    @patch.object(session_manager, "is_monitoring_mode")
    async def test_handle_document_in_all_projects_mode_mentions_project(
        self,
        mock_is_monitoring: MagicMock,
        mock_is_all_projects: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Document input is blocked in global all mode."""
        mock_is_monitoring.return_value = True
        mock_is_all_projects.return_value = True

        update = _make_update()
        update.message.document = MagicMock()
        context = _make_context()
        await handle_document(update, context)

        sent_text = _setup_application.bot.send_message.call_args.args[1]
        assert "проект" in sent_text.lower()
        assert "сесси" in sent_text.lower()


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

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.silence_mode_registry")
    async def test_silence_mode_suppresses_intermediate_messages(
        self,
        mock_silence: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Silence mode подавляет промежуточные сообщения (is_final=False)."""
        mock_silence.is_enabled.return_value = True
        await send_response(TEST_CHAT_ID, "thinking...", 1, is_final=False)
        _setup_application.bot.send_message.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.silence_mode_registry")
    async def test_silence_mode_passes_final_messages(
        self,
        mock_silence: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Silence mode пропускает финальные сообщения (is_final=True)."""
        mock_silence.is_enabled.return_value = True
        await send_response(TEST_CHAT_ID, "Готово", 1, is_final=True)
        _setup_application.bot.send_message.assert_called()


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

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.silence_mode_registry")
    async def test_silence_mode_suppresses_intermediate_watcher_messages(
        self,
        mock_silence: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Silence mode подавляет промежуточные сообщения от watcher."""
        mock_silence.is_enabled.return_value = True
        await send_watcher_message(
            TEST_CHAT_ID, "thinking...", TEST_SESSION_ID, 1, is_final=False
        )
        _setup_application.bot.send_message.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.silence_mode_registry")
    @patch.object(session_manager, "get_bound_session")
    async def test_silence_mode_passes_final_watcher_messages(
        self,
        mock_get_bound: MagicMock,
        mock_silence: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Silence mode пропускает финальные сообщения от watcher."""
        mock_silence.is_enabled.return_value = True
        mock_get_bound.return_value = TEST_SESSION_ID
        await send_watcher_message(
            TEST_CHAT_ID, "Готово", TEST_SESSION_ID, 1, is_final=True
        )
        _setup_application.bot.send_message.assert_called()


class TestSendAllProjectsWatcherMessage:
    """Tests for global all-mode message sending."""

    @pytest.mark.asyncio()
    async def test_header_starts_with_project_and_session_command(
        self,
        _setup_application: MagicMock,
    ) -> None:
        """All message starts with /<project>s<session> project-name."""
        await send_all_projects_watcher_message(
            TEST_CHAT_ID,
            project_number=3,
            session_number=12,
            project_name="bloger",
            session_id=TEST_SESSION_ID,
            backend=BackendName.CLAUDE,
            text="Ответ из другого проекта",
            is_final=True,
        )

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text.startswith("/3s12 bloger")
        assert "Ответ из другого проекта" in sent_text
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
        mock_builder.connect_timeout.return_value = mock_builder
        mock_builder.read_timeout.return_value = mock_builder
        mock_builder.write_timeout.return_value = mock_builder
        mock_builder.pool_timeout.return_value = mock_builder
        mock_builder.connection_pool_size.return_value = mock_builder
        mock_builder.build.return_value = mock_app
        mock_builder_class.return_value = mock_builder

        result = setup_bot()

        assert result is mock_app
        # Должно быть минимум 8 обработчиков
        # (new, sessions, stop, all, /N, text, photo, document)
        assert mock_app.add_handler.call_count >= 10

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_setup_bot_registers_all_projects_command_alias(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """setup_bot registers /all_projects as an alias for all-project mode."""
        mock_app = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_builder = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.post_init.return_value = mock_builder
        mock_builder.concurrent_updates.return_value = mock_builder
        mock_builder.connect_timeout.return_value = mock_builder
        mock_builder.read_timeout.return_value = mock_builder
        mock_builder.write_timeout.return_value = mock_builder
        mock_builder.pool_timeout.return_value = mock_builder
        mock_builder.connection_pool_size.return_value = mock_builder
        mock_builder.build.return_value = mock_app
        mock_builder_class.return_value = mock_builder

        setup_bot()

        registered_commands = [
            command
            for call in mock_app.add_handler.call_args_list
            for command in getattr(call.args[0], "commands", set())
        ]
        assert "all_projects" in registered_commands


# --- Тесты post_init ---


class TestPostInit:
    """Тесты инициализации бота (очистка файлов, восстановление состояния, меню)."""

    @pytest.fixture(autouse=True)
    def _disable_watcher_task(self):
        """post_init tests should not leave the infinite watcher task running."""
        with patch(
            "claude_manager.bot.asyncio.create_task",
            side_effect=_discard_background_coro,
        ):
            yield

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_sets_commands(
        self, mock_session_mgr: MagicMock, mock_silence: MagicMock, mock_clean: AsyncMock,
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
        assert any(command.command == "all_projects" for command in commands)

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_restores_bindings(
        self, mock_session_mgr: MagicMock, mock_silence: MagicMock, mock_clean: AsyncMock,
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
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_continues_on_restore_error(
        self, mock_session_mgr: MagicMock, mock_silence: MagicMock, mock_clean: AsyncMock,
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

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_loads_silence_mode(
        self, mock_session_mgr: MagicMock, mock_silence: MagicMock, mock_clean: AsyncMock,
    ) -> None:
        """post_init вызывает silence_mode_registry.load_state()."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await post_init(mock_app)

        mock_silence.load_state.assert_called_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.telegram_file_downloader.clean_old_received_files", new_callable=AsyncMock)
    @patch("claude_manager.bot.silence_mode_registry")
    @patch("claude_manager.bot.session_manager")
    async def test_post_init_handles_silence_mode_load_error(
        self, mock_session_mgr: MagicMock, mock_silence: MagicMock, mock_clean: AsyncMock,
    ) -> None:
        """post_init не падает при ошибке загрузки silence mode."""
        mock_session_mgr.load_bindings = AsyncMock()
        mock_session_mgr.get_all_bindings.return_value = {}
        mock_silence.load_state.side_effect = Exception("test error")
        mock_app = MagicMock()
        mock_app.bot = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        # Не должно выбросить исключение — бот продолжает инициализацию
        await post_init(mock_app)

        mock_app.bot.set_my_commands.assert_called_once()
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
        assert "/all all" in sent_text
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
    @patch.object(all_projects_monitor, "is_enabled_for_chat")
    async def test_marks_all_mode_when_enabled(
        self,
        mock_is_all_projects: MagicMock,
    ) -> None:
        """Project list marks global all mode instead of marking a concrete project."""
        mock_is_all_projects.return_value = True
        projects = [_make_project_info("alpha", is_current=True)]
        update = _make_update()
        context = MagicMock()

        with patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ):
            await handle_projects(update, context)

        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        lines = sent_text.splitlines()
        assert lines[0] == f"{PROJECT_CURRENT_MARKER} {ALL_PROJECTS_MODE_LINE}"
        assert lines[1] == "/p1 alpha"

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
    async def test_failed_switch_restores_all_projects_mode(self) -> None:
        """Failed project switch from all mode restores global monitoring."""
        projects = [_make_project_info("alpha", path="/fake/alpha")]
        update = _make_update(text="/p1")
        context = MagicMock()
        switch_result = project_manager.SwitchResult(
            success=False,
            already_active=False,
            old_path="/fake/beta",
            new_path="/fake/alpha",
            pending_messages_count=0,
            pending_messages=[],
            error_message="switch failed",
        )

        with patch.object(
            all_projects_monitor,
            "disable_for_chat",
            return_value=True,
        ) as disable_mock, patch.object(
            all_projects_monitor,
            "enable_for_chat",
            new_callable=AsyncMock,
        ) as enable_mock, patch.object(
            all_projects_monitor,
            "has_enabled_chats",
            return_value=False,
        ), patch.object(
            session_watcher,
            "resume_all",
        ) as resume_all_mock, patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager,
            "switch_project",
            new=AsyncMock(return_value=switch_result),
        ):
            await handle_switch_project(update, context)

        disable_mock.assert_called_once_with(TEST_CHAT_ID)
        enable_mock.assert_awaited_once_with(TEST_CHAT_ID)
        resume_all_mock.assert_not_called()
        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "switch failed" in sent

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
    async def test_all_mode_same_project_collects_pending_messages(self) -> None:
        """Exiting all into the already active project still delivers all-mode messages."""
        projects = [_make_project_info("alpha", path="/fake/alpha", is_current=True)]
        update = _make_update(text="/p1")
        context = MagicMock()
        pending = [
            project_manager.PendingDeliveryItem(
                session_id="sess-alpha",
                backend=BackendName.CLAUDE,
                text="Ответ из all",
                is_final=True,
            )
        ]
        switch_result = project_manager.SwitchResult(
            success=True,
            already_active=True,
            old_path="/fake/alpha",
            new_path="/fake/alpha",
            pending_messages_count=0,
            pending_messages=[],
            error_message="",
        )

        with patch.object(
            all_projects_monitor,
            "disable_for_chat",
            return_value=True,
        ), patch.object(
            all_projects_monitor,
            "has_enabled_chats",
            return_value=False,
        ), patch.object(
            session_watcher,
            "resume_all",
        ), patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager,
            "switch_project",
            new=AsyncMock(return_value=switch_result),
        ), patch.object(
            project_manager,
            "collect_pending_messages_for_project",
            new=AsyncMock(return_value=(1, pending)),
        ) as collect_mock, patch.object(
            bot_module,
            "_deliver_pending_messages",
            new=AsyncMock(),
        ) as deliver_mock:
            await handle_switch_project(update, context)

        collect_mock.assert_awaited_once_with("/fake/alpha")
        deliver_mock.assert_awaited_once_with(TEST_CHAT_ID, pending)
        sent = bot_module._application.bot.send_message.call_args.args[1]
        assert "Переключено на проект" in sent
        assert "Непрочитанных сообщений: 1" in sent

    @pytest.mark.asyncio()
    async def test_silence_mode_hidden_pending_not_counted_after_switch(self) -> None:
        """Промежуточные pending-сообщения не считаются видимыми в silence mode."""
        projects = [_make_project_info("beta", path="/fake/beta")]
        update = _make_update(text="/p1")
        context = MagicMock()
        silence_mode_registry._silence_enabled = True

        pending = [
            project_manager.PendingDeliveryItem(
                session_id="codex-session",
                backend=BackendName.CODEX,
                text="Промежуточный ответ из фона",
                is_final=False,
            )
        ]
        switch_result = project_manager.SwitchResult(
            success=True, already_active=False,
            old_path="/fake/alpha", new_path="/fake/beta",
            pending_messages_count=1, pending_messages=pending,
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
            new=AsyncMock(),
        ) as register_session_mock:
            await handle_switch_project(update, context)

        sent = bot_module._application.bot.send_message
        switch_text = sent.call_args_list[0].args[1]
        assert "Непрочитанных сообщений" not in switch_text
        assert sent.await_count == 1
        register_session_mock.assert_not_awaited()

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

    @pytest.mark.asyncio()
    async def test_delivered_pending_message_clears_unread_snapshot(self) -> None:
        """После успешной pending-доставки старый unread snapshot очищается."""
        pending = [
            project_manager.PendingDeliveryItem(
                session_id="codex-session",
                backend=BackendName.CODEX,
                text="Ответ из фона",
                is_final=True,
            )
        ]

        with patch.object(
            daily_session_registry, "register_session",
            new=AsyncMock(return_value=7),
        ), patch(
            "claude_manager.unread_buffer.clear_snapshot_for_session_backend_pair"
        ) as clear_snapshot_mock:
            await bot_module._deliver_pending_messages(TEST_CHAT_ID, pending)

        clear_snapshot_mock.assert_called_once_with(
            "codex-session",
            BackendName.CODEX,
        )

    @pytest.mark.asyncio()
    async def test_silence_mode_hidden_pending_does_not_clear_snapshot(self) -> None:
        """Подавленное pending-сообщение не помечается доставленным."""
        pending = [
            project_manager.PendingDeliveryItem(
                session_id="codex-session",
                backend=BackendName.CODEX,
                text="Промежуточный ответ из фона",
                is_final=False,
            )
        ]
        silence_mode_registry._silence_enabled = True
        bot_module.unread_buffer.save_snapshot(
            "codex-session",
            BackendName.CODEX,
            raw_record_count=10,
            last_delivered_idx=3,
        )

        with patch.object(
            daily_session_registry, "register_session",
            new=AsyncMock(),
        ) as register_session_mock:
            await bot_module._deliver_pending_messages(TEST_CHAT_ID, pending)

        register_session_mock.assert_not_awaited()
        bot_module._application.bot.send_message.assert_not_called()
        assert bot_module.unread_buffer.restore_snapshot(
            "codex-session",
            BackendName.CODEX,
        ) is not None


class TestHandleSwitchProjectSession:
    """Tests for clickable /<project>s<session> commands from all mode."""

    @pytest.mark.asyncio()
    async def test_switches_project_and_binds_session_from_all_link(self) -> None:
        """All-mode command switches project and binds the exact linked session."""
        target = all_projects_monitor.AllProjectSessionLink(
            project_number=2,
            session_number=9,
            project_name="beta",
            project_path="/fake/beta",
            session_id="sess-beta",
            backend=BackendName.CODEX,
        )
        projects = [
            _make_project_info("alpha"),
            _make_project_info("beta", path="/fake/beta"),
        ]
        update = _make_update(text="/2s9")
        context = MagicMock()
        switch_result = project_manager.SwitchResult(
            success=True,
            already_active=False,
            old_path="/fake/alpha",
            new_path="/fake/beta",
            pending_messages_count=0,
            pending_messages=[],
            error_message="",
        )

        with patch.object(
            all_projects_monitor,
            "resolve_link",
            return_value=target,
        ), patch.object(
            all_projects_monitor,
            "disable_for_chat",
            return_value=True,
        ) as disable_mock, patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager,
            "switch_project",
            new=AsyncMock(return_value=switch_result),
        ) as switch_mock, patch.object(
            session_manager,
            "set_active_session",
            new=AsyncMock(return_value=9),
        ) as set_active_mock:
            await handle_switch_project_session(update, context)

        disable_mock.assert_called_once_with(TEST_CHAT_ID)
        switch_mock.assert_awaited_once_with("/fake/beta")
        set_active_mock.assert_awaited_once_with(
            TEST_CHAT_ID,
            "sess-beta",
            BackendName.CODEX,
        )
        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        assert "beta" in sent_text
        assert "#9" in sent_text

    @pytest.mark.asyncio()
    async def test_failed_link_switch_restores_all_projects_mode(self) -> None:
        """Failed all-link project switch restores global monitoring."""
        target = all_projects_monitor.AllProjectSessionLink(
            project_number=2,
            session_number=9,
            project_name="beta",
            project_path="/fake/beta",
            session_id="sess-beta",
            backend=BackendName.CODEX,
        )
        projects = [
            _make_project_info("alpha"),
            _make_project_info("beta", path="/fake/beta"),
        ]
        update = _make_update(text="/2s9")
        context = MagicMock()
        switch_result = project_manager.SwitchResult(
            success=False,
            already_active=False,
            old_path="/fake/alpha",
            new_path="/fake/beta",
            pending_messages_count=0,
            pending_messages=[],
            error_message="switch failed",
        )

        with patch.object(
            all_projects_monitor,
            "resolve_link",
            return_value=target,
        ), patch.object(
            all_projects_monitor,
            "disable_for_chat",
            return_value=True,
        ) as disable_mock, patch.object(
            all_projects_monitor,
            "enable_for_chat",
            new_callable=AsyncMock,
        ) as enable_mock, patch.object(
            all_projects_monitor,
            "has_enabled_chats",
            return_value=False,
        ), patch.object(
            session_watcher,
            "resume_all",
        ) as resume_all_mock, patch.object(
            project_manager,
            "scan_available_projects",
            new=AsyncMock(return_value=projects),
        ), patch.object(
            project_manager,
            "switch_project",
            new=AsyncMock(return_value=switch_result),
        ), patch.object(
            session_manager,
            "set_active_session",
            new_callable=AsyncMock,
        ) as set_active_mock:
            await handle_switch_project_session(update, context)

        disable_mock.assert_called_once_with(TEST_CHAT_ID)
        enable_mock.assert_awaited_once_with(TEST_CHAT_ID)
        resume_all_mock.assert_not_called()
        set_active_mock.assert_not_awaited()
        sent_text = bot_module._application.bot.send_message.call_args.args[1]
        assert "switch failed" in sent_text


# --- Тесты send_response с файловыми маркерами ---


class TestSendResponseFileMarkers:
    """Тесты обработки маркеров [SEND_FILE:path] в send_response."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/test.md"],
    )
    async def test_send_response_with_text_file_marker_sends_as_document(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_send_document: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Финальный ответ с маркером текстового файла — отправляется как document-вложение."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/test.md]",
            1,
            is_final=True,
        )
        mock_extract.assert_called_once()
        mock_send_document.assert_awaited_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.file_delivery.process_file_markers", new_callable=AsyncMock)
    @patch("claude_manager.bot.file_delivery.process_show_file_markers", new_callable=AsyncMock)
    async def test_send_response_not_final_skips_file_markers(
        self,
        mock_process_show: AsyncMock,
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
        mock_process_show.assert_not_awaited()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/image.png"],
    )
    async def test_send_response_with_binary_file_marker(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_send_document: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Маркер бинарного файла — отправляется как документ."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/image.png]",
            1,
            is_final=True,
        )
        mock_send_document.assert_awaited_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.file_delivery.send_as_document", new_callable=AsyncMock)
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
        mock_send_document: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Два маркера — оба файла отправлены как document-вложения."""
        await send_response(
            TEST_CHAT_ID,
            "answer [SEND_FILE:/tmp/a.md] [SEND_FILE:/tmp/b.md]",
            1,
            is_final=True,
        )
        assert mock_send_document.await_count == 2

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.telegram_sender.send_telegram_message", new_callable=AsyncMock)
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
    @patch("claude_manager.bot.file_delivery.send_as_document", new_callable=AsyncMock)
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
        mock_send_document: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Финальный ответ watcher с маркером — файл отправлен как document-вложение."""
        await send_watcher_message(
            TEST_CHAT_ID, "ответ [SEND_FILE:/tmp/file.md]",
            TEST_SESSION_ID, 1, is_final=True,
        )
        mock_send_document.assert_awaited_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.file_delivery.process_file_markers", new_callable=AsyncMock)
    @patch("claude_manager.bot.file_delivery.process_show_file_markers", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session", return_value=TEST_SESSION_ID)
    async def test_send_watcher_message_not_final_skips_markers(
        self,
        mock_get_bound: MagicMock,
        mock_process_show: AsyncMock,
        mock_process: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Промежуточный ответ watcher — маркеры не обрабатываются."""
        await send_watcher_message(
            TEST_CHAT_ID, "text [SEND_FILE:/tmp/file.md]",
            TEST_SESSION_ID, 1, is_final=False,
        )
        mock_process.assert_not_awaited()
        mock_process_show.assert_not_awaited()


class TestHandleRestart:
    """Тесты обработчика /restart — самоперезапуск бота."""

    async def test_sends_warning_message_and_writes_marker(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """handle_restart: пишет маркер с chat_id и шлёт «Перезапускаюсь через 2 сек»."""
        from claude_manager import bot

        marker_path = tmp_path / "restart-marker"
        monkeypatch.setattr(bot, "RESTART_MARKER_PATH", marker_path)

        update = MagicMock()
        update.effective_chat.id = 12345
        update.effective_user.id = next(iter(config_module.ALLOWED_USER_IDS))
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        with patch("claude_manager.bot.asyncio.create_subprocess_exec", new=AsyncMock()) as mock_exec:
            await bot.handle_restart(update, context)

        assert marker_path.read_text() == "12345"
        context.bot.send_message.assert_awaited_once()
        sent_text = context.bot.send_message.call_args[0][1]
        assert "Перезапускаюсь" in sent_text
        assert "2" in sent_text

    async def test_launches_detached_systemctl_subprocess(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """handle_restart: запускает bash -c 'sleep 2 && systemctl --user restart ...' detached."""
        from claude_manager import bot

        monkeypatch.setattr(bot, "RESTART_MARKER_PATH", tmp_path / "marker")

        update = MagicMock()
        update.effective_chat.id = 12345
        update.effective_user.id = next(iter(config_module.ALLOWED_USER_IDS))
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        with patch("claude_manager.bot.asyncio.create_subprocess_exec", new=AsyncMock()) as mock_exec:
            await bot.handle_restart(update, context)

        mock_exec.assert_awaited_once()
        args, kwargs = mock_exec.call_args
        # bash, "-c", "sleep 2 && systemctl --user restart claude-manager.service"
        assert args[0] == "bash"
        assert args[1] == "-c"
        assert "systemctl --user restart claude-manager.service" in args[2]
        assert "sleep 2" in args[2]
        assert kwargs.get("start_new_session") is True
        # stdout/stderr должны быть DEVNULL чтобы пайпы не висели
        assert kwargs.get("stdout") == asyncio.subprocess.DEVNULL
        assert kwargs.get("stderr") == asyncio.subprocess.DEVNULL
