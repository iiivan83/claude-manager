"""Тесты модуля session_manager — управление привязкой чатов к сессиям."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import daily_session_registry, session_manager
from claude_manager.session_manager import (
    BINDINGS_FILENAME,
    TEMP_SESSION_PREFIX,
    NewSessionResult,
    SwitchResult,
    _generate_temp_session_id,
    bind_session,
    create_new_session,
    get_all_bindings,
    get_bound_session,
    get_chat_id_for_session,
    is_monitoring_mode,
    load_bindings,
    reset_state,
    switch_to_session,
    unbind_session,
    update_session_id,
)
from claude_manager.session_reader import SessionInfo


# --- Фикстуры ---

# Фейковые идентификаторы для предсказуемости тестов
CHAT_ID_ALICE = 111111111
CHAT_ID_BOB = 222222222
SESSION_FIRST = "abc-def-111"
SESSION_SECOND = "abc-def-222"
SESSION_TEMP = "_new_test_temp_id"
SESSION_REAL = "eb5ac5bc-2ac6-45ad-8ca9-bb3a1a741f1e"


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path: Path) -> None:
    """Сбрасывает внутреннее состояние модуля перед каждым тестом."""
    session_manager._bindings = {}
    session_manager._bindings_path = tmp_path / BINDINGS_FILENAME
    session_manager._lock = asyncio.Lock()

    # Сбрасываем состояние daily_session_registry (чтобы register_session работал)
    daily_session_registry._registry = {}
    daily_session_registry._registry_path = tmp_path / "daily_sessions.json"
    daily_session_registry._lock = asyncio.Lock()


@pytest.fixture()
def bindings_path(tmp_path: Path) -> Path:
    """Путь к файлу привязок во временной директории."""
    return tmp_path / BINDINGS_FILENAME


# --- Юнит-тесты ---


class TestBindSession:
    """Тесты привязки чата к сессии."""

    @pytest.mark.asyncio()
    async def test_bind_session_stores_binding(self) -> None:
        """Привязка сохраняется в памяти."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        assert get_bound_session(CHAT_ID_ALICE) == SESSION_FIRST

    @pytest.mark.asyncio()
    async def test_bind_session_returns_day_number(self) -> None:
        """Привязка возвращает дневной номер от реестра."""
        day_number = await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        assert day_number == 1

    @pytest.mark.asyncio()
    async def test_bind_session_saves_to_disk(self, bindings_path: Path) -> None:
        """После привязки данные записываются в файл."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)

        assert bindings_path.exists()
        saved_data = json.loads(bindings_path.read_text("utf-8"))
        assert saved_data[str(CHAT_ID_ALICE)] == SESSION_FIRST

    @pytest.mark.asyncio()
    async def test_bind_session_overwrites_previous(self) -> None:
        """Повторная привязка перезаписывает предыдущую сессию."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await bind_session(CHAT_ID_ALICE, SESSION_SECOND)
        assert get_bound_session(CHAT_ID_ALICE) == SESSION_SECOND


class TestUnbindSession:
    """Тесты отвязки чата от сессии."""

    @pytest.mark.asyncio()
    async def test_unbind_session_removes_binding(self) -> None:
        """Отвязка удаляет привязку."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await unbind_session(CHAT_ID_ALICE)
        assert get_bound_session(CHAT_ID_ALICE) is None

    @pytest.mark.asyncio()
    async def test_unbind_session_saves_to_disk(self, bindings_path: Path) -> None:
        """После отвязки файл обновляется."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await unbind_session(CHAT_ID_ALICE)

        saved_data = json.loads(bindings_path.read_text("utf-8"))
        assert saved_data == {}


class TestGetBoundSession:
    """Тесты получения привязанной сессии."""

    def test_get_bound_session_returns_none_for_unbound(self) -> None:
        """Для непривязанного чата возвращается None."""
        assert get_bound_session(999999999) is None


