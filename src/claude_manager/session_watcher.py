"""Backend-aware monitoring of coding-agent session files."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from claude_manager import (
    coding_agent_backend,
    config,
    daily_session_registry,
    session_manager,
)
from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    SessionUnreadState,
)

logger = logging.getLogger(__name__)

# Из BRD CJM-07: watcher проверяет файлы сессий каждые 2 секунды.
POLL_INTERVAL_SECONDS = 2

# Задержка после непредвиденной ошибки в бесконечном цикле start().
ERROR_RETRY_DELAY_SECONDS = 10

# Защита от утечки pause_session(), если handler упал до resume_session().
PAUSE_LEAK_SAFETY_TIMEOUT_SECONDS = 120

# Backoff для временно отсутствующих или пустых файлов сессий.
MISSING_FILE_RETRY_BASE_SECONDS = 5
MISSING_FILE_RETRY_MAX_SECONDS = 60
MISSING_FILE_RETRY_STATE_TTL_SECONDS = 300

# Compatibility marker for old Claude helper functions and legacy snapshots.
NO_RESPONSE_MARKERS = frozenset({"No response requested."})

MessageCallback = Callable[..., Awaitable[None]]
CurrentSessionGetter = Callable[[int], Awaitable[object]]


@dataclass
class SessionWatcherState:
    """Cursor state for one session file inside one backend watcher."""

    raw_count: int = 0
    parsed_message_count: int = 0
    cli_process_is_currently_writing_session_file: bool = False
    last_delivered_idx: int = -1
    paused_at: float | None = None


@dataclass
class MissingFileRetryState:
    """Retry/backoff state for temporarily missing session files."""

    first_seen_missing_at_monotonic: float
    retry_after_monotonic: float
    attempt_count: int = 1


_watchers: dict[BackendName, "SessionWatcher"] = {}
_callback: MessageCallback | None = None
_get_current_session: CurrentSessionGetter | None = None


def _is_empty_response(text: str) -> bool:
    """Return whether an assistant text is empty or a no-response marker."""
    if not text or not text.strip():
        return True
    return text.strip() in NO_RESPONSE_MARKERS


def _extract_message_text(message: dict) -> str | None:
    """Compatibility helper for old Claude JSONL message dictionaries."""
    if message.get("type") != "assistant":
        return None

    message_body = message.get("message", {})
    content = message_body.get("content")

    if content is None:
        return None

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_value = block.get("text", "")
                if text_value:
                    text_parts.append(text_value)
        if text_parts:
            return " ".join(text_parts)

    return None


def _extract_assistant_messages(
    all_messages: list[dict],
    already_seen_count: int,
) -> list[str]:
    """Compatibility helper extracting new assistant texts from raw records."""
    new_messages = all_messages[already_seen_count:]
    result: list[str] = []

    for message in new_messages:
        text = _extract_message_text(message)
        if text is not None:
            result.append(text)

    return result


def _coerce_backend_name(backend: BackendName | str) -> BackendName:
    if isinstance(backend, BackendName):
        return backend
    return BackendName(backend)


def _callback_accepts_backend(callback: MessageCallback) -> bool:
    """Return whether callback appears to accept the new backend argument."""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return True

    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional_count += 1

    return positional_count >= 7


async def _invoke_callback(
    callback: MessageCallback,
    chat_id: int,
    session_id: str,
    backend: BackendName,
    day_number: int,
    text: str,
    is_current_session: bool,
    is_final: bool,
) -> None:
    """Invoke new 7-arg callbacks, falling back to the old 6-arg shape."""
    if _callback_accepts_backend(callback):
        await callback(
            chat_id,
            session_id,
            backend,
            day_number,
            text,
            is_current_session,
            is_final,
        )
        return

    await callback(
        chat_id,
        session_id,
        day_number,
        text,
        is_current_session,
        is_final,
    )


def _current_session_matches(
    current_session: object,
    session_id: str,
    backend: BackendName,
) -> bool:
    """Compare backend-aware ActiveSession values and legacy string values."""
    if current_session is None:
        return False

    active_session_id = getattr(current_session, "session_id", None)
    active_backend = getattr(current_session, "backend", None)
    if isinstance(active_session_id, str) and active_backend is not None:
        return active_session_id == session_id and active_backend == backend

    if isinstance(current_session, str):
        return current_session == session_id

    return current_session == session_id


def _message_should_be_delivered(message: SessionMessage) -> bool:
    if message.role != "assistant":
        return False
    if message.is_empty_response:
        return False
    return not _is_empty_response(message.text)


class SessionWatcher:
    """Monitor session files owned by one coding-agent backend."""

    def __init__(self, backend: CodingAgentBackend) -> None:
        self.backend = backend
        self._states: dict[str, SessionWatcherState] = {}
        self._missing_files: dict[str, MissingFileRetryState] = {}
        self._global_paused = False
        self._project_generation = 0

    @property
    def backend_name(self) -> BackendName:
        return self.backend.name

    async def poll_once(
        self,
        callback: MessageCallback,
        get_current_session: CurrentSessionGetter,
    ) -> None:
        """Run one sequential scan of this backend's session files."""
        if self._global_paused:
            return

        project_path = config.WORKING_DIR
        project_generation = self._project_generation
        session_ids, files_by_session_id = await self._get_sessions_to_monitor(
            project_path=project_path,
        )
        if not self._poll_context_is_current(project_path, project_generation):
            return

        active_ids = set(session_ids)

        stale_ids = [
            session_id
            for session_id, state in self._states.items()
            if session_id not in active_ids and state.paused_at is None
        ]
        for session_id in stale_ids:
            del self._states[session_id]

        for session_id in session_ids:
            if not self._poll_context_is_current(project_path, project_generation):
                break
            try:
                await self._check_session(
                    session_id,
                    files_by_session_id,
                    callback,
                    get_current_session,
                    project_path,
                    project_generation,
                )
            except Exception:
                logger.error(
                    "Ошибка проверки сессии %s (%s)",
                    session_id,
                    self.backend.name.value,
                    exc_info=True,
                )

    async def _get_sessions_to_monitor(
        self,
        project_path: str | None = None,
        *,
        include_registry: bool = True,
        apply_missing_backoff: bool = True,
    ) -> tuple[list[str], dict[str, SessionFileInfo]]:
        """Return session ids and file metadata visible to this backend."""
        effective_project_path = project_path or config.WORKING_DIR
        files = await self.backend.list_all_session_files_for_project(
            effective_project_path
        )
        files_by_session_id = {info.session_id: info for info in files}
        session_ids = [info.session_id for info in files]
        existing_ids = set(session_ids)

        if include_registry:
            today_sessions = await daily_session_registry.get_all_today_sessions()
            for entry in today_sessions.values():
                entry_session_id = getattr(entry, "session_id", None)
                entry_backend = getattr(entry, "backend", BackendName.CLAUDE)
                if (
                    isinstance(entry_session_id, str)
                    and entry_backend == self.backend.name
                    and entry_session_id not in existing_ids
                ):
                    session_ids.append(entry_session_id)
                    existing_ids.add(entry_session_id)

        if apply_missing_backoff:
            session_ids = [
                session_id
                for session_id in session_ids
                if self._should_check_missing_file_now(session_id)
            ]

        return session_ids, files_by_session_id

    async def _check_session(
        self,
        session_id: str,
        files_by_session_id: dict[str, SessionFileInfo],
        callback: MessageCallback,
        get_current_session: CurrentSessionGetter,
        project_path: str | None = None,
        project_generation: int | None = None,
    ) -> None:
        if (
            project_path is not None
            and project_generation is not None
            and not self._poll_context_is_current(project_path, project_generation)
        ):
            return

        previous = self._states.get(session_id, SessionWatcherState())

        if previous.paused_at is not None:
            pause_age = time.monotonic() - previous.paused_at
            if pause_age < PAUSE_LEAK_SAFETY_TIMEOUT_SECONDS:
                return
            previous.paused_at = None
            logger.warning(
                "Пауза сессии %s (%s) превысила %d сек — автоматическое снятие",
                session_id,
                self.backend.name.value,
                PAUSE_LEAK_SAFETY_TIMEOUT_SECONDS,
            )

        file_info = files_by_session_id.get(session_id)
        if file_info is None:
            self._mark_missing_file(session_id)
            return

        snapshot = await self.backend.read_session_file_snapshot(
            file_info.file_path
        )
        if snapshot.raw_record_count == 0:
            self._mark_missing_file(session_id)
            return

        if self._snapshot_is_unchanged(previous, snapshot):
            return

        messages = snapshot.messages
        candidate_indices = list(
            range(previous.last_delivered_idx + 1, len(messages))
        )
        if snapshot.is_turn_active and candidate_indices:
            last_idx = len(messages) - 1
            candidate_indices = [
                index for index in candidate_indices if index < last_idx
            ]

        deliverable = [
            (index, messages[index])
            for index in candidate_indices
            if _message_should_be_delivered(messages[index])
        ]

        if deliverable:
            if (
                project_path is not None
                and project_generation is not None
                and not self._poll_context_is_current(project_path, project_generation)
            ):
                return

            try:
                day_number = await daily_session_registry.register_session(
                    session_id,
                    backend=self.backend.name,
                )
            except Exception:
                logger.error(
                    "Ошибка регистрации сессии %s (%s) в дневном реестре",
                    session_id,
                    self.backend.name.value,
                    exc_info=True,
                )
                return

            for position, (_index, message) in enumerate(deliverable):
                is_final = (
                    not snapshot.is_turn_active
                    and position == len(deliverable) - 1
                )
                if (
                    project_path is not None
                    and project_generation is not None
                    and not self._poll_context_is_current(
                        project_path,
                        project_generation,
                    )
                ):
                    return
                await self._deliver_message(
                    session_id,
                    day_number,
                    message.text,
                    is_final,
                    callback,
                    get_current_session,
                )

        if snapshot.is_turn_active and messages:
            new_last_delivered_idx = max(
                previous.last_delivered_idx,
                len(messages) - 2,
            )
        else:
            new_last_delivered_idx = len(messages) - 1

        self._states[session_id] = SessionWatcherState(
            raw_count=snapshot.raw_record_count,
            parsed_message_count=len(messages),
            cli_process_is_currently_writing_session_file=snapshot.is_turn_active,
            last_delivered_idx=new_last_delivered_idx,
            paused_at=previous.paused_at,
        )
        self._missing_files.pop(session_id, None)

    async def _deliver_message(
        self,
        session_id: str,
        day_number: int,
        text: str,
        is_final: bool,
        callback: MessageCallback,
        get_current_session: CurrentSessionGetter,
    ) -> None:
        owner_chat_id = session_manager.find_chat_by_session_id(
            session_id,
            self.backend.name,
        )
        if owner_chat_id is not None:
            target_chat_ids = [owner_chat_id]
        else:
            target_chat_ids = [
                chat_id
                for chat_id in config.ALLOWED_USER_IDS
                if chat_id != getattr(config, "E2E_TEST_USER_ID", None)
            ]

        for chat_id in target_chat_ids:
            try:
                current_session = await get_current_session(chat_id)
                is_current_session = _current_session_matches(
                    current_session,
                    session_id,
                    self.backend.name,
                )
                await _invoke_callback(
                    callback,
                    chat_id,
                    session_id,
                    self.backend.name,
                    day_number,
                    text,
                    is_current_session,
                    is_final,
                )
            except Exception:
                logger.error(
                    "Ошибка при отправке watcher-сообщения из сессии %s (%s)",
                    session_id,
                    self.backend.name.value,
                    exc_info=True,
                )

    def _snapshot_is_unchanged(
        self,
        state: SessionWatcherState,
        snapshot: SessionFileSnapshot,
    ) -> bool:
        return (
            snapshot.raw_record_count == state.raw_count
            and len(snapshot.messages) == state.parsed_message_count
            and snapshot.is_turn_active
            == state.cli_process_is_currently_writing_session_file
        )

    def _poll_context_is_current(
        self,
        project_path: str,
        project_generation: int,
    ) -> bool:
        return (
            not self._global_paused
            and config.WORKING_DIR == project_path
            and self._project_generation == project_generation
        )

    def _mark_missing_file(self, session_id: str) -> None:
        now = time.monotonic()
        existing = self._missing_files.get(session_id)
        if existing is None:
            attempt_count = 1
            first_seen = now
        else:
            attempt_count = existing.attempt_count + 1
            first_seen = existing.first_seen_missing_at_monotonic

        delay = min(
            MISSING_FILE_RETRY_BASE_SECONDS * (2 ** (attempt_count - 1)),
            MISSING_FILE_RETRY_MAX_SECONDS,
        )
        self._missing_files[session_id] = MissingFileRetryState(
            first_seen_missing_at_monotonic=first_seen,
            retry_after_monotonic=now + delay,
            attempt_count=attempt_count,
        )

    def _should_check_missing_file_now(self, session_id: str) -> bool:
        missing_state = self._missing_files.get(session_id)
        if missing_state is None:
            return True

        now = time.monotonic()
        missing_age = now - missing_state.first_seen_missing_at_monotonic
        if missing_age > MISSING_FILE_RETRY_STATE_TTL_SECONDS:
            del self._missing_files[session_id]
            return True

        if now < missing_state.retry_after_monotonic:
            return False

        del self._missing_files[session_id]
        return True

    async def reset_state(self) -> None:
        """Initialize cursors so historical messages are not redelivered."""
        self._project_generation += 1
        self._missing_files.clear()
        session_ids, files_by_session_id = await self._get_sessions_to_monitor(
            project_path=config.WORKING_DIR,
            include_registry=False,
            apply_missing_backoff=False,
        )
        new_states: dict[str, SessionWatcherState] = {}

        for session_id in session_ids:
            file_info = files_by_session_id.get(session_id)
            if file_info is None:
                continue
            snapshot = await self.backend.read_session_file_snapshot(
                file_info.file_path
            )
            new_states[session_id] = SessionWatcherState(
                raw_count=snapshot.raw_record_count,
                parsed_message_count=len(snapshot.messages),
                cli_process_is_currently_writing_session_file=snapshot.is_turn_active,
                last_delivered_idx=len(snapshot.messages) - 1,
                paused_at=None,
            )

        self._states.clear()
        self._states.update(new_states)
        self._missing_files.clear()

        logger.info(
            "Состояние session_watcher (%s) сброшено: %d сессий",
            self.backend.name.value,
            len(new_states),
        )

    def pause_session(self, session_id: str) -> None:
        state = self._states.setdefault(session_id, SessionWatcherState())
        state.paused_at = time.monotonic()
        logger.debug(
            "Watcher (%s): сессия %s на паузе",
            self.backend.name.value,
            session_id,
        )

    async def resume_session(self, session_id: str) -> None:
        state = self._states.get(session_id)
        if state is None:
            return

        state.paused_at = None
        files = await self.backend.list_all_session_files_for_project(
            config.WORKING_DIR
        )
        file_info = next(
            (info for info in files if info.session_id == session_id),
            None,
        )
        if file_info is None:
            return

        snapshot = await self.backend.read_session_file_snapshot(
            file_info.file_path
        )
        state.raw_count = snapshot.raw_record_count
        state.parsed_message_count = len(snapshot.messages)
        state.cli_process_is_currently_writing_session_file = (
            snapshot.is_turn_active
        )
        state.last_delivered_idx = len(snapshot.messages) - 1
        self._missing_files.pop(session_id, None)

        logger.debug(
            "Watcher (%s): сессия %s снята с паузы, last_delivered_idx=%d",
            self.backend.name.value,
            session_id,
            state.last_delivered_idx,
        )

    def pause_all(self) -> None:
        self._global_paused = True

    def resume_all(self) -> None:
        self._global_paused = False

    def update_session_id(
        self,
        old_session_id: str,
        new_session_id: str,
    ) -> None:
        if old_session_id in self._states:
            self._states[new_session_id] = self._states.pop(old_session_id)
        if old_session_id in self._missing_files:
            self._missing_files.pop(old_session_id, None)

        logger.info(
            "Watcher (%s): session_id обновлён %s -> %s",
            self.backend.name.value,
            old_session_id,
            new_session_id,
        )

    def get_seen_counts_snapshot(self) -> dict[str, SessionUnreadState]:
        return {
            session_id: SessionUnreadState(
                raw_record_count=state.raw_count,
                last_delivered_idx=state.last_delivered_idx,
            )
            for session_id, state in self._states.items()
        }

    def is_session_paused(self, session_id: str) -> bool:
        state = self._states.get(session_id)
        if state is None or state.paused_at is None:
            return False
        return (
            time.monotonic() - state.paused_at
            < PAUSE_LEAK_SAFETY_TIMEOUT_SECONDS
        )


