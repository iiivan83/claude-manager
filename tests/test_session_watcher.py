"""Tests for backend-aware session file monitoring."""

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
from claude_manager.daily_session_registry import DailySessionEntry
from claude_manager.session_manager import ActiveSession
from claude_manager.session_watcher import _is_empty_response


TEST_CHAT_ID = 12345
PROJECT_DIR = "/fake/project"


class FakeBackend(CodingAgentBackend):
    """Configurable backend adapter used by watcher unit tests."""

    def __init__(self, name: BackendName) -> None:
        self._name = name
        self.files: list[SessionFileInfo] = []
        self.snapshots: dict[str, SessionFileSnapshot] = {}
        self.list_calls: list[str] = []
        self.list_lookback_history: list[int | None] = []
        self.snapshot_calls: list[str] = []

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
        self.list_calls.append(project_dir)
        self.list_lookback_history.append(lookback_days)
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
        self.snapshot_calls.append(file_path)
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


def _file(session_id: str, file_path: str | None = None) -> SessionFileInfo:
    return SessionFileInfo(
        session_id=session_id,
        file_path=file_path or f"/tmp/{session_id}.jsonl",
        last_modified_at=1.0,
        preview="preview",
    )


def _snapshot(
    *texts: str,
    raw_count: int | None = None,
    is_turn_active: bool = False,
) -> SessionFileSnapshot:
    messages = [
        SessionMessage(
            role="assistant",
            text=text,
            timestamp=None,
            is_empty_response=_is_empty_response(text),
        )
        for text in texts
    ]
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_count if raw_count is not None else len(messages),
        last_record=None,
        is_turn_active=is_turn_active,
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


class TestEmptyResponseDetection:
    """Verifies the deliverability filter rejects empty and no-response markers."""

    def test_empty_response_markers(self) -> None:
        assert _is_empty_response("") is True
        assert _is_empty_response(" No response requested. ") is True
        assert _is_empty_response("OK") is False


class TestBackendAwareWatcherInstances:
    def test_facade_routes_state_to_the_requested_backend(self) -> None:
        claude_backend = FakeBackend(BackendName.CLAUDE)
        codex_backend = FakeBackend(BackendName.CODEX)

        def get_backend(name: BackendName) -> FakeBackend:
            return {
                BackendName.CLAUDE: claude_backend,
                BackendName.CODEX: codex_backend,
            }[name]

        with patch.object(session_watcher.coding_agent_backend, "get_backend", get_backend):
            session_watcher.pause_session("same-session-id", BackendName.CLAUDE)

            claude_watcher = session_watcher._get_watcher(BackendName.CLAUDE)
            codex_watcher = session_watcher._get_watcher(BackendName.CODEX)

        assert claude_watcher is not codex_watcher
        assert claude_watcher.is_session_paused("same-session-id") is True
        assert codex_watcher.is_session_paused("same-session-id") is False

    @pytest.mark.asyncio
    async def test_resume_and_update_session_id_are_backend_scoped(self) -> None:
        claude_backend = FakeBackend(BackendName.CLAUDE)
        codex_backend = FakeBackend(BackendName.CODEX)
        codex_backend.files = [_file("real-id")]
        codex_backend.snapshots["/tmp/real-id.jsonl"] = _snapshot(
            "already delivered",
            raw_count=3,
        )

        def get_backend(name: BackendName) -> FakeBackend:
            return {
                BackendName.CLAUDE: claude_backend,
                BackendName.CODEX: codex_backend,
            }[name]

        with patch.object(session_watcher.coding_agent_backend, "get_backend", get_backend):
            session_watcher.pause_session("_new_123", BackendName.CODEX)
            session_watcher.update_session_id(
                "_new_123",
                "real-id",
                BackendName.CODEX,
            )
            await session_watcher.resume_session("real-id", BackendName.CODEX)

            claude_watcher = session_watcher._get_watcher(BackendName.CLAUDE)
            codex_watcher = session_watcher._get_watcher(BackendName.CODEX)

        assert "_new_123" not in codex_watcher._states
        assert codex_watcher._states["real-id"].paused_at is None
        assert codex_watcher._states["real-id"].last_delivered_idx == 0
        assert "real-id" not in claude_watcher._states


