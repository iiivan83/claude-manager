"""Интеграционные тесты изоляции E2E тестового аккаунта.

Проверяют, что E2E_TEST_USER_ID корректно обрабатывается
на стыке модулей config -> bot -> session_watcher.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import config, daily_session_registry, session_manager, session_watcher


# Константы для тестов
MAIN_USER_ID = 111111
E2E_USER_ID = 999999
TEST_SESSION_ID = "test-session-abc"
FAKE_TODAY = "2026-04-11"


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
    daily_session_registry._loaded_from_disk = True


@pytest.fixture(autouse=True)
def _reset_session_manager(tmp_path: Path) -> None:
    """Сбрасывает session_manager перед каждым тестом."""
    session_manager._bindings = {}
    session_manager._bindings_path = tmp_path / "sessions.json"
    session_manager._lock = asyncio.Lock()


class TestE2eUserIsolation:
    """Тесты изоляции E2E-пользователя от основного."""

    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_e2e_user_no_watcher_broadcast(self, _mock_today: object) -> None:
        """Watcher не отправляет E2E-пользователю broadcast о чужих сессиях.

        Сценарий: сессия создана вне бота (владелец неизвестен). Watcher
        в fallback-режиме рассылает уведомления всем из ALLOWED_USER_IDS.
        E2E_TEST_USER_ID должен быть исключён из этой рассылки.
        """
        callback = AsyncMock()
        session_watcher._callback = callback
        session_watcher._get_current_session = AsyncMock(return_value=None)

        # Watcher ещё не видел ни одного сообщения в этой сессии
        session_watcher._seen_message_counts[TEST_SESSION_ID] = 0

        # Фейковые сообщения: одно пользовательское, одно от Claude (финальное)
        fake_messages = [
            {"type": "user", "message": {"content": "Привет"}},
            {"type": "assistant", "message": {"content": "Ответ от Claude"}},
        ]

        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=fake_messages,
        ), patch.object(
            config, "ALLOWED_USER_IDS", {MAIN_USER_ID, E2E_USER_ID}
        ), patch.object(
            config, "E2E_TEST_USER_ID", E2E_USER_ID
        ), patch.object(
            config, "WORKING_DIR", "/tmp"
        ):
            # Сессия не привязана ни к кому — fallback на broadcast
            session_manager._bindings = {}

            await session_watcher._check_session(TEST_SESSION_ID)

        # Callback должен быть вызван ровно 1 раз — для основного пользователя
        assert callback.call_count == 1

        # Проверяем, что callback вызван именно для MAIN_USER_ID
        actual_chat_id = callback.call_args_list[0].args[0]
        assert actual_chat_id == MAIN_USER_ID

    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_e2e_user_gets_own_session_notifications(
        self, _mock_today: object
    ) -> None:
        """Watcher отправляет E2E-пользователю уведомления о его сессиях.

        Сценарий: E2E-пользователь создал сессию (он владелец). Watcher
        отправляет уведомления только владельцу — E2E-пользователь должен
        получить уведомление.
        """
        callback = AsyncMock()
        session_watcher._callback = callback
        session_watcher._get_current_session = AsyncMock(return_value=None)

        # Watcher ещё не видел ни одного сообщения
        session_watcher._seen_message_counts[TEST_SESSION_ID] = 0

        fake_messages = [
            {"type": "user", "message": {"content": "Тестовый запрос"}},
            {"type": "assistant", "message": {"content": "Тестовый ответ"}},
        ]

        with patch(
            "claude_manager.session_watcher.session_reader.get_session_messages",
            new_callable=AsyncMock,
            return_value=fake_messages,
        ), patch.object(
            config, "ALLOWED_USER_IDS", {MAIN_USER_ID}
        ), patch.object(
            config, "E2E_TEST_USER_ID", E2E_USER_ID
        ), patch.object(
            config, "WORKING_DIR", "/tmp"
        ):
            # E2E-пользователь — владелец сессии
            session_manager._bindings = {E2E_USER_ID: TEST_SESSION_ID}

            await session_watcher._check_session(TEST_SESSION_ID)

        # Callback вызван ровно 1 раз — для владельца (E2E-пользователя)
        assert callback.call_count == 1

        actual_chat_id = callback.call_args_list[0].args[0]
        assert actual_chat_id == E2E_USER_ID

    async def test_config_e2e_and_allowed_ids_independent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """E2E_TEST_USER_ID и ALLOWED_USER_IDS полностью независимы при load_config.

        Сценарий: загрузка конфигурации с обоими параметрами. E2E_TEST_USER_ID
        не должен попасть в ALLOWED_USER_IDS, и наоборот.
        """
        # Подготавливаем переменные окружения для load_config
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-123")
        monkeypatch.setenv("ALLOWED_USER_IDS", str(MAIN_USER_ID))
        monkeypatch.setenv("E2E_TEST_USER_ID", str(E2E_USER_ID))
        # WORKING_DIR указывает на существующую директорию
        monkeypatch.setenv("CLAUDE_WORKING_DIR", str(tmp_path))
        # PROJECTS_ROOT_DIR тоже должен существовать
        monkeypatch.setenv("PROJECTS_ROOT_DIR", str(tmp_path))

        # load_dotenv может перезаписать переменные — отключаем, чтобы
        # тест работал только с тем, что задано через monkeypatch
        with patch("claude_manager.config.load_dotenv"):
            config.load_config()

        # E2E_TEST_USER_ID не попал в ALLOWED_USER_IDS
        assert E2E_USER_ID not in config.ALLOWED_USER_IDS

        # E2E_TEST_USER_ID загружен корректно
        assert config.E2E_TEST_USER_ID == E2E_USER_ID

        # Основной пользователь в ALLOWED_USER_IDS
        assert MAIN_USER_ID in config.ALLOWED_USER_IDS
