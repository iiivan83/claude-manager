"""Tests for bounded recent session refresh."""

import asyncio
import json
from pathlib import Path

import pytest

from claude_manager import config
from claude_manager.coding_agent_backend import BackendName, SessionFileInfo
from claude_manager.recent_sessions_refresh import (
    ALL_MODE_SESSION_CANDIDATE_LIMIT,
    get_global_recent_sessions,
    get_project_recent_sessions,
    refresh_project_sessions,
)
from claude_manager.recent_sessions_store import RecentSessionHeader, RecentSessionsStore


class FakeBackend:
    """Backend double that records listing calls."""

    def __init__(
        self,
        name: BackendName,
        files_by_project: dict[str, list[SessionFileInfo]],
    ) -> None:
        self.name = name
        self.display_name = name.value
        self.files_by_project = files_by_project
        self.project_list_calls: list[str] = []
        self.operational_calls: list[tuple[str, int | None]] = []
        self.bulk_calls: list[list[str]] = []

    async def list_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        self.project_list_calls.append(project_dir)
        return self.files_by_project.get(project_dir, [])

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        self.operational_calls.append((project_dir, lookback_days))
        return self.files_by_project.get(project_dir, [])

    async def list_all_session_files_for_projects(
        self,
        project_dirs: list[str],
    ) -> dict[str, list[SessionFileInfo]]:
        self.bulk_calls.append(project_dirs)
        raise AssertionError("bulk listing is forbidden for recent_sessions refresh")


def _file(
    session_id: str,
    mtime: float,
    preview: str = "preview",
) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=f"/sessions/{session_id}.jsonl",
        last_modified_at=mtime,
        preview=preview,
    )


def _write_codex_rollout(
    file_path: Path,
    session_id: str,
    project_path: str,
    *,
    thread_source: str,
) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": project_path,
                    "thread_source": thread_source,
                },
            }
        )
        + "\n",
        "utf-8",
    )


