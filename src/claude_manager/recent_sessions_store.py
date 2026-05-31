"""Persistent recent session header storage."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_manager.coding_agent_backend import BackendName


RECENT_SESSIONS_FILENAME = "recent_sessions.sqlite3"
DEFAULT_DB_PATH = (
    Path.home() / ".local" / "state" / "claude-manager" / RECENT_SESSIONS_FILENAME
)
SCHEMA_VERSION = "1"
PROJECT_STORAGE_LIMIT = 30
CURSOR_SCOPE_PROJECT = "project"
CURSOR_SCOPE_ALL = "all"
_BUSY_TIMEOUT_MS = 1000


@dataclass(frozen=True)
class RecentSessionHeader:
    """One persisted session-list candidate."""

    project_path: str
    backend: BackendName
    session_id: str
    file_path: str
    last_modified_at: float
    preview: str = ""
    raw_record_count: int | None = None
    cursor_record_count: int | None = None
    file_missing: bool = False
    refresh_status: str = "ok"
    stale_reason: str = ""


@dataclass(frozen=True)
class RecentSessionCursorState:
    """Independent cursor state that is not pruned with header rows."""

    project_path: str
    backend: BackendName
    session_id: str
    file_path: str
    cursor_scope: str
    raw_record_count: int | None
    last_delivered_idx: int | None
    last_modified_at: float | None


class RecentSessionsStoreError(Exception):
    """Raised for degraded persistent-store operations."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recent_sessions (
    project_path TEXT NOT NULL,
    backend TEXT NOT NULL,
    session_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    last_modified_at REAL NOT NULL,
    preview TEXT NOT NULL DEFAULT '',
    raw_record_count INTEGER,
    cursor_record_count INTEGER,
    file_missing INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_refreshed_at TEXT NOT NULL,
    refresh_status TEXT NOT NULL DEFAULT 'ok',
    stale_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_path, backend, session_id)
);
CREATE INDEX IF NOT EXISTS idx_recent_sessions_project_mtime
ON recent_sessions (project_path, last_modified_at DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_recent_sessions_global_mtime
ON recent_sessions (last_modified_at DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_recent_sessions_file_path
ON recent_sessions (file_path);
CREATE TABLE IF NOT EXISTS session_cursor_state (
    project_path TEXT NOT NULL,
    backend TEXT NOT NULL,
    session_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    raw_record_count INTEGER,
    last_delivered_idx INTEGER,
    last_modified_at REAL,
    cursor_scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_path, backend, session_id, cursor_scope)
);
CREATE INDEX IF NOT EXISTS idx_session_cursor_state_updated_at
ON session_cursor_state (updated_at DESC);
CREATE TABLE IF NOT EXISTS recent_sessions_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_UPSERT_HEADER_SQL = """
INSERT INTO recent_sessions (
    project_path, backend, session_id, file_path, last_modified_at, preview,
    raw_record_count, cursor_record_count, file_missing, created_at, updated_at,
    last_refreshed_at, refresh_status, stale_reason
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_path, backend, session_id) DO UPDATE SET
    file_path = excluded.file_path,
    last_modified_at = excluded.last_modified_at,
    preview = excluded.preview,
    raw_record_count = excluded.raw_record_count,
    cursor_record_count = excluded.cursor_record_count,
    file_missing = excluded.file_missing,
    updated_at = excluded.updated_at,
    last_refreshed_at = excluded.last_refreshed_at,
    refresh_status = excluded.refresh_status,
    stale_reason = excluded.stale_reason
"""
_ORDER_BY_RECENT = (
    "ORDER BY last_modified_at DESC, updated_at DESC, backend ASC, session_id ASC"
)


class RecentSessionsStore:
    """Async facade around SQLite recent session storage."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize schema idempotently."""
        await self._to_thread(self._initialize_blocking)

    async def upsert_headers(self, headers: list[RecentSessionHeader]) -> None:
        """Insert or update headers and prune each touched project to 30 rows."""
        if not headers:
            return
        async with self._write_lock:
            await self._to_thread(self._upsert_headers_blocking, headers)

    async def query_project(
        self,
        project_path: str,
        limit: int = PROJECT_STORAGE_LIMIT,
    ) -> list[RecentSessionHeader]:
        """Return active rows for one project by stable recent order."""
        return await self._to_thread(
            self._query_project_blocking, project_path, _normalized_limit(limit)
        )

    async def query_global(self, limit: int) -> list[RecentSessionHeader]:
        """Return active rows across projects by stable recent order."""
        return await self._to_thread(self._query_global_blocking, _normalized_limit(limit))

    async def query_global_for_projects(
        self,
        project_paths: list[str],
        limit: int,
    ) -> list[RecentSessionHeader]:
        """Return active global rows limited to selected projects."""
        if not project_paths:
            return []
        return await self._to_thread(
            self._query_global_for_projects_blocking,
            list(dict.fromkeys(project_paths)),
            _normalized_limit(limit),
        )

    async def mark_missing(
        self,
        project_path: str,
        backend: BackendName,
        session_id: str,
    ) -> None:
        """Hide one header row from active queries while preserving cursor state."""
        async with self._write_lock:
            await self._to_thread(
                self._mark_missing_blocking, project_path, backend, session_id
            )

    async def upsert_cursor_state(self, state: RecentSessionCursorState) -> None:
        """Persist independent cursor state."""
        async with self._write_lock:
            await self._to_thread(self._upsert_cursor_state_blocking, state)

    async def get_cursor_state(
        self,
        project_path: str,
        backend: BackendName,
        session_id: str,
        cursor_scope: str,
    ) -> RecentSessionCursorState | None:
        """Return independent cursor state for one scope."""
        return await self._to_thread(
            self._get_cursor_state_blocking,
            project_path,
            backend,
            session_id,
            cursor_scope,
        )

    async def _to_thread(self, func: Any, *args: object) -> Any:
        try:
            return await asyncio.to_thread(func, *args)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise RecentSessionsStoreError(str(exc)) from exc

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize_blocking(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection, connection:
            connection.executescript(_SCHEMA_SQL)
            connection.execute(
                """
                INSERT INTO recent_sessions_meta (key, value, updated_at)
                VALUES ('schema_version', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (SCHEMA_VERSION, _utc_now()),
            )

    def _upsert_headers_blocking(self, headers: list[RecentSessionHeader]) -> None:
        now = _utc_now()
        with self._connection() as connection, connection:
            connection.executemany(
                _UPSERT_HEADER_SQL, [_header_params(header, now) for header in headers]
            )
            for project_path in {header.project_path for header in headers}:
                self._prune_project(connection, project_path)

    def _query_project_blocking(
        self, project_path: str, limit: int
    ) -> list[RecentSessionHeader]:
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM recent_sessions
                WHERE project_path = ? AND file_missing = 0
                {_ORDER_BY_RECENT}
                LIMIT ?
                """,
                (project_path, limit),
            ).fetchall()
        return [_row_to_header(row) for row in rows]

    def _query_global_blocking(self, limit: int) -> list[RecentSessionHeader]:
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM recent_sessions
                WHERE file_missing = 0
                {_ORDER_BY_RECENT}
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_header(row) for row in rows]

    def _query_global_for_projects_blocking(
        self,
        project_paths: list[str],
        limit: int,
    ) -> list[RecentSessionHeader]:
        placeholders = ", ".join("?" for _ in project_paths)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM recent_sessions
                WHERE file_missing = 0 AND project_path IN ({placeholders})
                {_ORDER_BY_RECENT}
                LIMIT ?
                """,
                (*project_paths, limit),
            ).fetchall()
        return [_row_to_header(row) for row in rows]

    def _mark_missing_blocking(
        self, project_path: str, backend: BackendName, session_id: str
    ) -> None:
        now = _utc_now()
        with self._connection() as connection, connection:
            connection.execute(
                """
                UPDATE recent_sessions
                SET file_missing = 1, updated_at = ?, last_refreshed_at = ?,
                    refresh_status = 'missing', stale_reason = 'file_missing'
                WHERE project_path = ? AND backend = ? AND session_id = ?
                """,
                (now, now, project_path, backend.value, session_id),
            )

    def _upsert_cursor_state_blocking(self, state: RecentSessionCursorState) -> None:
        now = _utc_now()
        with self._connection() as connection, connection:
            connection.execute(
                """
                INSERT INTO session_cursor_state (
                    project_path, backend, session_id, file_path, raw_record_count,
                    last_delivered_idx, last_modified_at, cursor_scope, created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_path, backend, session_id, cursor_scope)
                DO UPDATE SET
                    file_path = excluded.file_path,
                    raw_record_count = excluded.raw_record_count,
                    last_delivered_idx = excluded.last_delivered_idx,
                    last_modified_at = excluded.last_modified_at,
                    updated_at = excluded.updated_at
                """,
                (
                    state.project_path,
                    state.backend.value,
                    state.session_id,
                    state.file_path,
                    state.raw_record_count,
                    state.last_delivered_idx,
                    state.last_modified_at,
                    state.cursor_scope,
                    now,
                    now,
                ),
            )

    def _get_cursor_state_blocking(
        self,
        project_path: str,
        backend: BackendName,
        session_id: str,
        cursor_scope: str,
    ) -> RecentSessionCursorState | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM session_cursor_state
                WHERE project_path = ? AND backend = ?
                    AND session_id = ? AND cursor_scope = ?
                """,
                (project_path, backend.value, session_id, cursor_scope),
            ).fetchone()
        return _row_to_cursor_state(row) if row is not None else None

    def _prune_project(
        self, connection: sqlite3.Connection, project_path: str
    ) -> None:
        connection.execute(
            f"""
            DELETE FROM recent_sessions
            WHERE project_path = ?
              AND rowid NOT IN (
                  SELECT rowid FROM recent_sessions
                  WHERE project_path = ?
                  {_ORDER_BY_RECENT}
                  LIMIT ?
              )
            """,
            (project_path, project_path, PROJECT_STORAGE_LIMIT),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_limit(limit: int) -> int:
    return max(0, int(limit))


def _header_params(header: RecentSessionHeader, now: str) -> tuple[object, ...]:
    return (
        header.project_path,
        header.backend.value,
        header.session_id,
        header.file_path,
        header.last_modified_at,
        header.preview,
        header.raw_record_count,
        header.cursor_record_count,
        int(header.file_missing),
        now,
        now,
        now,
        header.refresh_status,
        header.stale_reason,
    )


def _row_to_header(row: sqlite3.Row) -> RecentSessionHeader:
    return RecentSessionHeader(
        project_path=row["project_path"],
        backend=BackendName(row["backend"]),
        session_id=row["session_id"],
        file_path=row["file_path"],
        last_modified_at=row["last_modified_at"],
        preview=row["preview"],
        raw_record_count=row["raw_record_count"],
        cursor_record_count=row["cursor_record_count"],
        file_missing=bool(row["file_missing"]),
        refresh_status=row["refresh_status"],
        stale_reason=row["stale_reason"],
    )


def _row_to_cursor_state(row: sqlite3.Row) -> RecentSessionCursorState:
    return RecentSessionCursorState(
        project_path=row["project_path"],
        backend=BackendName(row["backend"]),
        session_id=row["session_id"],
        file_path=row["file_path"],
        cursor_scope=row["cursor_scope"],
        raw_record_count=row["raw_record_count"],
        last_delivered_idx=row["last_delivered_idx"],
        last_modified_at=row["last_modified_at"],
    )
