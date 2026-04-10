"""Тесты модуля unread_buffer — буфер непрочитанных сообщений при переключении проектов."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import unread_buffer
from claude_manager.unread_buffer import (
    PendingMessage,
    _extract_message_text,
    _is_empty_response,
    _is_snapshot_expired,
    clear_snapshot,
    cleanup_expired,
    has_pending,
    save_snapshot,
    get_pending_messages,
)


# --- Вспомогательные инструменты ---

TEST_PROJECT = "/tmp/test-project"
OTHER_PROJECT = "/tmp/other-project"


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


@pytest.fixture(autouse=True)
def _reset_buffer_state():
    """Сбрасывает внутреннее состояние буфера перед каждым тестом."""
    unread_buffer._snapshots.clear()
    yield
    unread_buffer._snapshots.clear()


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
        """Служебный маркер 'No response requested.' считается пустым."""
        assert _is_empty_response("No response requested.") is True

    def test_no_response_with_whitespace(self) -> None:
        """Служебный маркер с пробелами по краям тоже считается пустым."""
        assert _is_empty_response("  No response requested.  ") is True

    def test_normal_text_is_not_empty(self) -> None:
        """Обычный текст не считается пустым ответом."""
        assert _is_empty_response("Привет, мир!") is False

    def test_none_is_empty(self) -> None:
        """None считается пустым ответом (falsy-значение)."""
        assert _is_empty_response(None) is True


# --- Юнит-тесты _extract_message_text ---


class TestExtractMessageText:
    """Тесты извлечения текста из сообщений Claude."""

    def test_string_content(self) -> None:
        """Извлекает текст, когда content — строка."""
        message = _make_assistant_message("Готово!")
        assert _extract_message_text(message) == "Готово!"

    def test_list_content(self) -> None:
        """Извлекает и склеивает текст из списка блоков."""
        message = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Часть 1"},
                    {"type": "text", "text": "Часть 2"},
                ],
            },
        }
        assert _extract_message_text(message) == "Часть 1 Часть 2"

    def test_returns_none_for_user_message(self) -> None:
        """Пользовательские сообщения игнорируются — возвращает None."""
        message = _make_user_message("Привет")
        assert _extract_message_text(message) is None

    def test_returns_none_for_no_content(self) -> None:
        """Сообщение без поля content — возвращает None."""
        message = {"type": "assistant", "message": {}}
        assert _extract_message_text(message) is None

    def test_returns_none_for_empty_list_content(self) -> None:
        """Пустой список блоков — возвращает None."""
        message = {"type": "assistant", "message": {"content": []}}
        assert _extract_message_text(message) is None

    def test_skips_non_text_blocks(self) -> None:
        """Блоки с type != 'text' пропускаются."""
        message = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "bash"},
                    {"type": "text", "text": "Результат"},
                ],
            },
        }
        assert _extract_message_text(message) == "Результат"


# --- Юнит-тесты _is_snapshot_expired ---


class TestIsSnapshotExpired:
    """Тесты проверки просроченности снапшота по TTL."""

    @patch("claude_manager.unread_buffer.config")
    def test_fresh_snapshot_not_expired(self, mock_config) -> None:
        """Снапшот, созданный только что, не просрочен."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3
        snapshot = unread_buffer.ProjectSnapshot(
            seen_counts={}, switch_time=datetime.now(),
        )
        assert _is_snapshot_expired(snapshot) is False

    @patch("claude_manager.unread_buffer.config")
    def test_old_snapshot_is_expired(self, mock_config) -> None:
        """Снапшот старше TTL считается просроченным."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3
        old_time = datetime.now() - timedelta(hours=4)
        snapshot = unread_buffer.ProjectSnapshot(
            seen_counts={}, switch_time=old_time,
        )
        assert _is_snapshot_expired(snapshot) is True

    @patch("claude_manager.unread_buffer.config")
    def test_exactly_at_ttl_boundary(self, mock_config) -> None:
        """Снапшот ровно на границе TTL — ещё не просрочен (нестрого)."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3
        # timedelta чуть меньше 3 часов — ещё валиден
        boundary_time = datetime.now() - timedelta(hours=3) + timedelta(seconds=1)
        snapshot = unread_buffer.ProjectSnapshot(
            seen_counts={}, switch_time=boundary_time,
        )
        assert _is_snapshot_expired(snapshot) is False


# --- Тесты save_snapshot ---


