"""Тесты модуля session_watcher — мониторинг сессий Claude Code в реальном времени."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import session_watcher
from claude_manager.session_watcher import (
    ERROR_RETRY_DELAY_SECONDS,
    _extract_assistant_messages,
    _extract_message_text,
    _get_sessions_to_monitor,
    _is_empty_response,
    get_seen_counts_snapshot,
)


# --- Вспомогательные инструменты ---

# ID пользователя для тестов
TEST_CHAT_ID = 12345


def _make_assistant_message(text: str, content_as_list: bool = False) -> dict:
    """Создаёт запись сообщения Claude для тестирования."""
    if content_as_list:
        content = [{"type": "text", "text": text}]
    else:
        content = text
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
    }


def _make_user_message(text: str) -> dict:
    """Создаёт запись сообщения пользователя для тестирования."""
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
    }


def _make_system_message() -> dict:
    """Создаёт системную запись для тестирования."""
    return {"type": "system", "timestamp": "2026-03-30T10:00:00Z"}


def _make_progress_message() -> dict:
    """Создаёт событие прогресса (выполнение инструмента) для тестирования."""
    return {"type": "progress", "message": {"content": ""}}


@pytest.fixture(autouse=True)
def _reset_watcher_state():
    """Сбрасывает внутреннее состояние watcher перед каждым тестом."""
    session_watcher._seen_message_counts = {}
    session_watcher._paused_sessions = set()
    session_watcher._callback = None
    session_watcher._get_current_session = None
    yield
    session_watcher._seen_message_counts = {}
    session_watcher._paused_sessions = set()
    session_watcher._callback = None
    session_watcher._get_current_session = None


@pytest.fixture()
def mock_callback() -> AsyncMock:
    """Фейковый callback для отслеживания вызовов при обнаружении сообщений."""
    return AsyncMock()


@pytest.fixture()
def mock_get_current_session() -> AsyncMock:
    """Фейковая функция получения текущей сессии пользователя."""
    return AsyncMock(return_value=None)


# --- Юнит-тесты _is_empty_response ---


class TestIsEmptyResponse:
    """Тесты проверки пустых и служебных ответов Claude."""

    def test_empty_string(self) -> None:
        """Пустая строка считается пустым ответом."""
        assert _is_empty_response("") is True

    def test_whitespace_only(self) -> None:
        """Строка из пробелов считается пустым ответом."""
        assert _is_empty_response("   \n\t  ") is True

    def test_no_response_requested(self) -> None:
        """Служебный ответ 'No response requested.' считается пустым."""
        assert _is_empty_response("No response requested.") is True

    def test_no_response_requested_with_whitespace(self) -> None:
        """Служебный ответ с пробелами по краям тоже считается пустым."""
        assert _is_empty_response("  No response requested.  ") is True

    def test_normal_text(self) -> None:
        """Нормальный текст не считается пустым."""
        assert _is_empty_response("Файл main.py содержит точку входа") is False

    def test_short_text(self) -> None:
        """Короткий, но непустой текст не считается пустым."""
        assert _is_empty_response("OK") is False


# --- Юнит-тесты _extract_message_text ---


class TestExtractMessageText:
    """Тесты извлечения текста из сообщений Claude."""

    def test_string_content(self) -> None:
        """Извлечение текста из сообщения со строковым content."""
        message = _make_assistant_message("Привет, вот ответ")
        result = _extract_message_text(message)
        assert result == "Привет, вот ответ"

    def test_list_content(self) -> None:
        """Извлечение текста из сообщения с content в виде списка."""
        message = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Часть 1"},
                    {"type": "text", "text": "Часть 2"},
                ],
            },
        }
        result = _extract_message_text(message)
        assert result == "Часть 1 Часть 2"

    def test_list_content_with_non_text_blocks(self) -> None:
        """Нетекстовые блоки в списке content игнорируются."""
        message = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "bash"},
                    {"type": "text", "text": "Результат"},
                ],
            },
        }
        result = _extract_message_text(message)
        assert result == "Результат"

    def test_user_message_returns_none(self) -> None:
        """Сообщение пользователя (не assistant) возвращает None."""
        message = _make_user_message("Посмотри файл")
        result = _extract_message_text(message)
        assert result is None

    def test_no_content_returns_none(self) -> None:
        """Сообщение без поля content возвращает None."""
        message = {"type": "assistant", "message": {}}
        result = _extract_message_text(message)
        assert result is None

    def test_empty_list_content_returns_none(self) -> None:
        """Пустой список content возвращает None."""
        message = {"type": "assistant", "message": {"content": []}}
        result = _extract_message_text(message)
        assert result is None


# --- Юнит-тесты _extract_assistant_messages ---


class TestExtractAssistantMessages:
    """Тесты извлечения новых ответов Claude из списка сообщений."""

    def test_extracts_new_assistant_messages(self) -> None:
        """Извлекает текст новых сообщений Claude."""
        all_messages = [
            _make_system_message(),
            _make_user_message("Вопрос 1"),
            _make_assistant_message("Ответ 1"),
            _make_user_message("Вопрос 2"),
            _make_assistant_message("Ответ 2"),
        ]
        # Первые 3 строки уже обработаны
        result = _extract_assistant_messages(all_messages, already_seen_count=3)
        assert result == ["Ответ 2"]

    def test_ignores_user_messages(self) -> None:
        """Сообщения пользователя не включаются в результат."""
        all_messages = [
            _make_user_message("Вопрос"),
            _make_assistant_message("Ответ"),
            _make_user_message("Ещё вопрос"),
        ]
        result = _extract_assistant_messages(all_messages, already_seen_count=0)
        assert result == ["Ответ"]

    def test_no_new_messages(self) -> None:
        """Если новых сообщений нет — пустой список."""
        all_messages = [
            _make_system_message(),
            _make_user_message("Вопрос"),
            _make_assistant_message("Ответ"),
        ]
        result = _extract_assistant_messages(all_messages, already_seen_count=3)
        assert result == []

    def test_multiple_new_assistant_messages(self) -> None:
        """Несколько новых сообщений Claude извлекаются по порядку."""
        all_messages = [
            _make_system_message(),
            _make_assistant_message("Первый"),
            _make_assistant_message("Второй"),
            _make_assistant_message("Третий"),
        ]
        result = _extract_assistant_messages(all_messages, already_seen_count=1)
        assert result == ["Первый", "Второй", "Третий"]


# --- Юнит-тесты _get_sessions_to_monitor ---


class TestGetSessionsToMonitor:
    """Тесты получения списка сессий для мониторинга."""

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_combines_reader_and_registry(
        self, mock_reader, mock_registry
    ) -> None:
        """Объединяет сессии из session_reader и daily_session_registry."""
        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[
                _FakeSessionInfo("A"),
                _FakeSessionInfo("B"),
            ]
        )
        mock_registry.get_all_today_sessions = AsyncMock(
            return_value={1: "B", 2: "C"}
        )

        result = await _get_sessions_to_monitor()

        assert "A" in result
        assert "B" in result
        assert "C" in result
        # Без дубликатов: "B" встречается и там и там, но в результате один раз
        assert len(result) == 3

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_empty_reader_and_registry(
        self, mock_reader, mock_registry
    ) -> None:
        """Если нет сессий нигде — пустой список."""
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})

        result = await _get_sessions_to_monitor()

        assert result == []


class _FakeSessionInfo:
    """Имитация SessionInfo для тестов (только session_id)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