@pytest.mark.asyncio()
async def test_project_query_uses_store_first_without_backend_listing(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    backend = FakeBackend(
        BackendName.CODEX,
        {"/projects/alpha": [_file("from-backend", 2.0)]},
    )

    await refresh_project_sessions(
        project_path="/projects/alpha",
        backends=[backend],
        store=store,
    )
    backend.project_list_calls.clear()

    result = await get_project_recent_sessions(
        project_path="/projects/alpha",
        backends=[backend],
        store=store,
        limit=15,
    )

    assert [row.session_id for row in result.rows] == ["from-backend"]
    assert backend.project_list_calls == []


@pytest.mark.asyncio()
async def test_project_query_hides_cached_codex_subagent_rows(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    project_path = "/projects/alpha"
    user_file = tmp_path / "user.jsonl"
    subagent_file = tmp_path / "subagent.jsonl"
    _write_codex_rollout(user_file, "user", project_path, thread_source="user")
    _write_codex_rollout(
        subagent_file,
        "subagent",
        project_path,
        thread_source="subagent",
    )
    await store.upsert_headers(
        [
            RecentSessionHeader(
                project_path=project_path,
                backend=BackendName.CODEX,
                session_id="subagent",
                file_path=str(subagent_file),
                last_modified_at=20.0,
            ),
            RecentSessionHeader(
                project_path=project_path,
                backend=BackendName.CODEX,
                session_id="user",
                file_path=str(user_file),
                last_modified_at=10.0,
            ),
        ]
    )
    backend = FakeBackend(BackendName.CODEX, {project_path: []})

    result = await get_project_recent_sessions(
        project_path=project_path,
        backends=[backend],
        store=store,
        limit=15,
    )

    assert [row.session_id for row in result.rows] == ["user"]
    assert backend.project_list_calls == []
    assert [row.session_id for row in await store.query_project(project_path)] == ["user"]


@pytest.mark.asyncio()
async def test_project_query_can_refresh_populated_store_in_background(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    backend = FakeBackend(
        BackendName.CODEX,
        {"/projects/alpha": [_file("old", 1.0)]},
    )
    await refresh_project_sessions("/projects/alpha", [backend], store)
    backend.files_by_project["/projects/alpha"] = [
        _file("old", 1.0),
        _file("new", 2.0),
    ]
    backend.project_list_calls.clear()

    result = await get_project_recent_sessions(
        project_path="/projects/alpha",
        backends=[backend],
        store=store,
        limit=15,
        refresh_on_hit=True,
    )

    assert [row.session_id for row in result.rows] == ["old"]
    rows = []
    for _ in range(10):
        rows = await store.query_project("/projects/alpha", limit=15)
        if [row.session_id for row in rows] == ["new", "old"]:
            break
        await asyncio.sleep(0)
    assert [row.session_id for row in rows] == ["new", "old"]


@pytest.mark.asyncio()
async def test_empty_project_query_runs_one_bounded_refresh(tmp_path: Path) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    backend = FakeBackend(
        BackendName.CLAUDE,
        {"/projects/alpha": [_file("s1", 10.0, "alpha task")]},
    )

    result = await get_project_recent_sessions(
        project_path="/projects/alpha",
        backends=[backend],
        store=store,
        limit=15,
    )

    assert [row.session_id for row in result.rows] == ["s1"]
    assert backend.project_list_calls == ["/projects/alpha"]


@pytest.mark.asyncio()
async def test_global_refresh_uses_bounded_per_project_operational_listing(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    backend = FakeBackend(
        BackendName.CODEX,
        {
            "/projects/alpha": [_file("a", 30.0)],
            "/projects/beta": [_file("b", 20.0)],
        },
    )

    result = await get_global_recent_sessions(
        project_paths=["/projects/alpha", "/projects/beta"],
        backends=[backend],
        store=store,
        limit=ALL_MODE_SESSION_CANDIDATE_LIMIT,
    )

    assert [row.session_id for row in result.rows] == ["a", "b"]
    assert backend.operational_calls == [
        ("/projects/alpha", config.OPERATIONAL_SESSION_LOOKBACK_DAYS),
        ("/projects/beta", config.OPERATIONAL_SESSION_LOOKBACK_DAYS),
    ]
    assert backend.bulk_calls == []


@pytest.mark.asyncio()
async def test_global_query_filters_visible_projects_before_limit(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    backend = FakeBackend(BackendName.CLAUDE, {})
    await store.upsert_headers(
        [
            RecentSessionHeader(
                project_path="/projects/hidden",
                backend=BackendName.CLAUDE,
                session_id="hidden",
                file_path="/sessions/hidden.jsonl",
                last_modified_at=99.0,
            ),
            RecentSessionHeader(
                project_path="/projects/alpha",
                backend=BackendName.CLAUDE,
                session_id="visible",
                file_path="/sessions/visible.jsonl",
                last_modified_at=1.0,
            ),
        ]
    )

    result = await get_global_recent_sessions(
        project_paths=["/projects/alpha"],
        backends=[backend],
        store=store,
        limit=1,
    )

    assert [(row.project_path, row.session_id) for row in result.rows] == [
        ("/projects/alpha", "visible"),
    ]
    assert backend.operational_calls == []


@pytest.mark.asyncio()
async def test_project_refresh_updates_preview_when_mtime_changes(
    tmp_path: Path,
) -> None:
    store = RecentSessionsStore(tmp_path / "recent.sqlite3")
    await store.initialize()
    backend = FakeBackend(
        BackendName.CLAUDE,
        {"/projects/alpha": [_file("s1", 1.0, "old")]},
    )

    await refresh_project_sessions("/projects/alpha", [backend], store)
    backend.files_by_project["/projects/alpha"] = [_file("s1", 2.0, "new")]
    await refresh_project_sessions("/projects/alpha", [backend], store)

    rows = await store.query_project("/projects/alpha", limit=30)
    assert rows[0].preview == "new"
    assert rows[0].last_modified_at == 2.0
