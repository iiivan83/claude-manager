"""Collect backend-aware pending messages for project switching."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from claude_manager import coding_agent_backend, config, unread_buffer
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    SessionUnreadState,
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_PENDING_READS = 16


@dataclass(frozen=True)
class PendingDeliveryItem:
    """Unread message that bot.py delivers after project return."""

    session_id: str
    backend: BackendName
    text: str
    is_final: bool


def _has_new_messages(
    old_state: SessionUnreadState,
    snapshot: SessionFileSnapshot,
) -> bool:
    """Return whether parsed messages appeared after the saved cursor."""
    return (
        snapshot.raw_record_count > old_state.raw_record_count
        or len(snapshot.messages) > old_state.last_delivered_idx + 1
    )


def _message_is_deliverable(message: SessionMessage) -> bool:
    """Return whether a session message should be delivered as pending."""
    if message.role != "assistant":
        return False
    if message.is_empty_response:
        return False
    return bool(message.text.strip())


def _build_pending_items_from_delta(
    session_id: str,
    backend: BackendName,
    delta: list[SessionMessage],
    is_turn_active: bool,
) -> list[PendingDeliveryItem]:
    """Create pending items from new messages of one session."""
    deliverable_messages = [
        message for message in delta if _message_is_deliverable(message)
    ]
    return [
        PendingDeliveryItem(
            session_id=session_id,
            backend=backend,
            text=message.text,
            is_final=not is_turn_active and index == len(deliverable_messages) - 1,
        )
        for index, message in enumerate(deliverable_messages)
    ]


async def _collect_pending_for_session_file(
    backend_adapter: CodingAgentBackend,
    file_info: SessionFileInfo,
) -> list[PendingDeliveryItem]:
    """Collect pending items for one backend-owned session file."""
    backend = backend_adapter.name
    old_state = unread_buffer.restore_snapshot(file_info.session_id, backend)
    if old_state is None:
        return []
    if (
        old_state.last_modified_at is not None
        and file_info.last_modified_at <= old_state.last_modified_at
    ):
        return []

    read_cursor = getattr(
        backend_adapter,
        "read_session_file_cursor",
        backend_adapter.read_session_file_snapshot,
    )
    cursor = await read_cursor(file_info.file_path)
    if cursor.raw_record_count <= old_state.raw_record_count:
        return []

    snapshot = await backend_adapter.read_session_file_snapshot(file_info.file_path)
    if not _has_new_messages(old_state, snapshot):
        return []

    delta = snapshot.messages[old_state.last_delivered_idx + 1:]
    return _build_pending_items_from_delta(
        file_info.session_id,
        backend,
        delta,
        snapshot.is_turn_active,
    )


async def _collect_pending_for_backend(
    backend_adapter: CodingAgentBackend,
    project_path: str,
) -> list[PendingDeliveryItem]:
    """Collect pending items for all files of one backend."""
    backend = backend_adapter.name
    session_files = await backend_adapter.list_all_session_files_for_project(
        project_path,
        lookback_days=config.OPERATIONAL_SESSION_LOOKBACK_DAYS,
    )
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PENDING_READS)

    async def collect_one(file_info: SessionFileInfo) -> list[PendingDeliveryItem]:
        async with semaphore:
            try:
                return await _collect_pending_for_session_file(
                    backend_adapter, file_info,
                )
            except Exception:
                logger.warning(
                    "Не удалось собрать pending delivery: %s (%s)",
                    file_info.session_id,
                    backend.value,
                    exc_info=True,
                )
                return []

    pending_groups = await asyncio.gather(
        *(collect_one(file_info) for file_info in session_files)
    )
    return [item for group in pending_groups for item in group]


async def collect_pending_messages(
    target_path: str,
) -> tuple[int, list[PendingDeliveryItem]]:
    """Collect unread messages for the project that becomes active."""
    pending_items: list[PendingDeliveryItem] = []
    for backend_adapter in coding_agent_backend.get_all_backends():
        pending_items.extend(
            await _collect_pending_for_backend(backend_adapter, target_path)
        )

    unread_buffer.clear_expired()
    return len(pending_items), pending_items
