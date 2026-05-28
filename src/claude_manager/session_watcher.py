"""Thin facade over the per-backend session file pollers.

This module keeps the global registry of pollers (one per coding-agent backend),
exposes the original public API (`start`, `pause_all`, `pause_session`, etc.) so
external callers and tests do not have to change, and re-exports a few names so
that legacy imports like `from claude_manager.session_watcher import SessionWatcher`
keep working. All real polling logic lives in `coding_agent_session_file_poller`.
"""

from __future__ import annotations

import asyncio
import logging

from claude_manager import (
    coding_agent_backend,
    config,
    daily_session_registry,
    session_manager,
)
from claude_manager.coding_agent_backend import BackendName, SessionUnreadState
from claude_manager.coding_agent_session_file_poller import (
    SessionWatcher,
    _is_empty_response,
)
from claude_manager.session_file_polling_cursors import (
    CurrentSessionGetter,
    MessageCallback,
    SessionWatcherState,
)
from claude_manager.session_file_polling_intervals import (
    ERROR_RETRY_DELAY_SECONDS,
    POLL_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BackendName",
    "ERROR_RETRY_DELAY_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "SessionWatcher",
    "SessionWatcherState",
    "_is_empty_response",
    "coding_agent_backend",
    "config",
    "daily_session_registry",
    "get_seen_counts_snapshot",
    "is_session_paused",
    "pause_all",
    "pause_session",
    "reset_state",
    "resume_all",
    "resume_session",
    "session_manager",
    "start",
    "update_session_id",
]


_watchers: dict[BackendName, SessionWatcher] = {}
_callback: MessageCallback | None = None
_get_current_session: CurrentSessionGetter | None = None


def _coerce_backend_name(backend: BackendName | str) -> BackendName:
    if isinstance(backend, BackendName):
        return backend
    return BackendName(backend)


def _get_watcher(backend: BackendName | str = BackendName.CLAUDE) -> SessionWatcher:
    backend_name = _coerce_backend_name(backend)
    if backend_name not in _watchers:
        _watchers[backend_name] = SessionWatcher(
            coding_agent_backend.get_backend(backend_name)
        )
    return _watchers[backend_name]


def _get_all_watchers() -> list[SessionWatcher]:
    return [_get_watcher(backend) for backend in BackendName]


async def _poll_sessions(
    callback: MessageCallback | None = None,
    get_current_session: CurrentSessionGetter | None = None,
) -> None:
    """Run one poll for all backend watchers."""
    effective_callback = callback or _callback
    effective_get_current_session = get_current_session or _get_current_session
    if effective_callback is None or effective_get_current_session is None:
        return

    for watcher in _get_all_watchers():
        await watcher.poll_once(
            effective_callback,
            effective_get_current_session,
        )


def pause_all() -> None:
    """Pause all backend watchers during project switching."""
    for watcher in _get_all_watchers():
        watcher.pause_all()
    logger.info("Мониторинг всех сессий приостановлен (глобальная пауза)")


def resume_all() -> None:
    """Resume all backend watchers after project switching."""
    for watcher in _get_all_watchers():
        watcher.resume_all()
    logger.info("Мониторинг всех сессий возобновлён (глобальная пауза снята)")


def pause_session(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Pause monitoring for one backend-owned session."""
    _get_watcher(backend).pause_session(session_id)


async def resume_session(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Resume monitoring for one backend-owned session."""
    await _get_watcher(backend).resume_session(session_id)


async def reset_state() -> None:
    """Reset accumulated state for all backend watchers."""
    for watcher in _get_all_watchers():
        await watcher.reset_state()
    logger.info("Состояние session_watcher сброшено для переключения проекта")


def update_session_id(
    old_session_id: str,
    new_session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Move watcher state from a temporary id to a real backend-owned id."""
    _get_watcher(backend).update_session_id(old_session_id, new_session_id)


def get_seen_counts_snapshot(
    backend: BackendName | str = BackendName.CLAUDE,
) -> dict[str, SessionUnreadState]:
    """Return a copy of unread cursor state for one backend."""
    return _get_watcher(backend).get_seen_counts_snapshot()


def is_session_paused(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> bool:
    """Return whether a backend-owned session is currently paused."""
    return _get_watcher(backend).is_session_paused(session_id)


async def start(
    callback: MessageCallback,
    get_current_session: CurrentSessionGetter,
) -> None:
    """Start the infinite background watcher loop for all backends."""
    global _callback, _get_current_session

    _callback = callback
    _get_current_session = get_current_session

    for watcher in _get_all_watchers():
        await watcher.reset_state()

    logger.info("Мониторинг сессий запущен (%d backend-ов)", len(_watchers))

    try:
        while True:
            try:
                await _poll_sessions(callback, get_current_session)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "Непредвиденная ошибка в цикле мониторинга",
                    exc_info=True,
                )
                await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)
                continue

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Мониторинг сессий остановлен")