# --- Юнит-тесты start ---


class TestStart:
    """Тесты функции start — запуск мониторинга."""

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_start_initializes_seen_counts(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """При запуске watcher запоминает текущее количество сообщений."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        # Две сессии на диске: 5 и 3 сообщения
        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[
                _FakeSessionInfo("session-1"),
                _FakeSessionInfo("session-2"),
            ]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})

        session_messages = {
            "session-1": [_make_system_message()] * 5,
            "session-2": [_make_system_message()] * 3,
        }
        mock_reader.get_session_messages = AsyncMock(
            side_effect=lambda sid, _dir: session_messages.get(sid, [])
        )

        # Запускаем start и отменяем после первого цикла
        task = asyncio.create_task(
            session_watcher.start(mock_callback, mock_get_current_session)
        )
        # Даём время на инициализацию
        await asyncio.sleep(0.05)
        task.cancel()
        # start() ловит CancelledError внутри себя и завершается нормально
        await task

        assert session_watcher._seen_message_counts["session-1"] == 5
        assert session_watcher._seen_message_counts["session-2"] == 3
        # callback не вызван — старые сообщения не отправляются
        mock_callback.assert_not_called()


# --- Юнит-тесты обнаружения новых сообщений ---


class TestDetectNewMessages:
    """Тесты обнаружения новых сообщений и вызова callback."""

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_detects_new_assistant_message(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Watcher обнаруживает новое сообщение Claude и вызывает callback."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_get_current_session.return_value = "session-1"
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 3}

        # В сессии стало 5 сообщений (было 3), новое — assistant
        # Файл заканчивается на assistant → Claude ещё работает → is_final=False
        messages = [
            _make_system_message(),
            _make_user_message("Вопрос"),
            _make_assistant_message("Старый ответ"),
            _make_user_message("Новый вопрос"),
            _make_assistant_message("Файл main.py содержит точку входа"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        mock_callback.assert_called_once_with(
            TEST_CHAT_ID,
            "session-1",
            1,
            "Файл main.py содержит точку входа",
            True,
            False,
        )

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_active_session_marks_messages_as_intermediate(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Если файл заканчивается на progress — Claude ещё работает, is_final=False."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_get_current_session.return_value = "session-1"
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 2}

        # После текстового ответа есть progress-события → Claude работает
        messages = [
            _make_system_message(),
            _make_user_message("Вопрос"),
            _make_assistant_message("Промежуточный ответ"),
            _make_progress_message(),
            _make_progress_message(),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        mock_callback.assert_called_once()
        call_args = mock_callback.call_args[0]
        is_final_arg = call_args[5]
        assert is_final_arg is False

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_completed_exchange_marks_last_as_final(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Если файл заканчивается на user — обмен завершён, последний текст is_final=True."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_get_current_session.return_value = "session-1"
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 2}

        # Claude ответил, потом пользователь написал новое → обмен завершён
        messages = [
            _make_system_message(),
            _make_user_message("Вопрос"),
            _make_assistant_message("Финальный ответ"),
            _make_user_message("Следующий вопрос"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        mock_callback.assert_called_once()
        call_args = mock_callback.call_args[0]
        is_final_arg = call_args[5]
        assert is_final_arg is True

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_ignores_new_user_message(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Watcher не отправляет сообщения пользователя."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 2}

        # Новое сообщение — от пользователя, не от Claude
        messages = [
            _make_system_message(),
            _make_assistant_message("Старый ответ"),
            _make_user_message("Посмотри файл main.py"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)

        await session_watcher._poll_sessions()

        mock_callback.assert_not_called()

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_callback_receives_correct_day_number(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Callback получает правильный дневной номер сессии."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        # Сессия зарегистрирована под номером 3
        mock_registry.register_session = AsyncMock(return_value=3)

        await session_watcher._poll_sessions()

        call_args = mock_callback.call_args
        day_number_arg = call_args[0][2]
        assert day_number_arg == 3

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_callback_receives_is_current_session_true(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Callback получает is_current_session=True для текущей сессии."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID
        # Текущая сессия — session-1
        mock_get_current_session.return_value = "session-1"

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        call_args = mock_callback.call_args
        is_current_arg = call_args[0][4]
        assert is_current_arg is True

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_callback_receives_is_current_session_false(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Callback получает is_current_session=False для чужой сессии."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID
        # Текущая сессия пользователя — session-2, а обновление в session-1
        mock_get_current_session.return_value = "session-2"

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        call_args = mock_callback.call_args
        is_current_arg = call_args[0][4]
        assert is_current_arg is False

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_sends_only_to_session_owner(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Если у сессии есть владелец — callback вызван ровно 1 раз для него."""
        owner_chat_id = 99999
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID, 77777, owner_chat_id}
        mock_session_manager.get_chat_id_for_session.return_value = owner_chat_id

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ владельцу"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        # callback вызван ровно 1 раз — только для владельца
        mock_callback.assert_called_once()
        call_chat_id = mock_callback.call_args[0][0]
        assert call_chat_id == owner_chat_id

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_fallback_to_all_users_when_no_owner(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Нет владельца — fallback на всех из ALLOWED_USER_IDS."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {111, 222}
        # Владелец не найден — сессия создана вне бота
        mock_session_manager.get_chat_id_for_session.return_value = None

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ всем"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        # callback вызван 2 раза — по одному для каждого ID из ALLOWED_USER_IDS
        assert mock_callback.call_count == 2
        called_chat_ids = {call[0][0] for call in mock_callback.call_args_list}
        assert called_chat_ids == {111, 222}


# --- Юнит-тесты pause/resume ---


class TestPauseResume:
    """Тесты приостановки и возобновления мониторинга сессий."""

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_pause_session_skips_monitoring(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Приостановленная сессия не проверяется."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        # Ставим на паузу
        session_watcher.pause_session("session-1")

        messages = [
            _make_system_message(),
            _make_assistant_message("Новый ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)

        await session_watcher._poll_sessions()

        # callback не вызван — сессия на паузе
        mock_callback.assert_not_called()

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_resume_session_updates_seen_count(
        self,
        mock_reader,
        mock_config,
    ) -> None:
        """При снятии паузы watcher обновляет счётчик до актуального значения."""
        mock_config.WORKING_DIR = "/fake/project"

        session_watcher._seen_message_counts = {"session-1": 5}
        session_watcher.pause_session("session-1")

        # Обработчик отправил 2 новых сообщения, теперь их 7
        messages = [_make_system_message()] * 7
        mock_reader.get_session_messages = AsyncMock(return_value=messages)

        await session_watcher.resume_session("session-1")

        assert session_watcher._seen_message_counts["session-1"] == 7
        assert "session-1" not in session_watcher._paused_sessions

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_paused_session_other_sessions_monitored(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Пауза одной сессии не влияет на мониторинг других."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {
            "session-1": 1,
            "session-2": 1,
        }

        # session-1 на паузе
        session_watcher.pause_session("session-1")

        messages_with_new = [
            _make_system_message(),
            _make_assistant_message("Новый ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[
                _FakeSessionInfo("session-1"),
                _FakeSessionInfo("session-2"),
            ]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(
            return_value=messages_with_new
        )
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        # callback вызван только для session-2, не для session-1
        assert mock_callback.call_count == 1
        call_session_id = mock_callback.call_args[0][1]
        assert call_session_id == "session-2"


# --- Юнит-тесты update_session_id ---


class TestUpdateSessionId:
    """Тесты обновления session_id во внутренних словарях."""

    def test_transfers_state(self) -> None:
        """Обновление session_id переносит счётчик и статус паузы."""
        session_watcher._seen_message_counts["_new_0001"] = 5
        session_watcher.pause_session("_new_0001")

        session_watcher.update_session_id("_new_0001", "real-session-id")

        assert session_watcher._seen_message_counts["real-session-id"] == 5
        assert "real-session-id" in session_watcher._paused_sessions
        assert "_new_0001" not in session_watcher._seen_message_counts
        assert "_new_0001" not in session_watcher._paused_sessions

    def test_nonexistent_old_id(self) -> None:
        """Обновление несуществующего session_id ничего не ломает."""
        session_watcher.update_session_id("nonexistent", "new-id")

        assert "nonexistent" not in session_watcher._seen_message_counts
        assert "new-id" not in session_watcher._seen_message_counts

    def test_transfers_only_seen_count(self) -> None:
        """Если есть только счётчик (без паузы) — переносит только его."""
        session_watcher._seen_message_counts["old-id"] = 10

        session_watcher.update_session_id("old-id", "new-id")

        assert session_watcher._seen_message_counts["new-id"] == 10
        assert "old-id" not in session_watcher._seen_message_counts
        assert "new-id" not in session_watcher._paused_sessions

    def test_transfers_only_pause(self) -> None:
        """Если есть только пауза (без счётчика) — переносит только её."""
        session_watcher._paused_sessions.add("old-id")

        session_watcher.update_session_id("old-id", "new-id")

        assert "new-id" in session_watcher._paused_sessions
        assert "old-id" not in session_watcher._paused_sessions


# --- Юнит-тесты get_seen_counts_snapshot ---


class TestGetSeenCountsSnapshot:
    """Тесты функции get_seen_counts_snapshot — копия счётчиков для снапшота."""

    def test_returns_copy_of_counts(self) -> None:
        """Возвращает копию словаря _seen_message_counts."""
        session_watcher._seen_message_counts = {
            "session-1": 5,
            "session-2": 10,
        }

        snapshot = get_seen_counts_snapshot()

        assert snapshot == {"session-1": 5, "session-2": 10}

    def test_returned_dict_is_independent(self) -> None:
        """Изменение возвращённого словаря не влияет на оригинал."""
        session_watcher._seen_message_counts = {"session-1": 5}

        snapshot = get_seen_counts_snapshot()
        snapshot["session-1"] = 999
        snapshot["session-new"] = 1

        # Оригинал не изменился
        assert session_watcher._seen_message_counts == {"session-1": 5}

    def test_empty_counts_returns_empty_dict(self) -> None:
        """При пустых счётчиках возвращается пустой словарь."""
        session_watcher._seen_message_counts = {}

        snapshot = get_seen_counts_snapshot()

        assert snapshot == {}


# --- Граничные случаи ---


class TestEdgeCases:
    """Тесты граничных случаев и нестандартных ситуаций."""

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_no_sessions_on_disk(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """При отсутствии сессий watcher продолжает работать."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session

        mock_reader.get_recent_sessions = AsyncMock(return_value=[])
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})

        # Не должен упасть
        await session_watcher._poll_sessions()

        mock_callback.assert_not_called()

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_session_deleted_between_cycles(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Сессия удалена с диска — запись удаляется из _seen_message_counts."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 5}

        # Во втором цикле session_reader не возвращает session-1
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})

        await session_watcher._poll_sessions()

        # Запись удалена из _seen_message_counts
        assert "session-1" not in session_watcher._seen_message_counts

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_multiple_new_messages_in_one_cycle(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Несколько новых сообщений Claude за один цикл."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ 1"),
            _make_assistant_message("Ответ 2"),
            _make_assistant_message("Ответ 3"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        mock_registry.register_session = AsyncMock(return_value=1)

        await session_watcher._poll_sessions()

        # callback вызван 3 раза — по разу для каждого нового сообщения
        assert mock_callback.call_count == 3

        # Проверяем порядок — сообщения идут по порядку
        texts = [call[0][3] for call in mock_callback.call_args_list]
        assert texts == ["Ответ 1", "Ответ 2", "Ответ 3"]

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_empty_response_not_sent(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Пустой ответ Claude не отправляется пользователю."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message(""),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)

        await session_watcher._poll_sessions()

        mock_callback.assert_not_called()

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_no_response_requested_not_sent(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Служебный ответ 'No response requested.' не отправляется."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("No response requested."),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)

        await session_watcher._poll_sessions()

        mock_callback.assert_not_called()

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_resume_without_pause_no_error(
        self,
        mock_reader,
        mock_config,
    ) -> None:
        """Вызов resume_session без pause не вызывает ошибку."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_reader.get_session_messages = AsyncMock(return_value=[])

        # Не должен упасть
        await session_watcher.resume_session("session-1")

        assert "session-1" not in session_watcher._paused_sessions

    def test_pause_nonexistent_session_no_error(self) -> None:
        """Пауза несуществующей сессии не вызывает ошибку."""
        session_watcher.pause_session("nonexistent-session")

        assert "nonexistent-session" in session_watcher._paused_sessions


# --- Тесты ошибок ---


class TestErrorHandling:
    """Тесты обработки ошибок и устойчивости мониторинга."""

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_callback_exception_does_not_stop_monitoring(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
    ) -> None:
        """Ошибка в callback не останавливает мониторинг."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        # callback выбрасывает ошибку
        failing_callback = AsyncMock(
            side_effect=RuntimeError("Telegram API error")
        )
        mock_get_current_session = AsyncMock(return_value=None)

        session_watcher._callback = failing_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {
            "session-1": 1,
            "session-2": 1,
        }

        messages_with_new = [
            _make_system_message(),
            _make_assistant_message("Новый ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[
                _FakeSessionInfo("session-1"),
                _FakeSessionInfo("session-2"),
            ]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(
            return_value=messages_with_new
        )
        mock_registry.register_session = AsyncMock(return_value=1)

        # Не должен упасть, несмотря на ошибку callback
        await session_watcher._poll_sessions()

        # callback вызван для обеих сессий (ошибка не остановила обработку)
        assert failing_callback.call_count == 2

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.session_manager")
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_session_reader_error_does_not_stop_monitoring(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_session_manager,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Ошибка чтения файла не останавливает мониторинг."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}
        mock_session_manager.get_chat_id_for_session.return_value = TEST_CHAT_ID

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {
            "session-1": 1,
            "session-2": 1,
        }

        def session_messages_side_effect(session_id, _dir):
            if session_id == "session-1":
                raise OSError("File read error")
            return [
                _make_system_message(),
                _make_assistant_message("Новый ответ"),
            ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[
                _FakeSessionInfo("session-1"),
                _FakeSessionInfo("session-2"),
            ]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(
            side_effect=session_messages_side_effect
        )
        mock_registry.register_session = AsyncMock(return_value=1)

        # Не должен упасть — ошибка ловится на уровне _poll_sessions
        # Но _check_session бросит ошибку для session-1
        # Нужно убедиться, что это не ломает всё
        await session_watcher._poll_sessions()

        # callback вызван для session-2 (session-1 упала с ошибкой)
        assert mock_callback.call_count == 1

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_cancelled_error_stops_gracefully(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
        caplog,
    ) -> None:
        """asyncio.CancelledError корректно завершает мониторинг."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        mock_reader.get_recent_sessions = AsyncMock(return_value=[])
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})

        task = asyncio.create_task(
            session_watcher.start(mock_callback, mock_get_current_session)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        # start() ловит CancelledError внутри себя и завершается нормально
        await task

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_unexpected_error_retries_after_delay(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
        caplog,
    ) -> None:
        """Непредвиденная ошибка — watcher логирует и продолжает."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        mock_reader.get_recent_sessions = AsyncMock(return_value=[])
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=[])

        poll_count = 0
        original_poll = session_watcher._poll_sessions

        async def poll_with_error():
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                raise RuntimeError("Unexpected error")
            # После первого вызова — работает нормально
            await original_poll()

        with (
            patch.object(session_watcher, "_poll_sessions", side_effect=poll_with_error),
            caplog.at_level(logging.ERROR, logger="claude_manager.session_watcher"),
        ):
            task = asyncio.create_task(
                session_watcher.start(mock_callback, mock_get_current_session)
            )
            # Даём время на ошибку и повторную попытку
            await asyncio.sleep(0.1)
            task.cancel()
            await task

        # Проверяем, что ошибка залогирована
        assert any(
            "Непредвиденная ошибка" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    @patch("claude_manager.session_watcher.config")
    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_daily_registry_error_does_not_stop_monitoring(
        self,
        mock_reader,
        mock_registry,
        mock_config,
        mock_callback,
        mock_get_current_session,
    ) -> None:
        """Ошибка в daily_session_registry не останавливает мониторинг."""
        mock_config.WORKING_DIR = "/fake/project"
        mock_config.ALLOWED_USER_IDS = {TEST_CHAT_ID}

        session_watcher._callback = mock_callback
        session_watcher._get_current_session = mock_get_current_session
        session_watcher._seen_message_counts = {"session-1": 1}

        messages = [
            _make_system_message(),
            _make_assistant_message("Ответ"),
        ]

        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("session-1")]
        )
        mock_registry.get_all_today_sessions = AsyncMock(return_value={})
        mock_reader.get_session_messages = AsyncMock(return_value=messages)
        # register_session бросает ошибку
        mock_registry.register_session = AsyncMock(
            side_effect=OSError("Registry file error")
        )

        # Не должен упасть
        await session_watcher._poll_sessions()

        # callback не вызван — ошибка произошла до вызова callback
        mock_callback.assert_not_called()


class TestResetState:
    """Тесты сброса состояния watcher при переключении проекта."""

    async def test_reset_clears_seen_counts(self) -> None:
        """После reset_state старые ключи заменяются на ключи нового проекта."""
        session_watcher._seen_message_counts.clear()
        session_watcher._seen_message_counts["old-sess-1"] = 5
        session_watcher._seen_message_counts["old-sess-2"] = 10

        with patch.object(session_watcher, "_get_sessions_to_monitor", return_value=[]), \
             patch.object(session_watcher.session_reader, "get_session_messages", return_value=[]):
            await session_watcher.reset_state()

        # Старые ключи очищены, новых сессий нет — словарь пуст
        assert len(session_watcher._seen_message_counts) == 0

    async def test_reset_clears_paused_sessions(self) -> None:
        """После reset_state set _paused_sessions пуст."""
        session_watcher._paused_sessions.clear()
        session_watcher._paused_sessions.add("sess-paused-1")
        session_watcher._paused_sessions.add("sess-paused-2")

        with patch.object(session_watcher, "_get_sessions_to_monitor", return_value=[]), \
             patch.object(session_watcher.session_reader, "get_session_messages", return_value=[]):
            await session_watcher.reset_state()

        assert len(session_watcher._paused_sessions) == 0

    async def test_reset_state_keeps_dict_identity(self) -> None:
        """reset_state использует clear()+update(), а не пересоздание — ссылки остаются валидными."""
        original_counts = session_watcher._seen_message_counts
        original_paused = session_watcher._paused_sessions

        with patch.object(session_watcher, "_get_sessions_to_monitor", return_value=[]), \
             patch.object(session_watcher.session_reader, "get_session_messages", return_value=[]):
            await session_watcher.reset_state()

        # Те же объекты в памяти — значит clear()+update(), а не новый dict/set
        assert session_watcher._seen_message_counts is original_counts
        assert session_watcher._paused_sessions is original_paused

    async def test_reset_initializes_counts_for_new_project(self) -> None:
        """reset_state заполняет счётчики для сессий нового проекта."""
        session_watcher._seen_message_counts.clear()
        session_watcher._seen_message_counts["old-sess"] = 99

        # Новый проект содержит две сессии с 3 и 7 сообщениями
        mock_messages_by_session = {
            "new-sess-a": [{"type": "user"}] * 3,
            "new-sess-b": [{"type": "user"}] * 7,
        }

        async def fake_get_messages(session_id, _working_dir):
            return mock_messages_by_session.get(session_id, [])

        with patch.object(
                session_watcher, "_get_sessions_to_monitor",
                return_value=["new-sess-a", "new-sess-b"],
             ), \
             patch.object(
                session_watcher.session_reader, "get_session_messages",
                side_effect=fake_get_messages,
             ):
            await session_watcher.reset_state()

        # Старые ключи удалены, новые проинициализированы
        assert "old-sess" not in session_watcher._seen_message_counts
        assert session_watcher._seen_message_counts["new-sess-a"] == 3
        assert session_watcher._seen_message_counts["new-sess-b"] == 7
