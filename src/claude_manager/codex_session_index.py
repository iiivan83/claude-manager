"""In-memory operational index for Codex rollout session files."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from claude_manager.codex_session_file_reader import (
    MAX_LINES_FOR_PREVIEW,
    ROLLOUT_TYPE_SESSION_META,
    _extract_uuid_from_rollout_filename,
    _read_file_lines_blocking,
)
from claude_manager.codex_session_index_paths import (
    DirectorySignature,
    _candidate_paths_from_uuid_v7,
    _directory_signature_blocking,
    _file_belongs_to_window,
    _file_date_in_default_window,
    _list_rollout_files_blocking,
    _normalize_sessions_root,
)
from claude_manager.codex_session_metadata import is_subagent_meta_record as _is_subagent
from claude_manager.coding_agent_backend import SessionFileInfo

logger = logging.getLogger(__name__)

CODEX_SESSION_INDEX_TTL_SECONDS = 30.0
CODEX_SESSION_INDEX_DEFAULT_LOOKBACK_DAYS = 4
MAX_CONCURRENT_INDEX_READS = 16


@dataclass(frozen=True)
class _IndexKey:
    sessions_root: str
    lookback_days: int
    window_end_date: date


@dataclass(frozen=True)
class _IndexedRollout:
    session_id: str
    file_path: str
    project_dir: str
    last_modified_at: float


@dataclass
class _IndexState:
    built_at_monotonic: float
    directory_signature: DirectorySignature
    files_by_project: dict[str, list[_IndexedRollout]]


_index_cache: dict[_IndexKey, _IndexState] = {}
_cache_lock = asyncio.Lock()


def clear_cache() -> None:
    """Clear the in-memory Codex operational index, primarily for tests."""
    _index_cache.clear()


async def list_project_session_file_infos(
    sessions_root: str,
    project_dir: str,
    lookback_days: int,
    today: date | None = None,
) -> list[SessionFileInfo]:
    """Return lightweight Codex session files for one project from the index."""
    window_end_date = today or date.today()
    normalized_root = _normalize_sessions_root(sessions_root)
    key = _IndexKey(normalized_root, lookback_days, window_end_date)

    async with _cache_lock:
        signature = await asyncio.to_thread(
            _directory_signature_blocking,
            normalized_root,
            window_end_date,
            lookback_days,
        )
        state = await _get_or_rebuild_state_locked(
            key,
            signature,
            window_end_date,
        )
        indexed_files = await _refresh_project_mtimes_locked(state, project_dir)
        return [_to_session_file_info(indexed) for indexed in indexed_files]


async def refresh_known_session(
    sessions_root: str,
    session_id: str,
    project_dir: str | None = None,
    today: date | None = None,
) -> SessionFileInfo | None:
    """Refresh one known Codex session in the index when its file can be located."""
    normalized_root = _normalize_sessions_root(sessions_root)
    window_end_date = today or date.today()
    indexed = await _read_known_indexed_rollout(
        normalized_root,
        session_id,
        project_dir,
        window_end_date,
    )
    if indexed is None:
        return None

    async with _cache_lock:
        for key, state in _index_cache.items():
            if key.sessions_root != normalized_root:
                continue
            if not _file_belongs_to_window(indexed.file_path, normalized_root, key):
                continue
            _upsert_indexed_rollout(state, indexed)

    return _to_session_file_info(indexed)


async def _get_or_rebuild_state_locked(
    key: _IndexKey, signature: DirectorySignature, today: date,
) -> _IndexState:
    state = _index_cache.get(key)
    now = asyncio.get_running_loop().time()
    if (
        state is not None
        and state.directory_signature == signature
        and now - state.built_at_monotonic < CODEX_SESSION_INDEX_TTL_SECONDS
    ):
        return state

    state = await _build_index_state(key, signature, today)
    _index_cache[key] = state
    return state


async def _build_index_state(
    key: _IndexKey, signature: DirectorySignature, today: date,
) -> _IndexState:
    file_paths = await asyncio.to_thread(
        _list_rollout_files_blocking, key.sessions_root, today, key.lookback_days,
    )
    indexed_files = await _gather_optional_factories_with_concurrency_limit(
        [lambda path=file_path: _read_indexed_rollout(path) for file_path in file_paths]
    )
    files_by_project: dict[str, list[_IndexedRollout]] = {}
    for indexed in indexed_files:
        files_by_project.setdefault(indexed.project_dir, []).append(indexed)
    for project_files in files_by_project.values():
        project_files.sort(key=lambda item: item.last_modified_at, reverse=True)
    return _IndexState(asyncio.get_running_loop().time(), signature, files_by_project)


async def _gather_optional_factories_with_concurrency_limit(
    factories: list[Callable[[], Awaitable[object | None]]],
) -> list:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_INDEX_READS)

    async def run(factory: Callable[[], Awaitable[object | None]]) -> object | None:
        async with semaphore:
            return await factory()

    tasks = [asyncio.create_task(run(factory)) for factory in factories]
    try:
        results = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [result for result in results if result is not None]


async def _read_indexed_rollout(file_path: str) -> _IndexedRollout | None:
    try:
        return await asyncio.to_thread(_read_indexed_rollout_blocking, file_path)
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return None
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return None


def _read_indexed_rollout_blocking(file_path: str) -> _IndexedRollout | None:
    meta_record = _read_session_meta_record_blocking(file_path)
    if meta_record is None:
        return None
    payload = _payload_from_meta_record(meta_record)
    project_dir = payload.get("cwd")
    if not isinstance(project_dir, str):
        return None
    raw_session_id = payload.get("id")
    session_id = raw_session_id if isinstance(raw_session_id, str) else None
    session_id = session_id or _extract_uuid_from_rollout_filename(file_path)
    if session_id is None:
        return None
    return _IndexedRollout(
        session_id=session_id,
        file_path=file_path,
        project_dir=project_dir,
        last_modified_at=os.path.getmtime(file_path),
    )


def _read_session_meta_record_blocking(file_path: str) -> dict[str, object] | None:
    raw_lines = _read_file_lines_blocking(file_path, MAX_LINES_FOR_PREVIEW)
    for raw_line in raw_lines:
        try:
            parsed_value = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed_value, dict) and parsed_value.get(
            "type"
        ) == ROLLOUT_TYPE_SESSION_META:
            return parsed_value
    return None


def _payload_from_meta_record(meta_record: dict[str, object]) -> dict[str, object]:
    payload = meta_record.get("payload")
    return payload if isinstance(payload, dict) and not _is_subagent(meta_record) else {}


async def _refresh_project_mtimes_locked(
    state: _IndexState, project_dir: str,
) -> list[_IndexedRollout]:
    refreshed = await asyncio.to_thread(
        _refresh_project_mtimes_blocking, state.files_by_project.get(project_dir, []),
    )
    state.files_by_project[project_dir] = refreshed
    return refreshed


def _refresh_project_mtimes_blocking(
    indexed_files: list[_IndexedRollout],
) -> list[_IndexedRollout]:
    refreshed: list[_IndexedRollout] = []
    for indexed in indexed_files:
        try:
            last_modified_at = os.path.getmtime(indexed.file_path)
        except OSError:
            continue
        refreshed.append(
            _IndexedRollout(
                indexed.session_id,
                indexed.file_path,
                indexed.project_dir,
                last_modified_at,
            )
        )
    refreshed.sort(key=lambda item: item.last_modified_at, reverse=True)
    return refreshed


async def _read_known_indexed_rollout(
    sessions_root: str, session_id: str, project_dir: str | None, today: date,
) -> _IndexedRollout | None:
    for file_path in _candidate_paths_from_uuid_v7(sessions_root, session_id):
        if not await asyncio.to_thread(os.path.isfile, file_path):
            continue
        if not _file_date_in_default_window(
            file_path, sessions_root, today, CODEX_SESSION_INDEX_DEFAULT_LOOKBACK_DAYS,
        ):
            return None
        indexed = await _read_indexed_rollout(file_path)
        if indexed is None:
            return None
        if project_dir is not None and indexed.project_dir != project_dir:
            return None
        if indexed.session_id != session_id:
            return None
        return indexed
    return None


def _upsert_indexed_rollout(state: _IndexState, indexed: _IndexedRollout) -> None:
    project_files = [
        item
        for item in state.files_by_project.get(indexed.project_dir, [])
        if item.session_id != indexed.session_id
    ]
    project_files.append(indexed)
    project_files.sort(key=lambda item: item.last_modified_at, reverse=True)
    state.files_by_project[indexed.project_dir] = project_files


def _to_session_file_info(indexed: _IndexedRollout) -> SessionFileInfo:
    return SessionFileInfo(
        indexed.session_id, indexed.file_path, indexed.last_modified_at, preview="",
    )
