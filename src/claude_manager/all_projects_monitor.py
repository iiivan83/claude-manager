"""Global all-project session monitoring.

This module scans session files across every configured project while keeping
its own delivery cursors. It intentionally does not advance the normal
project watcher state, so messages shown in all-project mode remain pending
when the user switches into the concrete project.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from claude_manager import (
    coding_agent_backend,
    config,
    project_manager,
    session_watcher,
    unread_buffer,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = session_watcher.POLL_INTERVAL_SECONDS
ERROR_RETRY_DELAY_SECONDS = session_watcher.ERROR_RETRY_DELAY_SECONDS
REGISTRY_FILENAME = "daily_sessions.json"
DATE_FORMAT = "%Y-%m-%d"
MAX_CONCURRENT_BASELINE_READS = 16

AllProjectsMessageCallback = Callable[
    [int, int, int, str, str, BackendName, str, bool],
    Awaitable[None],
]


@dataclass(frozen=True)
class AllProjectSessionLink:
    """Target encoded by a /<project>s<session> command in all mode."""

    project_number: int
    session_number: int
    project_name: str
    project_path: str
    session_id: str
    backend: BackendName


@dataclass(frozen=True)
class _ProjectSession:
    """One visible session with all numbering needed for display and routing."""

    project_number: int
    project_name: str
    project_path: str
    session_number: int
    file_info: SessionFileInfo
    backend: CodingAgentBackend


@dataclass
class _AllMonitorState:
    """Delivery cursor for one project/session/backend in all mode."""

    raw_record_count: int = 0
    parsed_message_count: int = 0
    last_delivered_idx: int = -1
    is_turn_active: bool = False
    last_modified_at: float = 0.0


_enabled_chat_ids: set[int] = set()
_states: dict[tuple[str, str, BackendName], _AllMonitorState] = {}
_links: dict[tuple[int, int], AllProjectSessionLink] = {}
_lock = asyncio.Lock()


def reset_state() -> None:
    """Clear all in-memory monitor state."""
    _enabled_chat_ids.clear()
    _states.clear()
    _links.clear()


def is_enabled_for_chat(chat_id: int) -> bool:
    """Return whether a chat is in global all-project mode."""
    return chat_id in _enabled_chat_ids


def has_enabled_chats() -> bool:
    """Return whether any chat currently receives all-project messages."""
    return bool(_enabled_chat_ids)


def disable_for_chat(chat_id: int) -> bool:
    """Disable all-project mode for a chat and return whether it was enabled."""
    if chat_id not in _enabled_chat_ids:
        return False

    _enabled_chat_ids.remove(chat_id)
    return True


def resolve_link(
    project_number: int,
    session_number: int,
) -> AllProjectSessionLink | None:
    """Resolve a displayed all-mode command back to its exact session target."""
    return _links.get((project_number, session_number))


def _state_key(
    project_path: str,
    session_id: str,
    backend: BackendName,
) -> tuple[str, str, BackendName]:
    """Build a stable cursor key for one project/session/backend."""
    return (project_path, session_id, backend)


def _message_should_be_delivered(message: SessionMessage) -> bool:
    """Return whether a parsed session message should be sent to all mode."""
    if message.role != "assistant":
        return False
    if message.is_empty_response:
        return False
    return bool(message.text.strip())


async def _load_project_today_numbers(
    project_path: str,
) -> dict[tuple[str, BackendName], int]:
    """Read a project's daily session numbers without mutating global registry."""
    registry_path = Path(project_path) / REGISTRY_FILENAME
    try:
        raw_text = await asyncio.to_thread(registry_path.read_text, "utf-8")
        raw_registry = json.loads(raw_text)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "Failed to read daily session registry for project %s",
            project_path,
            exc_info=True,
        )
        return {}

    if not isinstance(raw_registry, dict):
        return {}

    today_entries = raw_registry.get(date.today().strftime(DATE_FORMAT), {})
    if not isinstance(today_entries, dict):
        return {}

    result: dict[tuple[str, BackendName], int] = {}
    for raw_number, raw_entry in today_entries.items():
        try:
            session_number = int(raw_number)
        except (TypeError, ValueError):
            continue

        session_id: str | None = None
        backend = BackendName.CLAUDE
        if isinstance(raw_entry, str):
            session_id = raw_entry
        elif isinstance(raw_entry, dict):
            raw_session_id = raw_entry.get("session_id")
            raw_backend = raw_entry.get("backend")
            if isinstance(raw_session_id, str):
                session_id = raw_session_id
            if isinstance(raw_backend, str):
                try:
                    backend = BackendName(raw_backend)
                except ValueError:
                    continue

        if session_id:
            result[(session_id, backend)] = session_number

    return result


