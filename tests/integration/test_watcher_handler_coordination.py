"""Интеграционные тесты: координация watcher и handler.

Проверяет механизм паузы, который предотвращает дублирование ответов:
- pause_session блокирует отправку от watcher
- send_message обработчик отправляет ответ
- resume_session возобновляет мониторинг watcher
- Watcher не отправляет сообщения, которые уже отправил handler

Все тесты используют реальные модули session_watcher и session_reader
с фейковыми файлами сессий на диске.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import config, daily_session_registry, session_watcher
from claude_manager.session_watcher import (
    _check_session,
    _extract_assistant_messages,
    _extract_message_text,
    _is_empty_response,
    pause_session,
    resume_session,
    update_session_id,
)


# --- Фейковые данные ---

FAKE_TODAY = "2026-03-30"
SESSION_ID = "watcher-test-session"
CHAT_ID = 111111


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _reset_watcher_state() -> None:
    """Сбрасывает внутреннее состояние watcher перед каждым тестом."""
    session_watcher._seen_message_counts.clear()
    session_watcher._paused_sessions.clear()
    session_watcher._callback = None
    session_watcher._get_current_session = None


@pytest.fixture(autouse=True)
def _reset_daily_registry(tmp_path: Path) -> None:
    """Сбрасывает daily_session_registry перед каждым тестом."""
    daily_session_registry._registry = {}
    daily_session_registry._registry_path = tmp_path / "daily_sessions.json"
    daily_session_registry._lock = asyncio.Lock()


@pytest.fixture()
def sessions_dir(tmp_path: Path) -> Path:
    """Создаёт временную директорию для файлов сессий."""
    sessions_path = tmp_path / "sessions"
    sessions_path.mkdir()
    return sessions_path


# --- Тесты: механизм паузы ---


class TestPauseResumeMechanism:
    """Пауза/возобновление мониторинга конкретной сессии."""

    def test_pause_adds_session_to_paused_set(self) -> None:
        """pause_session добавляет сессию в множество приостановленных."""
        pause_session(SESSION_ID)

        assert SESSION_ID in session_watcher._paused_sessions

    @pytest.mark.asyncio()
    async def test_resume_removes_session_from_paused_set(self) -> None:
        """resume_session убирает сессию из множества приостановленных."""
        pause_session(SESSION_ID)

        # Мокаем чтение файлов (watcher при resume перечитывает сессию)
        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await resume_session(SESSION_ID)

        assert SESSION_ID not in session_watcher._paused_sessions

    @pytest.mark.asyncio()
    async def test_paused_session_skipped_during_check(self) -> None:
        """Приостановленная сессия не проверяется при _check_session."""
        pause_session(SESSION_ID)

        # Настраиваем callback, чтобы проверить, что он НЕ вызывается
        callback_calls: list = []

        async def fake_callback(*args) -> None:
            callback_calls.append(args)

        session_watcher._callback = fake_callback
        session_watcher._get_current_session = AsyncMock(return_value=None)

        # Даже если есть новые сообщения — они не отправятся
        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=[
                {"type": "assistant", "message": {"content": "Новый ответ"}},
            ],
        ):
            await _check_session(SESSION_ID)

        assert len(callback_calls) == 0

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_resume_updates_seen_count(self, _mock_today: object) -> None:
        """resume_session обновляет счётчик, чтобы не дублировать ответы."""
        # Представим, что handler уже отправил ответ — в файле 5 строк
        fake_messages = [
            {"type": "user", "message": {"content": "Привет"}},
            {"type": "assistant", "message": {"content": "Ответ 1"}},
            {"type": "user", "message": {"content": "Ещё"}},
            {"type": "assistant", "message": {"content": "Ответ 2"}},
            {"type": "assistant", "message": {"content": "Ответ 3"}},
        ]

        pause_session(SESSION_ID)

        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=fake_messages,
        ):
            await resume_session(SESSION_ID)

        # Счётчик обновлён до актуального количества строк
        assert session_watcher._seen_message_counts[SESSION_ID] == 5


# --- Тесты: полный цикл pause -> handler -> resume ---


class TestFullPauseHandlerResumeCycle:
    """Полный цикл: handler приостанавливает watcher, обрабатывает, возобновляет."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_handler_pauses_then_resumes_watcher(
        self, _mock_today: object
    ) -> None:
        """Имитация цикла: pause -> обработка -> resume -> watcher не дублирует."""
        callback_calls: list = []

        async def fake_callback(chat_id, session_id, day_number, text, is_current) -> None:
            callback_calls.append(text)

        session_watcher._callback = fake_callback
        session_watcher._get_current_session = AsyncMock(return_value=SESSION_ID)

        # Watcher знает, что в сессии было 2 сообщения
        session_watcher._seen_message_counts[SESSION_ID] = 2

        # Шаг 1: Handler приостанавливает watcher
        pause_session(SESSION_ID)

        # Шаг 2: Handler обрабатывает сообщение (Claude добавил ответ, теперь 4 строки)
        messages_after_handler = [
            {"type": "user", "message": {"content": "Привет"}},
            {"type": "assistant", "message": {"content": "Ответ 1"}},
            {"type": "user", "message": {"content": "Вопрос"}},
            {"type": "assistant", "message": {"content": "Ответ 2"}},
        ]

        # Шаг 3: Handler возобновляет watcher (обновляет счётчик до 4)
        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=messages_after_handler,
        ):
            await resume_session(SESSION_ID)

        assert session_watcher._seen_message_counts[SESSION_ID] == 4

        # Шаг 4: Watcher проверяет сессию — новых сообщений нет (всё уже обработано)
        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=messages_after_handler,
        ), patch(
            "claude_manager.session_watcher.config"
        ) as mock_config:
            mock_config.ALLOWED_USER_IDS = {CHAT_ID}
            mock_config.WORKING_DIR = "/tmp"

            await _check_session(SESSION_ID)

        # Callback НЕ вызван — дубликатов нет
        assert len(callback_calls) == 0


