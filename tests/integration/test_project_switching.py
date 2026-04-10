"""Интеграционные тесты переключения между проектами.

Проверяет координацию модулей project_manager, config, session_manager,
daily_session_registry, session_watcher и process_manager при переключении
между двумя реальными директориями (не мокаются файлы, только процессы Claude).
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_manager import (
    config,
    daily_session_registry,
    process_manager,
    project_manager,
    session_manager,
    session_watcher,
)
from claude_manager.daily_session_registry import REGISTRY_FILENAME
from claude_manager.session_manager import BINDINGS_FILENAME


# --- Фикстуры ---


TEST_CHAT_ID = 123456789
TEST_SESSION_A = "session-in-project-a"
TEST_SESSION_B = "session-in-project-b"


@pytest.fixture()
def project_layout(tmp_path: Path) -> dict[str, Path]:
    """Создаёт структуру с PROJECTS_ROOT и двумя пустыми проектами."""
    projects_root = tmp_path / "projects_root"
    projects_root.mkdir()
    project_a = projects_root / "project_a"
    project_b = projects_root / "project_b"
    project_a.mkdir()
    project_b.mkdir()
    last_project_file = tmp_path / ".claude-manager-current-project"
    return {
        "root": projects_root,
        "project_a": project_a,
        "project_b": project_b,
        "last_file": last_project_file,
    }


@pytest.fixture(autouse=True)
def _reset_all_module_state() -> None:
    """Полный сброс состояния всех модулей перед каждым тестом."""
    session_manager._bindings = {}
    session_manager._bindings_path = None
    session_manager._lock = asyncio.Lock()

    daily_session_registry._registry = {}
    daily_session_registry._registry_path = None
    daily_session_registry._loaded_from_disk = False
    daily_session_registry._lock = asyncio.Lock()

    session_watcher._seen_message_counts.clear()
    session_watcher._paused_sessions.clear()

    process_manager._processes.clear()
    process_manager._busy_flags.clear()
    process_manager._stop_events.clear()

    project_manager._switch_lock = asyncio.Lock()


# --- Тесты ---


class TestFullSwitchCycle:
    """Полный цикл переключения проектов с проверкой координации модулей."""

    @pytest.mark.asyncio()
    async def test_switch_between_two_projects(
        self, project_layout: dict[str, Path]
    ) -> None:
        """Полный цикл: A → привязка и сессия → B (пусто) → A (всё вернулось)."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        project_b = project_layout["project_b"]
        last_file = project_layout["last_file"]

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file):

            # В проекте A создаём привязку и регистрируем сессию
            await session_manager.load_bindings()
            await daily_session_registry.load_registry()
            await session_manager.bind_session(TEST_CHAT_ID, TEST_SESSION_A)

            assert (project_a / BINDINGS_FILENAME).exists()
            assert (project_a / REGISTRY_FILENAME).exists()

            # Переключаемся на project_b
            result = await project_manager.switch_project(str(project_b))

            assert result.success is True
            assert result.already_active is False
            assert result.stopped_processes_count == 0
            assert config.WORKING_DIR == str(project_b)

            # Состояние сброшено: в B нет привязок и сессий
            assert session_manager.get_bound_session(TEST_CHAT_ID) is None
            sessions_in_b = await daily_session_registry.get_all_today_sessions()
            assert TEST_SESSION_A not in sessions_in_b.values()

            # Файл последнего проекта создан и содержит путь к B
            assert last_file.exists()
            assert last_file.read_text("utf-8") == str(project_b)

            # Возвращаемся в project_a — привязка и сессия должны восстановиться
            result_back = await project_manager.switch_project(str(project_a))

            assert result_back.success is True
            assert config.WORKING_DIR == str(project_a)

            # Привязка и сессия восстановились из файлов проекта A
            assert session_manager.get_bound_session(TEST_CHAT_ID) == TEST_SESSION_A
            sessions_in_a = await daily_session_registry.get_all_today_sessions()
            assert TEST_SESSION_A in sessions_in_a.values()

    @pytest.mark.asyncio()
    async def test_switch_to_same_project_is_noop(
        self, project_layout: dict[str, Path]
    ) -> None:
        """Переключение на текущий проект возвращает already_active и не трогает состояние."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        last_file = project_layout["last_file"]

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file):

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()
            await session_manager.bind_session(TEST_CHAT_ID, TEST_SESSION_A)

            result = await project_manager.switch_project(str(project_a))

            assert result.already_active is True
            assert result.success is True
            # Привязка сохранилась — reset_state не вызывался
            assert session_manager.get_bound_session(TEST_CHAT_ID) == TEST_SESSION_A

    @pytest.mark.asyncio()
    async def test_switch_stops_running_processes(
        self, project_layout: dict[str, Path]
    ) -> None:
        """При переключении все процессы Claude останавливаются."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        project_b = project_layout["project_b"]
        last_file = project_layout["last_file"]

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file):

            # Заполняем _processes фейковыми процессами
            fake_process_1 = _make_fake_running_process()
            fake_process_2 = _make_fake_running_process()
            process_manager._processes["sess-1"] = fake_process_1
            process_manager._processes["sess-2"] = fake_process_2
            process_manager._busy_flags["sess-1"] = False
            process_manager._busy_flags["sess-2"] = False
            process_manager._stop_events["sess-1"] = asyncio.Event()
            process_manager._stop_events["sess-2"] = asyncio.Event()

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()

            result = await project_manager.switch_project(str(project_b))

            assert result.success is True
            assert result.stopped_processes_count == 2
            assert len(process_manager._processes) == 0

    @pytest.mark.asyncio()
    async def test_switch_to_nonexistent_fails_gracefully(
        self, project_layout: dict[str, Path]
    ) -> None:
        """Несуществующий путь — success=False, WORKING_DIR и state не меняются."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        last_file = project_layout["last_file"]

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file):

            await session_manager.load_bindings()
            await session_manager.bind_session(TEST_CHAT_ID, TEST_SESSION_A)

            nonexistent = root / "does_not_exist"
            result = await project_manager.switch_project(str(nonexistent))

            assert result.success is False
            assert "не существует" in result.error_message
            assert config.WORKING_DIR == str(project_a)
            # Привязка сохранилась
            assert session_manager.get_bound_session(TEST_CHAT_ID) == TEST_SESSION_A
            # Файл последнего проекта не создан
            assert not last_file.exists()

    @pytest.mark.asyncio()
    async def test_path_traversal_attack_blocked(
        self, project_layout: dict[str, Path], tmp_path: Path
    ) -> None:
        """Попытка переключиться на папку вне PROJECTS_ROOT_DIR блокируется."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        last_file = project_layout["last_file"]

        outside_dir = tmp_path / "outside_root"
        outside_dir.mkdir()

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file):

            result = await project_manager.switch_project(str(outside_dir))

            assert result.success is False
            assert "вне корневой папки" in result.error_message
            assert config.WORKING_DIR == str(project_a)


# --- Вспомогательные функции ---


def _make_fake_running_process():
    """Создаёт фейковый ClaudeProcess для тестов интеграции с process_manager."""
    from unittest.mock import AsyncMock, MagicMock

    subprocess_mock = MagicMock()
    subprocess_mock.pid = 99999
    subprocess_mock.returncode = None
    subprocess_mock.terminate = MagicMock()
    subprocess_mock.wait = AsyncMock(return_value=0)
    subprocess_mock.kill = MagicMock()
    subprocess_mock.stdin = MagicMock()
    subprocess_mock.stdout = MagicMock()
    subprocess_mock.stderr = MagicMock()

    from claude_manager.claude_runner import ClaudeProcess
    return ClaudeProcess(subprocess_mock)
