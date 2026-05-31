"""Bounded refresh facade for recent session candidates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from claude_manager import coding_agent_backend, config
from claude_manager.codex_session_metadata import is_subagent_session_file
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
)
from claude_manager.recent_sessions_store import RecentSessionHeader, RecentSessionsStore


logger = logging.getLogger(__name__)

SESSION_LIST_LIMIT = 15
ALL_MODE_SESSION_CANDIDATE_LIMIT = 80


@dataclass(frozen=True)
class RecentSessionsQueryResult:
    """Rows plus non-fatal warnings for Telegram-facing callers."""

    rows: list[RecentSessionHeader]
    degraded_messages: list[str]


_BACKGROUND_PROJECT_REFRESHES: dict[str, asyncio.Task[RecentSessionsQueryResult]] = {}


def get_default_store() -> RecentSessionsStore:
    """Return a store bound to the default state path."""
    return RecentSessionsStore()


async def refresh_project_sessions(
    project_path: str,
    backends: list[CodingAgentBackend] | None = None,
    store: RecentSessionsStore | None = None,
) -> RecentSessionsQueryResult:
    """Refresh one project with bounded user-facing session metadata."""
    active_store = _resolve_store(store)
    await active_store.initialize()
    active_backends = _resolve_backends(backends)
    degraded_messages: list[str] = []
    headers: list[RecentSessionHeader] = []

    for backend in active_backends:
        try:
            session_files = await backend.list_session_files_for_project(project_path)
        except Exception as exc:
            _record_backend_failure(
                degraded_messages,
                backend,
                project_path,
                "project recent-session refresh",
                exc,
            )
            continue
        headers.extend(
            _header_from_session_file(project_path, backend, session_file)
            for session_file in session_files
        )

    await active_store.upsert_headers(headers)
    rows = await active_store.query_project(project_path, limit=SESSION_LIST_LIMIT)
    return RecentSessionsQueryResult(rows=rows, degraded_messages=degraded_messages)


async def refresh_global_sessions(
    project_paths: list[str],
    backends: list[CodingAgentBackend] | None = None,
    store: RecentSessionsStore | None = None,
    limit: int = ALL_MODE_SESSION_CANDIDATE_LIMIT,
) -> RecentSessionsQueryResult:
    """Refresh all-mode candidates with bounded per-project operational listing."""
    active_store = _resolve_store(store)
    await active_store.initialize()
    active_backends = _resolve_backends(backends)
    degraded_messages: list[str] = []
    headers: list[RecentSessionHeader] = []

    for project_path in project_paths:
        for backend in active_backends:
            try:
                session_files = await backend.list_all_session_files_for_project(
                    project_path,
                    lookback_days=config.OPERATIONAL_SESSION_LOOKBACK_DAYS,
                )
            except Exception as exc:
                _record_backend_failure(
                    degraded_messages,
                    backend,
                    project_path,
                    "global recent-session refresh",
                    exc,
                )
                continue
            headers.extend(
                _header_from_session_file(project_path, backend, session_file)
                for session_file in session_files
            )

    await active_store.upsert_headers(headers)
    rows = await active_store.query_global_for_projects(project_paths, limit=limit)
    return RecentSessionsQueryResult(rows=rows, degraded_messages=degraded_messages)


async def get_project_recent_sessions(
    project_path: str,
    backends: list[CodingAgentBackend] | None = None,
    store: RecentSessionsStore | None = None,
    limit: int = SESSION_LIST_LIMIT,
    refresh_on_hit: bool = False,
) -> RecentSessionsQueryResult:
    """Return project rows from store, with one bounded refresh if empty."""
    active_store = _resolve_store(store)
    await active_store.initialize()
    rows = await _hide_subagent_rows(
        active_store,
        await active_store.query_project(project_path, limit=limit),
    )
    if rows:
        if refresh_on_hit:
            _schedule_project_background_refresh(
                project_path,
                refresh_project_sessions(project_path, backends, active_store),
            )
        return RecentSessionsQueryResult(rows=rows, degraded_messages=[])

    refresh_result = await refresh_project_sessions(project_path, backends, active_store)
    rows = await _hide_subagent_rows(
        active_store,
        await active_store.query_project(project_path, limit=limit),
    )
    return RecentSessionsQueryResult(
        rows=rows,
        degraded_messages=refresh_result.degraded_messages,
    )


async def get_global_recent_sessions(
    project_paths: list[str],
    backends: list[CodingAgentBackend] | None = None,
    store: RecentSessionsStore | None = None,
    limit: int = ALL_MODE_SESSION_CANDIDATE_LIMIT,
) -> RecentSessionsQueryResult:
    """Return global rows from store, with one bounded refresh if empty."""
    active_store = _resolve_store(store)
    await active_store.initialize()
    rows = await _hide_subagent_rows(
        active_store,
        await active_store.query_global_for_projects(project_paths, limit=limit),
    )
    if rows:
        return RecentSessionsQueryResult(rows=rows, degraded_messages=[])

    refresh_result = await refresh_global_sessions(
        project_paths,
        backends,
        active_store,
        limit=limit,
    )
    rows = await _hide_subagent_rows(
        active_store,
        await active_store.query_global_for_projects(project_paths, limit=limit),
    )
    return RecentSessionsQueryResult(
        rows=rows,
        degraded_messages=refresh_result.degraded_messages,
    )


def _resolve_store(store: RecentSessionsStore | None) -> RecentSessionsStore:
    return store if store is not None else get_default_store()


def _resolve_backends(
    backends: list[CodingAgentBackend] | None,
) -> list[CodingAgentBackend]:
    return backends if backends is not None else coding_agent_backend.get_all_backends()


async def _hide_subagent_rows(
    store: RecentSessionsStore,
    rows: list[RecentSessionHeader],
) -> list[RecentSessionHeader]:
    visible_rows: list[RecentSessionHeader] = []
    for row in rows:
        if row.backend != BackendName.CODEX:
            visible_rows.append(row)
            continue
        if await is_subagent_session_file(row.file_path):
            await store.mark_missing(row.project_path, row.backend, row.session_id)
            continue
        visible_rows.append(row)
    return visible_rows


def _schedule_project_background_refresh(
    project_path: str,
    coroutine: Coroutine[Any, Any, RecentSessionsQueryResult],
) -> None:
    if (task := _BACKGROUND_PROJECT_REFRESHES.get(project_path)) is not None:
        if not task.done():
            coroutine.close()
            return
    task = asyncio.create_task(coroutine)
    _BACKGROUND_PROJECT_REFRESHES[project_path] = task
    task.add_done_callback(
        lambda done_task: _finish_project_background_refresh(project_path, done_task)
    )


def _finish_project_background_refresh(
    project_path: str,
    task: asyncio.Task[RecentSessionsQueryResult],
) -> None:
    if _BACKGROUND_PROJECT_REFRESHES.get(project_path) is task:
        _BACKGROUND_PROJECT_REFRESHES.pop(project_path, None)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Background recent-session refresh failed", exc_info=True)


def _header_from_session_file(
    project_path: str,
    backend: CodingAgentBackend,
    session_file: SessionFileInfo,
) -> RecentSessionHeader:
    return RecentSessionHeader(
        project_path=project_path,
        backend=backend.name,
        session_id=session_file.session_id,
        file_path=session_file.file_path,
        last_modified_at=session_file.last_modified_at,
        preview=session_file.preview,
    )


def _record_backend_failure(
    degraded_messages: list[str],
    backend: CodingAgentBackend,
    project_path: str,
    operation: str,
    exc: Exception,
) -> None:
    backend_name = backend.name.value
    message = f"{operation} failed for {backend_name} in {project_path}: {exc}"
    logger.warning(message, exc_info=True)
    degraded_messages.append(message)
