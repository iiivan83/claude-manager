"""Интеграционные тесты: жизненный цикл сессии.

Проверяет взаимодействие session_manager и daily_session_registry:
- Создание сессии через session_manager регистрирует её в daily_registry
- Поиск сессии по дневному номеру через /N
- Обновление session_id (временный -> реальный) синхронизируется в обоих модулях
- Переключение между сессиями через switch_to_session
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import daily_session_registry, session_manager
from claude_manager.daily_session_registry import REGISTRY_FILENAME
from claude_manager.session_manager import BINDINGS_FILENAME


# --- Фейковые данные ---

FAKE_TODAY = "2026-03-30"
CHAT_ID = 111111
SESSION_ALPHA = "session-alpha-001"
SESSION_BETA = "session-beta-002"
SESSION_REAL = "eb5ac5bc-2ac6-45ad-8ca9-bb3a1a741f1e"


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path: Path) -> None:
    """Сбрасывает состояние обоих модулей перед каждым тестом."""
    # daily_session_registry
    daily_session_registry._registry = {}
    daily_session_registry._registry_path = tmp_path / REGISTRY_FILENAME
    daily_session_registry._lock = asyncio.Lock()
    daily_session_registry._loaded_from_disk = True

    # session_manager
    session_manager._bindings = {}
    session_manager._bindings_path = tmp_path / BINDINGS_FILENAME
    session_manager._lock = asyncio.Lock()


# --- Тесты: создание сессии и регистрация ---


class TestCreateAndRegister:
    """Создание сессии через session_manager регистрирует её в daily_registry."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_create_new_session_registers_in_daily_registry(
        self, _mock_today: object
    ) -> None:
        """Создание новой сессии автоматически присваивает ей дневной номер."""
        result = await session_manager.create_new_session(CHAT_ID)

        # session_manager создал временный ID и привязал к чату
        assert result.session_id.startswith("_new_")
        assert result.day_number == 1

        # daily_registry знает об этой сессии
        found_id = await daily_session_registry.get_session_id_by_number(1)
        assert found_id == result.session_id

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_two_sessions_get_sequential_numbers(
        self, _mock_today: object
    ) -> None:
        """Две сессии подряд получают номера 1 и 2."""
        first = await session_manager.create_new_session(CHAT_ID)
        second = await session_manager.create_new_session(CHAT_ID)

        assert first.day_number == 1
        assert second.day_number == 2

        # Оба номера доступны в daily_registry
        assert await daily_session_registry.get_session_id_by_number(1) == first.session_id
        assert await daily_session_registry.get_session_id_by_number(2) == second.session_id

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_bind_existing_session_registers_in_daily_registry(
        self, _mock_today: object
    ) -> None:
        """bind_session для известного session_id регистрирует его в реестре."""
        day_number = await session_manager.bind_session(CHAT_ID, SESSION_ALPHA)

        assert day_number == 1
        assert session_manager.get_bound_session(CHAT_ID) == SESSION_ALPHA

        found_id = await daily_session_registry.get_session_id_by_number(1)
        assert found_id == SESSION_ALPHA


# --- Тесты: обновление session_id ---