def _get_watcher(backend: BackendName | str = BackendName.CLAUDE) -> SessionWatcher:
    backend_name = _coerce_backend_name(backend)
    if backend_name not in _watchers:
        _watchers[backend_name] = SessionWatcher(
            coding_agent_backend.get_backend(backend_name)
        )
    return _watchers[backend_name]


def _get_all_watchers() -> list[SessionWatcher]:
    return [_get_watcher(backend) for backend in BackendName]


async def _get_sessions_to_monitor(
    backend: BackendName | str = BackendName.CLAUDE,
) -> list[str]:
    """Compatibility wrapper returning only session ids for one backend."""
    session_ids, _files = await _get_watcher(backend)._get_sessions_to_monitor()
    return session_ids


async def _check_session(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Compatibility wrapper around one backend watcher check."""
    callback = _callback
    get_current_session = _get_current_session
    if callback is None or get_current_session is None:
        return

    watcher = _get_watcher(backend)
    _session_ids, files_by_session_id = await watcher._get_sessions_to_monitor()
    await watcher._check_session(
        session_id,
        files_by_session_id,
        callback,
        get_current_session,
    )


async def _poll_sessions(
    callback: MessageCallback | None = None,
    get_current_session: CurrentSessionGetter | None = None,
) -> None:
    """Run one poll for all backend watchers."""
    effective_callback = callback or _callback
    effective_get_current_session = get_current_session or _get_current_session
    if effective_callback is None or effective_get_current_session is None:
        return

    for watcher in _get_all_watchers():
        await watcher.poll_once(
            effective_callback,
            effective_get_current_session,
        )


def pause_all() -> None:
    """Pause all backend watchers during project switching."""
    for watcher in _get_all_watchers():
        watcher.pause_all()
    logger.info("Мониторинг всех сессий приостановлен (глобальная пауза)")


def resume_all() -> None:
    """Resume all backend watchers after project switching."""
    for watcher in _get_all_watchers():
        watcher.resume_all()
    logger.info("Мониторинг всех сессий возобновлён (глобальная пауза снята)")


def pause_session(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Pause monitoring for one backend-owned session."""
    _get_watcher(backend).pause_session(session_id)


async def resume_session(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Resume monitoring for one backend-owned session."""
    await _get_watcher(backend).resume_session(session_id)


async def reset_state() -> None:
    """Reset accumulated state for all backend watchers."""
    for watcher in _get_all_watchers():
        await watcher.reset_state()
    logger.info("Состояние session_watcher сброшено для переключения проекта")


def update_session_id(
    old_session_id: str,
    new_session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> None:
    """Move watcher state from a temporary id to a real backend-owned id."""
    _get_watcher(backend).update_session_id(old_session_id, new_session_id)


def get_seen_counts_snapshot(
    backend: BackendName | str = BackendName.CLAUDE,
) -> dict[str, SessionUnreadState]:
    """Return a copy of unread cursor state for one backend."""
    return _get_watcher(backend).get_seen_counts_snapshot()


def is_session_paused(
    session_id: str,
    backend: BackendName | str = BackendName.CLAUDE,
) -> bool:
    """Return whether a backend-owned session is currently paused."""
    return _get_watcher(backend).is_session_paused(session_id)


async def start(
    callback: MessageCallback,
    get_current_session: CurrentSessionGetter,
) -> None:
    """Start the infinite background watcher loop for all backends."""
    global _callback, _get_current_session

    _callback = callback
    _get_current_session = get_current_session

    for watcher in _get_all_watchers():
        await watcher.reset_state()

    logger.info("Мониторинг сессий запущен (%d backend-ов)", len(_watchers))

    try:
        while True:
            try:
                for watcher in _get_all_watchers():
                    await watcher.poll_once(callback, get_current_session)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "Непредвиденная ошибка в цикле мониторинга",
                    exc_info=True,
                )
                await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)
                continue

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Мониторинг сессий остановлен")
