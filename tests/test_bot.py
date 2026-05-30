"""Тесты модуля bot — транспортный слой Telegram-бота."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ParseMode

from claude_manager import (
    all_projects_monitor,
    current_backend_registry,
    daily_session_registry,
    session_manager,
    session_reader,
    session_watcher,
    silence_mode_registry,
)
from claude_manager.coding_agent_backend import BackendName

from claude_manager.bot import (
    ALL_PROJECTS_MODE_LINE,
    EMPTY_PROJECTS_TEMPLATE,
    INVALID_PROJECT_NUMBER_TEMPLATE,
    PROJECT_ALREADY_ACTIVE_TEMPLATE,
    PROJECT_CURRENT_MARKER,
    PROJECT_SWITCH_ERROR_TEMPLATE,
    PROJECT_SWITCH_SUCCESS_TEMPLATE,
    _check_access,
    handle_projects,
    handle_switch_project,
    handle_switch_project_session,
    setup_bot,
)
from claude_manager.telegram_response_delivery import (
    _format_clickable_session_number,
    _format_session_header, _is_current_session,
    send_all_projects_watcher_message,
    send_response, send_watcher_message,
)
from claude_manager.claude_interaction import (
    EMPTY_RESPONSE_TEXT,
    MONITORING_MODE_MESSAGE,
    NO_RESPONSE_MARKER,
)
from claude_manager import file_sender, project_manager, project_pending_delivery, unread_buffer
import claude_manager.bot as bot_module
import claude_manager.config as config_module
import claude_manager.telegram_response_delivery as delivery_module
from claude_manager.session_manager import ActiveSession
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
    original_e2e = config_module.E2E_TEST_USER_ID
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
    config_module.E2E_TEST_USER_ID = None
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.E2E_TEST_USER_ID = original_e2e
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
    original = bot_module._application, delivery_module._application
    bot_module._application = delivery_module._application = mock_app
    yield mock_app
    bot_module._application, delivery_module._application = original


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
    @patch("claude_manager.telegram_response_delivery.silence_mode_registry")
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
    @patch("claude_manager.telegram_response_delivery.silence_mode_registry")
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
    @patch("claude_manager.telegram_response_delivery.silence_mode_registry")
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
    @patch("claude_manager.telegram_response_delivery.silence_mode_registry")
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


def test_bot_reexports_agent_handlers() -> None:
    """Old imports from claude_manager.bot keep working for agent handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_agent_handlers

    assert bot_module.handle_agent is telegram_agent_handlers.handle_agent
    assert bot_module.handle_agent_callback is telegram_agent_handlers.handle_agent_callback


def test_bot_reexports_session_handlers() -> None:
    """Old imports from claude_manager.bot keep working for session handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_session_handlers

    assert bot_module.handle_new is telegram_session_handlers.handle_new
    assert bot_module.handle_sessions is telegram_session_handlers.handle_sessions
    assert bot_module.handle_stop is telegram_session_handlers.handle_stop
    assert bot_module.handle_all is telegram_session_handlers.handle_all
    assert (
        bot_module.handle_switch_session
        is telegram_session_handlers.handle_switch_session
    )


def test_bot_reexports_input_handlers() -> None:
    """Old imports from claude_manager.bot keep working for input handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_input_handlers

    assert bot_module.handle_message is telegram_input_handlers.handle_message
    assert bot_module.handle_photo is telegram_input_handlers.handle_photo
    assert bot_module.handle_document is telegram_input_handlers.handle_document


def test_bot_reexports_lifecycle_handlers() -> None:
    """Old imports from claude_manager.bot keep working for lifecycle handlers."""
    from claude_manager import bot as bot_module
    from claude_manager import telegram_lifecycle_handlers

    assert bot_module.post_init is telegram_lifecycle_handlers.post_init
    assert bot_module.handle_restart is telegram_lifecycle_handlers.handle_restart
    assert bot_module.handle_silence_on is telegram_lifecycle_handlers.handle_silence_on
    assert bot_module.handle_silence_off is telegram_lifecycle_handlers.handle_silence_off
    assert bot_module.BOT_COMMANDS is telegram_lifecycle_handlers.BOT_COMMANDS


class TestSetupBot:
    """Тесты настройки бота."""

    @patch("claude_manager.bot.ApplicationBuilder")
    def test_setup_bot_registers_handlers_in_expected_order(
        self,
        mock_builder_class: MagicMock,
    ) -> None:
        """setup_bot registers command handlers before broad text handlers."""
        mock_app = MagicMock()
        mock_app.add_handler = MagicMock()
        mock_app.add_error_handler = MagicMock()
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
        handlers = [call.args[0] for call in mock_app.add_handler.call_args_list]
        command_sets = [getattr(handler, "commands", set()) for handler in handlers]
        assert command_sets[0] == {"new"}
        assert command_sets[1] == {"agent"}
        assert command_sets[3] == {"sessions"}
        assert command_sets[4] == {"stop"}
        assert command_sets[5] == {"all", "all_projects"}
        assert command_sets[6] == {"projects"}
        assert command_sets[7] == {"silence_on"}
        assert command_sets[8] == {"silence_off"}
        assert command_sets[9] == {"restart"}
        assert mock_app.add_error_handler.called

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
            project_pending_delivery,
            "collect_pending_messages",
            new=AsyncMock(return_value=(1, pending)),
        ) as collect_mock, patch.object(
            project_pending_delivery,
            "deliver_pending_messages",
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
            await project_pending_delivery.deliver_pending_messages(TEST_CHAT_ID, pending)

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
        unread_buffer.save_snapshot(
            "codex-session",
            BackendName.CODEX,
            raw_record_count=10,
            last_delivered_idx=3,
        )

        with patch.object(
            daily_session_registry, "register_session",
            new=AsyncMock(),
        ) as register_session_mock:
            await project_pending_delivery.deliver_pending_messages(TEST_CHAT_ID, pending)

        register_session_mock.assert_not_awaited()
        bot_module._application.bot.send_message.assert_not_called()
        assert unread_buffer.restore_snapshot(
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
    @patch("claude_manager.telegram_response_delivery.file_delivery.send_as_document", new_callable=AsyncMock)
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
    @patch("claude_manager.telegram_response_delivery.file_delivery.process_file_markers", new_callable=AsyncMock)
    @patch("claude_manager.telegram_response_delivery.file_delivery.process_show_file_markers", new_callable=AsyncMock)
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
    @patch("claude_manager.telegram_response_delivery.file_delivery.send_as_document", new_callable=AsyncMock)
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
    @patch("claude_manager.telegram_response_delivery.file_delivery.send_as_document", new_callable=AsyncMock)
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
    @patch("claude_manager.telegram_response_delivery.telegram_sender.send_telegram_message", new_callable=AsyncMock)
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
    @patch("claude_manager.telegram_response_delivery.file_delivery.send_as_document", new_callable=AsyncMock)
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
    @patch("claude_manager.telegram_response_delivery.file_delivery.process_file_markers", new_callable=AsyncMock)
    @patch("claude_manager.telegram_response_delivery.file_delivery.process_show_file_markers", new_callable=AsyncMock)
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