def _assign_session_numbers(
    project_path: str,
    sessions_with_backend: list[tuple[SessionFileInfo, CodingAgentBackend]],
    registry_numbers: dict[tuple[str, BackendName], int],
) -> list[tuple[int, SessionFileInfo, CodingAgentBackend]]:
    """Assign display numbers, preferring the project's daily registry."""
    del project_path
    sessions_with_backend.sort(
        key=lambda item: item[0].last_modified_at,
        reverse=True,
    )

    used_numbers: set[int] = set()
    next_fallback_number = max(registry_numbers.values(), default=0) + 1
    result: list[tuple[int, SessionFileInfo, CodingAgentBackend]] = []

    for file_info, backend in sessions_with_backend:
        registry_key = (file_info.session_id, backend.name)
        session_number = registry_numbers.get(registry_key)
        if session_number is None or session_number in used_numbers:
            while next_fallback_number in used_numbers:
                next_fallback_number += 1
            session_number = next_fallback_number
            next_fallback_number += 1
        used_numbers.add(session_number)
        result.append((session_number, file_info, backend))

    return result


async def _collect_backend_sessions_individually(
    backend: CodingAgentBackend,
    projects: list[project_manager.ProjectInfo],
) -> dict[str, list[SessionFileInfo]]:
    """Collect backend session files one project at a time as a fallback."""
    files_by_project: dict[str, list[SessionFileInfo]] = {}
    for project in projects:
        try:
            files_by_project[project.absolute_path] = (
                await backend.list_all_session_files_for_project(
                    project.absolute_path
                )
            )
        except Exception:
            logger.warning(
                "Failed to read sessions for project %s (%s)",
                project.absolute_path,
                backend.name.value,
                exc_info=True,
            )
            files_by_project[project.absolute_path] = []
    return files_by_project


async def _collect_backend_sessions_by_project(
    backend: CodingAgentBackend,
    projects: list[project_manager.ProjectInfo],
) -> dict[str, list[SessionFileInfo]]:
    """Collect backend session files for all projects in one backend call."""
    project_paths = [project.absolute_path for project in projects]
    bulk_lister = getattr(backend, "list_all_session_files_for_projects", None)
    if bulk_lister is None:
        return await _collect_backend_sessions_individually(backend, projects)

    try:
        files_by_project = await bulk_lister(project_paths)
    except Exception:
        logger.warning(
            "Failed to bulk-read sessions for backend %s",
            backend.name.value,
            exc_info=True,
        )
        return await _collect_backend_sessions_individually(backend, projects)

    return {
        project_path: files_by_project.get(project_path, [])
        for project_path in project_paths
    }


async def _collect_project_sessions() -> list[_ProjectSession]:
    """Collect numbered session files across every visible project."""
    projects = await project_manager.scan_available_projects()
    project_sessions: list[_ProjectSession] = []
    links: dict[tuple[int, int], AllProjectSessionLink] = {}
    backend_files_by_project = [
        (
            backend,
            await _collect_backend_sessions_by_project(backend, projects),
        )
        for backend in coding_agent_backend.get_all_backends()
    ]

    for project_number, project in enumerate(projects, start=1):
        sessions_with_backend: list[tuple[SessionFileInfo, CodingAgentBackend]] = []

        for backend, files_by_project in backend_files_by_project:
            files = files_by_project.get(project.absolute_path, [])
            sessions_with_backend.extend(
                (file_info, backend) for file_info in files
            )

        registry_numbers = await _load_project_today_numbers(project.absolute_path)
        numbered_sessions = _assign_session_numbers(
            project.absolute_path,
            sessions_with_backend,
            registry_numbers,
        )

        for session_number, file_info, backend in numbered_sessions:
            project_session = _ProjectSession(
                project_number=project_number,
                project_name=project.name,
                project_path=project.absolute_path,
                session_number=session_number,
                file_info=file_info,
                backend=backend,
            )
            project_sessions.append(project_session)
            links[(project_number, session_number)] = AllProjectSessionLink(
                project_number=project_number,
                session_number=session_number,
                project_name=project.name,
                project_path=project.absolute_path,
                session_id=file_info.session_id,
                backend=backend.name,
            )

    _links.clear()
    _links.update(links)
    return project_sessions