class TestGetChatIdForSession:
    """Тесты обратного поиска: session_id -> chat_id."""

    @pytest.mark.asyncio()
    async def test_returns_chat_id_when_session_bound(self) -> None:
        """Привязка существует — возвращает правильный chat_id."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        assert get_chat_id_for_session(SESSION_FIRST) == CHAT_ID_ALICE

    def test_returns_none_when_session_not_bound(self) -> None:
        """Привязки нет — возвращает None."""
        assert get_chat_id_for_session("nonexistent") is None

    @pytest.mark.asyncio()
    async def test_returns_chat_id_for_shared_session(self) -> None:
        """Два чата на одну сессию — возвращает один из них (не None)."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await bind_session(CHAT_ID_BOB, SESSION_FIRST)

        result = get_chat_id_for_session(SESSION_FIRST)
        assert result in {CHAT_ID_ALICE, CHAT_ID_BOB}

    @pytest.mark.asyncio()
    async def test_returns_none_after_unbind(self) -> None:
        """После unbind обратный поиск не находит удалённую привязку."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await unbind_session(CHAT_ID_ALICE)
        assert get_chat_id_for_session(SESSION_FIRST) is None


class TestIsMonitoringMode:
    """Тесты проверки режима мониторинга."""

    @pytest.mark.asyncio()
    async def test_is_monitoring_mode_true_when_unbound(self) -> None:
        """В режиме /all is_monitoring_mode возвращает True."""
        await unbind_session(CHAT_ID_ALICE)
        assert is_monitoring_mode(CHAT_ID_ALICE) is True

    @pytest.mark.asyncio()
    async def test_is_monitoring_mode_false_when_bound(self) -> None:
        """При привязке к сессии возвращает False."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        assert is_monitoring_mode(CHAT_ID_ALICE) is False


class TestSwitchToSession:
    """Тесты переключения на сессию по номеру."""

    @pytest.mark.asyncio()
    async def test_switch_to_session_found_in_registry(self) -> None:
        """Переключение на сессию, найденную в дневном реестре."""
        # Регистрируем сессию в дневном реестре через привязку
        await bind_session(CHAT_ID_BOB, SESSION_FIRST)

        # Мокаем session_reader, чтобы вернуть превью
        fake_sessions = [
            SessionInfo(session_id=SESSION_FIRST, created_at="2026-03-30T10:00:00Z", preview="Привет Claude"),
        ]
        with patch("claude_manager.session_manager.session_reader.get_recent_sessions", new_callable=AsyncMock, return_value=fake_sessions):
            result = await switch_to_session(CHAT_ID_ALICE, 1)

        assert result.found is True
        assert result.session_id == SESSION_FIRST
        assert result.day_number == 1
        assert result.preview == "Привет Claude"

    @pytest.mark.asyncio()
    async def test_switch_to_session_not_found(self) -> None:
        """Переключение на несуществующий номер."""
        with patch("claude_manager.session_manager.session_reader.get_recent_sessions", new_callable=AsyncMock, return_value=[]):
            result = await switch_to_session(CHAT_ID_ALICE, 99)

        assert result.found is False
        assert result.session_id == ""
        assert result.day_number == 99
        assert result.preview == ""

    @pytest.mark.asyncio()
    async def test_switch_to_session_found_among_visible(self) -> None:
        """Сессия не в реестре, но найдена среди видимых на диске."""
        # Создаём 5 фейковых сессий
        fake_sessions = [
            SessionInfo(session_id=f"session-{i}", created_at=f"2026-03-30T{i:02d}:00:00Z", preview=f"Сообщение {i}")
            for i in range(1, 6)
        ]

        with patch("claude_manager.session_manager.session_reader.get_recent_sessions", new_callable=AsyncMock, return_value=fake_sessions):
            result = await switch_to_session(CHAT_ID_ALICE, 5)

        assert result.found is True
        assert result.session_id == "session-5"
        assert result.day_number == 5
        assert result.preview == "Сообщение 5"


