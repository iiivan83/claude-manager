"""Тесты модуля daily_session_registry — дневная нумерация сессий."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import daily_session_registry
from claude_manager.coding_agent_backend import BackendName
from claude_manager.daily_session_registry import (
    DATE_FORMAT,
    DailySessionEntry,
    REGISTRY_FILENAME,
    _get_today_key,
    _ensure_today_registry,
    _next_day_number,
    _remove_orphan_entries,
    _remove_phantom_entries,
    get_backend_for_session,
    get_all_today_sessions,
    get_session_id_by_number,
    load_registry,
    lookup_by_number,
    register_session,
    reset_state,
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
    async def test_register_session_with_backend_returns_entry(
        self, _mock_today: object
    ) -> None:
        """Регистрация сохраняет session_id вместе с backend."""
        result = await register_session("codex-session", BackendName.CODEX)

        assert result == 1
        assert await lookup_by_number(1) == DailySessionEntry(
            session_id="codex-session",
            backend=BackendName.CODEX,
        )

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_same_session_id_different_backend_gets_distinct_number(
        self, _mock_today: object
    ) -> None:
        """The same UUID under different backends is distinct ownership."""
        claude_number = await register_session("shared-uuid", BackendName.CLAUDE)
        codex_number = await register_session("shared-uuid", BackendName.CODEX)

        assert claude_number == 1
        assert codex_number == 2
        assert await lookup_by_number(1) == DailySessionEntry(
            "shared-uuid", BackendName.CLAUDE
        )
        assert await lookup_by_number(2) == DailySessionEntry(
            "shared-uuid", BackendName.CODEX
        )

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
        assert saved_data[FAKE_TODAY]["1"] == {
            "session_id": "abc123",
            "backend": "claude",
            "summary": "",
        }


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
    async def test_update_session_id_preserves_backend(
        self, _mock_today: object
    ) -> None:
        """Replacing a temp id keeps the backend that created the session."""
        await register_session("_new_codex", BackendName.CODEX)

        await update_session_id("_new_codex", "real-codex-id")

        assert await lookup_by_number(1) == DailySessionEntry(
            "real-codex-id", BackendName.CODEX
        )

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "_get_today_key", return_value=FAKE_TODAY)
    async def test_update_session_id_preserves_summary(
        self, _mock_today: object
    ) -> None:
        """Replacing a temp id keeps the generated session summary."""
        daily_session_registry._registry[FAKE_TODAY] = {
            "1": {
                "session_id": "_new_codex",
                "backend": "codex",
                "summary": "Загрузка отзывов за период",
            }
        }

        await update_session_id("_new_codex", "real-codex-id")

        entry = await lookup_by_number(1)
        assert getattr(entry, "summary", None) == "Загрузка отзывов за период"

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
    async def test_get_all_today_sessions_returns_entries(
        self, _mock_today: object
    ) -> None:
        """All sessions exposes backend-aware entries."""
        await register_session("claude-session", BackendName.CLAUDE)
        await register_session("codex-session", BackendName.CODEX)

        result = await get_all_today_sessions()

        assert result == {
            1: DailySessionEntry("claude-session", BackendName.CLAUDE),
            2: DailySessionEntry("codex-session", BackendName.CODEX),
        }

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
    async def test_load_migrates_old_format_to_claude_entries(
        self, tmp_path: Path
    ) -> None:
        """Old string values load as Claude entries and save back as dicts."""
        registry_file = tmp_path / REGISTRY_FILENAME
        registry_file.write_text(
            json.dumps({FAKE_TODAY: {"1": "old-session"}}),
            "utf-8",
        )

        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ), patch.object(
            daily_session_registry, "_remove_orphan_entries", return_value=0
        ), patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await load_registry()
            assert await lookup_by_number(1) == DailySessionEntry(
                "old-session", BackendName.CLAUDE
            )
        saved_data = json.loads(registry_file.read_text("utf-8"))
        assert saved_data[FAKE_TODAY]["1"] == {
            "session_id": "old-session",
            "backend": "claude",
            "summary": "",
        }

    @pytest.mark.asyncio()
    async def test_load_registry_reads_summary_field(self, tmp_path: Path) -> None:
        """Existing registry summary values are loaded into DailySessionEntry."""
        registry_file = tmp_path / REGISTRY_FILENAME
        registry_file.write_text(
            json.dumps({
                FAKE_TODAY: {
                    "1": {
                        "session_id": "review-loader",
                        "backend": "codex",
                        "summary": "Загрузка отзывов без фотографий",
                    }
                }
            }),
            "utf-8",
        )

        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ), patch.object(
            daily_session_registry, "_remove_orphan_entries", return_value=0
        ), patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await load_registry()
            entry = await lookup_by_number(1)
            assert getattr(entry, "summary", None) == "Загрузка отзывов без фотографий"

    @pytest.mark.asyncio()
    async def test_get_backend_for_session_searches_all_days(self) -> None:
        """Reverse lookup finds the backend for a known session across days."""
        daily_session_registry._registry = {
            FAKE_YESTERDAY: {
                "1": DailySessionEntry("old-codex", BackendName.CODEX)
            }
        }

        assert await get_backend_for_session("old-codex") == BackendName.CODEX
        assert await get_backend_for_session("missing") is None

    @pytest.mark.asyncio()
    async def test_load_missing_file_creates_empty_registry(
        self, tmp_path: Path
    ) -> None:
        """Отсутствие файла не вызывает ошибку — создаётся пустой реестр."""
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ), patch.object(
            daily_session_registry, "_remove_orphan_entries", return_value=0
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
        ), patch.object(
            daily_session_registry, "_remove_orphan_entries", return_value=0
        ):
            with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
                await load_registry()

            assert daily_session_registry._registry.get(FAKE_TODAY) == {}

    @pytest.mark.asyncio()
    async def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Данные сохраняются и корректно восстанавливаются."""
        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ), patch.object(
            daily_session_registry, "_remove_orphan_entries", return_value=0
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


class TestResetState:
    """Тесты сброса состояния daily_session_registry при переключении проекта."""

    @pytest.mark.asyncio()
    async def test_reset_clears_registry(self, tmp_path: Path) -> None:
        """После reset_state реестр сброшен и перезагружен из текущего WORKING_DIR."""
        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):
            await register_session("old-session")
            assert len(await get_all_today_sessions()) >= 1

            await reset_state()
            # Файл существует — реестр перезагрузится с той же сессией
            sessions = await get_all_today_sessions()
            assert "old-session" in sessions.values()

    @pytest.mark.asyncio()
    async def test_reset_reloads_from_new_path(self, tmp_path: Path) -> None:
        """reset_state после смены WORKING_DIR читает новый файл реестра."""
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        with patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):
            with patch("claude_manager.config.WORKING_DIR", str(project_a)):
                await register_session("session-in-a")

            with patch("claude_manager.config.WORKING_DIR", str(project_b)):
                await reset_state()
                sessions = await get_all_today_sessions()
                assert "session-in-a" not in sessions.values()

    @pytest.mark.asyncio()
    async def test_reset_preserves_save_capability(self, tmp_path: Path) -> None:
        """После reset_state можно снова регистрировать сессии — _loaded_from_disk восстановлен."""
        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):
            await reset_state()
            # Должно корректно зарегистрировать сессию и записать файл
            await register_session("after-reset")
            registry_file = tmp_path / REGISTRY_FILENAME
            assert registry_file.exists()

    @pytest.mark.asyncio()
    async def test_reset_resets_internal_path(self, tmp_path: Path) -> None:
        """После reset_state путь к файлу пересчитывается из нового WORKING_DIR."""
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        with patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):
            with patch("claude_manager.config.WORKING_DIR", str(project_a)):
                await load_registry()

            with patch("claude_manager.config.WORKING_DIR", str(project_b)):
                await reset_state()
            await register_session("new-in-b")

            registry_in_b = project_b / REGISTRY_FILENAME
            assert registry_in_b.exists()
            content_b = json.loads(registry_in_b.read_text("utf-8"))
            assert "new-in-b" in str(content_b)


