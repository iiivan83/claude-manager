"""Интеграционные тесты: конкурентный доступ к общему состоянию.

Проверяет корректность при одновременных операциях:
- Параллельная регистрация сессий в daily_session_registry
- Параллельные bind/unbind в session_manager
- Атомарность записи файлов (sessions.json и daily_sessions.json)
- Защита от потери данных при гонке потоков
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_manager import daily_session_registry, session_manager
from claude_manager.daily_session_registry import REGISTRY_FILENAME
from claude_manager.session_manager import BINDINGS_FILENAME


# --- Фейковые данные ---

FAKE_TODAY = "2026-03-30"

# Количество параллельных операций для тестов конкурентности
CONCURRENT_OPERATIONS_COUNT = 20


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path: Path) -> None:
    """Сбрасывает состояние обоих модулей перед каждым тестом."""
    daily_session_registry._registry = {}
    daily_session_registry._registry_path = tmp_path / REGISTRY_FILENAME
    daily_session_registry._lock = asyncio.Lock()

    session_manager._bindings = {}
    session_manager._bindings_path = tmp_path / BINDINGS_FILENAME
    session_manager._lock = asyncio.Lock()
    session_manager._temp_counter = 0


# --- Тесты: параллельная регистрация сессий ---


class TestConcurrentRegistration:
    """Параллельная регистрация сессий в daily_session_registry."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_register_assigns_unique_numbers(
        self, _mock_today: object
    ) -> None:
        """Параллельная регистрация разных сессий — каждая получает уникальный номер."""
        session_ids = [f"session-{i}" for i in range(CONCURRENT_OPERATIONS_COUNT)]

        # Запускаем все регистрации одновременно
        tasks = [
            daily_session_registry.register_session(sid)
            for sid in session_ids
        ]
        numbers = await asyncio.gather(*tasks)

        # Все номера уникальны
        assert len(set(numbers)) == CONCURRENT_OPERATIONS_COUNT

        # Номера идут от 1 до N (не обязательно по порядку, но все присутствуют)
        expected_numbers = set(range(1, CONCURRENT_OPERATIONS_COUNT + 1))
        assert set(numbers) == expected_numbers

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_register_same_session_returns_same_number(
        self, _mock_today: object
    ) -> None:
        """Параллельная регистрация одной и той же сессии — один и тот же номер."""
        session_id = "same-session"

        tasks = [
            daily_session_registry.register_session(session_id)
            for _ in range(CONCURRENT_OPERATIONS_COUNT)
        ]
        numbers = await asyncio.gather(*tasks)

        # Все возвращённые номера одинаковые
        assert len(set(numbers)) == 1
        assert numbers[0] == 1

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_register_file_not_corrupted(
        self, _mock_today: object, tmp_path: Path
    ) -> None:
        """После параллельной регистрации файл daily_sessions.json корректен."""
        session_ids = [f"file-test-{i}" for i in range(CONCURRENT_OPERATIONS_COUNT)]

        tasks = [
            daily_session_registry.register_session(sid)
            for sid in session_ids
        ]
        await asyncio.gather(*tasks)

        # Файл существует и содержит валидный JSON
        registry_file = tmp_path / REGISTRY_FILENAME
        assert registry_file.exists()

        saved_data = json.loads(registry_file.read_text("utf-8"))
        today_entries = saved_data[FAKE_TODAY]

        # Все сессии записаны
        saved_session_ids = set(today_entries.values())
        assert saved_session_ids == set(session_ids)


# --- Тесты: параллельные операции с привязками ---


class TestConcurrentBindings:
    """Параллельные bind/unbind в session_manager."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_bind_different_chats(
        self, _mock_today: object
    ) -> None:
        """Параллельная привязка разных чатов к разным сессиям."""
        pairs = [
            (100000 + i, f"bind-session-{i}")
            for i in range(CONCURRENT_OPERATIONS_COUNT)
        ]

        tasks = [
            session_manager.bind_session(chat_id, session_id)
            for chat_id, session_id in pairs
        ]
        await asyncio.gather(*tasks)

        # Все привязки установлены
        all_bindings = session_manager.get_all_bindings()
        assert len(all_bindings) == CONCURRENT_OPERATIONS_COUNT

        for chat_id, session_id in pairs:
            assert all_bindings[chat_id] == session_id

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_bind_unbind_no_crash(
        self, _mock_today: object
    ) -> None:
        """Одновременные bind и unbind не вызывают ошибок."""
        chat_id = 999999

        async def bind_then_unbind(session_suffix: int) -> None:
            """Привязывает и тут же отвязывает."""
            await session_manager.bind_session(chat_id, f"sess-{session_suffix}")
            await session_manager.unbind_session(chat_id)

        tasks = [bind_then_unbind(i) for i in range(10)]
        # Не должно бросить исключение
        await asyncio.gather(*tasks)

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_bindings_file_valid_json(
        self, _mock_today: object, tmp_path: Path
    ) -> None:
        """После конкурентных операций sessions.json содержит валидный JSON."""
        pairs = [
            (200000 + i, f"json-session-{i}")
            for i in range(CONCURRENT_OPERATIONS_COUNT)
        ]

        tasks = [
            session_manager.bind_session(chat_id, session_id)
            for chat_id, session_id in pairs
        ]
        await asyncio.gather(*tasks)

        # Файл существует и содержит валидный JSON
        bindings_file = tmp_path / BINDINGS_FILENAME
        assert bindings_file.exists()

        saved_data = json.loads(bindings_file.read_text("utf-8"))
        assert len(saved_data) == CONCURRENT_OPERATIONS_COUNT


# --- Тесты: параллельное создание новых сессий ---


class TestConcurrentNewSessions:
    """Параллельное создание новых сессий через session_manager."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_create_new_sessions_unique_ids(
        self, _mock_today: object
    ) -> None:
        """Параллельное создание новых сессий — все ID уникальны."""
        chat_ids = [300000 + i for i in range(10)]

        tasks = [
            session_manager.create_new_session(chat_id)
            for chat_id in chat_ids
        ]
        results = await asyncio.gather(*tasks)

        # Все session_id уникальны
        session_ids = [r.session_id for r in results]
        assert len(set(session_ids)) == len(chat_ids)

        # Все дневные номера уникальны
        day_numbers = [r.day_number for r in results]
        assert len(set(day_numbers)) == len(chat_ids)


# --- Тесты: параллельное обновление session_id ---


class TestConcurrentUpdateSessionId:
    """Параллельное обновление session_id в обоих модулях."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_update_different_sessions(
        self, _mock_today: object
    ) -> None:
        """Параллельное обновление разных session_id не теряет данные."""
        count = 10
        chat_ids = [400000 + i for i in range(count)]
        temp_ids = []

        # Создаём сессии последовательно (чтобы temp_id были предсказуемы)
        for chat_id in chat_ids:
            result = await session_manager.create_new_session(chat_id)
            temp_ids.append(result.session_id)

        # Обновляем все session_id параллельно
        real_ids = [f"real-uuid-{i}" for i in range(count)]
        tasks = [
            session_manager.update_session_id(chat_id, old_id, new_id)
            for chat_id, old_id, new_id in zip(chat_ids, temp_ids, real_ids)
        ]
        await asyncio.gather(*tasks)

        # Все привязки обновлены на новые ID
        all_bindings = session_manager.get_all_bindings()
        for chat_id, expected_id in zip(chat_ids, real_ids):
            assert all_bindings[chat_id] == expected_id

        # Все дневные номера указывают на реальные ID
        for i in range(count):
            found_id = await daily_session_registry.get_session_id_by_number(i + 1)
            assert found_id == real_ids[i]
