"""Collect backend-aware pending messages for project switching."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from claude_manager import (
    coding_agent_backend,
    config,
    daily_session_registry,
    reply_anchor_registry,
    silence_mode_registry,
    telegram_response_delivery,
    unread_buffer,
)
from claude_manager.coding_agent_backend import (
    CURSOR_ONLY_PARSED_MESSAGE_COUNT,
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


def _pending_message_is_visible_now(pending_message: object) -> bool:
    """Return whether a pending message will be shown immediately."""
    is_final = getattr(pending_message, "is_final", True)
    return bool(is_final) or not silence_mode_registry.is_enabled()


def _get_visible_pending_messages(pending_messages: list[object]) -> list[object]:
    """Return pending messages not suppressed by silence mode."""
    return [
        pending_message
        for pending_message in pending_messages
        if _pending_message_is_visible_now(pending_message)
    ]


def count_visible_pending_messages(result: object) -> int:
    """Count pending messages that will be visible to the user."""
    pending_messages = getattr(result, "pending_messages", [])
    if not pending_messages:
        return getattr(result, "pending_messages_count", 0)
    return len(_get_visible_pending_messages(pending_messages))


async def _register_pending_session(
    pending: object,
    backend: BackendName,
) -> int | None:
    """Register a pending session before delivery."""
    try:
        return await daily_session_registry.register_session(
            pending.session_id,
            backend,
        )
    except Exception:
        logger.error(
            "Ошибка регистрации сессии %s при доставке буфера",
            getattr(pending, "session_id", "<unknown>"),
            exc_info=True,
        )
        return None


async def deliver_pending_messages(
    chat_id: int,
    pending_messages: list[object],
) -> None:
    """Deliver visible pending messages after project switch."""
    for pending in _get_visible_pending_messages(pending_messages):
        backend = getattr(pending, "backend", BackendName.CLAUDE)
        is_final = getattr(pending, "is_final", True)
        day_number = await _register_pending_session(pending, backend)
        if day_number is None:
            continue

        send_response_kwargs = {"is_final": is_final}
        reply_to_message_id = reply_anchor_registry.get_anchor(
            config.WORKING_DIR,
            backend,
            pending.session_id,
        )
        if reply_to_message_id is not None:
            send_response_kwargs["reply_to_message_id"] = reply_to_message_id
        await telegram_response_delivery.send_response(
            chat_id,
            pending.text,
            day_number,
            backend,
            session_id=pending.session_id,
            **send_response_kwargs,
        )
        unread_buffer.clear_snapshot_for_session_backend_pair(
            pending.session_id,
            backend,
        )


def _should_collect_for_all_mode_same_project(
    result: object,
    was_all_projects_mode: bool,
) -> bool:
    """Return whether all-mode exit needs same-project pending collection."""
    return (
        was_all_projects_mode
        and getattr(result, "success", False)
        and getattr(result, "already_active", False)
    )


async def include_pending_for_all_mode_same_project(
    result: object,
    target_project: object,
    was_all_projects_mode: bool,
) -> object:
    """Collect pending messages when all mode exits into the active project."""
    if not _should_collect_for_all_mode_same_project(result, was_all_projects_mode):
        return result

    pending_count, pending_messages = await collect_pending_messages(
        target_project.absolute_path
    )
    return replace(
        result,
        already_active=False,
        pending_messages_count=pending_count,
        pending_messages=pending_messages,
    )


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


def _messages_after_raw_cursor(
    messages: list[SessionMessage],
    raw_record_count: int,
    session_id: str,
) -> list[SessionMessage]:
    """Выбирает сообщения после raw-курсора, когда parsed-индексу доверять нельзя."""
    if not any(message.raw_record_index is not None for message in messages):
        # Без raw-индексов отличить новое от старого нельзя: лучше ничего не
        # доставить, чем вывалить всю историю сессии в чат (P1-1).
        logger.warning(
            "Cursor-only снапшот без raw-индексов: pending-доставка пропущена (%s)",
            session_id,
        )
        return []
    return [
        message
        for message in messages
        if message.raw_record_index is not None
        and message.raw_record_index > raw_record_count
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

    if old_state.parsed_message_count == CURSOR_ONLY_PARSED_MESSAGE_COUNT:
        delta = _messages_after_raw_cursor(
            snapshot.messages,
            old_state.raw_record_count,
            file_info.session_id,
        )
    else:
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
