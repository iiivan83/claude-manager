"""Tests for the Codex operational session-file index."""

import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from claude_manager import codex_session_index


TODAY = date(2026, 5, 30)


@pytest.fixture(autouse=True)
def _clear_codex_index_cache() -> None:
    codex_session_index.clear_cache()
    yield
    codex_session_index.clear_cache()


def _write_rollout_file(
    sessions_root: Path,
    session_date: date,
    session_id: str,
    project_dir: str,
    *,
    mtime: float | None = None,
    thread_source: str = "user",
) -> Path:
    file_path = (
        sessions_root
        / f"{session_date:%Y}"
        / f"{session_date:%m}"
        / f"{session_date:%d}"
        / f"rollout-{session_date:%Y-%m-%d}T01-02-03-{session_id}.jsonl"
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": project_dir,
                    "thread_source": thread_source,
                },
            }
        )
        + "\n",
        "utf-8",
    )
    if mtime is not None:
        os.utime(file_path, (mtime, mtime))
    return file_path


def _write_truncated_rollout_file(
    sessions_root: Path,
    session_date: date,
    session_id: str,
    project_dir: str,
) -> Path:
    """Пишет rollout с валидным session_meta, затем строку, оборванную на 2-байтном UTF-8.

    Воспроизводит файл, который CLI ещё дописывает: хвост оборван посреди 2-байтной
    UTF-8 последовательности (\\xd0 из 'П'), поэтому open(encoding='utf-8').readlines()
    бросает UnicodeDecodeError (это ValueError, НЕ OSError).
    """
    file_path = (
        sessions_root
        / f"{session_date:%Y}"
        / f"{session_date:%m}"
        / f"{session_date:%d}"
        / f"rollout-{session_date:%Y-%m-%d}T01-02-04-{session_id}.jsonl"
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("wb") as file_handle:
        meta_record = {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": project_dir},
        }
        file_handle.write((json.dumps(meta_record) + "\n").encode("utf-8"))
        file_handle.write(b'{"type":"session_meta","payload":{"cwd":"\xd0')
    return file_path


async def test_index_groups_rollouts_by_project(tmp_path: Path) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_a = "/projects/a"
    project_b = "/projects/b"
    _write_rollout_file(sessions_root, TODAY, "session-a", project_a)
    _write_rollout_file(sessions_root, TODAY, "session-b", project_b)

    infos_a = await codex_session_index.list_project_session_file_infos(
        str(sessions_root),
        project_a,
        lookback_days=4,
        today=TODAY,
    )
    infos_b = await codex_session_index.list_project_session_file_infos(
        str(sessions_root),
        project_b,
        lookback_days=4,
        today=TODAY,
    )

    assert [info.session_id for info in infos_a] == ["session-a"]
    assert [info.session_id for info in infos_b] == ["session-b"]


async def test_index_excludes_codex_subagent_rollouts(tmp_path: Path) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/projects/a"
    _write_rollout_file(sessions_root, TODAY, "user-session", project_dir)
    _write_rollout_file(
        sessions_root,
        TODAY,
        "subagent-session",
        project_dir,
        thread_source="subagent",
    )

    infos = await codex_session_index.list_project_session_file_infos(
        str(sessions_root),
        project_dir,
        lookback_days=4,
        today=TODAY,
    )

    assert [info.session_id for info in infos] == ["user-session"]


async def test_codex_index_build_skips_rollout_with_truncated_multibyte(
    tmp_path: Path,
) -> None:
    """Построение индекса пропускает битый на лету rollout, индексирует валидный (P2-24).

    До фикса `_read_indexed_rollout` для битого файла бросал UnicodeDecodeError, а
    `_gather_optional_factories_with_concurrency_limit` (gather без return_exceptions)
    ронял ВЕСЬ индекс. После фикса битый проглатывается в None, gather не видит исключения.
    """
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/projects/a"
    _write_rollout_file(sessions_root, TODAY, "valid-session", project_dir)
    _write_truncated_rollout_file(sessions_root, TODAY, "truncated-session", project_dir)

    infos = await codex_session_index.list_project_session_file_infos(
        str(sessions_root),
        project_dir,
        lookback_days=4,
        today=TODAY,
    )

    session_ids = {info.session_id for info in infos}
    assert "valid-session" in session_ids


async def test_index_keeps_only_sliding_lookback_window(tmp_path: Path) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/projects/a"
    _write_rollout_file(sessions_root, TODAY - timedelta(days=3), "new", project_dir)
    _write_rollout_file(sessions_root, TODAY - timedelta(days=4), "old", project_dir)

    infos = await codex_session_index.list_project_session_file_infos(
        str(sessions_root),
        project_dir,
        lookback_days=4,
        today=TODAY,
    )

    assert [info.session_id for info in infos] == ["new"]


async def test_repeated_calls_reuse_index_without_meta_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_a = "/projects/a"
    project_b = "/projects/b"
    _write_rollout_file(sessions_root, TODAY, "session-a", project_a)
    _write_rollout_file(sessions_root, TODAY, "session-b", project_b)
    original_read = codex_session_index._read_session_meta_record_blocking
    read_count = 0

    def tracking_read(file_path: str) -> dict[str, object] | None:
        nonlocal read_count
        read_count += 1
        return original_read(file_path)

    monkeypatch.setattr(
        codex_session_index,
        "_read_session_meta_record_blocking",
        tracking_read,
    )

    await codex_session_index.list_project_session_file_infos(
        str(sessions_root), project_a, lookback_days=4, today=TODAY,
    )
    await codex_session_index.list_project_session_file_infos(
        str(sessions_root), project_b, lookback_days=4, today=TODAY,
    )

    assert read_count == 2


async def test_new_rollout_file_changes_directory_signature_and_rebuilds(
    tmp_path: Path,
) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/projects/a"
    _write_rollout_file(sessions_root, TODAY, "first", project_dir)

    first_infos = await codex_session_index.list_project_session_file_infos(
        str(sessions_root), project_dir, lookback_days=4, today=TODAY,
    )
    _write_rollout_file(sessions_root, TODAY, "second", project_dir)
    second_infos = await codex_session_index.list_project_session_file_infos(
        str(sessions_root), project_dir, lookback_days=4, today=TODAY,
    )

    assert [info.session_id for info in first_infos] == ["first"]
    assert {info.session_id for info in second_infos} == {"first", "second"}


async def test_returned_project_files_refresh_mtime_without_meta_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/projects/a"
    file_path = _write_rollout_file(
        sessions_root, TODAY, "session-a", project_dir, mtime=1000.0,
    )
    original_read = codex_session_index._read_session_meta_record_blocking
    read_count = 0

    def tracking_read(path: str) -> dict[str, object] | None:
        nonlocal read_count
        read_count += 1
        return original_read(path)

    monkeypatch.setattr(
        codex_session_index,
        "_read_session_meta_record_blocking",
        tracking_read,
    )

    await codex_session_index.list_project_session_file_infos(
        str(sessions_root), project_dir, lookback_days=4, today=TODAY,
    )
    os.utime(file_path, (2000.0, 2000.0))
    refreshed_infos = await codex_session_index.list_project_session_file_infos(
        str(sessions_root), project_dir, lookback_days=4, today=TODAY,
    )

    assert read_count == 1
    assert refreshed_infos[0].last_modified_at == 2000.0


async def test_known_session_refresh_updates_one_file(tmp_path: Path) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_dir = "/projects/a"
    session_id = "019e7317-83ed-7721-a7e1-4b62e9922582"
    file_path = (
        sessions_root
        / "2026"
        / "05"
        / "29"
        / f"rollout-2026-05-29T09-36-21-{session_id}.jsonl"
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": session_id, "cwd": project_dir},
            }
        )
        + "\n",
        "utf-8",
    )
    os.utime(file_path, (3000.0, 3000.0))

    refreshed = await codex_session_index.refresh_known_session(
        str(sessions_root),
        session_id,
        project_dir=project_dir,
        today=TODAY,
    )

    assert refreshed is not None
    assert refreshed.session_id == session_id
    assert refreshed.file_path == str(file_path)
    assert refreshed.last_modified_at == 3000.0


async def test_concurrent_callers_share_one_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions_root = tmp_path / ".codex" / "sessions"
    project_a = "/projects/a"
    project_b = "/projects/b"
    _write_rollout_file(sessions_root, TODAY, "session-a", project_a)
    _write_rollout_file(sessions_root, TODAY, "session-b", project_b)
    original_read = codex_session_index._read_indexed_rollout
    read_count = 0

    async def tracking_read(file_path: str):
        nonlocal read_count
        read_count += 1
        await asyncio.sleep(0.01)
        return await original_read(file_path)

    monkeypatch.setattr(codex_session_index, "_read_indexed_rollout", tracking_read)

    await asyncio.gather(
        codex_session_index.list_project_session_file_infos(
            str(sessions_root), project_a, lookback_days=4, today=TODAY,
        ),
        codex_session_index.list_project_session_file_infos(
            str(sessions_root), project_b, lookback_days=4, today=TODAY,
        ),
    )

    assert read_count == 2
