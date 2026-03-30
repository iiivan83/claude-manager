"""Тесты модуля daily_session_registry — дневная нумерация сессий."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_manager import daily_session_registry
from claude_manager.daily_session_registry import (
    DATE_FORMAT,
    REGISTRY_FILENAME,
    _get_today_key,
    _ensure_today_registry,
    _next_day_number,
    get_all_today_sessions,
    get_session_id_by_number,
    load_registry,
    register_session,
    update_session_id,
)


# --- Фикстуры ---

# Фейковая дата "сегодня" для предсказуемости тестов
FAKE_TODAY = "2026-03-30"
FAKE_YESTERDAY = "2026-03-29"


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path: Path) -> None:
    """Сбрасывает внутреннее состояние модуля перед каждым тестом.

    Без этого тесты влияли бы друг на друга через глобальные переменные.
    """
    daily_session_registry._registry = {}
    daily_session_registry._registry_path = tmp_path / REGISTRY_FILENAME
    daily_session_registry._lock = asyncio.Lock()
    daily_session_registry._loaded_from_disk = True


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    """Путь к файлу реестра во временной директории."""
    return tmp_path / REGISTRY_FILENAME


# --- Юнит-тесты ---


class TestRegisterSession:
    """Тесты регистрации сессий."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_register_first_session_returns_one(
        self, _mock_today: object
    ) -> None:
        """Первая сессия за день получает номер 1."""
        result = await register_session("abc123-def456")
        assert result == 1

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_register_second_session_returns_two(
        self, _mock_today: object
    ) -> None:
        """Вторая сессия за день получает номер 2."""
        await register_session("first-session")
        result = await register_session("second-session")
        assert result == 2

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_register_same_session_twice_returns_same_number(
        self, _mock_today: object
    ) -> None:
        """Повторная регистрация возвращает тот же номер (идемпотентность)."""
        first_call = await register_session("abc123")
        second_call = await register_session("abc123")
        assert first_call == 1
        assert second_call == 1

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_register_session_with_temporary_id(
        self, _mock_today: object
    ) -> None:
        """Сессия с временным ID формата _new_XXXX регистрируется нормально."""
        result = await register_session("_new_0001")
        assert result == 1

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_register_session_saves_to_disk(
        self, _mock_today: object
    ) -> None:
        """После регистрации данные записываются в файл."""
        await register_session("abc123")

        registry_file = daily_session_registry._registry_path
        assert registry_file is not None
        assert registry_file.exists()

        saved_data = json.loads(registry_file.read_text("utf-8"))
        assert FAKE_TODAY in saved_data
        assert saved_data[FAKE_TODAY]["1"] == "abc123"


class TestGetSessionIdByNumber:
    """Тесты поиска сессии по дневному номеру."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_get_session_id_by_existing_number(
        self, _mock_today: object
    ) -> None:
        """Зарегистрированная сессия находится по номеру."""
        await register_session("first-session")
        await register_session("second-session")

        result = await get_session_id_by_number(2)
        assert result == "second-session"

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_get_session_id_by_nonexistent_number(
        self, _mock_today: object
    ) -> None:
        """Несуществующий номер возвращает None."""
        await register_session("only-session")

        result = await get_session_id_by_number(99)
        assert result is None


class TestUpdateSessionId:
    """Тесты обновления ID сессии."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_update_session_id_replaces_old_with_new(
        self, _mock_today: object
    ) -> None:
        """Временный ID заменяется на реальный."""
        await register_session("_new_0001")

        await update_session_id("_new_0001", "real-session-id")

        result = await get_session_id_by_number(1)
        assert result == "real-session-id"

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_update_nonexistent_session_id_no_error(
        self, _mock_today: object
    ) -> None:
        """Обновление несуществующего ID не вызывает ошибку."""
        await register_session("existing-session")

        # Не должно бросать исключение
        await update_session_id("nonexistent-id", "new-id")

        # Существующая сессия не изменилась
        result = await get_session_id_by_number(1)
        assert result == "existing-session"

    @pytest.mark.asyncio()
    async def test_update_session_id_in_yesterday_registry(self) -> None:
        """Обновление ID в записях предыдущего дня работает корректно."""
        # Вручную заполняем реестр вчерашним днём
        daily_session_registry._registry[FAKE_YESTERDAY] = {"1": "_new_0001"}

        await update_session_id("_new_0001", "real-id")

        yesterday_entries = daily_session_registry._registry[FAKE_YESTERDAY]
        assert yesterday_entries["1"] == "real-id"


