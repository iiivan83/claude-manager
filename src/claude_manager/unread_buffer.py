"""Backend-aware in-memory cursor buffer for pending delivery."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from claude_manager import config
from claude_manager.coding_agent_backend import BackendName, SessionUnreadState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionUnreadSnapshot:
    """Saved unread cursor plus the time it was captured."""

    state: SessionUnreadState
    saved_at: datetime


@dataclass(frozen=True)
class PendingMessage:
    """Compatibility DTO for pending messages until project_manager migrates."""

    session_id: str
    text: str


_snapshots: dict[tuple[str, BackendName], SessionUnreadSnapshot] = {}


def _now() -> datetime:
    """Возвращает текущее локальное время."""
    return datetime.now()


def _is_expired(snapshot: SessionUnreadSnapshot) -> bool:
    """Проверяет, просрочен ли snapshot по TTL."""
    ttl = timedelta(hours=config.UNREAD_BUFFER_TTL_HOURS)
    return _now() - snapshot.saved_at > ttl


def save_snapshot(
    session_id: str,
    backend: BackendName | dict[str, int],
    raw_record_count: int | None = None,
    last_delivered_idx: int | None = None,
    last_modified_at: float | None = None,
) -> None:
    """Сохраняет cursor-состояние для пары session_id/backend."""
    if isinstance(backend, dict):
        logger.debug(
            "Legacy project snapshot ignored for %s (%d sessions)",
            session_id,
            len(backend),
        )
        return

    if raw_record_count is None or last_delivered_idx is None:
        raise TypeError(
            "raw_record_count and last_delivered_idx are required for "
            "backend-aware unread snapshots"
        )

    state = SessionUnreadState(
        raw_record_count=raw_record_count,
        last_delivered_idx=last_delivered_idx,
        last_modified_at=last_modified_at,
    )
    _snapshots[(session_id, backend)] = SessionUnreadSnapshot(
        state=state,
        saved_at=_now(),
    )
    logger.debug(
        "Unread snapshot saved for %s (%s): raw_count=%d, last_delivered_idx=%d",
        session_id,
        backend.value,
        raw_record_count,
        last_delivered_idx,
    )


def restore_snapshot(
    session_id: str,
    backend: BackendName,
) -> SessionUnreadState | None:
    """Восстанавливает cursor-состояние для пары session_id/backend."""
    key = (session_id, backend)
    snapshot = _snapshots.get(key)
    if snapshot is None:
        return None

    if _is_expired(snapshot):
        del _snapshots[key]
        logger.info(
            "Unread snapshot expired for %s (%s), TTL=%d h",
            session_id,
            backend.value,
            config.UNREAD_BUFFER_TTL_HOURS,
        )
        return None

    return snapshot.state


def clear_expired() -> None:
    """Удаляет все просроченные unread snapshots."""
    expired_keys = [
        key for key, snapshot in _snapshots.items()
        if _is_expired(snapshot)
    ]
    for key in expired_keys:
        del _snapshots[key]

    if expired_keys:
        logger.info(
            "Удалено %d просроченных снапшотов из unread_buffer",
            len(expired_keys),
        )


def clear_snapshot_for_session_backend_pair(
    session_id: str,
    backend: BackendName,
) -> None:
    """Удаляет snapshot для конкретной пары session_id/backend."""
    removed = _snapshots.pop((session_id, backend), None)
    if removed is not None:
        logger.debug(
            "Unread snapshot cleared for %s (%s)",
            session_id,
            backend.value,
        )


async def get_pending_messages(project_path: str) -> list[PendingMessage]:
    """Compatibility no-op for the old project-path pending API."""
    logger.debug("Legacy pending-message lookup ignored for %s", project_path)
    return []


def clear_snapshot(project_path: str) -> None:
    """Compatibility no-op for the old project-path snapshot API."""
    logger.debug("Legacy project snapshot clear ignored for %s", project_path)


def has_pending(project_path: str) -> bool:
    """Compatibility no-op for the old project-path pending API."""
    logger.debug("Legacy pending check ignored for %s", project_path)
    return False


def cleanup_expired() -> None:
    """Compatibility wrapper for the new clear_expired name."""
    clear_expired()