class TestRemovePhantomEntries:
    """Тесты фильтрации фантомных записей с временными ID (префикс _new_)."""

    def test_removes_phantom_entries(self) -> None:
        """Записи с префиксом _new_ удаляются из реестра."""
        daily_session_registry._registry = {
            FAKE_TODAY: {
                "1": "real-session-aaa",
                "2": "_new_0001",
                "3": "real-session-bbb",
                "4": "_new_0002",
            }
        }

        removed = _remove_phantom_entries()

        assert removed == 2
        assert daily_session_registry._registry[FAKE_TODAY] == {
            "1": "real-session-aaa",
            "3": "real-session-bbb",
        }

    def test_no_phantom_entries_nothing_removed(self) -> None:
        """Если фантомных записей нет — ничего не удаляется."""
        daily_session_registry._registry = {
            FAKE_TODAY: {
                "1": "session-aaa",
                "2": "session-bbb",
            }
        }

        removed = _remove_phantom_entries()

        assert removed == 0
        assert len(daily_session_registry._registry[FAKE_TODAY]) == 2

    def test_phantom_count_across_multiple_days(self) -> None:
        """Счётчик удалённых записей корректен при фантомах в нескольких днях."""
        daily_session_registry._registry = {
            FAKE_TODAY: {
                "1": "_new_0001",
                "2": "real-session",
            },
            FAKE_YESTERDAY: {
                "1": "_new_0002",
                "2": "_new_0003",
                "3": "another-real",
            },
        }

        removed = _remove_phantom_entries()

        assert removed == 3
        assert daily_session_registry._registry[FAKE_TODAY] == {"2": "real-session"}
        assert daily_session_registry._registry[FAKE_YESTERDAY] == {"3": "another-real"}

    def test_normal_entries_preserved_after_removal(self) -> None:
        """После удаления фантомных записей нормальные остаются на месте."""
        daily_session_registry._registry = {
            FAKE_TODAY: {
                "1": "keep-me",
                "2": "_new_temp",
                "3": "keep-me-too",
            }
        }

        _remove_phantom_entries()

        remaining = daily_session_registry._registry[FAKE_TODAY]
        assert remaining["1"] == "keep-me"
        assert remaining["3"] == "keep-me-too"
        assert "2" not in remaining

    @pytest.mark.asyncio()
    async def test_load_registry_filters_phantoms(self, tmp_path: Path) -> None:
        """При загрузке реестра с диска фантомные записи автоматически удаляются."""
        registry_data = {
            FAKE_TODAY: {
                "1": "real-session",
                "2": "_new_0001",
                "3": "_new_0002",
            }
        }
        registry_file = tmp_path / REGISTRY_FILENAME
        registry_file.write_text(json.dumps(registry_data), "utf-8")

        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ), patch.object(
            daily_session_registry, "_remove_orphan_entries", return_value=0
        ):
            with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
                await load_registry()

        today_entries = daily_session_registry._registry[FAKE_TODAY]
        assert today_entries == {"1": "real-session"}

    def test_empty_registry_returns_zero(self) -> None:
        """Пустой реестр — счётчик возвращает 0."""
        daily_session_registry._registry = {}

        removed = _remove_phantom_entries()

        assert removed == 0