class TestPollingThroughBackendContract:
    @pytest.mark.asyncio
    async def test_poll_reads_snapshot_and_delivers_backend_to_callback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CODEX)
        backend.files = [_file("codex-session")]
        backend.snapshots["/tmp/codex-session.jsonl"] = _snapshot(
            "Codex answer",
            raw_count=2,
        )
        callback = AsyncMock()
        get_current_session = AsyncMock(
            return_value=ActiveSession("codex-session", BackendName.CODEX)
        )

        watcher = session_watcher.SessionWatcher(backend)

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(return_value=7),
            ) as register_session,
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ) as find_owner,
        ):
            await watcher.poll_once(callback, get_current_session)

        assert backend.list_calls == [PROJECT_DIR]
        assert backend.snapshot_calls == ["/tmp/codex-session.jsonl"]
        register_session.assert_awaited_once_with(
            "codex-session",
            backend=BackendName.CODEX,
        )
        find_owner.assert_called_once_with("codex-session", BackendName.CODEX)
        callback.assert_awaited_once_with(
            TEST_CHAT_ID,
            "codex-session",
            BackendName.CODEX,
            7,
            "Codex answer",
            True,
            True,
        )

    @pytest.mark.asyncio
    async def test_get_sessions_to_monitor_filters_daily_registry_by_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CODEX)
        watcher = session_watcher.SessionWatcher(backend)

        with patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(
                return_value={
                    1: DailySessionEntry("claude-session", BackendName.CLAUDE),
                    2: DailySessionEntry("codex-session", BackendName.CODEX),
                }
            ),
        ):
            session_ids, _files = await watcher._get_sessions_to_monitor()

        assert session_ids == ["codex-session"]

    @pytest.mark.asyncio
    async def test_poll_stops_delivery_when_project_changes_mid_scan(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        backend.files = [_file("session-1"), _file("session-2")]
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot("First")
        backend.snapshots["/tmp/session-2.jsonl"] = _snapshot("Second")
        delivered: list[str] = []

        async def callback(
            _chat_id: int,
            _session_id: str,
            _backend: BackendName,
            _day_number: int,
            text: str,
            _is_current_session: bool,
            _is_final: bool,
        ) -> None:
            delivered.append(text)
            monkeypatch.setattr(
                session_watcher.config,
                "WORKING_DIR",
                "/fake/other-project",
            )

        watcher = session_watcher.SessionWatcher(backend)

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(side_effect=[1, 2]),
            ),
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ),
        ):
            await watcher.poll_once(callback, AsyncMock(return_value=None))

        assert delivered == ["First"]

    @pytest.mark.asyncio
    async def test_poll_once_requests_operational_lookback_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """poll_once must scope the backend listing to the operational lookback window.

        Без этого ограничения poll_once видит все codex-сессии за всё время через
        полный скан ~/.codex/sessions. Сессии за пределами baseline (reset_state с
        lookback) попадают в poll как «никогда не виденные», last_delivered_idx=-1,
        и вся их история выливается в Telegram (cold-start flood).
        """
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CODEX)
        watcher = session_watcher.SessionWatcher(backend)

        with patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ):
            await watcher.poll_once(AsyncMock(), AsyncMock(return_value=None))

        assert backend.list_lookback_history, (
            "poll_once не вызвал list_all_session_files_for_project"
        )
        assert (
            backend.list_lookback_history[-1]
            == session_watcher.config.OPERATIONAL_SESSION_LOOKBACK_DAYS
        ), (
            "poll_once должен ограничивать листинг сессий operational lookback окном, "
            f"но передал lookback_days={backend.list_lookback_history[-1]}"
        )


class TestBufferAndHold:
    @pytest.mark.asyncio
    async def test_active_turn_holds_last_assistant_message_until_terminal_snapshot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CODEX)
        backend.files = [_file("codex-session")]
        backend.snapshots["/tmp/codex-session.jsonl"] = _snapshot(
            "Final text",
            raw_count=1,
            is_turn_active=True,
        )
        callback = AsyncMock()
        get_current_session = AsyncMock(return_value=None)
        watcher = session_watcher.SessionWatcher(backend)

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(return_value=3),
            ),
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ),
        ):
            await watcher.poll_once(callback, get_current_session)
            callback.assert_not_called()

            backend.snapshots["/tmp/codex-session.jsonl"] = _snapshot(
                "Final text",
                raw_count=2,
                is_turn_active=False,
            )
            await watcher.poll_once(callback, get_current_session)

        callback.assert_awaited_once_with(
            TEST_CHAT_ID,
            "codex-session",
            BackendName.CODEX,
            3,
            "Final text",
            False,
            True,
        )

    @pytest.mark.asyncio
    async def test_active_turn_delivers_non_last_messages_as_intermediate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CODEX)
        backend.files = [_file("codex-session")]
        backend.snapshots["/tmp/codex-session.jsonl"] = _snapshot(
            "Chunk 1",
            "Chunk 2",
            raw_count=2,
            is_turn_active=True,
        )
        callback = AsyncMock()
        watcher = session_watcher.SessionWatcher(backend)

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(return_value=4),
            ),
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ),
        ):
            await watcher.poll_once(callback, AsyncMock(return_value=None))

        callback.assert_awaited_once_with(
            TEST_CHAT_ID,
            "codex-session",
            BackendName.CODEX,
            4,
            "Chunk 1",
            False,
            False,
        )
        assert watcher._states["codex-session"].last_delivered_idx == 0