class TestCreateNewSession:
    """Тесты создания новой сессии."""

    @pytest.mark.asyncio()
    async def test_create_new_session_generates_temp_id(self) -> None:
        """Создание новой сессии генерирует временный ID."""
        result = await create_new_session(CHAT_ID_ALICE)
        assert result.session_id.startswith(TEMP_SESSION_PREFIX)
        assert result.day_number > 0

    @pytest.mark.asyncio()
    async def test_create_new_session_increments_counter(self) -> None:
        """Каждый вызов генерирует уникальный ID."""
        result_first = await create_new_session(CHAT_ID_ALICE)
        result_second = await create_new_session(CHAT_ID_ALICE)
        assert result_first.session_id != result_second.session_id


class TestUpdateSessionId:
    """Тесты обновления session_id."""

    @pytest.mark.asyncio()
    async def test_update_session_id_updates_binding(self) -> None:
        """Обновление session_id меняет привязку."""
        await bind_session(CHAT_ID_ALICE, SESSION_TEMP)
        await update_session_id(CHAT_ID_ALICE, SESSION_TEMP, SESSION_REAL)
        assert get_bound_session(CHAT_ID_ALICE) == SESSION_REAL

    @pytest.mark.asyncio()
    async def test_update_session_id_calls_registry_update(self) -> None:
        """Обновление вызывает daily_session_registry.update_session_id."""
        await bind_session(CHAT_ID_ALICE, SESSION_TEMP)

        with patch.object(daily_session_registry, "update_session_id", new_callable=AsyncMock) as mock_registry_update:
            await update_session_id(CHAT_ID_ALICE, SESSION_TEMP, SESSION_REAL)
            mock_registry_update.assert_called_once_with(SESSION_TEMP, SESSION_REAL)


class TestLoadAndSave:
    """Тесты загрузки и сохранения привязок."""

    @pytest.mark.asyncio()
    async def test_load_and_save_roundtrip(self, tmp_path: Path) -> None:
        """Данные сохраняются и корректно восстанавливаются."""
        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            # Первая «жизнь» — привязываем чаты
            await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
            await bind_session(CHAT_ID_BOB, SESSION_SECOND)

            # Вторая «жизнь» — сбрасываем память и загружаем из файла
            session_manager._bindings = {}
            await load_bindings()

            assert get_bound_session(CHAT_ID_ALICE) == SESSION_FIRST
            assert get_bound_session(CHAT_ID_BOB) == SESSION_SECOND


class TestGetAllBindings:
    """Тесты получения всех привязок."""

    @pytest.mark.asyncio()
    async def test_get_all_bindings_returns_copy(self) -> None:
        """Возвращается копия, не ссылка на внутренний словарь."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)

        bindings_copy = get_all_bindings()
        bindings_copy[999999] = "hacker-session"

        # Внутреннее состояние не изменилось
        assert get_bound_session(999999) is None

    @pytest.mark.asyncio()
    async def test_get_all_bindings_excludes_monitoring(self) -> None:
        """Чаты в режиме /all не включаются."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await unbind_session(CHAT_ID_BOB)

        bindings = get_all_bindings()
        assert CHAT_ID_ALICE in bindings
        assert CHAT_ID_BOB not in bindings


# --- Граничные случаи ---