def _watcher_state_for_project_session(
    project_session: _ProjectSession,
    current_project_path: str,
    current_watcher_states: dict[BackendName, dict[str, object]],
) -> object | None:
    """Return normal watcher state when all mode enters from the active project."""
    if project_session.project_path != current_project_path:
        return None
    return current_watcher_states[project_session.backend.name].get(
        project_session.file_info.session_id
    )


def _baseline_state_from_snapshot(
    project_session: _ProjectSession,
    snapshot: SessionFileSnapshot,
    watcher_state: object | None,
) -> _AllMonitorState:
    """Build an all-mode baseline cursor from one session-file snapshot."""
    if watcher_state is None:
        last_delivered_idx = len(snapshot.messages) - 1
        raw_record_count = snapshot.raw_record_count
    else:
        last_delivered_idx = getattr(watcher_state, "last_delivered_idx")
        raw_record_count = getattr(watcher_state, "raw_record_count")

    return _AllMonitorState(
        raw_record_count=raw_record_count,
        parsed_message_count=len(snapshot.messages),
        last_delivered_idx=last_delivered_idx,
        is_turn_active=snapshot.is_turn_active,
        last_modified_at=project_session.file_info.last_modified_at,
    )


async def _read_baseline_state(
    project_session: _ProjectSession,
    current_project_path: str,
    current_watcher_states: dict[BackendName, dict[str, object]],
    semaphore: asyncio.Semaphore,
) -> tuple[tuple[str, str, BackendName], _AllMonitorState] | None:
    """Read one session baseline under a concurrency limit."""
    file_info = project_session.file_info
    backend = project_session.backend
    try:
        async with semaphore:
            snapshot = await backend.read_session_file_cursor(file_info.file_path)
    except Exception:
        logger.warning(
            "Failed to read all-mode baseline for session %s (%s)",
            file_info.session_id,
            backend.name.value,
            exc_info=True,
        )
        return None

    watcher_state = _watcher_state_for_project_session(
        project_session,
        current_project_path,
        current_watcher_states,
    )
    return (
        _state_key(
            project_session.project_path,
            file_info.session_id,
            backend.name,
        ),
        _baseline_state_from_snapshot(project_session, snapshot, watcher_state),
    )


async def _build_baseline_states(
    project_sessions: list[_ProjectSession],
) -> dict[tuple[str, str, BackendName], _AllMonitorState]:
    """Create initial cursors for the moment all-project mode is entered."""
    current_project_path = config.WORKING_DIR
    current_watcher_states = {
        backend: session_watcher.get_seen_counts_snapshot(backend)
        for backend in BackendName
    }
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BASELINE_READS)
    baseline_results = await asyncio.gather(
        *[
            _read_baseline_state(
                project_session,
                current_project_path,
                current_watcher_states,
                semaphore,
            )
            for project_session in project_sessions
        ]
    )
    return {
        key: state
        for result in baseline_results
        if result is not None
        for key, state in [result]
    }


async def enable_for_chat(chat_id: int) -> None:
    """Enable all-project monitoring and baseline all visible sessions."""
    session_watcher.pause_all()
    try:
        project_sessions = await _collect_project_sessions()
        baseline_states = await _build_baseline_states(project_sessions)
        async with _lock:
            _states.clear()
            _states.update(baseline_states)
            _enabled_chat_ids.add(chat_id)
    except Exception:
        session_watcher.resume_all()
        raise

    logger.info("Chat %d enabled global all-project mode", chat_id)


def _candidate_indices(
    previous: _AllMonitorState,
    snapshot: SessionFileSnapshot,
) -> list[int]:
    """Return message indices eligible for delivery in this poll."""
    raw_index_candidates = [
        index
        for index, message in enumerate(snapshot.messages)
        if (
            message.raw_record_index is not None
            and message.raw_record_index > previous.raw_record_count
        )
    ]
    messages_have_raw_indices = any(
        message.raw_record_index is not None for message in snapshot.messages
    )
    if messages_have_raw_indices:
        indices = raw_index_candidates
    else:
        indices = list(range(previous.last_delivered_idx + 1, len(snapshot.messages)))
    if snapshot.is_turn_active and indices:
        last_idx = len(snapshot.messages) - 1
        indices = [index for index in indices if index < last_idx]
    return indices


