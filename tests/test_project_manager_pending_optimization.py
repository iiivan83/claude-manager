"""Tests for project pending collection fast paths."""

import asyncio
from pathlib import Path

from claude_manager import coding_agent_backend, project_manager, unread_buffer
from claude_manager.coding_agent_backend import (
    BackendName,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)


class FakePendingBackend:
    """Small backend double for pending collection tests."""

    def __init__(
        self,
        files: list[SessionFileInfo],
        cursors: dict[str, SessionFileSnapshot],
        snapshots: dict[str, SessionFileSnapshot | Exception],
    ) -> None:
        self.name = BackendName.CODEX
        self.files = files
        self.cursors = cursors
        self.snapshots = snapshots
        self.cursor_calls: list[str] = []
        self.snapshot_calls: list[str] = []
        self.peak_cursor_reads = 0
        self._cursor_reads_in_flight = 0

    async def list_all_session_files_for_project(
        self,
        _project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        del lookback_days
        return self.files

    async def read_session_file_cursor(self, file_path: str) -> SessionFileSnapshot:
        self.cursor_calls.append(file_path)
        self._cursor_reads_in_flight += 1
        self.peak_cursor_reads = max(
            self.peak_cursor_reads,
            self._cursor_reads_in_flight,
        )
        try:
            await asyncio.sleep(0.01)
            return self.cursors[file_path]
        finally:
            self._cursor_reads_in_flight -= 1

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        self.snapshot_calls.append(file_path)
        result = self.snapshots[file_path]
        if isinstance(result, Exception):
            raise result
        return result


def _file(session_id: str, file_path: str, mtime: float) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=file_path,
        last_modified_at=mtime,
        preview="",
    )


def _snapshot(
    *texts: str,
    raw_count: int | None = None,
) -> SessionFileSnapshot:
    messages = [
        SessionMessage(
            role="assistant",
            text=text,
            timestamp=None,
            is_empty_response=False,
        )
        for text in texts
    ]
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_count if raw_count is not None else len(messages),
        last_record=None,
        is_turn_active=False,
    )


async def test_pending_unchanged_mtime_skips_cursor_and_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = str(tmp_path / "session.jsonl")
    backend = FakePendingBackend([_file("session", file_path, 10.0)], {}, {})
    unread_buffer.save_snapshot(
        "session",
        BackendName.CODEX,
        raw_record_count=2,
        last_delivered_idx=0,
        last_modified_at=10.0,
    )
    monkeypatch.setattr(coding_agent_backend, "get_all_backends", lambda: [backend])

    count, pending = await project_manager.collect_pending_messages_for_project(
        str(tmp_path)
    )

    assert count == 0
    assert pending == []
    assert backend.cursor_calls == []
    assert backend.snapshot_calls == []


async def test_pending_changed_mtime_but_same_cursor_skips_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = str(tmp_path / "session.jsonl")
    backend = FakePendingBackend(
        [_file("session", file_path, 11.0)],
        {file_path: _snapshot(raw_count=2)},
        {},
    )
    unread_buffer.save_snapshot(
        "session",
        BackendName.CODEX,
        raw_record_count=2,
        last_delivered_idx=0,
        last_modified_at=10.0,
    )
    monkeypatch.setattr(coding_agent_backend, "get_all_backends", lambda: [backend])

    count, pending = await project_manager.collect_pending_messages_for_project(
        str(tmp_path)
    )

    assert count == 0
    assert pending == []
    assert backend.cursor_calls == [file_path]
    assert backend.snapshot_calls == []


async def test_pending_grown_cursor_reads_snapshot_and_delivers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_path = str(tmp_path / "session.jsonl")
    backend = FakePendingBackend(
        [_file("session", file_path, 11.0)],
        {file_path: _snapshot(raw_count=3)},
        {file_path: _snapshot("old", "new", raw_count=3)},
    )
    # Валидное parsed-состояние (индекс 0 уже доставлен) — не cursor-only
    # сентинель, поэтому применяется срез-семантика по last_delivered_idx.
    unread_buffer.save_snapshot(
        "session",
        BackendName.CODEX,
        raw_record_count=1,
        last_delivered_idx=0,
        last_modified_at=10.0,
        parsed_message_count=1,
    )
    monkeypatch.setattr(coding_agent_backend, "get_all_backends", lambda: [backend])

    count, pending = await project_manager.collect_pending_messages_for_project(
        str(tmp_path)
    )

    assert count == 1
    assert pending[0].text == "new"
    assert backend.cursor_calls == [file_path]
    assert backend.snapshot_calls == [file_path]


async def test_pending_changed_files_are_checked_concurrently_and_errors_are_kept_local(
    tmp_path: Path,
    monkeypatch,
) -> None:
    files = []
    cursors = {}
    snapshots: dict[str, SessionFileSnapshot | Exception] = {}
    for index in range(8):
        session_id = f"session-{index}"
        file_path = str(tmp_path / f"{session_id}.jsonl")
        files.append(_file(session_id, file_path, 20.0))
        cursors[file_path] = _snapshot(raw_count=2)
        snapshots[file_path] = _snapshot("old", f"new {index}", raw_count=2)
        # Валидное parsed-состояние (индекс 0 уже доставлен) — не cursor-only
        # сентинель, поэтому применяется срез-семантика по last_delivered_idx.
        unread_buffer.save_snapshot(
            session_id,
            BackendName.CODEX,
            raw_record_count=1,
            last_delivered_idx=0,
            last_modified_at=10.0,
            parsed_message_count=1,
        )
    failing_path = files[0].file_path
    snapshots[failing_path] = RuntimeError("broken file")
    backend = FakePendingBackend(files, cursors, snapshots)
    monkeypatch.setattr(coding_agent_backend, "get_all_backends", lambda: [backend])

    count, pending = await project_manager.collect_pending_messages_for_project(
        str(tmp_path)
    )

    assert count == 7
    assert {item.text for item in pending} == {f"new {index}" for index in range(1, 8)}
    assert backend.peak_cursor_reads > 1
