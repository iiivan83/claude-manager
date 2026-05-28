"""Integration-style checks for handler/watch coordination."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from claude_manager import session_watcher
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    StopStrategy,
    TerminalStatus,
    UnifiedEvent,
)
from claude_manager.session_manager import ActiveSession


SESSION_ID = "watcher-test-session"
CHAT_ID = 111111
PROJECT_DIR = "/tmp/watcher-project"


class FakeBackend(CodingAgentBackend):
    def __init__(self, name: BackendName) -> None:
        self._name = name
        self.files: list[SessionFileInfo] = []
        self.snapshots: dict[str, SessionFileSnapshot] = {}

    @property
    def name(self) -> BackendName:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name.value

    def compose_subprocess_command_args(
        self,
        session_id: str,
        cwd: str,
        prompt_text: str,
        image_paths: list[str],
    ) -> list[str]:
        return []

    def encode_user_message_for_cli_stdin(
        self,
        prompt_text: str,
        image_paths: list[str],
    ) -> bytes:
        return b""

    def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None:
        return None

    def is_turn_complete_event(self, event: UnifiedEvent) -> bool:
        return False

    def read_session_id_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def locate_session_files_directory_for_project(self, project_dir: str) -> str:
        return project_dir

    async def list_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        return await self.list_all_session_files_for_project(project_dir)

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        del project_dir
        del lookback_days
        return list(self.files)

    async def session_file_exists_for_project(
        self,
        session_id: str,
        project_dir: str,
    ) -> bool:
        return any(info.session_id == session_id for info in self.files)

    async def read_messages_from_session_file(
        self,
        file_path: str,
    ) -> list[SessionMessage]:
        return self.snapshots[file_path].messages

    def text_markers_indicating_empty_response(self) -> frozenset[str]:
        return frozenset({"No response requested."})

    def event_types_meaning_cli_is_busy(self) -> frozenset[str]:
        return frozenset()

    def is_turn_terminal_session_record(self, record: dict[str, object]) -> bool:
        return False

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        return self.snapshots[file_path]

    def is_error_event(self, event: UnifiedEvent) -> bool:
        return False

    def read_error_text_from_event(self, event: UnifiedEvent) -> str | None:
        return None

    def read_terminal_status_from_event(
        self,
        event: UnifiedEvent,
    ) -> TerminalStatus | None:
        return None

    def get_stop_strategy(self) -> StopStrategy:
        return StopStrategy(steps=())


def _file(session_id: str) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=f"/tmp/{session_id}.jsonl",
        last_modified_at=1.0,
        preview="preview",
    )


def _snapshot(*texts: str, raw_count: int | None = None) -> SessionFileSnapshot:
    return SessionFileSnapshot(
        messages=[
            SessionMessage(
                role="assistant",
                text=text,
                timestamp=None,
                is_empty_response=False,
            )
            for text in texts
        ],
        raw_record_count=raw_count if raw_count is not None else len(texts),
        last_record=None,
        is_turn_active=False,
    )


@pytest.fixture(autouse=True)
def _reset_watcher_state() -> None:
    session_watcher._watchers = {}
    session_watcher._callback = None
    session_watcher._get_current_session = None
    yield
    session_watcher._watchers = {}
    session_watcher._callback = None
    session_watcher._get_current_session = None


@pytest.fixture()
def fake_backend() -> FakeBackend:
    backend = FakeBackend(BackendName.CLAUDE)
    backend.files = [_file(SESSION_ID)]
    backend.snapshots[f"/tmp/{SESSION_ID}.jsonl"] = _snapshot(
        "Handler already delivered this",
        raw_count=4,
    )
    return backend


@pytest.mark.asyncio
async def test_pause_handler_resume_cycle_does_not_duplicate_handler_output(
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    callback = AsyncMock()
    codex_backend = FakeBackend(BackendName.CODEX)

    def get_backend(name: BackendName) -> FakeBackend:
        return {
            BackendName.CLAUDE: fake_backend,
            BackendName.CODEX: codex_backend,
        }[name]

    with (
        patch.object(
            session_watcher.coding_agent_backend,
            "get_backend",
            side_effect=get_backend,
        ),
        patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            session_watcher.daily_session_registry,
            "register_session",
            new=AsyncMock(return_value=1),
        ),
        patch.object(
            session_watcher.session_manager,
            "find_chat_by_session_id",
            new=Mock(return_value=CHAT_ID),
        ),
    ):
        session_watcher.pause_session(SESSION_ID, BackendName.CLAUDE)
        await session_watcher.resume_session(SESSION_ID, BackendName.CLAUDE)
        await session_watcher._poll_sessions(
            callback,
            AsyncMock(return_value=ActiveSession(SESSION_ID, BackendName.CLAUDE)),
        )

    callback.assert_not_called()


def test_update_session_id_transfers_pause_for_only_one_backend() -> None:
    claude_backend = FakeBackend(BackendName.CLAUDE)
    codex_backend = FakeBackend(BackendName.CODEX)

    def get_backend(name: BackendName) -> FakeBackend:
        return {
            BackendName.CLAUDE: claude_backend,
            BackendName.CODEX: codex_backend,
        }[name]

    with patch.object(session_watcher.coding_agent_backend, "get_backend", get_backend):
        session_watcher.pause_session("_new_temp", BackendName.CODEX)
        session_watcher.update_session_id(
            "_new_temp",
            "real-session",
            BackendName.CODEX,
        )

        claude_watcher = session_watcher._get_watcher(BackendName.CLAUDE)
        codex_watcher = session_watcher._get_watcher(BackendName.CODEX)

    assert "real-session" in codex_watcher._states
    assert "_new_temp" not in codex_watcher._states
    assert "real-session" not in claude_watcher._states


@pytest.mark.asyncio
async def test_current_session_comparison_uses_backend_pair(
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
    callback = AsyncMock()
    watcher = session_watcher.SessionWatcher(fake_backend)

    with (
        patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            session_watcher.daily_session_registry,
            "register_session",
            new=AsyncMock(return_value=1),
        ),
        patch.object(
            session_watcher.session_manager,
            "find_chat_by_session_id",
            new=Mock(return_value=CHAT_ID),
        ),
    ):
        await watcher.poll_once(
            callback,
            AsyncMock(return_value=ActiveSession(SESSION_ID, BackendName.CODEX)),
        )

    callback.assert_awaited_once()
    assert callback.call_args.args[5] is False