class TestSaveSnapshot:
    """Тесты сохранения снапшота при уходе из проекта."""

    def test_saves_copy_of_seen_counts(self) -> None:
        """Сохраняет копию словаря — изменение оригинала не влияет на снапшот."""
        original = {"session-1": 5, "session-2": 10}
        save_snapshot(TEST_PROJECT, original)

        # Изменяем оригинал — снапшот не должен измениться
        original["session-1"] = 999
        original["session-3"] = 42

        snapshot = unread_buffer._snapshots[TEST_PROJECT]
        assert snapshot.seen_counts == {"session-1": 5, "session-2": 10}

    def test_saves_switch_time(self) -> None:
        """switch_time в снапшоте близок к datetime.now()."""
        before = datetime.now()
        save_snapshot(TEST_PROJECT, {"s1": 1})
        after = datetime.now()

        snapshot = unread_buffer._snapshots[TEST_PROJECT]
        assert before <= snapshot.switch_time <= after

    def test_overwrites_existing_snapshot(self) -> None:
        """Повторный вызов save_snapshot перезаписывает предыдущий снапшот."""
        save_snapshot(TEST_PROJECT, {"s1": 1})
        save_snapshot(TEST_PROJECT, {"s2": 2})

        snapshot = unread_buffer._snapshots[TEST_PROJECT]
        assert snapshot.seen_counts == {"s2": 2}
        assert "s1" not in snapshot.seen_counts


# --- Тесты get_pending_messages ---


class TestGetPendingMessages:
    """Тесты сканирования JSONL и получения непрочитанных сообщений."""

    @patch("claude_manager.unread_buffer.session_reader")
    @patch("claude_manager.unread_buffer.config")
    async def test_returns_new_assistant_messages(
        self, mock_config, mock_reader,
    ) -> None:
        """Сообщения после seen_count возвращаются как непрочитанные."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        # В сессии было 2 сообщения, потом появилось ещё одно
        mock_reader.get_session_messages = AsyncMock(return_value=[
            _make_user_message("Привет"),
            _make_assistant_message("Привет!"),
            _make_assistant_message("Новый ответ"),
        ])
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])

        save_snapshot(TEST_PROJECT, {"sess-1": 2})

        result = await get_pending_messages(TEST_PROJECT)

        assert len(result) == 1
        assert result[0].session_id == "sess-1"
        assert result[0].text == "Новый ответ"

    @patch("claude_manager.unread_buffer.session_reader")
    @patch("claude_manager.unread_buffer.config")
    async def test_skips_user_messages(self, mock_config, mock_reader) -> None:
        """Пользовательские сообщения не возвращаются как непрочитанные."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        mock_reader.get_session_messages = AsyncMock(return_value=[
            _make_user_message("Первый вопрос"),
            _make_user_message("Второй вопрос"),  # новое — пользовательское
        ])
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])

        save_snapshot(TEST_PROJECT, {"sess-1": 1})

        result = await get_pending_messages(TEST_PROJECT)
        assert result == []

    @patch("claude_manager.unread_buffer.session_reader")
    @patch("claude_manager.unread_buffer.config")
    async def test_skips_empty_responses(self, mock_config, mock_reader) -> None:
        """Служебный ответ 'No response requested.' пропускается."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        mock_reader.get_session_messages = AsyncMock(return_value=[
            _make_user_message("Сделай что-нибудь"),
            _make_assistant_message("No response requested."),
        ])
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])

        save_snapshot(TEST_PROJECT, {"sess-1": 1})

        result = await get_pending_messages(TEST_PROJECT)
        assert result == []

    async def test_returns_empty_for_no_snapshot(self) -> None:
        """Без снапшота — возвращает пустой список."""
        result = await get_pending_messages(TEST_PROJECT)
        assert result == []

    @patch("claude_manager.unread_buffer.session_reader")
    @patch("claude_manager.unread_buffer.config")
    async def test_returns_empty_and_clears_expired_snapshot(
        self, mock_config, mock_reader,
    ) -> None:
        """Просроченный TTL — возвращает пустой список и удаляет снапшот."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        # Создаём снапшот, который уже просрочен
        unread_buffer._snapshots[TEST_PROJECT] = unread_buffer.ProjectSnapshot(
            seen_counts={"sess-1": 5},
            switch_time=datetime.now() - timedelta(hours=4),
        )

        result = await get_pending_messages(TEST_PROJECT)

        assert result == []
        assert TEST_PROJECT not in unread_buffer._snapshots

    @patch("claude_manager.unread_buffer.session_reader")
    @patch("claude_manager.unread_buffer.config")
    async def test_detects_new_sessions(self, mock_config, mock_reader) -> None:
        """Сессии, которых не было в снапшоте, обрабатываются с seen_count=0."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        # Известная сессия — без новых сообщений
        # Новая сессия — с одним assistant-сообщением
        async def mock_get_messages(session_id, project_path):
            if session_id == "known-sess":
                return [_make_user_message("Старый вопрос")]
            if session_id == "new-sess":
                return [
                    _make_user_message("Вопрос"),
                    _make_assistant_message("Ответ из новой сессии"),
                ]
            return []

        mock_reader.get_session_messages = AsyncMock(side_effect=mock_get_messages)

        # get_recent_sessions возвращает обе сессии
        from claude_manager.session_reader import SessionInfo
        mock_reader.get_recent_sessions = AsyncMock(return_value=[
            SessionInfo(session_id="known-sess", created_at="2026-01-01", preview=""),
            SessionInfo(session_id="new-sess", created_at="2026-01-01", preview=""),
        ])

        save_snapshot(TEST_PROJECT, {"known-sess": 1})

        result = await get_pending_messages(TEST_PROJECT)

        assert len(result) == 1
        assert result[0].session_id == "new-sess"
        assert result[0].text == "Ответ из новой сессии"

    @patch("claude_manager.unread_buffer.session_reader")
    @patch("claude_manager.unread_buffer.config")
    async def test_no_new_messages(self, mock_config, mock_reader) -> None:
        """Если seen_count == len(messages), возвращает пустой список."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        mock_reader.get_session_messages = AsyncMock(return_value=[
            _make_user_message("Вопрос"),
            _make_assistant_message("Ответ"),
        ])
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])

        # seen_count == 2, сообщений тоже 2 — ничего нового
        save_snapshot(TEST_PROJECT, {"sess-1": 2})

        result = await get_pending_messages(TEST_PROJECT)
        assert result == []