class TestGetAllTodaySessions:
    """Тесты получения всех сессий за сегодня."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_get_all_today_sessions_returns_copy(
        self, _mock_today: object
    ) -> None:
        """Возвращает копию — изменение не влияет на внутреннее состояние."""
        await register_session("session-one")
        await register_session("session-two")

        result = await get_all_today_sessions()

        # Модифицируем возвращённый словарь
        result[999] = "hacker-session"

        # Внутреннее состояние не изменилось
        internal_result = await get_all_today_sessions()
        assert 999 not in internal_result

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_get_all_today_sessions_empty(
        self, _mock_today: object
    ) -> None:
        """Пустой реестр за сегодня возвращает пустой словарь."""
        result = await get_all_today_sessions()
        assert result == {}

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_get_all_today_sessions_has_int_keys(
        self, _mock_today: object
    ) -> None:
        """Ключи в результате — числа (int), а не строки."""
        await register_session("session-one")

        result = await get_all_today_sessions()
        for key in result:
            assert isinstance(key, int)


class TestLoadRegistry:
    """Тесты загрузки реестра из файла."""

    @pytest.mark.asyncio()
    async def test_load_missing_file_creates_empty_registry(
        self, tmp_path: Path
    ) -> None:
        """Отсутствие файла не вызывает ошибку — создаётся пустой реестр."""
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ):
            daily_session_registry._registry_path = None
            with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
                await load_registry()

            # Реестр пуст, но секция для сегодня создана
            assert daily_session_registry._registry.get(FAKE_TODAY) == {}

    @pytest.mark.asyncio()
    async def test_load_corrupted_json_creates_empty_registry(
        self, tmp_path: Path
    ) -> None:
        """Повреждённый JSON не ломает модуль — создаётся пустой реестр."""
        # Записываем битый JSON
        corrupted_file = tmp_path / REGISTRY_FILENAME
        corrupted_file.write_text("{not valid json", "utf-8")

        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ):
            with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
                await load_registry()

            assert daily_session_registry._registry.get(FAKE_TODAY) == {}

    @pytest.mark.asyncio()
    async def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Данные сохраняются и корректно восстанавливаются."""
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ):
            with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
                # Первая «жизнь» — регистрируем сессии
                await load_registry()
                await register_session("session-a")
                await register_session("session-b")
                await register_session("session-c")

                # Вторая «жизнь» — сбрасываем память и загружаем из файла
                daily_session_registry._registry = {}
                await load_registry()

                result = await get_all_today_sessions()
                assert result == {1: "session-a", 2: "session-b", 3: "session-c"}


# --- Граничные случаи ---


class TestEdgeCases:
    """Граничные случаи: смена дня, параллельный доступ, пропуски в нумерации."""

    @pytest.mark.asyncio()
    async def test_midnight_reset_creates_new_day(self) -> None:
        """После полуночи нумерация начинается с 1."""
        # Регистрируем сессии "вчера"
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_YESTERDAY
        ):
            await register_session("yesterday-1")
            await register_session("yesterday-2")

        # Наступил новый день
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ):
            result = await register_session("today-first")
            assert result == 1

    @pytest.mark.asyncio()
    async def test_yesterday_sessions_preserved_after_midnight(self) -> None:
        """Вчерашние записи сохраняются при смене дня."""
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_YESTERDAY
        ):
            await register_session("yesterday-session")

        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ):
            await register_session("today-session")

        # Записи за оба дня на месте
        assert FAKE_YESTERDAY in daily_session_registry._registry
        assert FAKE_TODAY in daily_session_registry._registry
        assert daily_session_registry._registry[FAKE_YESTERDAY]["1"] == "yesterday-session"

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_concurrent_register_sessions(
        self, _mock_today: object
    ) -> None:
        """Две параллельные регистрации не теряют данные."""
        results = await asyncio.gather(
            register_session("session-aaa"),
            register_session("session-bbb"),
        )

        # Обе сессии получили разные номера
        assert sorted(results) == [1, 2]

        # Обе зарегистрированы
        all_sessions = await get_all_today_sessions()
        assert len(all_sessions) == 2
        assert "session-aaa" in all_sessions.values()
        assert "session-bbb" in all_sessions.values()

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_number_after_gap_in_sequence(
        self, _mock_today: object
    ) -> None:
        """При пропуске в нумерации — следующий номер = max + 1."""
        # Вручную создаём реестр с «дырой» (номера 1, 2, 5)
        daily_session_registry._registry[FAKE_TODAY] = {
            "1": "session-a",
            "2": "session-b",
            "5": "session-c",
        }

        result = await register_session("new-session")

        # Следующий номер — 6 (max(1,2,5) + 1)
        assert result == 6


# --- Тесты ошибок ---


class TestErrors:
    """Тесты обработки ошибок."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_save_to_readonly_directory_raises_oserror(
        self, _mock_today: object, tmp_path: Path
    ) -> None:
        """Ошибка записи проксируется вызывающему коду."""
        # Указываем путь в несуществующей директории
        impossible_path = tmp_path / "nonexistent_dir" / REGISTRY_FILENAME
        daily_session_registry._registry_path = impossible_path

        with pytest.raises(OSError):
            await register_session("abc123")

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_atomic_write_preserves_original_on_failure(
        self, _mock_today: object, tmp_path: Path
    ) -> None:
        """При ошибке записи оригинальный файл не повреждён."""
        # Создаём валидный файл реестра
        registry_file = tmp_path / REGISTRY_FILENAME
        original_content = json.dumps({FAKE_TODAY: {"1": "original-session"}})
        registry_file.write_text(original_content, "utf-8")

        daily_session_registry._registry_path = registry_file
        daily_session_registry._registry = {FAKE_TODAY: {"1": "original-session"}}

        # _save_registry выбрасывает ошибку — имитация сбоя записи на диск
        with patch.object(
            daily_session_registry, "_save_registry", side_effect=OSError("disk full")
        ):
            with pytest.raises(OSError, match="disk full"):
                await register_session("new-session")

        # Оригинальный файл не повреждён (ни write_text, ни os.replace не вызывались)
        saved_content = registry_file.read_text("utf-8")
        assert saved_content == original_content