class TestResetState:
    @pytest.mark.asyncio
    async def test_reset_state_marks_existing_messages_as_seen(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        backend.files = [_file("session-1")]
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "Old 1",
            "Old 2",
            raw_count=5,
        )
        watcher = session_watcher.SessionWatcher(backend)
        watcher.pause_session("old-session")

        with patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ):
            await watcher.reset_state()

        assert list(watcher._states) == ["session-1"]
        assert watcher._states["session-1"].raw_count == 5
        assert watcher._states["session-1"].last_delivered_idx == 1
        assert watcher.is_session_paused("old-session") is False

    @pytest.mark.asyncio
    async def test_reset_state_marks_files_seen_even_if_missing_backoff_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        backend.files = [_file("stale-session")]
        backend.snapshots["/tmp/stale-session.jsonl"] = _snapshot(
            "Old historical message",
            raw_count=4,
        )
        watcher = session_watcher.SessionWatcher(backend)
        watcher._mark_missing_file("stale-session")

        with patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ):
            await watcher.reset_state()

        assert list(watcher._states) == ["stale-session"]
        assert watcher._states["stale-session"].raw_count == 4
        assert watcher._states["stale-session"].last_delivered_idx == 0
        assert "stale-session" not in watcher._missing_files

    @pytest.mark.asyncio
    async def test_reset_state_reads_session_snapshots_concurrently(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reset_state должен читать снапшоты сессий параллельно, иначе переключение проекта тормозит."""
        import asyncio as asyncio_module

        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        session_count = 8
        for index in range(session_count):
            session_id = f"session-{index}"
            backend.files.append(_file(session_id))
            backend.snapshots[f"/tmp/{session_id}.jsonl"] = _snapshot(
                f"Old {index}",
                raw_count=index + 1,
            )

        in_flight_count = 0
        peak_in_flight_count = 0
        original_read = backend.read_session_file_snapshot

        async def tracking_read(file_path: str) -> SessionFileSnapshot:
            nonlocal in_flight_count, peak_in_flight_count
            in_flight_count += 1
            peak_in_flight_count = max(peak_in_flight_count, in_flight_count)
            try:
                await asyncio_module.sleep(0.01)
                return await original_read(file_path)
            finally:
                in_flight_count -= 1

        backend.read_session_file_snapshot = tracking_read  # type: ignore[assignment]

        watcher = session_watcher.SessionWatcher(backend)

        with patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ):
            await watcher.reset_state()

        assert len(watcher._states) == session_count
        assert peak_in_flight_count > 1, (
            "reset_state читает файлы последовательно — переключение проектов будет тормозить "
            f"при большом числе сессий (peak concurrency = {peak_in_flight_count})"
        )

    async def test_reset_state_requests_operational_lookback_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reset_state must scope the backend listing to the operational lookback window.

        Иначе при переключении проекта watcher сканирует всю историю Codex (десятки тысяч
        файлов), которая полностью не имеет отношения к текущему проекту.
        """
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        watcher = session_watcher.SessionWatcher(backend)

        with patch.object(
            session_watcher.daily_session_registry,
            "get_all_today_sessions",
            new=AsyncMock(return_value={}),
        ):
            await watcher.reset_state()

        assert backend.list_lookback_history, (
            "reset_state не вызвал list_all_session_files_for_project"
        )
        assert (
            backend.list_lookback_history[-1]
            == session_watcher.config.OPERATIONAL_SESSION_LOOKBACK_DAYS
        ), (
            "reset_state должен ограничивать листинг сессий operational lookback окном, "
            f"но передал lookback_days={backend.list_lookback_history[-1]}"
        )


class TestHandlerOwnedFinalNotDuplicated:
    """Watcher не должен доставлять финал сессии, которую обрабатывает обработчик запроса.

    Сценарий дубликата: обработчик берёт сессию на паузу и сам доставит финал. Если
    stdout молчит дольше таймаута, agent-silence watchdog снимает паузу, чтобы показать
    промежуточный прогресс. Когда после этого приходит финал, watcher доставляет его как
    is_final=True — и обработчик тоже доставляет его. Пользователь получает финал дважды.
    В режиме тишины это особенно заметно: промежуточные подавлены, виден только дубль финала.
    """

    @pytest.mark.asyncio
    async def test_watchdog_resume_does_not_let_watcher_deliver_handler_final(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        backend.files = [_file("session-1")]
        # Снимок на момент срабатывания watchdog: ход активен, один промежуточный блок.
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "thinking...",
            raw_count=1,
            is_turn_active=True,
        )
        callback = AsyncMock()
        get_current_session = AsyncMock(return_value=None)
        watcher = session_watcher.SessionWatcher(backend)

        # Обработчик берёт сессию на паузу (старт send_to_claude_and_respond).
        watcher.pause_session("session-1")
        # watchdog снимает паузу после таймаута тишины, чтобы показать прогресс.
        await watcher.resume_session("session-1")

        # Ход завершился: появился финал, который доставит сам обработчик.
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "thinking...",
            "FINAL ANSWER",
            raw_count=2,
            is_turn_active=False,
        )

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(return_value=5),
            ),
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ),
        ):
            await watcher.poll_once(callback, get_current_session)

        final_deliveries = [
            call for call in callback.await_args_list if call.args[6] is True
        ]
        assert not final_deliveries, (
            "watcher доставил финал сессии, которой владеет обработчик запроса — "
            "финальный ответ придёт дважды (от обработчика и от watcher)"
        )

    @pytest.mark.asyncio
    async def test_handler_owned_session_still_delivers_intermediate_progress(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Даже когда финал принадлежит обработчику, watchdog-resume watcher должен
        показывать промежуточный прогресс — иначе теряется смысл watchdog."""
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        backend.files = [_file("session-1")]
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "chunk 1",
            raw_count=1,
            is_turn_active=True,
        )
        callback = AsyncMock()
        watcher = session_watcher.SessionWatcher(backend)
        watcher.pause_session("session-1")
        await watcher.resume_session("session-1")

        # Новые промежуточные блоки, ход ещё активен (финала пока нет).
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "chunk 1",
            "chunk 2",
            "chunk 3",
            raw_count=3,
            is_turn_active=True,
        )

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(return_value=9),
            ),
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ),
        ):
            await watcher.poll_once(callback, AsyncMock(return_value=None))

        # chunk 2 доставлен как промежуточный; chunk 3 удержан (последний при активном ходе).
        callback.assert_awaited_once_with(
            TEST_CHAT_ID,
            "session-1",
            BackendName.CLAUDE,
            9,
            "chunk 2",
            False,
            False,
        )

    @pytest.mark.asyncio
    async def test_after_clear_ownership_watcher_delivers_final_again(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """После снятия владения (обработчик завершил доставку) watcher снова доставляет
        финал — например, поздние терминальные обновления или следующий ход."""
        monkeypatch.setattr(session_watcher.config, "WORKING_DIR", PROJECT_DIR)
        backend = FakeBackend(BackendName.CLAUDE)
        backend.files = [_file("session-1")]
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "earlier final",
            raw_count=1,
            is_turn_active=True,
        )
        callback = AsyncMock()
        watcher = session_watcher.SessionWatcher(backend)
        watcher.pause_session("session-1")
        # Обработчик завершает: finally вызывает resume_session, затем снятие владения.
        await watcher.resume_session("session-1")
        watcher.clear_handler_owns_final_delivery("session-1")

        # Появляется новый финал уже без владельца-обработчика.
        backend.snapshots["/tmp/session-1.jsonl"] = _snapshot(
            "earlier final",
            "new terminal final",
            raw_count=2,
            is_turn_active=False,
        )

        with (
            patch.object(
                session_watcher.daily_session_registry,
                "get_all_today_sessions",
                new=AsyncMock(return_value={}),
            ),
            patch.object(
                session_watcher.daily_session_registry,
                "register_session",
                new=AsyncMock(return_value=11),
            ),
            patch.object(
                session_watcher.session_manager,
                "find_chat_by_session_id",
                new=Mock(return_value=TEST_CHAT_ID),
            ),
        ):
            await watcher.poll_once(callback, AsyncMock(return_value=None))

        callback.assert_awaited_once_with(
            TEST_CHAT_ID,
            "session-1",
            BackendName.CLAUDE,
            11,
            "new terminal final",
            False,
            True,
        )
