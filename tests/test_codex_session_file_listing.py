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


def _write_truncated_rollout_file(
    file_path: Path,
    session_id: str,
    project_dir: str,
) -> None:
    """Пишет rollout с валидным session_meta, затем строку, оборванную на 2-байтном UTF-8.

    Воспроизводит файл, который CLI ещё дописывает: хвост оборван посреди 2-байтной
    UTF-8 последовательности (\\xd0 из 'П'), поэтому open(encoding='utf-8').readlines()
    бросает UnicodeDecodeError (это ValueError, НЕ OSError).
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("wb") as file_handle:
        meta_record = {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": project_dir},
        }
        file_handle.write((json.dumps(meta_record) + "\n").encode("utf-8"))
        file_handle.write(b'{"type":"session_meta","payload":{"cwd":"\xd0')


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


@pytest.mark.asyncio()
async def test_codex_project_listing_skips_rollout_with_truncated_multibyte(
    tmp_path: Path,
) -> None:
    """Листинг проекта пропускает битый на лету rollout, отдаёт валидный (P2-24).

    До фикса `_read_project_meta_pair` для битого файла бросал UnicodeDecodeError, а
    `_gather_optional_results_with_concurrency_limit` (gather без return_exceptions)
    ронял ВЕСЬ листинг — валидный файл терялся. После фикса битый проглатывается в
    None, gather исключения не видит.
    """
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/home/ivan/claude-sandbox/demo"
    valid_session_id = "019e7317-83ed-7721-a7e1-4b62e9922582"
    good_file = sessions_root / "good.jsonl"
    bad_file = sessions_root / "bad.jsonl"
    _write_rollout_file(good_file, valid_session_id, project_dir)
    _write_truncated_rollout_file(bad_file, "truncated-session", project_dir)
    sessions_root.mkdir(parents=True, exist_ok=True)

    with patch(
        "claude_manager.codex_session_file_listing._list_rollout_files_blocking",
        return_value=[str(good_file), str(bad_file)],
    ):
        infos = await list_session_file_infos_for_project(
            str(sessions_root),
            project_dir,
        )

    session_ids = {info.session_id for info in infos}
    assert valid_session_id in session_ids