# --- Тесты: update_session_id в watcher ---


class TestWatcherUpdateSessionId:
    """Обновление session_id переносит состояние watcher."""

    def test_seen_count_transferred_to_new_id(self) -> None:
        """Счётчик обработанных сообщений переносится на новый session_id."""
        session_watcher._seen_message_counts["old-id"] = 10

        update_session_id("old-id", "new-id")

        assert "old-id" not in session_watcher._seen_message_counts
        assert session_watcher._seen_message_counts["new-id"] == 10

    def test_paused_status_transferred_to_new_id(self) -> None:
        """Статус паузы переносится на новый session_id."""
        pause_session("old-id")

        update_session_id("old-id", "new-id")

        assert "old-id" not in session_watcher._paused_sessions
        assert "new-id" in session_watcher._paused_sessions

    def test_update_unknown_id_does_nothing(self) -> None:
        """Обновление несуществующего session_id не вызывает ошибок."""
        # Просто не должно упасть
        update_session_id("nonexistent-id", "another-id")

        assert "another-id" not in session_watcher._seen_message_counts
        assert "another-id" not in session_watcher._paused_sessions


# --- Тесты: извлечение сообщений ---


class TestExtractMessages:
    """Извлечение текста из сообщений Claude."""

    def test_extract_text_from_string_content(self) -> None:
        """Строковое поле content возвращается как есть."""
        message = {"type": "assistant", "message": {"content": "Привет"}}
        assert _extract_message_text(message) == "Привет"

    def test_extract_text_from_list_content(self) -> None:
        """Текстовые блоки из списка content склеиваются."""
        message = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Первая часть"},
                    {"type": "text", "text": "Вторая часть"},
                ],
            },
        }
        result = _extract_message_text(message)
        assert "Первая часть" in result
        assert "Вторая часть" in result

    def test_non_assistant_message_returns_none(self) -> None:
        """Сообщения не от assistant возвращают None."""
        message = {"type": "user", "message": {"content": "Запрос"}}
        assert _extract_message_text(message) is None

    def test_extract_new_messages_after_seen(self) -> None:
        """Функция пропускает уже обработанные сообщения."""
        all_messages = [
            {"type": "user", "message": {"content": "Старый вопрос"}},
            {"type": "assistant", "message": {"content": "Старый ответ"}},
            {"type": "user", "message": {"content": "Новый вопрос"}},
            {"type": "assistant", "message": {"content": "Новый ответ"}},
        ]

        # Видели первые 2 сообщения — извлекаем только из новых
        new_texts = _extract_assistant_messages(all_messages, already_seen_count=2)

        assert len(new_texts) == 1
        assert new_texts[0] == "Новый ответ"


# --- Тесты: пустые/служебные ответы ---


class TestEmptyResponses:
    """Пустые и служебные ответы Claude."""

    def test_empty_string_is_empty_response(self) -> None:
        assert _is_empty_response("") is True

    def test_whitespace_only_is_empty_response(self) -> None:
        assert _is_empty_response("   ") is True

    def test_no_response_marker_is_empty(self) -> None:
        assert _is_empty_response("No response requested.") is True

    def test_normal_text_is_not_empty(self) -> None:
        assert _is_empty_response("Привет, мир!") is False