class TestUpdateSessionId:
    """Обновление ID синхронизируется в session_manager и daily_registry."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_update_session_id_propagates_to_both_modules(
        self, _mock_today: object
    ) -> None:
        """Обновление session_id в session_manager обновляет и daily_registry."""
        # Создаём сессию с временным ID
        result = await session_manager.create_new_session(CHAT_ID)
        temp_id = result.session_id

        # Обновляем на реальный ID
        await session_manager.update_session_id(CHAT_ID, temp_id, SESSION_REAL)

        # session_manager: привязка обновлена
        assert session_manager.get_bound_session(CHAT_ID) == SESSION_REAL

        # daily_registry: номер указывает на новый ID
        found_id = await daily_session_registry.get_session_id_by_number(1)
        assert found_id == SESSION_REAL

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_update_session_id_preserves_day_number(
        self, _mock_today: object
    ) -> None:
        """После обновления ID дневной номер сессии не меняется."""
        result = await session_manager.create_new_session(CHAT_ID)
        temp_id = result.session_id
        original_number = result.day_number

        await session_manager.update_session_id(CHAT_ID, temp_id, SESSION_REAL)

        # Тот же номер — тот же слот, но с новым ID
        new_number = await daily_session_registry.register_session(SESSION_REAL)
        assert new_number == original_number


# --- Тесты: поиск сессии по номеру ---


class TestFindByNumber:
    """Поиск сессии по дневному номеру через switch_to_session."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_switch_to_known_session_by_number(
        self, _mock_today: object
    ) -> None:
        """Переключение на сессию по номеру, если она есть в реестре."""
        # Регистрируем сессию напрямую в реестре
        await daily_session_registry.register_session(SESSION_ALPHA)

        # Мокаем session_reader, чтобы не ходить на диск
        with patch(
            "claude_manager.session_manager.session_reader.get_recent_sessions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await session_manager.switch_to_session(CHAT_ID, 1)

        assert result.found is True
        assert result.session_id == SESSION_ALPHA
        assert result.day_number == 1

        # Чат привязан к найденной сессии
        assert session_manager.get_bound_session(CHAT_ID) == SESSION_ALPHA

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_switch_to_nonexistent_number_returns_not_found(
        self, _mock_today: object
    ) -> None:
        """Попытка переключиться на несуществующий номер возвращает found=False."""
        with patch(
            "claude_manager.session_manager.session_reader.get_recent_sessions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await session_manager.switch_to_session(CHAT_ID, 99)

        assert result.found is False
        assert result.day_number == 99

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_switch_overwrites_previous_binding(
        self, _mock_today: object
    ) -> None:
        """Переключение на другую сессию меняет привязку чата."""
        await daily_session_registry.register_session(SESSION_ALPHA)
        await daily_session_registry.register_session(SESSION_BETA)

        with patch(
            "claude_manager.session_manager.session_reader.get_recent_sessions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await session_manager.switch_to_session(CHAT_ID, 1)
            assert session_manager.get_bound_session(CHAT_ID) == SESSION_ALPHA

            await session_manager.switch_to_session(CHAT_ID, 2)
            assert session_manager.get_bound_session(CHAT_ID) == SESSION_BETA


# --- Тесты: отвязка и мониторинг ---


class TestUnbindAndMonitoring:
    """Отвязка сессии переводит чат в режим мониторинга /all."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_unbind_after_bind_enables_monitoring(
        self, _mock_today: object
    ) -> None:
        """После unbind чат возвращается в режим мониторинга."""
        await session_manager.bind_session(CHAT_ID, SESSION_ALPHA)
        assert not session_manager.is_monitoring_mode(CHAT_ID)

        await session_manager.unbind_session(CHAT_ID)
        assert session_manager.is_monitoring_mode(CHAT_ID)
        assert session_manager.get_bound_session(CHAT_ID) is None


# --- Тесты: персистентность через файлы ---


class TestFilePersistence:
    """Данные сохраняются на диск и загружаются при перезапуске."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_bindings_survive_reload(
        self, _mock_today: object, tmp_path: Path
    ) -> None:
        """Привязки сохраняются в файл и загружаются обратно."""
        # Создаём привязку — она сохранится в sessions.json
        await session_manager.bind_session(CHAT_ID, SESSION_ALPHA)

        # Проверяем, что файл создан
        bindings_file = tmp_path / BINDINGS_FILENAME
        assert bindings_file.exists()

        # Проверяем содержимое файла
        saved_data = json.loads(bindings_file.read_text("utf-8"))
        assert str(CHAT_ID) in saved_data
        assert saved_data[str(CHAT_ID)] == SESSION_ALPHA

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_registry_survives_reload(
        self, _mock_today: object, tmp_path: Path
    ) -> None:
        """Дневной реестр сохраняется в файл и загружаются обратно."""
        await daily_session_registry.register_session(SESSION_ALPHA)
        await daily_session_registry.register_session(SESSION_BETA)

        # Проверяем, что файл создан
        registry_file = tmp_path / REGISTRY_FILENAME
        assert registry_file.exists()

        # Проверяем содержимое файла
        saved_data = json.loads(registry_file.read_text("utf-8"))
        assert FAKE_TODAY in saved_data
        assert saved_data[FAKE_TODAY]["1"] == SESSION_ALPHA
        assert saved_data[FAKE_TODAY]["2"] == SESSION_BETA
