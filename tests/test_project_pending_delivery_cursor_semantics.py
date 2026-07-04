"""Cursor-semantics tests for project-switch pending collection (P1-1)."""

import pytest

from claude_manager import unread_buffer
from claude_manager.coding_agent_backend import (
    CURSOR_ONLY_PARSED_MESSAGE_COUNT,
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)

# project_pending_delivery участвует в цикле импортов
# (project_pending_delivery -> telegram_response_delivery -> claude_interaction
# -> all_projects_monitor -> project_manager -> project_pending_delivery).
# Прямой импорт функции ниже как первой точки входа в цепочку падает с
# partially initialized module. Предзагрузка project_manager полностью
# инициализирует цепочку до импорта тестируемой функции.
from claude_manager import project_manager as _preload_import_chain  # noqa: F401
from claude_manager.project_pending_delivery import (
    _collect_pending_for_session_file,
)


class CursorAwareFakeBackend:
    """Фейковый backend-адаптер с раздельными cursor- и snapshot-чтениями."""

    name = BackendName.CLAUDE

    def __init__(
        self,
        cursor: SessionFileSnapshot,
        snapshot: SessionFileSnapshot,
    ) -> None:
        self._cursor = cursor
        self._snapshot = snapshot

    async def read_session_file_cursor(self, file_path: str) -> SessionFileSnapshot:
        del file_path
        return self._cursor

    async def read_session_file_snapshot(self, file_path: str) -> SessionFileSnapshot:
        del file_path
        return self._snapshot


@pytest.fixture(autouse=True)
def _clean_unread_buffer():
    unread_buffer._snapshots.clear()
    yield
    unread_buffer._snapshots.clear()


def _assistant_message(text: str, raw_record_index: int | None) -> SessionMessage:
    return SessionMessage(
        role="assistant",
        text=text,
        timestamp=None,
        is_empty_response=False,
        raw_record_index=raw_record_index,
    )


def _file_info(session_id: str) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=f"/fake/{session_id}.jsonl",
        last_modified_at=100.0,
        preview="preview",
    )


def _snapshot(
    messages: list[SessionMessage],
    raw_record_count: int,
) -> SessionFileSnapshot:
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_record_count,
        last_record={},
        is_turn_active=False,
    )


async def test_cursor_only_state_delivers_only_messages_after_raw_cursor() -> None:
    """Cursor-only baseline (-1) не вываливает всю историю сессии (P1-1)."""
    unread_buffer.save_snapshot(
        "sess-1",
        BackendName.CLAUDE,
        raw_record_count=3,
        last_delivered_idx=-1,
        parsed_message_count=CURSOR_ONLY_PARSED_MESSAGE_COUNT,
    )
    messages = [
        _assistant_message("старый ответ 1", raw_record_index=1),
        _assistant_message("старый ответ 2", raw_record_index=2),
        _assistant_message("старый ответ 3", raw_record_index=3),
        _assistant_message("новый ответ", raw_record_index=4),
    ]
    backend_adapter = CursorAwareFakeBackend(
        cursor=_snapshot([], raw_record_count=4),
        snapshot=_snapshot(messages, raw_record_count=4),
    )

    pending_items = await _collect_pending_for_session_file(
        backend_adapter,
        _file_info("sess-1"),
    )

    assert [item.text for item in pending_items] == ["новый ответ"]


async def test_cursor_only_state_without_raw_indices_delivers_nothing() -> None:
    """Без raw-индексов отличить новое от старого нельзя — лучше тишина, чем лавина."""
    unread_buffer.save_snapshot(
        "sess-2",
        BackendName.CLAUDE,
        raw_record_count=3,
        last_delivered_idx=-1,
        parsed_message_count=CURSOR_ONLY_PARSED_MESSAGE_COUNT,
    )
    messages = [
        _assistant_message("старый ответ", raw_record_index=None),
        _assistant_message("новый ответ", raw_record_index=None),
    ]
    backend_adapter = CursorAwareFakeBackend(
        cursor=_snapshot([], raw_record_count=4),
        snapshot=_snapshot(messages, raw_record_count=4),
    )

    pending_items = await _collect_pending_for_session_file(
        backend_adapter,
        _file_info("sess-2"),
    )

    assert pending_items == []


async def test_valid_parsed_state_keeps_slice_semantics() -> None:
    """Обычное parsed-состояние доставляет по last_delivered_idx, как раньше."""
    unread_buffer.save_snapshot(
        "sess-3",
        BackendName.CLAUDE,
        raw_record_count=3,
        last_delivered_idx=1,
        parsed_message_count=2,
    )
    messages = [
        _assistant_message("доставлен раньше 1", raw_record_index=1),
        _assistant_message("доставлен раньше 2", raw_record_index=3),
        _assistant_message("новый ответ", raw_record_index=4),
    ]
    backend_adapter = CursorAwareFakeBackend(
        cursor=_snapshot([], raw_record_count=4),
        snapshot=_snapshot(messages, raw_record_count=4),
    )

    pending_items = await _collect_pending_for_session_file(
        backend_adapter,
        _file_info("sess-3"),
    )

    assert [item.text for item in pending_items] == ["новый ответ"]