def _ensure_unread_snapshot(
    session_id: str,
    backend: BackendName,
    previous: _AllMonitorState,
) -> None:
    """Save normal-project unread state without overwriting older snapshots."""
    if unread_buffer.restore_snapshot(session_id, backend) is not None:
        return

    unread_buffer.save_snapshot(
        session_id,
        backend,
        raw_record_count=previous.raw_record_count,
        last_delivered_idx=previous.last_delivered_idx,
    )


def _next_state_from_snapshot(
    previous: _AllMonitorState,
    snapshot: SessionFileSnapshot,
    last_modified_at: float,
) -> _AllMonitorState:
    """Build the next all-mode cursor after a snapshot was processed."""
    if snapshot.is_turn_active and snapshot.messages:
        last_delivered_idx = max(
            previous.last_delivered_idx,
            len(snapshot.messages) - 2,
        )
    else:
        last_delivered_idx = len(snapshot.messages) - 1

    return _AllMonitorState(
        raw_record_count=snapshot.raw_record_count,
        parsed_message_count=len(snapshot.messages),
        last_delivered_idx=last_delivered_idx,
        is_turn_active=snapshot.is_turn_active,
        last_modified_at=last_modified_at,
    )


async def _deliver_project_session_delta(
    chat_ids: list[int],
    project_session: _ProjectSession,
    snapshot: SessionFileSnapshot,
    previous: _AllMonitorState,
    callback: AllProjectsMessageCallback,
) -> None:
    """Deliver new assistant messages from one project session."""
    deliverable = [
        (index, snapshot.messages[index])
        for index in _candidate_indices(previous, snapshot)
        if _message_should_be_delivered(snapshot.messages[index])
    ]
    if not deliverable:
        return

    file_info = project_session.file_info
    backend_name = project_session.backend.name
    _ensure_unread_snapshot(file_info.session_id, backend_name, previous)

    for position, (_index, message) in enumerate(deliverable):
        is_final = not snapshot.is_turn_active and position == len(deliverable) - 1
        for chat_id in chat_ids:
            await callback(
                chat_id,
                project_session.project_number,
                project_session.session_number,
                project_session.project_name,
                file_info.session_id,
                backend_name,
                message.text,
                is_final,
            )


async def _check_project_session(
    chat_ids: list[int],
    project_session: _ProjectSession,
    callback: AllProjectsMessageCallback,
) -> None:
    """Check one session file and advance only all-mode state."""
    file_info = project_session.file_info
    backend = project_session.backend
    key = _state_key(project_session.project_path, file_info.session_id, backend.name)
    previous = _states.get(key, _AllMonitorState())
    if previous.last_modified_at >= file_info.last_modified_at:
        return

    snapshot = await backend.read_session_file_snapshot(file_info.file_path)
    if snapshot.raw_record_count == 0:
        return

    if (
        snapshot.raw_record_count == previous.raw_record_count
        and len(snapshot.messages) == previous.parsed_message_count
        and snapshot.is_turn_active == previous.is_turn_active
    ):
        return

    await _deliver_project_session_delta(
        chat_ids,
        project_session,
        snapshot,
        previous,
        callback,
    )
    _states[key] = _next_state_from_snapshot(
        previous,
        snapshot,
        file_info.last_modified_at,
    )


async def poll_once(callback: AllProjectsMessageCallback) -> None:
    """Run one all-project scan and deliver new messages to enabled chats."""
    async with _lock:
        chat_ids = sorted(_enabled_chat_ids)
    if not chat_ids:
        return

    project_sessions = await _collect_project_sessions()
    active_keys = {
        _state_key(
            session.project_path,
            session.file_info.session_id,
            session.backend.name,
        )
        for session in project_sessions
    }

    for stale_key in [key for key in _states if key not in active_keys]:
        del _states[stale_key]

    for project_session in project_sessions:
        try:
            await _check_project_session(chat_ids, project_session, callback)
        except Exception:
            logger.warning(
                "All-project monitor failed for %s/%s (%s)",
                project_session.project_name,
                project_session.file_info.session_id,
                project_session.backend.name.value,
                exc_info=True,
            )


async def start(callback: AllProjectsMessageCallback) -> None:
    """Start the infinite background loop for all-project monitoring."""
    logger.info("Global all-project monitor started")
    try:
        while True:
            try:
                await poll_once(callback)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Unexpected all-project monitor error", exc_info=True)
                await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)
                continue

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Global all-project monitor stopped")
