"""Tests for persistent recent session headers."""

import sqlite3
from pathlib import Path

import pytest

from claude_manager.coding_agent_backend import BackendName
from claude_manager.recent_sessions_store import (
    CURSOR_SCOPE_ALL,
    CURSOR_SCOPE_PROJECT,
    SCHEMA_VERSION,
    RecentSessionCursorState,
    RecentSessionHeader,
    RecentSessionsStore,
    RecentSessionsStoreError,
)


PROJECT_A = "/projects/alpha"
PROJECT_B = "/projects/beta"


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "recent_sessions.sqlite3"


def _header(
    session_id: str,
    *,
    project_path: str = PROJECT_A,
    backend: BackendName = BackendName.CLAUDE,
    mtime: float = 1.0,
    preview: str = "preview",
) -> RecentSessionHeader:
    return RecentSessionHeader(
        project_path=project_path,
        backend=backend,
        session_id=session_id,
        file_path=f"/sessions/{backend.value}/{session_id}.jsonl",
        last_modified_at=mtime,
        preview=preview,
        raw_record_count=3,
        cursor_record_count=3,
    )


@pytest.mark.asyncio()
async def test_schema_init_is_idempotent(tmp_path: Path) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))

    await store.initialize()
    await store.initialize()

    rows = await store.query_project(PROJECT_A, limit=30)
    assert rows == []


@pytest.mark.asyncio()
async def test_initialize_writes_schema_version_meta(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    store = RecentSessionsStore(db_path)

    await store.initialize()

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM recent_sessions_meta WHERE key = 'schema_version'"
        ).fetchone()

    assert row == (SCHEMA_VERSION,)


@pytest.mark.asyncio()
async def test_upsert_updates_existing_backend_aware_row(tmp_path: Path) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()

    await store.upsert_headers([_header("same", mtime=1.0, preview="old")])
    await store.upsert_headers([_header("same", mtime=2.0, preview="new")])

    rows = await store.query_project(PROJECT_A, limit=30)
    assert len(rows) == 1
    assert rows[0].session_id == "same"
    assert rows[0].last_modified_at == 2.0
    assert rows[0].preview == "new"


@pytest.mark.asyncio()
async def test_same_session_id_in_different_backends_does_not_collide(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()

    await store.upsert_headers(
        [
            _header("shared", backend=BackendName.CLAUDE, mtime=1.0),
            _header("shared", backend=BackendName.CODEX, mtime=2.0),
        ]
    )

    rows = await store.query_project(PROJECT_A, limit=30)
    assert [(row.backend, row.session_id) for row in rows] == [
        (BackendName.CODEX, "shared"),
        (BackendName.CLAUDE, "shared"),
    ]


@pytest.mark.asyncio()
async def test_query_uses_stable_sort_order(tmp_path: Path) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()

    await store.upsert_headers(
        [
            _header("b", backend=BackendName.CODEX, mtime=10.0),
            _header("a", backend=BackendName.CLAUDE, mtime=10.0),
            _header("new", backend=BackendName.CLAUDE, mtime=20.0),
        ]
    )

    rows = await store.query_project(PROJECT_A, limit=30)
    assert [row.session_id for row in rows] == ["new", "a", "b"]


@pytest.mark.asyncio()
async def test_prune_keeps_30_newest_rows_per_project_across_backends(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()

    await store.upsert_headers(
        [
            _header(
                f"s{i:02}",
                backend=BackendName.CLAUDE if i % 2 else BackendName.CODEX,
                mtime=float(i),
            )
            for i in range(31)
        ]
    )

    rows = await store.query_project(PROJECT_A, limit=40)
    assert len(rows) == 30
    assert rows[0].session_id == "s30"
    assert rows[-1].session_id == "s01"


@pytest.mark.asyncio()
async def test_global_query_sorts_across_projects_and_applies_cap(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()

    await store.upsert_headers(
        [
            _header("a", project_path=PROJECT_A, mtime=1.0),
            _header("b", project_path=PROJECT_B, mtime=3.0),
            _header("c", project_path=PROJECT_A, mtime=2.0),
        ]
    )

    rows = await store.query_global(limit=2)
    assert [(row.project_path, row.session_id) for row in rows] == [
        (PROJECT_B, "b"),
        (PROJECT_A, "c"),
    ]
    filtered_rows = await store.query_global_for_projects([PROJECT_A], limit=2)
    assert [(row.project_path, row.session_id) for row in filtered_rows] == [
        (PROJECT_A, "c"),
        (PROJECT_A, "a"),
    ]


@pytest.mark.asyncio()
async def test_cursor_state_survives_header_pruning(tmp_path: Path) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()
    await store.upsert_cursor_state(
        RecentSessionCursorState(
            project_path=PROJECT_A,
            backend=BackendName.CLAUDE,
            session_id="s00",
            file_path="/sessions/s00.jsonl",
            cursor_scope=CURSOR_SCOPE_ALL,
            raw_record_count=5,
            last_delivered_idx=4,
            last_modified_at=1.0,
        )
    )

    await store.upsert_headers([_header(f"s{i:02}", mtime=float(i)) for i in range(31)])

    assert await store.get_cursor_state(
        PROJECT_A, BackendName.CLAUDE, "s00", CURSOR_SCOPE_ALL
    ) == RecentSessionCursorState(
        project_path=PROJECT_A,
        backend=BackendName.CLAUDE,
        session_id="s00",
        file_path="/sessions/s00.jsonl",
        cursor_scope=CURSOR_SCOPE_ALL,
        raw_record_count=5,
        last_delivered_idx=4,
        last_modified_at=1.0,
    )
    assert (
        await store.get_cursor_state(
            PROJECT_A, BackendName.CLAUDE, "s00", CURSOR_SCOPE_PROJECT
        )
        is None
    )


@pytest.mark.asyncio()
async def test_mark_missing_hides_row_without_deleting_cursor(tmp_path: Path) -> None:
    store = RecentSessionsStore(_db_path(tmp_path))
    await store.initialize()
    await store.upsert_headers([_header("missing")])
    await store.upsert_cursor_state(
        RecentSessionCursorState(
            project_path=PROJECT_A,
            backend=BackendName.CLAUDE,
            session_id="missing",
            file_path="/sessions/missing.jsonl",
            cursor_scope=CURSOR_SCOPE_ALL,
            raw_record_count=3,
            last_delivered_idx=2,
            last_modified_at=1.0,
        )
    )

    await store.mark_missing(PROJECT_A, BackendName.CLAUDE, "missing")

    assert await store.query_project(PROJECT_A, limit=30) == []
    assert (
        await store.get_cursor_state(
            PROJECT_A, BackendName.CLAUDE, "missing", CURSOR_SCOPE_ALL
        )
        is not None
    )


@pytest.mark.asyncio()
async def test_public_methods_convert_sqlite_errors(tmp_path: Path) -> None:
    store = RecentSessionsStore(tmp_path)

    with pytest.raises(RecentSessionsStoreError):
        await store.initialize()
