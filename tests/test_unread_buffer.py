"""Тесты модуля unread_buffer — cursor-состояние непрочитанных сообщений."""

from datetime import datetime, timedelta
import inspect

import pytest

from claude_manager import unread_buffer
from claude_manager.coding_agent_backend import BackendName, SessionUnreadState
from claude_manager.unread_buffer import (
    PendingMessage,
    SessionUnreadSnapshot,
    _is_expired,
    clear_expired,
    clear_snapshot,
    clear_snapshot_for_session_backend_pair,
    get_pending_messages,
    has_pending,
    restore_snapshot,
    save_snapshot,
)


SESSION_ID = "session-shared-id"


def _saved_at(hours_ago: int) -> datetime:
    """Возвращает время сохранения в прошлом."""
    return datetime.now() - timedelta(hours=hours_ago)


@pytest.fixture(autouse=True)
def _reset_buffer_state() -> None:
    """Сбрасывает состояние буфера вокруг каждого теста."""
    unread_buffer._snapshots.clear()
    yield
    unread_buffer._snapshots.clear()


class TestBackendAwareSnapshots:
    """Тесты сохранения и восстановления backend-aware снапшотов."""

    def test_save_and_restore_for_claude_session(self) -> None:
        """Claude snapshot восстанавливается как SessionUnreadState."""
        save_snapshot(
            SESSION_ID,
            BackendName.CLAUDE,
            raw_record_count=42,
            last_delivered_idx=5,
        )

        assert restore_snapshot(SESSION_ID, BackendName.CLAUDE) == SessionUnreadState(
            raw_record_count=42,
            last_delivered_idx=5,
        )

    def test_save_and_restore_for_codex_session(self) -> None:
        """Codex snapshot хранит те же cursor-поля."""
        save_snapshot(
            SESSION_ID,
            BackendName.CODEX,
            raw_record_count=17,
            last_delivered_idx=3,
        )

        assert restore_snapshot(SESSION_ID, BackendName.CODEX) == SessionUnreadState(
            raw_record_count=17,
            last_delivered_idx=3,
        )

    def test_same_session_id_different_backend_independent(self) -> None:
        """Одинаковый session_id под разными backend-ами хранит две записи."""
        save_snapshot(SESSION_ID, BackendName.CLAUDE, 100, 4)
        save_snapshot(SESSION_ID, BackendName.CODEX, 200, 8)

        assert restore_snapshot(SESSION_ID, BackendName.CLAUDE) == SessionUnreadState(
            raw_record_count=100,
            last_delivered_idx=4,
        )
        assert restore_snapshot(SESSION_ID, BackendName.CODEX) == SessionUnreadState(
            raw_record_count=200,
            last_delivered_idx=8,
        )

    def test_save_overwrites_same_pair(self) -> None:
        """Повторное сохранение той же пары перезаписывает cursor."""
        save_snapshot(SESSION_ID, BackendName.CLAUDE, 10, 1)
        save_snapshot(SESSION_ID, BackendName.CLAUDE, 11, 2)

        assert restore_snapshot(SESSION_ID, BackendName.CLAUDE) == SessionUnreadState(
            raw_record_count=11,
            last_delivered_idx=2,
        )

    def test_restore_missing_pair_returns_none(self) -> None:
        """Отсутствующая пара возвращает None."""
        assert restore_snapshot("missing", BackendName.CLAUDE) is None


class TestExpiration:
    """Тесты TTL-очистки."""

    def test_restore_removes_expired_snapshot(self) -> None:
        """Просроченный snapshot удаляется при restore."""
        unread_buffer._snapshots[(SESSION_ID, BackendName.CLAUDE)] = (
            SessionUnreadSnapshot(
                state=SessionUnreadState(raw_record_count=3, last_delivered_idx=1),
                saved_at=_saved_at(hours_ago=4),
            )
        )

        assert restore_snapshot(SESSION_ID, BackendName.CLAUDE) is None
        assert (SESSION_ID, BackendName.CLAUDE) not in unread_buffer._snapshots

    def test_clear_expired_removes_only_old_snapshots(self) -> None:
        """Массовая очистка удаляет старые записи и оставляет свежие."""
        unread_buffer._snapshots[(SESSION_ID, BackendName.CLAUDE)] = (
            SessionUnreadSnapshot(
                state=SessionUnreadState(raw_record_count=3, last_delivered_idx=1),
                saved_at=_saved_at(hours_ago=4),
            )
        )
        unread_buffer._snapshots[(SESSION_ID, BackendName.CODEX)] = (
            SessionUnreadSnapshot(
                state=SessionUnreadState(raw_record_count=9, last_delivered_idx=2),
                saved_at=_saved_at(hours_ago=1),
            )
        )

        clear_expired()

        assert (SESSION_ID, BackendName.CLAUDE) not in unread_buffer._snapshots
        assert (SESSION_ID, BackendName.CODEX) in unread_buffer._snapshots

    def test_ttl_boundary_is_not_expired(self) -> None:
        """Ровно на границе TTL запись ещё валидна."""
        snapshot = SessionUnreadSnapshot(
            state=SessionUnreadState(raw_record_count=1, last_delivered_idx=0),
            saved_at=datetime.now()
            - timedelta(hours=unread_buffer.config.UNREAD_BUFFER_TTL_HOURS)
            + timedelta(seconds=1),
        )

        assert _is_expired(snapshot) is False


class TestExplicitClear:
    """Тесты явной очистки пары."""

    def test_clear_snapshot_for_session_backend_pair_removes_only_matching_pair(
        self,
    ) -> None:
        """Очистка одной пары не удаляет запись другого backend-а."""
        save_snapshot(SESSION_ID, BackendName.CLAUDE, 100, 4)
        save_snapshot(SESSION_ID, BackendName.CODEX, 200, 8)

        clear_snapshot_for_session_backend_pair(SESSION_ID, BackendName.CLAUDE)

        assert restore_snapshot(SESSION_ID, BackendName.CLAUDE) is None
        assert restore_snapshot(SESSION_ID, BackendName.CODEX) == SessionUnreadState(
            raw_record_count=200,
            last_delivered_idx=8,
        )

    def test_clear_snapshot_for_missing_pair_is_noop(self) -> None:
        """Очистка отсутствующей пары не вызывает ошибку."""
        clear_snapshot_for_session_backend_pair("missing", BackendName.CODEX)
        assert unread_buffer._snapshots == {}


class TestModuleBoundaries:
    """Тесты границ ответственности модуля."""

    def test_module_does_not_import_session_reader(self) -> None:
        """unread_buffer не читает JSONL напрямую через session_reader."""
        source = inspect.getsource(unread_buffer)

        assert "session_reader" not in source

    async def test_legacy_get_pending_messages_is_empty_compatibility_path(self) -> None:
        """Старый project-path API больше не читает файлы и возвращает пустой список."""
        result = await get_pending_messages("/tmp/project")

        assert result == []

    def test_pending_message_type_kept_for_project_manager_compatibility(self) -> None:
        """PendingMessage остаётся доступным до миграции project_manager."""
        assert PendingMessage(session_id=SESSION_ID, text="hello").text == "hello"

    def test_legacy_project_path_helpers_are_noops(self) -> None:
        """Старые project-path helpers не создают backend snapshots."""
        save_snapshot("/tmp/project", {"session": 3})

        assert has_pending("/tmp/project") is False
        clear_snapshot("/tmp/project")
        assert unread_buffer._snapshots == {}
