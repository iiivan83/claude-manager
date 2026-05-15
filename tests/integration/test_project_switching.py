"""Интеграционные тесты переключения между проектами.

Проверяет координацию модулей project_manager, config, session_manager,
daily_session_registry, session_watcher и unread_buffer при переключении
между двумя реальными директориями.
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
    unread_buffer,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionUnreadState,
)
from claude_manager.daily_session_registry import REGISTRY_FILENAME
from claude_manager.session_manager import BINDINGS_FILENAME
from claude_manager.session_watcher import SessionWatcherState


# --- Фикстуры ---


TEST_CHAT_ID = 123456789
TEST_SESSION_A = "session-in-project-a"
TEST_SESSION_B = "session-in-project-b"


class FakeBackend:
    """Backend with no visible session files for project switching tests."""

    def __init__(self, name: BackendName) -> None:
        self.name = name

    async def list_all_session_files_for_project(
        self,
        _project_dir: str,
    ) -> list[SessionFileInfo]:
        return []

    async def read_session_file_snapshot(
        self,
        _file_path: str,
    ) -> SessionFileSnapshot:
        return SessionFileSnapshot(
            messages=[],
            raw_record_count=0,
            last_record=None,
            is_turn_active=False,
        )


def _install_fake_watchers() -> None:
    session_watcher._watchers = {
        BackendName.CLAUDE: session_watcher.SessionWatcher(
            FakeBackend(BackendName.CLAUDE)
        ),
        BackendName.CODEX: session_watcher.SessionWatcher(
            FakeBackend(BackendName.CODEX)
        ),
    }


def _watchers_globally_paused() -> bool:
    return all(watcher._global_paused for watcher in session_watcher._watchers.values())


def _watchers_globally_resumed() -> bool:
    return all(
        not watcher._global_paused
        for watcher in session_watcher._watchers.values()
    )


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

    _install_fake_watchers()

    unread_buffer._snapshots.clear()

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
             patch.object(config, "LAST_PROJECT_FILE", last_file), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):

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
             patch.object(config, "LAST_PROJECT_FILE", last_file), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()
            await session_manager.bind_session(TEST_CHAT_ID, TEST_SESSION_A)

            result = await project_manager.switch_project(str(project_a))

            assert result.already_active is True
            assert result.success is True
            # Привязка сохранилась — reset_state не вызывался
            assert session_manager.get_bound_session(TEST_CHAT_ID) == TEST_SESSION_A

    @pytest.mark.asyncio()
    async def test_switch_saves_backend_watcher_snapshots(
        self, project_layout: dict[str, Path]
    ) -> None:
        """При переключении сохраняются snapshot-ы обоих backend watcher-ов."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        project_b = project_layout["project_b"]
        last_file = project_layout["last_file"]

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):

            # Подготавливаем watcher: две сессии с обработанными сообщениями
            claude_watcher = session_watcher._get_watcher(BackendName.CLAUDE)
            claude_watcher._states["sess-1"] = SessionWatcherState(
                raw_count=5,
                last_delivered_idx=4,
            )
            claude_watcher._states["sess-2"] = SessionWatcherState(
                raw_count=3,
                last_delivered_idx=2,
            )
            codex_watcher = session_watcher._get_watcher(BackendName.CODEX)
            codex_watcher._states["codex-sess-1"] = SessionWatcherState(
                raw_count=7,
                last_delivered_idx=6,
            )

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()

            result = await project_manager.switch_project(str(project_b))

            assert result.success is True
            assert result.pending_messages_count == 0
            assert unread_buffer.restore_snapshot(
                "sess-1",
                BackendName.CLAUDE,
            ) == SessionUnreadState(raw_record_count=5, last_delivered_idx=4)
            assert unread_buffer.restore_snapshot(
                "codex-sess-1",
                BackendName.CODEX,
            ) == SessionUnreadState(raw_record_count=7, last_delivered_idx=6)

    @pytest.mark.asyncio()
    async def test_processes_continue_running_during_switch(
        self,
        project_layout: dict[str, Path],
    ) -> None:
        """Переключение проекта не останавливает запущенные CLI-процессы."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        project_b = project_layout["project_b"]
        last_file = project_layout["last_file"]
        process_key = ("running-session", BackendName.CLAUDE)
        process_object = object()
        process_manager._processes[process_key] = process_object

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()

            result = await project_manager.switch_project(str(project_b))

            assert result.success is True
            assert process_manager._processes[process_key] is process_object

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

    @pytest.mark.asyncio()
    async def test_watcher_paused_during_full_switch_cycle(
        self, project_layout: dict[str, Path]
    ) -> None:
        """Watcher приостановлен (global_paused=True) внутри _reset_all_state_modules."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        project_b = project_layout["project_b"]
        last_file = project_layout["last_file"]

        # Запоминаем значение _global_paused внутри reset_state watcher
        global_paused_during_reset: list[bool] = []
        original_watcher_reset = session_watcher.reset_state

        async def tracking_watcher_reset() -> None:
            global_paused_during_reset.append(_watchers_globally_paused())
            await original_watcher_reset()

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()

            with patch.object(
                session_watcher, "reset_state", tracking_watcher_reset
            ):
                result = await project_manager.switch_project(str(project_b))

            assert result.success is True
            # Во время reset_state watcher был на глобальной паузе
            assert global_paused_during_reset == [True]
            # После завершения переключения пауза снята
            assert _watchers_globally_resumed() is True

    @pytest.mark.asyncio()
    async def test_watcher_unpaused_after_rollback(
        self, project_layout: dict[str, Path]
    ) -> None:
        """При ошибке и откате watcher всё равно разблокирован (try/finally)."""
        root = project_layout["root"]
        project_a = project_layout["project_a"]
        project_b = project_layout["project_b"]
        last_file = project_layout["last_file"]

        with patch.object(config, "PROJECTS_ROOT_DIR", str(root)), \
             patch.object(config, "WORKING_DIR", str(project_a)), \
             patch.object(config, "LAST_PROJECT_FILE", last_file), \
             patch.object(daily_session_registry, "_remove_orphan_entries", return_value=0):

            await session_manager.load_bindings()
            await daily_session_registry.load_registry()

            # session_manager.reset_state бросает ошибку — переключение не удаётся
            with patch.object(
                session_manager, "reset_state",
                AsyncMock(side_effect=RuntimeError("simulated")),
            ):
                result = await project_manager.switch_project(str(project_b))

            assert result.success is False
            # Watcher разблокирован — _global_paused снят благодаря finally
            assert _watchers_globally_resumed() is True
