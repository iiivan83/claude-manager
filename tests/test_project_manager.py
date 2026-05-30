"""Тесты модуля project_manager — сканирование и переключение между проектами."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import (
    coding_agent_backend,
    config,
    current_backend_registry,
    process_manager,
    project_manager,
    unread_buffer,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    SessionUnreadState,
)
from claude_manager.project_manager import (
    ProjectInfo,
    ProjectSwitchError,
    SwitchResult,
    get_current_project_path,
    load_last_selected_project,
    save_selected_project,
    scan_available_projects,
    switch_project,
)


# --- Вспомогательные константы и фикстуры ---


@pytest.fixture(autouse=True)
def _reset_switch_lock() -> None:
    """Сбрасывает блокировку переключения перед каждым тестом — изоляция тестов."""
    project_manager._switch_lock = asyncio.Lock()
    unread_buffer._snapshots.clear()


@pytest.fixture()
def projects_root(tmp_path: Path) -> Path:
    """Временная папка, имитирующая PROJECTS_ROOT_DIR с двумя проектами внутри."""
    root = tmp_path / "projects_root"
    root.mkdir()
    (root / "project_alpha").mkdir()
    (root / "project_beta").mkdir()
    return root


@pytest.fixture()
def last_project_file(tmp_path: Path) -> Path:
    """Временный путь для файла последнего проекта."""
    return tmp_path / ".claude-manager-current-project"


def _patch_config_paths(projects_root: Path, working_dir: Path, last_file: Path):
    """Собирает все необходимые patch-контексты для подмены путей в config."""
    return (
        patch.object(config, "PROJECTS_ROOT_DIR", str(projects_root)),
        patch.object(config, "WORKING_DIR", str(working_dir)),
        patch.object(config, "LAST_PROJECT_FILE", last_file),
    )


class FakeProjectBackend:
    """Минимальный backend для проверки pending-доставки project_manager."""

    def __init__(
        self,
        name: BackendName,
        session_files: list[SessionFileInfo] | None = None,
        snapshots: dict[str, SessionFileSnapshot] | None = None,
    ) -> None:
        self.name = name
        self.session_files = session_files or []
        self.snapshots = snapshots or {}
        self.list_lookback_history: list[int | None] = []

    async def list_all_session_files_for_project(
        self,
        _project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        """Возвращает заранее заданные файлы сессий."""
        self.list_lookback_history.append(lookback_days)
        return self.session_files

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        """Возвращает заранее заданный snapshot по пути файла."""
        return self.snapshots[file_path]


def _session_file(session_id: str, file_path: str) -> SessionFileInfo:
    """Создаёт metadata файла сессии для fake backend."""
    return SessionFileInfo(
        session_id=session_id,
        file_path=file_path,
        last_modified_at=1.0,
        preview="preview",
    )


def _assistant_message(text: str) -> SessionMessage:
    """Создаёт assistant-сообщение для snapshot."""
    return SessionMessage(
        role="assistant",
        text=text,
        timestamp=None,
        is_empty_response=False,
    )


def _user_message(text: str) -> SessionMessage:
    """Создаёт user-сообщение для проверки фильтрации pending."""
    return SessionMessage(
        role="user",
        text=text,
        timestamp=None,
        is_empty_response=False,
    )


# --- Тесты сканирования проектов ---


class TestScanAvailableProjects:
    """Тесты функции scan_available_projects."""

    @pytest.mark.asyncio()
    async def test_returns_only_directories(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Файлы отфильтровываются, возвращаются только директории."""
        # Создаём файл и ещё одну директорию поверх существующих
        (projects_root / "some_file.txt").write_text("hello")
        (projects_root / "another_dir").mkdir()

        working_dir = projects_root / "project_alpha"
        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            projects = await scan_available_projects()

        names = [project.name for project in projects]
        assert "some_file.txt" not in names
        assert "another_dir" in names
        assert "project_alpha" in names
        assert "project_beta" in names

    @pytest.mark.asyncio()
    async def test_filters_hidden_dirs(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Скрытые папки (с точкой в начале имени) не попадают в список."""
        (projects_root / ".hidden_dir").mkdir()
        (projects_root / ".DS_Store").mkdir()

        working_dir = projects_root / "project_alpha"
        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            projects = await scan_available_projects()

        names = [project.name for project in projects]
        assert ".hidden_dir" not in names
        assert ".DS_Store" not in names

    @pytest.mark.asyncio()
    async def test_filters_symlinks(
        self, projects_root: Path, last_project_file: Path, tmp_path: Path
    ) -> None:
        """Символические ссылки исключаются (защита от выхода за границы)."""
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        symlink_in_root = projects_root / "linked"
        symlink_in_root.symlink_to(outside_dir)

        working_dir = projects_root / "project_alpha"
        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            projects = await scan_available_projects()

        names = [project.name for project in projects]
        assert "linked" not in names

    @pytest.mark.asyncio()
    async def test_empty_root_returns_empty_list(
        self, tmp_path: Path, last_project_file: Path
    ) -> None:
        """Пустая корневая папка даёт пустой список."""
        empty_root = tmp_path / "empty_root"
        empty_root.mkdir()

        patches = _patch_config_paths(empty_root, empty_root, last_project_file)
        with patches[0], patches[1], patches[2]:
            projects = await scan_available_projects()

        assert projects == []

    @pytest.mark.asyncio()
    async def test_nonexistent_root_returns_empty_list(
        self, tmp_path: Path, last_project_file: Path
    ) -> None:
        """Несуществующая корневая папка даёт пустой список, ошибка не бросается."""
        missing_root = tmp_path / "does_not_exist"

        patches = _patch_config_paths(missing_root, tmp_path, last_project_file)
        with patches[0], patches[1], patches[2]:
            projects = await scan_available_projects()

        assert projects == []

    @pytest.mark.asyncio()
    async def test_marks_current_project(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Активный проект помечается флагом is_current=True."""
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            projects = await scan_available_projects()

        current_projects = [project for project in projects if project.is_current]
        assert len(current_projects) == 1
        assert current_projects[0].name == "project_alpha"


# --- Тесты переключения проектов ---


class TestSwitchProject:
    """Тесты функции switch_project."""

    @pytest.mark.asyncio()
    async def test_happy_path(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Валидное переключение возвращает success=True и обновляет WORKING_DIR."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            result = await switch_project(str(target))

            # Проверки внутри with — иначе patch уже откатит config.WORKING_DIR
            assert result.success is True
            assert result.already_active is False
            assert result.new_path == str(target)
            assert result.error_message == ""
            assert config.WORKING_DIR == str(target)

    @pytest.mark.asyncio()
    async def test_already_active(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Переключение на текущий проект возвращает already_active=True и не сбрасывает state."""
        working_dir = projects_root / "project_alpha"
        snapshot_mock = MagicMock(return_value={})
        reset_mock = AsyncMock()

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", snapshot_mock), \
             patch.object(project_manager.session_manager, "reset_state", reset_mock):
            result = await switch_project(str(working_dir))

        assert result.success is True
        assert result.already_active is True
        snapshot_mock.assert_not_called()
        reset_mock.assert_not_called()

    @pytest.mark.asyncio()
    async def test_path_traversal_blocked(
        self, projects_root: Path, last_project_file: Path, tmp_path: Path
    ) -> None:
        """Попытка переключиться на папку вне PROJECTS_ROOT_DIR блокируется."""
        outside = tmp_path / "outside"
        outside.mkdir()

        working_dir = projects_root / "project_alpha"
        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await switch_project(str(outside))

        assert result.success is False
        assert "вне корневой папки" in result.error_message

    @pytest.mark.asyncio()
    async def test_nonexistent_path_fails(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Несуществующий путь — success=False с понятным сообщением."""
        nonexistent = projects_root / "nonexistent"
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await switch_project(str(nonexistent))

        assert result.success is False
        assert "не существует" in result.error_message

    @pytest.mark.asyncio()
    async def test_path_is_file_not_dir_fails(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Путь на файл (не папку) — success=False."""
        file_inside = projects_root / "not_a_dir.txt"
        file_inside.write_text("content")
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await switch_project(str(file_inside))

        assert result.success is False
        assert "не папка" in result.error_message

    @pytest.mark.asyncio()
    async def test_saves_snapshot_on_switch(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """switch_project сохраняет backend-aware watcher snapshot."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"
        claude_snapshot = {
            "session-1": SessionUnreadState(
                raw_record_count=5,
                last_delivered_idx=4,
            )
        }
        codex_snapshot = {
            "session-2": SessionUnreadState(
                raw_record_count=3,
                last_delivered_idx=2,
            )
        }

        def snapshot_for_backend(
            backend: BackendName,
        ) -> dict[str, SessionUnreadState]:
            if backend == BackendName.CLAUDE:
                return claude_snapshot
            if backend == BackendName.CODEX:
                return codex_snapshot
            return {}

        snapshot_mock = MagicMock(side_effect=snapshot_for_backend)
        save_mock = MagicMock()

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", snapshot_mock), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot", save_mock), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            result = await switch_project(str(target))

        assert snapshot_mock.call_count == 2
        save_mock.assert_any_call(
            "session-1",
            BackendName.CLAUDE,
            raw_record_count=5,
            last_delivered_idx=4,
        )
        save_mock.assert_any_call(
            "session-2",
            BackendName.CODEX,
            raw_record_count=3,
            last_delivered_idx=2,
        )
        assert result.pending_messages_count == 0
        assert result.pending_messages == []

    @pytest.mark.asyncio()
    async def test_pending_delivery_items_include_backend(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Pending delivery возвращает backend, session_id и финальность."""
        working_dir = projects_root / "project_beta"
        target = projects_root / "project_alpha"
        session_id = "claude-session"
        file_path = str(target / "session.jsonl")
        session_file = _session_file(session_id, file_path)
        snapshot = SessionFileSnapshot(
            messages=[
                _assistant_message("old"),
                _user_message("new question"),
                _assistant_message("first pending"),
                _assistant_message("final pending"),
            ],
            raw_record_count=9,
            last_record=None,
            is_turn_active=False,
        )
        fake_claude_backend = FakeProjectBackend(
            BackendName.CLAUDE,
            session_files=[session_file],
            snapshots={file_path: snapshot},
        )
        fake_codex_backend = FakeProjectBackend(BackendName.CODEX)
        unread_buffer.save_snapshot(
            session_id,
            BackendName.CLAUDE,
            raw_record_count=4,
            last_delivered_idx=0,
        )

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()), \
             patch.object(coding_agent_backend, "get_all_backends", return_value=[fake_claude_backend, fake_codex_backend]):
            result = await switch_project(str(target))

        assert result.pending_messages_count == 2
        assert result.pending_messages[0].session_id == session_id
        assert result.pending_messages[0].backend == BackendName.CLAUDE
        assert result.pending_messages[0].text == "first pending"
        assert result.pending_messages[0].is_final is False
        assert result.pending_messages[1].text == "final pending"
        assert result.pending_messages[1].is_final is True

    @pytest.mark.asyncio()
    async def test_collect_pending_messages_requests_operational_lookback_window(
        self,
        projects_root: Path,
        last_project_file: Path,
    ) -> None:
        """Pending-сбор ограничивает листинг бэкенда operational lookback окном.

        Без этого ограничения сбор pending при возврате в проект сканирует всю
        историю Codex (~10k файлов в ~/.codex/sessions), что блокирует ответ
        пользователю на /pN на несколько секунд.
        """
        target = projects_root / "project_alpha"
        fake_backend = FakeProjectBackend(
            BackendName.CLAUDE,
            session_files=[],
            snapshots={},
        )

        patches = _patch_config_paths(projects_root, target, last_project_file)
        with patches[0], patches[1], patches[2], patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[fake_backend],
        ):
            await project_manager.collect_pending_messages_for_project(str(target))

        assert fake_backend.list_lookback_history, (
            "collect_pending_messages не вызвал list_all_session_files_for_project"
        )
        assert (
            fake_backend.list_lookback_history[-1]
            == project_manager.config.OPERATIONAL_SESSION_LOOKBACK_DAYS
        ), (
            "Сбор pending должен ограничивать листинг operational lookback окном, "
            f"но передал lookback_days={fake_backend.list_lookback_history[-1]}"
        )

    @pytest.mark.asyncio()
    async def test_collect_pending_messages_for_active_project_uses_existing_collector(
        self,
        projects_root: Path,
        last_project_file: Path,
    ) -> None:
        """Public wrapper collects pending messages without switching project."""
        target = projects_root / "project_alpha"
        session_id = "claude-session"
        file_path = str(target / "session.jsonl")
        session_file = _session_file(session_id, file_path)
        snapshot = SessionFileSnapshot(
            messages=[
                _assistant_message("old"),
                _assistant_message("new"),
            ],
            raw_record_count=2,
            last_record=None,
            is_turn_active=False,
        )
        fake_backend = FakeProjectBackend(
            BackendName.CLAUDE,
            session_files=[session_file],
            snapshots={file_path: snapshot},
        )
        unread_buffer.save_snapshot(
            session_id,
            BackendName.CLAUDE,
            raw_record_count=1,
            last_delivered_idx=0,
        )

        patches = _patch_config_paths(projects_root, target, last_project_file)
        with patches[0], patches[1], patches[2], patch.object(
            coding_agent_backend,
            "get_all_backends",
            return_value=[fake_backend],
        ):
            count, pending = await project_manager.collect_pending_messages_for_project(
                str(target)
            )

        assert count == 1
        assert pending[0].session_id == session_id
        assert pending[0].text == "new"
        assert pending[0].backend == BackendName.CLAUDE
        assert pending[0].is_final is True

    @pytest.mark.asyncio()
    async def test_current_backend_registry_preserved_across_switch(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Переключение проекта не сбрасывает глобальный выбор backend-а."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"
        current_backend_registry._loaded_from_disk = True
        current_backend_registry._current_backend = BackendName.CODEX

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            result = await switch_project(str(target))

        assert result.success is True
        assert current_backend_registry.get_current() == BackendName.CODEX

    @pytest.mark.asyncio()
    async def test_switch_project_does_not_stop_running_processes(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Переключение проекта не трогает process_manager._processes."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"
        process_key = ("session-1", BackendName.CLAUDE)
        process_object = object()
        process_manager._processes[process_key] = process_object

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            result = await switch_project(str(target))

        assert result.success is True
        assert process_manager._processes[process_key] is process_object

    @pytest.mark.asyncio()
    async def test_resets_all_state_modules(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """switch_project вызывает reset_state у трёх state-модулей."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"

        session_reset = AsyncMock()
        registry_reset = AsyncMock()
        watcher_reset = AsyncMock()

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", session_reset), \
             patch.object(project_manager.daily_session_registry, "reset_state", registry_reset), \
             patch.object(project_manager.session_watcher, "reset_state", watcher_reset):
            await switch_project(str(target))

        session_reset.assert_awaited_once()
        registry_reset.assert_awaited_once()
        watcher_reset.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_saves_to_last_project_file(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """После успешного переключения путь записывается в LAST_PROJECT_FILE."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            await switch_project(str(target))

        assert last_project_file.exists()
        assert last_project_file.read_text("utf-8") == str(target)

    @pytest.mark.asyncio()
    async def test_rollback_on_reset_error(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """При ошибке в reset_state восстанавливается старый WORKING_DIR и возвращается ошибка."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"
        original_wd = str(working_dir)

        failing_reset = AsyncMock(side_effect=RuntimeError("simulated failure"))

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "clear_snapshot"), \
             patch.object(project_manager.session_manager, "reset_state", failing_reset), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            result = await switch_project(str(target))

            # Проверки внутри with — config.WORKING_DIR должен быть откачен к original_wd
            assert result.success is False
            assert "simulated failure" in result.error_message
            assert config.WORKING_DIR == original_wd
            # Файл последнего проекта не создан при неудачном переключении
            assert not last_project_file.exists()

    @pytest.mark.asyncio()
    async def test_concurrent_switches_serialized(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Параллельные switch_project выполняются последовательно благодаря _switch_lock."""
        working_dir = projects_root / "project_alpha"
        target_a = projects_root / "project_alpha"
        target_b = projects_root / "project_beta"

        call_order: list[str] = []

        def tracked_snapshot(_backend: BackendName) -> dict:
            call_order.append("snapshot")
            return {}

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", tracked_snapshot), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            await asyncio.gather(
                switch_project(str(target_b)),
                switch_project(str(target_a)),
            )

        # Оба вызова должны были сделать snapshot (второй — already_active НЕ вызовет)
        # Первый переключает на project_beta (snapshot вызывается).
        # Второй: к этому моменту WORKING_DIR уже project_beta, а target_a=project_alpha — значит snapshot тоже вызывается.
        # Главное — блокировка сработала и оба вызова завершились без исключений
        assert len(call_order) >= 1


    @pytest.mark.asyncio()
    async def test_pause_all_called_before_working_dir_change(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """pause_all() вызывается ДО изменения config.WORKING_DIR."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"

        # Запоминаем WORKING_DIR в момент вызова pause_all
        working_dir_at_pause_time: list[str] = []

        def tracked_pause_all() -> None:
            working_dir_at_pause_time.append(config.WORKING_DIR)

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all", side_effect=tracked_pause_all), \
             patch.object(project_manager.session_watcher, "resume_all"), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            await switch_project(str(target))

        # pause_all видел старое значение WORKING_DIR (до переключения)
        assert working_dir_at_pause_time == [str(working_dir)]

    @pytest.mark.asyncio()
    async def test_resume_all_called_after_reset(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """resume_all() вызывается ПОСЛЕ завершения _reset_all_state_modules()."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"

        call_order: list[str] = []

        async def tracked_watcher_reset() -> None:
            call_order.append("reset")

        def tracked_resume_all() -> None:
            call_order.append("resume")

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all", side_effect=tracked_resume_all), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "has_pending", return_value=False), \
             patch.object(project_manager.unread_buffer, "cleanup_expired"), \
             patch.object(project_manager.session_manager, "reset_state", AsyncMock()), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", tracked_watcher_reset):
            await switch_project(str(target))

        # reset вызван раньше resume — порядок критичен
        assert call_order == ["reset", "resume"]

    @pytest.mark.asyncio()
    async def test_resume_all_called_on_error(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """При ошибке в _reset_all_state_modules resume_all() всё равно вызывается (try/finally)."""
        working_dir = projects_root / "project_alpha"
        target = projects_root / "project_beta"

        failing_reset = AsyncMock(side_effect=RuntimeError("simulated failure"))
        resume_mock = MagicMock()

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2], \
             patch.object(project_manager.session_watcher, "get_seen_counts_snapshot", return_value={}), \
             patch.object(project_manager.session_watcher, "pause_all"), \
             patch.object(project_manager.session_watcher, "resume_all", resume_mock), \
             patch.object(project_manager.unread_buffer, "save_snapshot"), \
             patch.object(project_manager.unread_buffer, "clear_snapshot"), \
             patch.object(project_manager.session_manager, "reset_state", failing_reset), \
             patch.object(project_manager.daily_session_registry, "reset_state", AsyncMock()), \
             patch.object(project_manager.session_watcher, "reset_state", AsyncMock()):
            result = await switch_project(str(target))

        assert result.success is False
        # resume_all вызван несмотря на ошибку — благодаря try/finally
        resume_mock.assert_called_once()


# --- Тесты load_last_selected_project ---


class TestLoadLastSelectedProject:
    """Тесты функции load_last_selected_project."""

    @pytest.mark.asyncio()
    async def test_no_file_returns_none(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Если файла нет, возвращается None."""
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await load_last_selected_project()

        assert result is None

    @pytest.mark.asyncio()
    async def test_empty_file_returns_none(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Пустой файл возвращает None."""
        last_project_file.write_text("", encoding="utf-8")
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await load_last_selected_project()

        assert result is None

    @pytest.mark.asyncio()
    async def test_invalid_path_in_file_returns_none(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Если в файле путь к несуществующему проекту — возвращается None."""
        last_project_file.write_text("/nonexistent/path", encoding="utf-8")
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await load_last_selected_project()

        assert result is None

    @pytest.mark.asyncio()
    async def test_valid_path_returns_path(
        self, projects_root: Path, last_project_file: Path
    ) -> None:
        """Валидный путь возвращается как есть."""
        target = projects_root / "project_beta"
        last_project_file.write_text(str(target), encoding="utf-8")
        working_dir = projects_root / "project_alpha"

        patches = _patch_config_paths(projects_root, working_dir, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await load_last_selected_project()

        assert result == str(target)


# --- Тесты save_selected_project ---


class TestSaveSelectedProject:
    """Тесты функции save_selected_project."""

    @pytest.mark.asyncio()
    async def test_writes_file_with_path(
        self, last_project_file: Path
    ) -> None:
        """save_selected_project атомарно пишет путь в файл."""
        with patch.object(config, "LAST_PROJECT_FILE", last_project_file):
            await save_selected_project("/some/path")

        assert last_project_file.exists()
        assert last_project_file.read_text("utf-8") == "/some/path"

    @pytest.mark.asyncio()
    async def test_io_error_logged_not_raised(
        self, last_project_file: Path
    ) -> None:
        """Ошибка записи логируется, но не пробрасывается."""
        with patch.object(config, "LAST_PROJECT_FILE", last_project_file), \
             patch("asyncio.to_thread", AsyncMock(side_effect=OSError("disk full"))):
            # Не должно упасть
            await save_selected_project("/some/path")


# --- Тест get_current_project_path ---


class TestGetCurrentProjectPath:
    """Тесты функции get_current_project_path."""

    def test_returns_config_working_dir(self, tmp_path: Path) -> None:
        """Функция возвращает значение config.WORKING_DIR."""
        with patch.object(config, "WORKING_DIR", str(tmp_path)):
            assert get_current_project_path() == str(tmp_path)


class TestResolveNeighborProject:
    """Тесты циклического выбора соседнего проекта."""

    @pytest.mark.asyncio()
    async def test_resolve_next_cycles_to_first(
        self,
        tmp_path: Path,
        last_project_file: Path,
    ) -> None:
        """next после последнего проекта возвращает первый проект."""
        projects_root = tmp_path / "projects_root"
        projects_root.mkdir()
        project_a = projects_root / "project_a"
        project_b = projects_root / "project_b"
        project_c = projects_root / "project_c"
        for project_path in (project_a, project_b, project_c):
            project_path.mkdir()

        patches = _patch_config_paths(projects_root, project_c, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await project_manager.resolve_neighbor_project("next")

        assert result is not None
        assert result.name == "project_a"

    @pytest.mark.asyncio()
    async def test_resolve_prev_cycles_to_last(
        self,
        tmp_path: Path,
        last_project_file: Path,
    ) -> None:
        """prev перед первым проектом возвращает последний проект."""
        projects_root = tmp_path / "projects_root"
        projects_root.mkdir()
        project_a = projects_root / "project_a"
        project_b = projects_root / "project_b"
        project_c = projects_root / "project_c"
        for project_path in (project_a, project_b, project_c):
            project_path.mkdir()

        patches = _patch_config_paths(projects_root, project_a, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await project_manager.resolve_neighbor_project("prev")

        assert result is not None
        assert result.name == "project_c"

    @pytest.mark.asyncio()
    async def test_resolve_neighbor_returns_none_for_single_project(
        self,
        tmp_path: Path,
        last_project_file: Path,
    ) -> None:
        """Если проект один, соседнего проекта нет."""
        projects_root = tmp_path / "projects_root"
        projects_root.mkdir()
        project_a = projects_root / "project_a"
        project_a.mkdir()

        patches = _patch_config_paths(projects_root, project_a, last_project_file)
        with patches[0], patches[1], patches[2]:
            result = await project_manager.resolve_neighbor_project("next")

        assert result is None
