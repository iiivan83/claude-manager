"""Тесты Telegram handlers для управления сессиями."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    current_backend_registry,
    daily_session_registry,
    process_manager,
    session_manager,
    telegram_session_handlers as session_handlers,
)
from claude_manager.coding_agent_backend import BackendName, SessionFileInfo
from claude_manager.process_manager import StopResult
from claude_manager.session_manager import (
    ActiveSession,
    NewSessionResult,
    SwitchResult,
)
import claude_manager.bot as bot_module
import claude_manager.config as config_module


ALLOWED_USER_ID = 12345
DENIED_USER_ID = 99999
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "abc-def-111"


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
    """Настраивает config для session-handler тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_e2e = config_module.E2E_TEST_USER_ID
    original_working_dir = config_module.WORKING_DIR
    original_current_backend = current_backend_registry._current_backend
    original_current_backend_loaded = current_backend_registry._loaded_from_disk
    original_bindings = session_manager._bindings.copy()
    original_bindings_path = session_manager._bindings_path
    original_bindings_loaded = session_manager._bindings_loaded_from_disk
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.E2E_TEST_USER_ID = None
    config_module.WORKING_DIR = "/tmp/test_working_dir"
    current_backend_registry._current_backend = BackendName.CLAUDE
    current_backend_registry._loaded_from_disk = True
    session_manager._bindings = {}
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.E2E_TEST_USER_ID = original_e2e
    config_module.WORKING_DIR = original_working_dir
    current_backend_registry._current_backend = original_current_backend
    current_backend_registry._loaded_from_disk = original_current_backend_loaded
    session_manager._bindings = original_bindings
    session_manager._bindings_path = original_bindings_path
    session_manager._bindings_loaded_from_disk = original_bindings_loaded


@pytest.fixture(autouse=True)
def _setup_application():
    """Устанавливает фейковый Application для session-handler модуля."""
    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    original = bot_module._application
    bot_module._application = mock_app
    session_handlers.init_callbacks(
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
    """Создаёт фейковый Update для session-handler тестов."""
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
        await session_handlers.handle_new(update, context)

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
        await session_handlers.handle_new(update, context)

        mock_create_session.assert_called_once_with(TEST_CHAT_ID, BackendName.CLAUDE)

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get(
            "text", sent.call_args[0][1] if len(sent.call_args[0]) > 1 else ""
        )
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
        await session_handlers.handle_new(update, context)

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
        await session_handlers.handle_new(update, context)
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
            await session_handlers.handle_sessions(update, context)

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
            await session_handlers.handle_sessions(update, context)

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
        mock_register.side_effect = [1, 2, 3]

        update = _make_update(text="/sessions")
        context = _make_context()
        with patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[claude_backend],
        ):
            await session_handlers.handle_sessions(update, context)

        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "/1" in sent_text
        assert "/2" in sent_text
        assert "/3" in sent_text
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
            await session_handlers.handle_sessions(update, context)

        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Нет сессий" in sent_text


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
        await session_handlers.handle_stop(update, context)

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
        await session_handlers.handle_stop(update, context)

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
        await session_handlers.handle_stop(update, context)

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
        await session_handlers.handle_stop(update, context)

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
        await session_handlers.handle_all(update, context)

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
        await session_handlers.handle_all(update, context)

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
        await session_handlers.handle_switch_session(update, context)

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
        await session_handlers.handle_switch_session(update, context)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "#99" in sent_text
        assert "не найдена" in sent_text