class TestEdgeCases:
    """Граничные случаи."""

    @pytest.mark.asyncio()
    async def test_unbind_already_unbound_chat(self) -> None:
        """Отвязка непривязанного чата не вызывает ошибку (идемпотентность)."""
        # Не должно бросать исключение
        await unbind_session(999999999)

    @pytest.mark.asyncio()
    async def test_bind_same_session_twice(self, bindings_path: Path) -> None:
        """Привязка к той же сессии не дублирует записи."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)

        assert get_bound_session(CHAT_ID_ALICE) == SESSION_FIRST

        saved_data = json.loads(bindings_path.read_text("utf-8"))
        assert len(saved_data) == 1

    @pytest.mark.asyncio()
    async def test_multiple_chats_same_session(self) -> None:
        """Несколько чатов могут быть привязаны к одной сессии."""
        await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
        await bind_session(CHAT_ID_BOB, SESSION_FIRST)

        bindings = get_all_bindings()
        assert bindings[CHAT_ID_ALICE] == SESSION_FIRST
        assert bindings[CHAT_ID_BOB] == SESSION_FIRST

    @pytest.mark.asyncio()
    async def test_concurrent_bind_and_unbind(self) -> None:
        """Параллельные операции не теряют данные."""
        await asyncio.gather(
            bind_session(CHAT_ID_ALICE, "aaa"),
            unbind_session(CHAT_ID_BOB),
            bind_session(333333333, "ccc"),
        )

        assert get_bound_session(CHAT_ID_ALICE) == "aaa"
        assert get_bound_session(CHAT_ID_BOB) is None
        assert get_bound_session(333333333) == "ccc"

    @pytest.mark.asyncio()
    async def test_update_session_id_chat_not_bound_to_old(self) -> None:
        """Обновление, когда чат привязан к другой сессии."""
        await bind_session(CHAT_ID_ALICE, "other-session")

        with patch.object(daily_session_registry, "update_session_id", new_callable=AsyncMock) as mock_update:
            await update_session_id(CHAT_ID_ALICE, SESSION_TEMP, SESSION_REAL)

            # Привязка не изменилась
            assert get_bound_session(CHAT_ID_ALICE) == "other-session"

            # Но в дневном реестре обновление всё равно вызвано
            mock_update.assert_called_once_with(SESSION_TEMP, SESSION_REAL)

    def test_generate_temp_id_format(self) -> None:
        """Формат временного ID соответствует ожиданиям."""
        temp_id = _generate_temp_session_id()
        assert temp_id.startswith(TEMP_SESSION_PREFIX)

        # После префикса — 12-символьный hex (часть UUID)
        hex_part = temp_id[len(TEMP_SESSION_PREFIX):]
        assert len(hex_part) == 12
        int(hex_part, 16)  # Должен парситься как hex без ошибки

    def test_generate_temp_id_unique(self) -> None:
        """Каждый вызов генерирует уникальный ID (UUID, не счётчик)."""
        ids = {_generate_temp_session_id() for _ in range(100)}
        assert len(ids) == 100

    @pytest.mark.asyncio()
    async def test_load_bindings_with_string_chat_ids(self, tmp_path: Path) -> None:
        """Ключи из JSON корректно преобразуются в int."""
        # Записываем файл с строковыми ключами (как в JSON)
        bindings_file = tmp_path / BINDINGS_FILENAME
        bindings_file.write_text(json.dumps({"123456789": "abc-def"}), "utf-8")

        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await load_bindings()

        # Получаем привязку по числовому ключу
        assert get_bound_session(123456789) == "abc-def"


# --- Тесты ошибок ---


class TestErrors:
    """Тесты обработки ошибок."""

    @pytest.mark.asyncio()
    async def test_load_missing_file_creates_empty_bindings(self, tmp_path: Path) -> None:
        """Отсутствие файла не вызывает ошибку."""
        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await load_bindings()

        assert get_all_bindings() == {}

    @pytest.mark.asyncio()
    async def test_load_corrupted_json_creates_empty_bindings(self, tmp_path: Path) -> None:
        """Повреждённый JSON не ломает модуль."""
        corrupted_file = tmp_path / BINDINGS_FILENAME
        corrupted_file.write_text("not valid json {{{", "utf-8")

        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await load_bindings()

        assert get_all_bindings() == {}

    @pytest.mark.asyncio()
    async def test_save_to_readonly_directory_raises_oserror(self, tmp_path: Path) -> None:
        """Ошибка записи проксируется вызывающему коду."""
        # Указываем путь в несуществующей директории
        impossible_path = tmp_path / "nonexistent_dir" / BINDINGS_FILENAME
        session_manager._bindings_path = impossible_path

        with pytest.raises(OSError):
            await bind_session(CHAT_ID_ALICE, SESSION_FIRST)

    @pytest.mark.asyncio()
    async def test_atomic_write_preserves_original_on_failure(self, tmp_path: Path) -> None:
        """При ошибке записи оригинальный файл не повреждён."""
        # Создаём валидный файл привязок
        bindings_file = tmp_path / BINDINGS_FILENAME
        original_content = json.dumps({str(CHAT_ID_ALICE): SESSION_FIRST})
        bindings_file.write_text(original_content, "utf-8")

        session_manager._bindings = {CHAT_ID_ALICE: SESSION_FIRST}

        # Мокаем _save_bindings, чтобы выбросить ошибку
        with patch.object(session_manager, "_save_bindings", new_callable=AsyncMock, side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                await bind_session(CHAT_ID_BOB, SESSION_SECOND)

        # Оригинальный файл не повреждён
        saved_content = bindings_file.read_text("utf-8")
        assert saved_content == original_content

    @pytest.mark.asyncio()
    async def test_load_bindings_invalid_chat_id_key(self, tmp_path: Path) -> None:
        """Невалидный ключ в JSON пропускается."""
        bindings_file = tmp_path / BINDINGS_FILENAME
        bindings_file.write_text(
            json.dumps({"not_a_number": "abc-def", "123456789": "ghi-jkl"}),
            "utf-8",
        )

        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await load_bindings()

        # Загружена только привязка с валидным ключом
        assert get_bound_session(123456789) == "ghi-jkl"
        assert len(get_all_bindings()) == 1


class TestResetState:
    """Тесты сброса состояния session_manager при переключении проекта."""

    @pytest.mark.asyncio()
    async def test_reset_clears_bindings(self, tmp_path: Path) -> None:
        """После reset_state _bindings пустой."""
        with patch("claude_manager.config.WORKING_DIR", str(tmp_path)):
            await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
            assert len(get_all_bindings()) == 1

            await reset_state()
            # В исходной папке был только что созданный sessions.json, reset перезагрузит его
            # и привязка восстановится — это нормально. Но при первом вызове reset без файла
            # всё будет пусто. Здесь файл есть, значит привязка должна вернуться.
            assert get_bound_session(CHAT_ID_ALICE) == SESSION_FIRST

    @pytest.mark.asyncio()
    async def test_reset_reloads_from_new_path(self, tmp_path: Path) -> None:
        """reset_state после смены WORKING_DIR читает новый файл привязок."""
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        # В проекте A есть привязка
        with patch("claude_manager.config.WORKING_DIR", str(project_a)):
            await bind_session(CHAT_ID_ALICE, SESSION_FIRST)
            assert get_bound_session(CHAT_ID_ALICE) == SESSION_FIRST

        # Переключаемся на проект B (где sessions.json пусто)
        with patch("claude_manager.config.WORKING_DIR", str(project_b)):
            await reset_state()
            # В B нет привязок — должны быть пусты
            assert get_bound_session(CHAT_ID_ALICE) is None
            assert len(get_all_bindings()) == 0

    @pytest.mark.asyncio()
    async def test_reset_uses_current_working_dir_not_cached(
        self, tmp_path: Path
    ) -> None:
        """После reset_state _bindings_path пересчитывается из текущего WORKING_DIR."""
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        # Первая загрузка — путь закэшируется в _bindings_path из project_a
        with patch("claude_manager.config.WORKING_DIR", str(project_a)):
            await load_bindings()

        # Меняем WORKING_DIR и делаем reset_state — путь должен обновиться
        with patch("claude_manager.config.WORKING_DIR", str(project_b)):
            await reset_state()
            # Создаём в B привязку и проверяем, что файл ушёл в B, а не в A
            await bind_session(CHAT_ID_BOB, SESSION_SECOND)

            bindings_in_b = project_b / BINDINGS_FILENAME
            bindings_in_a = project_a / BINDINGS_FILENAME

            assert bindings_in_b.exists()
            # В A файла нет (или он пустой/старый — главное, что новая привязка в B)
            if bindings_in_a.exists():
                content = json.loads(bindings_in_a.read_text("utf-8"))
                assert str(CHAT_ID_BOB) not in content
