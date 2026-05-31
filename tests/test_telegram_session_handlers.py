"""Тесты Telegram handlers для управления сессиями."""

from types import SimpleNamespace
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
from claude_manager.coding_agent_backend import BackendName
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
        session_files: list[object],
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.session_files = session_files

    async def list_session_files_for_project(
        self,
        _project_dir: str,
    ) -> list[object]:
        """Возвращает заранее заданные файлы сессий."""
        return self.session_files


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

    @staticmethod
    def _row(
        session_id: str,
        backend: BackendName = BackendName.CLAUDE,
        preview: str = "Preview",
        last_modified_at: float = 10.0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            session_id=session_id,
            backend=backend,
            preview=preview,
            last_modified_at=last_modified_at,
        )

    @staticmethod
    def _recent_result(
        rows: list[SimpleNamespace],
        degraded_messages: list[str] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            rows=rows,
            degraded_messages=degraded_messages or [],
        )

    @staticmethod
    def _direct_listing_guard(
        backend: BackendName = BackendName.CLAUDE,
    ) -> FakeBackendForSessionList:
        direct_backend = FakeBackendForSessionList(backend, "unused", [])
        direct_backend.list_session_files_for_project = AsyncMock(
            side_effect=AssertionError("direct backend listing is obsolete")
        )
        return direct_backend

    async def _handle_with_recent(
        self,
        mock_recent: AsyncMock,
        direct_backend: FakeBackendForSessionList,
    ) -> None:
        update = _make_update(text="/sessions")
        context = _make_context()
        with (
            patch.object(
                session_handlers,
                "recent_sessions_refresh",
                SimpleNamespace(get_project_recent_sessions=mock_recent),
                create=True,
            ),
            patch.object(
                coding_agent_backend,
                "get_all_backends",
                return_value=[direct_backend],
            ),
        ):
            await session_handlers.handle_sessions(update, context)

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(
        daily_session_registry,
        "get_session_summary",
        new_callable=AsyncMock,
        create=True,
    )
    async def test_handle_sessions_reads_recent_rows_without_backend_listing(
        self,
        mock_get_summary: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions читает recent rows и не сканирует backend напрямую."""
        row = self._row("codex-new", BackendName.CODEX, "Смержить ветку", 20.0)
        mock_recent = AsyncMock(return_value=self._recent_result([row]))
        direct_backend = self._direct_listing_guard(
            BackendName.CODEX,
        )
        mock_register.return_value = 1
        mock_get_summary.return_value = ""

        await self._handle_with_recent(mock_recent, direct_backend)

        mock_recent.assert_awaited_once_with(
            config_module.WORKING_DIR,
            limit=session_handlers.SESSION_LIST_LIMIT,
            refresh_on_hit=True,
        )
        direct_backend.list_session_files_for_project.assert_not_awaited()
        mock_register.assert_awaited_once_with("codex-new", BackendName.CODEX)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text == "/1 ⚡ Смержить ветку"
        assert sent.call_args[1].get("parse_mode") is None

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(
        daily_session_registry,
        "get_session_summary",
        new_callable=AsyncMock,
        create=True,
    )
    async def test_handle_sessions_caps_recent_rows_to_session_limit(
        self,
        mock_get_summary: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions показывает не больше SESSION_LIST_LIMIT строк."""
        rows = [
            self._row(f"id-{number}", preview=f"Preview {number}", last_modified_at=float(number))
            for number in range(30)
        ]
        mock_recent = AsyncMock(return_value=self._recent_result(rows))
        direct_backend = self._direct_listing_guard()
        mock_register.side_effect = range(1, 31)
        mock_get_summary.return_value = ""

        await self._handle_with_recent(mock_recent, direct_backend)

        direct_backend.list_session_files_for_project.assert_not_awaited()
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        lines = sent_text.splitlines()
        assert len(lines) == session_handlers.SESSION_LIST_LIMIT
        assert lines[0].endswith("Preview 0")
        assert lines[-1].endswith("Preview 14")

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
        row = self._row(
            "id-1",
            preview="Давай доработаем инициализирующий скрипт загрузки данных по отзывам",
        )
        mock_recent = AsyncMock(return_value=self._recent_result([row]))
        direct_backend = self._direct_listing_guard()
        mock_register.return_value = 1
        mock_get_summary.return_value = "Загрузка отзывов за период"

        await self._handle_with_recent(mock_recent, direct_backend)

        direct_backend.list_session_files_for_project.assert_not_awaited()
        mock_get_summary.assert_awaited_once_with("id-1", BackendName.CLAUDE)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "/1 🤖 Загрузка отзывов за период" in sent_text
        assert "инициализирующий скрипт" not in sent_text

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(
        daily_session_registry,
        "get_session_summary",
        new_callable=AsyncMock,
        create=True,
    )
    async def test_handle_sessions_shows_list(
        self,
        mock_get_summary: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions показывает список сессий."""
        rows = [
            self._row("id-1", preview="Первая сессия"),
            self._row("id-2", BackendName.CODEX, "Вторая сессия", 11.0),
            self._row("id-3", preview="Третья сессия", last_modified_at=12.0),
        ]
        mock_recent = AsyncMock(return_value=self._recent_result(rows))
        direct_backend = self._direct_listing_guard()
        mock_register.side_effect = [1, 2, 3]
        mock_get_summary.return_value = ""

        await self._handle_with_recent(mock_recent, direct_backend)

        direct_backend.list_session_files_for_project.assert_not_awaited()
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
        """Пустой список сессий сохраняет смысл даже с degraded warning."""
        mock_recent = AsyncMock(
            return_value=self._recent_result([], ["codex temporarily unavailable"])
        )
        direct_backend = self._direct_listing_guard()

        await self._handle_with_recent(mock_recent, direct_backend)

        direct_backend.list_session_files_for_project.assert_not_awaited()
        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text.splitlines() == [
            "Нет сессий",
            "codex temporarily unavailable",
        ]

    @pytest.mark.asyncio()
    async def test_handle_sessions_reports_recent_query_failure(
        self,
        _setup_application: MagicMock,
    ) -> None:
        """Сбой recent store не должен оставлять пользователя без ответа."""
        mock_recent = AsyncMock(side_effect=RuntimeError("store locked"))
        direct_backend = self._direct_listing_guard()

        await self._handle_with_recent(mock_recent, direct_backend)

        direct_backend.list_session_files_for_project.assert_not_awaited()
        sent = _setup_application.bot.send_message
        sent.assert_called_once()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text == "Не удалось прочитать список сессий. Попробуйте ещё раз"
        assert sent.call_args[1].get("parse_mode") is None

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(
        daily_session_registry,
        "get_session_summary",
        new_callable=AsyncMock,
        create=True,
    )
    async def test_handle_sessions_appends_degraded_messages(
        self,
        mock_get_summary: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Команда /sessions добавляет предупреждения из recent refresh."""
        row = self._row("id-1", preview="Первая сессия")
        mock_recent = AsyncMock(
            return_value=self._recent_result(
                [row], ["codex failed for /tmp/test_working_dir"]
            )
        )
        direct_backend = self._direct_listing_guard()
        mock_register.return_value = 1
        mock_get_summary.return_value = ""

        await self._handle_with_recent(mock_recent, direct_backend)

        direct_backend.list_session_files_for_project.assert_not_awaited()
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text.splitlines() == [
            "/1 🤖 Первая сессия",
            "codex failed for /tmp/test_working_dir",
        ]


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
        mock_enable_all_projects.return_value = SimpleNamespace(
            enabled=True,
            message="custom all project message",
        )
        update = _make_update(text="/all")
        context = _make_context()
        await session_handlers.handle_all(update, context)

        mock_unbind.assert_called_once_with(TEST_CHAT_ID)
        mock_enable_all_projects.assert_awaited_once_with(TEST_CHAT_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text == "custom all project message"
        assert sent.call_args[1].get("parse_mode") is None

    @pytest.mark.asyncio()
    @patch.object(all_projects_monitor, "enable_for_chat", new_callable=AsyncMock)
    @patch.object(session_manager, "unbind_session", new_callable=AsyncMock)
    async def test_handle_all_projects_alias_switches_to_monitoring(
        self,
        mock_unbind: AsyncMock,
        mock_enable_all_projects: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Failed /all_projects entry keeps the current session binding."""
        mock_enable_all_projects.return_value = SimpleNamespace(
            enabled=False,
            message="no indexed recent sessions",
        )
        update = _make_update(text="/all_projects")
        context = _make_context()
        await session_handlers.handle_all(update, context)

        mock_unbind.assert_not_called()
        mock_enable_all_projects.assert_awaited_once_with(TEST_CHAT_ID)
        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert sent_text == "no indexed recent sessions"
        assert sent.call_args[1].get("parse_mode") is None


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
