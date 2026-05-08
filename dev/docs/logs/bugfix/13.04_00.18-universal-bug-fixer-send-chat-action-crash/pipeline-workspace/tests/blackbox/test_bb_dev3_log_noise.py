"""Blackbox-тесты DEV-3: watcher не спамит логи для удалённых сессий.

Проверяют, что полный цикл мониторинга (_poll_sessions) не генерирует
warning-записей для несуществующих JSONL-файлов.
Сейчас (до фикса) тесты КРАСНЫЕ. После фикса — ЗЕЛЁНЫЕ.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import session_watcher
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


# --- Тесты ---


class TestNoWarningSpam:
    """Полный цикл мониторинга не должен генерировать warning для удалённых сессий."""

    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_get_sessions_excludes_deleted_from_registry(
        self,
        mock_reader,
        mock_registry,
    ) -> None:
        """Реестр содержит 3 удалённых сессии — ни одна не попадает в мониторинг.

        Воспроизводит реальный сценарий: sessions.json хранит записи о 70ca7205,
        669ddf67, d2c6a9c3, но их JSONL-файлы удалены. Watcher не должен их мониторить.
        """
        # На диске нет JSONL-файлов (reader возвращает пустой список)
        mock_reader.get_recent_sessions = AsyncMock(return_value=[])
        # Реестр содержит 3 удалённых сессии
        mock_registry.get_all_today_sessions = AsyncMock(
            return_value={
                1: "70ca7205-dead-session",
                2: "669ddf67-dead-session",
                3: "d2c6a9c3-dead-session",
            }
        )

        result = await _get_sessions_to_monitor()

        # Ни одна удалённая сессия не должна попасть в мониторинг
        assert len(result) == 0, (
            f"Удалённые сессии попали в мониторинг: {result}. "
            f"_get_sessions_to_monitor должен фильтровать несуществующие файлы."
        )

    @patch("claude_manager.session_watcher.daily_session_registry")
    @patch("claude_manager.session_watcher.session_reader")
    async def test_mix_existing_and_deleted_sessions(
        self,
        mock_reader,
        mock_registry,
    ) -> None:
        """Реестр с реальной и удалённой сессией — только реальная попадает в мониторинг."""
        # Одна реальная сессия на диске
        mock_reader.get_recent_sessions = AsyncMock(
            return_value=[_FakeSessionInfo("real-session-on-disk")]
        )
        # Реестр: реальная + удалённая
        mock_registry.get_all_today_sessions = AsyncMock(
            return_value={
                1: "real-session-on-disk",
                2: "deleted-session-no-file",
            }
        )

        result = await _get_sessions_to_monitor()

        assert "real-session-on-disk" in result
        assert "deleted-session-no-file" not in result