class TestRemoveOrphanEntries:
    """Тесты удаления записей-сирот без .jsonl файла на диске."""

    @pytest.mark.asyncio()
    async def test_orphan_cleanup_uses_backend_session_exists(
        self, tmp_path: Path
    ) -> None:
        """Orphan cleanup delegates file ownership checks to each backend."""
        live_backend = AsyncMock()
        live_backend.session_file_exists_for_project.return_value = True
        orphan_backend = AsyncMock()
        orphan_backend.session_file_exists_for_project.return_value = False
        daily_session_registry._registry = {
            FAKE_TODAY: {
                "1": DailySessionEntry("live-claude", BackendName.CLAUDE),
                "2": DailySessionEntry("orphan-codex", BackendName.CODEX),
            }
        }

        def fake_get_backend(backend_name: BackendName):
            return live_backend if backend_name == BackendName.CLAUDE else orphan_backend

        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)), patch(
            "claude_manager.daily_session_registry.get_backend",
            side_effect=fake_get_backend,
        ):
            removed = await _remove_orphan_entries()

        assert removed == 1
        assert "1" in daily_session_registry._registry[FAKE_TODAY]
        assert "2" not in daily_session_registry._registry[FAKE_TODAY]
        live_backend.session_file_exists_for_project.assert_awaited_once_with(
            "live-claude", str(tmp_path)
        )
        orphan_backend.session_file_exists_for_project.assert_awaited_once_with(
            "orphan-codex", str(tmp_path)
        )

    @pytest.mark.asyncio()
    async def test_entry_with_existing_file_kept(self, tmp_path: Path) -> None:
        """Запись с session_id, для которого есть .jsonl файл, остаётся в реестре."""
        backend = AsyncMock()
        backend.session_file_exists_for_project.return_value = True
        daily_session_registry._registry = {
            FAKE_TODAY: {"1": DailySessionEntry("valid-session-abc", BackendName.CLAUDE)}
        }

        with patch(
            "claude_manager.daily_session_registry.get_backend",
            return_value=backend,
        ):
            removed = await _remove_orphan_entries()

        assert removed == 0
        assert daily_session_registry._registry[FAKE_TODAY]["1"] == "valid-session-abc"

    @pytest.mark.asyncio()
    async def test_entry_without_file_removed(self, tmp_path: Path) -> None:
        """Запись с session_id без .jsonl файла удаляется из реестра."""
        backend = AsyncMock()
        backend.session_file_exists_for_project.return_value = False
        daily_session_registry._registry = {
            FAKE_TODAY: {"1": DailySessionEntry("orphan-session-xyz", BackendName.CLAUDE)}
        }

        with patch(
            "claude_manager.daily_session_registry.get_backend",
            return_value=backend,
        ):
            removed = await _remove_orphan_entries()

        assert removed == 1
        assert "1" not in daily_session_registry._registry[FAKE_TODAY]

    @pytest.mark.asyncio()
    async def test_new_prefix_entry_skipped(self, tmp_path: Path) -> None:
        """Запись с _new_ пропускается — не удаляется, даже если файла нет."""
        daily_session_registry._registry = {
            FAKE_TODAY: {"1": DailySessionEntry("_new_0001", BackendName.CLAUDE)}
        }

        removed = await _remove_orphan_entries()

        assert removed == 0
        assert daily_session_registry._registry[FAKE_TODAY]["1"] == "_new_0001"

    @pytest.mark.asyncio()
    async def test_orphan_cleanup_checks_files_concurrently(
        self, tmp_path: Path
    ) -> None:
        """Проверка файлов сессий идёт параллельно — иначе переключение проектов тормозит."""
        entry_count = 8

        in_flight_count = 0
        peak_in_flight_count = 0

        async def tracking_session_file_exists(
            session_id: str, project_dir: str
        ) -> bool:
            nonlocal in_flight_count, peak_in_flight_count
            in_flight_count += 1
            peak_in_flight_count = max(peak_in_flight_count, in_flight_count)
            try:
                await asyncio.sleep(0.01)
                return True
            finally:
                in_flight_count -= 1

        backend = AsyncMock()
        backend.session_file_exists_for_project.side_effect = tracking_session_file_exists

        daily_session_registry._registry = {
            FAKE_TODAY: {
                str(index + 1): DailySessionEntry(
                    f"session-{index}", BackendName.CLAUDE,
                )
                for index in range(entry_count)
            }
        }

        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)), patch(
            "claude_manager.daily_session_registry.get_backend",
            return_value=backend,
        ):
            await _remove_orphan_entries()

        assert peak_in_flight_count > 1, (
            "_remove_orphan_entries проверяет файлы последовательно — "
            f"переключение проектов тормозит (peak concurrency = {peak_in_flight_count})"
        )

    @pytest.mark.asyncio()
    async def test_load_registry_removes_orphans_and_saves(
        self, tmp_path: Path
    ) -> None:
        """При загрузке реестра записи-сироты удаляются и результат сохраняется на диск."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # Создаём файл только для валидной сессии
        (sessions_dir / "valid-session.jsonl").write_text("{}", "utf-8")

        registry_data = {
            FAKE_TODAY: {
                "1": "valid-session",
                "2": "orphan-no-file",
            }
        }
        registry_file = tmp_path / REGISTRY_FILENAME
        registry_file.write_text(json.dumps(registry_data), "utf-8")

        backend = AsyncMock()

        async def session_exists(session_id: str, project_dir: str) -> bool:
            return session_id == "valid-session"

        backend.session_file_exists_for_project.side_effect = session_exists

        with patch.object(
            daily_session_registry, "_get_today_key", return_value=FAKE_TODAY
        ), patch(
            "claude_manager.daily_session_registry.get_backend",
            return_value=backend,
        ), patch(
            "claude_manager.config.WORKING_DIR", str(tmp_path)
        ):
            await load_registry()

        # В памяти — только валидная запись
        today_entries = daily_session_registry._registry[FAKE_TODAY]
        assert today_entries == {"1": "valid-session"}

        # На диске — тоже только валидная запись (сохранение произошло)
        saved_data = json.loads(registry_file.read_text("utf-8"))
        assert "2" not in saved_data.get(FAKE_TODAY, {})
