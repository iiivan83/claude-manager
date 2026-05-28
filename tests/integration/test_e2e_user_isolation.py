"""Интеграционные тесты изоляции E2E тестового аккаунта.

Проверяют, что E2E_TEST_USER_ID корректно обрабатывается
на стыке модулей config -> bot -> session_watcher.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import config, daily_session_registry, session_manager, session_watcher
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)


# Константы для тестов
MAIN_USER_ID = 111111
E2E_USER_ID = 999999
TEST_SESSION_ID = "test-session-abc"
FAKE_TODAY = "2026-04-11"


class FakeBackend:
    """Minimal backend reader for session_watcher integration tests."""

    name = BackendName.CLAUDE

    def __init__(self, session_id: str, snapshot: SessionFileSnapshot) -> None:
        self.session_id = session_id
        self.file_path = f"/tmp/{session_id}.jsonl"
        self.snapshot = snapshot

    async def list_all_session_files_for_project(
        self,
        _project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        del lookback_days
        return [
            SessionFileInfo(
                session_id=self.session_id,
                file_path=self.file_path,
                last_modified_at=1.0,
                preview="preview",
            )
        ]

    async def read_session_file_snapshot(
        self,
        _file_path: str,
    ) -> SessionFileSnapshot:
        return self.snapshot


def _snapshot(text: str) -> SessionFileSnapshot:
    return SessionFileSnapshot(
        messages=[
            SessionMessage(
                role="user",
                text="Запрос",
                timestamp=None,
                is_empty_response=False,
            ),
            SessionMessage(
                role="assistant",
                text=text,
                timestamp=None,
                is_empty_response=False,
            ),
        ],
        raw_record_count=2,
        last_record=None,
        is_turn_active=False,
    )


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _reset_watcher_state() -> None:
    """Сбрасывает внутреннее состояние watcher перед каждым тестом."""
    session_watcher._watchers = {}
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
    session_manager._bindings_loaded_from_disk = True
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
        watcher = session_watcher.SessionWatcher(
            FakeBackend(TEST_SESSION_ID, _snapshot("Ответ от Claude"))
        )

        with patch.object(
            config, "ALLOWED_USER_IDS", {MAIN_USER_ID, E2E_USER_ID}
        ), patch.object(
            config, "E2E_TEST_USER_ID", E2E_USER_ID
        ), patch.object(
            config, "WORKING_DIR", "/tmp"
        ):
            # Сессия не привязана ни к кому — fallback на broadcast
            session_manager._bindings = {}

            await watcher.poll_once(callback, AsyncMock(return_value=None))

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
        watcher = session_watcher.SessionWatcher(
            FakeBackend(TEST_SESSION_ID, _snapshot("Тестовый ответ"))
        )

        with patch.object(
            config, "ALLOWED_USER_IDS", {MAIN_USER_ID}
        ), patch.object(
            config, "E2E_TEST_USER_ID", E2E_USER_ID
        ), patch.object(
            config, "WORKING_DIR", "/tmp"
        ):
            # E2E-пользователь — владелец сессии
            session_manager._bindings = {E2E_USER_ID: TEST_SESSION_ID}

            await watcher.poll_once(callback, AsyncMock(return_value=None))

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
