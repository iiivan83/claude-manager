"""Tests for Codex rollout-file discovery helpers."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_manager.codex_session_file_listing import (
    list_session_file_infos_for_project,
    session_file_exists_for_project,
)


def _write_rollout_file(
    file_path: Path,
    session_id: str,
    project_dir: str,
    *,
    thread_source: str = "user",
) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "timestamp": "2026-05-29T09:36:21Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": project_dir, "thread_source": thread_source},
        }
    ]
    file_path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        "utf-8",
    )


@pytest.mark.asyncio()
async def test_session_file_exists_uses_uuid_date_before_full_scan(
    tmp_path: Path,
) -> None:
    """Exact UUIDv7 lookup checks the likely day directory before full history."""
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/home/ivan/claude-sandbox/bloger"
    session_id = "019e7317-83ed-7721-a7e1-4b62e9922582"
    rollout_file = (
        sessions_root
        / "2026"
        / "05"
        / "29"
        / f"rollout-2026-05-29T09-36-21-{session_id}.jsonl"
    )
    _write_rollout_file(rollout_file, session_id, project_dir)

    with patch(
        "claude_manager.codex_session_file_listing._list_all_rollout_files_blocking",
        side_effect=AssertionError("full scan should not run"),
    ) as full_scan:
        exists = await session_file_exists_for_project(
            str(sessions_root),
            session_id,
            project_dir,
        )

    assert exists is True
    full_scan.assert_not_called()


@pytest.mark.asyncio()
async def test_list_session_files_uses_two_day_hotfix_window(
    tmp_path: Path,
) -> None:
    """User-facing Codex session list uses the temporary two-day window."""
    sessions_root = tmp_path / ".codex" / "sessions"
    sessions_root.mkdir(parents=True)

    with patch(
        "claude_manager.codex_session_file_listing._list_rollout_files_blocking",
        return_value=[],
    ) as list_rollout_files:
        infos = await list_session_file_infos_for_project(
            str(sessions_root),
            "/home/ivan/claude-sandbox/claude_manager",
        )

    assert infos == []
    assert list_rollout_files.call_count == 1
    assert list_rollout_files.call_args.args[1] == 2


@pytest.mark.asyncio()
async def test_list_session_files_excludes_codex_subagents(tmp_path: Path) -> None:
    """Codex subagent rollouts are not user-facing session-list candidates."""
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/home/ivan/claude-sandbox/claude_manager"
    user_file = sessions_root / "user.jsonl"
    subagent_file = sessions_root / "subagent.jsonl"
    _write_rollout_file(user_file, "user-session", project_dir)
    _write_rollout_file(
        subagent_file,
        "subagent-session",
        project_dir,
        thread_source="subagent",
    )
    sessions_root.mkdir(parents=True, exist_ok=True)

    with patch(
        "claude_manager.codex_session_file_listing._list_rollout_files_blocking",
        return_value=[str(user_file), str(subagent_file)],
    ):
        infos = await list_session_file_infos_for_project(
            str(sessions_root),
            project_dir,
        )

    assert [info.session_id for info in infos] == ["user-session"]