# --- Тесты clear_snapshot ---


class TestClearSnapshot:
    """Тесты удаления снапшота."""

    def test_removes_existing_snapshot(self) -> None:
        """Удаляет существующий снапшот."""
        save_snapshot(TEST_PROJECT, {"s1": 1})
        assert TEST_PROJECT in unread_buffer._snapshots

        clear_snapshot(TEST_PROJECT)
        assert TEST_PROJECT not in unread_buffer._snapshots

    def test_noop_for_missing_snapshot(self) -> None:
        """Удаление несуществующего снапшота не вызывает ошибку."""
        clear_snapshot(TEST_PROJECT)  # не должен упасть
        assert TEST_PROJECT not in unread_buffer._snapshots


# --- Тесты has_pending ---


class TestHasPending:
    """Тесты проверки наличия снапшота."""

    def test_true_when_snapshot_exists(self) -> None:
        """True, если для проекта есть актуальный снапшот."""
        save_snapshot(TEST_PROJECT, {"s1": 1})
        assert has_pending(TEST_PROJECT) is True

    def test_false_when_no_snapshot(self) -> None:
        """False, если для проекта нет снапшота."""
        assert has_pending(TEST_PROJECT) is False

    @patch("claude_manager.unread_buffer.config")
    def test_false_and_clears_when_expired(self, mock_config) -> None:
        """False и удаляет снапшот, если он просрочен по TTL."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        unread_buffer._snapshots[TEST_PROJECT] = unread_buffer.ProjectSnapshot(
            seen_counts={"s1": 1},
            switch_time=datetime.now() - timedelta(hours=4),
        )

        assert has_pending(TEST_PROJECT) is False
        assert TEST_PROJECT not in unread_buffer._snapshots


# --- Тесты cleanup_expired ---


class TestCleanupExpired:
    """Тесты массовой очистки просроченных снапшотов."""

    @patch("claude_manager.unread_buffer.config")
    def test_removes_expired_snapshots(self, mock_config) -> None:
        """Удаляет все просроченные снапшоты."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        # Два проекта: один просрочен, другой свежий
        unread_buffer._snapshots[TEST_PROJECT] = unread_buffer.ProjectSnapshot(
            seen_counts={"s1": 1},
            switch_time=datetime.now() - timedelta(hours=4),
        )
        unread_buffer._snapshots[OTHER_PROJECT] = unread_buffer.ProjectSnapshot(
            seen_counts={"s2": 2},
            switch_time=datetime.now(),
        )

        cleanup_expired()

        assert TEST_PROJECT not in unread_buffer._snapshots
        assert OTHER_PROJECT in unread_buffer._snapshots

    @patch("claude_manager.unread_buffer.config")
    def test_keeps_fresh_snapshots(self, mock_config) -> None:
        """Свежие снапшоты не затрагиваются при очистке."""
        mock_config.UNREAD_BUFFER_TTL_HOURS = 3

        unread_buffer._snapshots[TEST_PROJECT] = unread_buffer.ProjectSnapshot(
            seen_counts={"s1": 5},
            switch_time=datetime.now() - timedelta(hours=1),
        )
        unread_buffer._snapshots[OTHER_PROJECT] = unread_buffer.ProjectSnapshot(
            seen_counts={"s2": 3},
            switch_time=datetime.now() - timedelta(minutes=30),
        )

        cleanup_expired()

        assert TEST_PROJECT in unread_buffer._snapshots
        assert OTHER_PROJECT in unread_buffer._snapshots
