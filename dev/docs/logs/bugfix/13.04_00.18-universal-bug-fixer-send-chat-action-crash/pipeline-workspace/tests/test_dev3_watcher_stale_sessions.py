"""Whitebox-тесты DEV-3: session_watcher спамит логи для несуществующих сессий.

Проверяют, что:
1. _get_sessions_to_monitor фильтрует сессии, чьи JSONL-файлы не существуют
2. session_reader.get_session_messages логирует debug (не warning) для отсутствующих файлов

Сейчас (до фикса) тесты КРАСНЫЕ — warning логируется, фильтрация не происходит.
После фикса тесты станут ЗЕЛЁНЫМИ.
"""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import session_watcher
from claude_manager.session_reader import get_session_messages
from claude_manager.session_watcher import _get_sessions_to_monitor


# --- Вспомогательные ---


class _FakeSessionInfo:
    """Имитация SessionInfo (только session_id)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _reset_watcher_state():
    """Сбрасывает внутреннее состояние watcher перед каждым тестом."""
    session_watcher._seen_message_counts = {}
    session_watcher._paused_sessions = set()


# --- Тесты _get_sessions_to_monitor: фильтрация несуществующих ---


class TestGetSessionsToMonitorFiltering:
    """_get_sessions_to_monitor должен исключать сессии без JSONL-файлов."""

    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_filters_out_nonexistent_registry_sessions(
        self,
        mock_reader,
        mock_registry,
    ) -> None:
        """Сессии из реестра, чьих файлов нет на диске, исключаются из списка.

        session_reader.get_recent_sessions уже возвращает только реальные файлы.
        Дневной реестр (daily_session_registry) может содержать ID удалённых сессий.
        _get_sessions_to_monitor должен проверить существование файла перед добавлением.
        """
        # session_reader возвращает одну реальную сессию
        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("existing-session")]
        )
        # Дневной реестр содержит 2 сессии: одна реальная (уже в reader), одна удалённая
        mock_registry.get_all_today_sessions = AsyncMock(
            return_value={1: "existing-session", 2: "deleted-session"}
        )

        result = await _get_sessions_to_monitor()

        # deleted-session не должен попасть в список мониторинга
        assert "deleted-session" not in result
        assert "existing-session" in result


# --- Тесты session_reader: уровень логирования ---


class TestSessionReaderLogLevel:
    """get_session_messages должен логировать debug (не warning) для отсутствующих файлов."""

    @pytest.fixture()
    def sessions_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Временная папка для файлов сессий (пустая — без файлов)."""
        sessions_path = tmp_path / "sessions"
        sessions_path.mkdir()
        monkeypatch.setattr(
            "claude_manager.session_reader._build_sessions_path",
            lambda project_dir: str(sessions_path),
        )
        return sessions_path

    async def test_missing_file_logs_debug_not_warning(
        self,
        sessions_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Отсутствующий файл сессии логируется на уровне debug, не warning.

        Для watcher это штатная ситуация: сессия удалена, но ещё есть в реестре.
        Warning спамит error.log каждые 2 секунды на ~1 МБ.
        """
        with caplog.at_level(logging.DEBUG, logger="claude_manager.session_reader"):
            await get_session_messages("nonexistent-session-id", "/fake/project")

        # Не должно быть warning/error записей про отсутствие файла
        warning_records = [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and "не найден" in record.message.lower()
        ]
        assert warning_records == [], (
            f"Ожидали debug-уровень для отсутствующего файла, но получили warning: "
            f"{[r.message for r in warning_records]}"
        )
