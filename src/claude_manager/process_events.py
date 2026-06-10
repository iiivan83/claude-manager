"""Event parsing and result assembly for managed CLI processes."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    TerminalStatus,
    UnifiedEvent,
    get_backend,
)
from claude_manager.claude_runner import BackendSubprocess
from claude_manager.process_state import (
    ManagedProcess,
    ProcessKey,
    _resolve_process_key_alias_unlocked,
    _resolve_session_id_alias_unlocked,
    _stop_events,
    update_session_id,
)
from claude_manager.process_types import (
    CONTENT_BLOCK_TEXT,
    CONTENT_BLOCK_THINKING,
    EMPTY_RESPONSE_MARKER,
    EVENT_TYPE_ASSISTANT,
    PROGRESS_THROTTLE_SECONDS,
    ProcessStoppedError,
    ProgressCallback,
    SendResult,
    SessionIdCallback,
)

logger = logging.getLogger("claude_manager.process_manager")


def _extract_progress_text(event: dict) -> str | None:
    """Извлекает текст прогресса Claude из assistant-события (text приоритетнее thinking)."""
    if event.get("type") != EVENT_TYPE_ASSISTANT:
        return None

    content_blocks = event.get("message", {}).get("content", [])

    text_content = None
    thinking_content = None

    for block in content_blocks:
        block_type = block.get("type")
        if block_type == CONTENT_BLOCK_TEXT and not text_content:
            text_content = block.get("text")
        elif block_type == CONTENT_BLOCK_THINKING and not thinking_content:
            thinking_content = block.get("thinking")

    return text_content or thinking_content


def _extract_result_text(event: dict) -> str:
    """Извлекает финальный текст ответа из события result."""
    text = event.get("result", "")

    if text is None:
        return ""

    if text == EMPTY_RESPONSE_MARKER:
        return ""

    return text


def _is_error_result(event: dict) -> bool:
    """Проверяет, является ли событие result ошибочным."""
    return event.get("is_error", False)


def _should_send_progress(last_progress_time: float) -> bool:
    """Проверяет, можно ли отправить промежуточное обновление."""
    if last_progress_time == 0.0:
        return True

    elapsed = time.monotonic() - last_progress_time
    return elapsed >= PROGRESS_THROTTLE_SECONDS


def _check_stop_requested(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> None:
    """Проверяет, запросил ли пользователь отмену. Бросает ProcessStoppedError."""
    process_key = _resolve_process_key_alias_unlocked(session_id, backend)
    if process_key not in _stop_events and backend == BackendName.CLAUDE:
        process_key = _resolve_session_id_alias_unlocked(session_id)
    stop_event = _stop_events.get(process_key)
    if stop_event is not None and stop_event.is_set():
        raise ProcessStoppedError("Запрос прерван командой /stop")


def _send_superseded_or_stopped(
    process_key: ProcessKey,
    owner_stop_event: asyncio.Event | None,
) -> bool:
    """True, если turn отменён командой /stop или перехвачен более новым send."""
    if owner_stop_event is None:
        return False
    if owner_stop_event.is_set():
        return True
    return _stop_events.get(process_key) is not owner_stop_event


def _abort_if_send_superseded(
    process_key: ProcessKey,
    owner_stop_event: asyncio.Event | None,
) -> None:
    """Прерывает turn ProcessStoppedError, если он отменён или перехвачен новым send."""
    if _send_superseded_or_stopped(process_key, owner_stop_event):
        raise ProcessStoppedError("Запрос прерван командой /stop")


async def _read_stderr(managed_process: ManagedProcess) -> str:
    """Читает stderr процесса CLI — там может быть причина падения."""
    stderr_max_length = 500
    try:
        if isinstance(managed_process, BackendSubprocess):
            return await managed_process.read_stderr_text()
        if managed_process.process.stderr is None:
            return ""
        raw = await managed_process.process.stderr.read(stderr_max_length)
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


async def _iter_events_from_managed_process(
    managed_process: ManagedProcess,
    backend_obj: CodingAgentBackend,
) -> AsyncIterator[UnifiedEvent]:
    """Читает backend-neutral события из старого или нового subprocess wrapper."""
    if isinstance(managed_process, BackendSubprocess):
        while True:
            raw_bytes = await managed_process.read_stdout_line()
            if not raw_bytes:
                return
            raw_line = raw_bytes.decode("utf-8").rstrip("\n")
            event = backend_obj.parse_stdout_line_into_event(raw_line)
            if event is None:
                continue
            yield event
            if backend_obj.is_turn_complete_event(event):
                return
        return

    async for event in managed_process.read_events():
        yield event


async def _call_session_id_callback(
    session_id_callback: SessionIdCallback,
    old_session_id: str,
    new_session_id: str,
    backend: BackendName,
    include_backend: bool,
) -> None:
    """Вызывает callback смены session_id в новой или legacy-сигнатуре."""
    if include_backend:
        await session_id_callback(old_session_id, new_session_id, backend)
        return
    await session_id_callback(old_session_id, new_session_id)


async def _process_events(
    claude_process: ManagedProcess,
    session_id: str,
    progress_callback: ProgressCallback | None,
    session_id_callback: SessionIdCallback | None = None,
    backend_obj: CodingAgentBackend | None = None,
    backend_name: BackendName = BackendName.CLAUDE,
    session_id_callback_includes_backend: bool = False,
) -> SendResult:
    """Читает поток событий от backend CLI и собирает результат."""
    last_progress_time = 0.0
    last_assistant_text = ""
    final_session_id = session_id
    terminal_status: TerminalStatus | None = None
    terminal_event: UnifiedEvent | None = None
    callback_fired = False
    effective_backend = backend_obj or get_backend(backend_name)
    pending_progress_text: str | None = None

    async for event in _iter_events_from_managed_process(
        claude_process, effective_backend,
    ):
        if session_id_callback_includes_backend:
            _check_stop_requested(final_session_id, backend_name)
        else:
            _check_stop_requested(final_session_id)

        is_terminal_event = effective_backend.is_turn_complete_event(event)

        if pending_progress_text is not None and not is_terminal_event:
            if progress_callback and _should_send_progress(last_progress_time):
                await progress_callback(final_session_id, pending_progress_text)
                last_progress_time = time.monotonic()
            pending_progress_text = None

        event_session_id = effective_backend.read_session_id_from_event(event)
        if event_session_id is not None and event_session_id != final_session_id:
            old_final = final_session_id
            final_session_id = event_session_id
            if session_id_callback_includes_backend:
                await update_session_id(old_final, event_session_id, backend_name)
            else:
                await update_session_id(old_final, event_session_id)
            if session_id_callback is not None and not callback_fired:
                callback_fired = True
                try:
                    await _call_session_id_callback(
                        session_id_callback,
                        old_final,
                        event_session_id,
                        backend_name,
                        session_id_callback_includes_backend,
                    )
                except Exception:
                    logger.error(
                        "Ошибка в session_id_callback: %s -> %s",
                        old_final, event_session_id, exc_info=True,
                    )

        progress_text = effective_backend.read_progress_text_from_event(event)
        if progress_text:
            pending_progress_text = progress_text

        assistant_text = effective_backend.read_assistant_text_from_event(event)
        if assistant_text is not None:
            last_assistant_text = assistant_text

        if is_terminal_event:
            terminal_event = event
            terminal_status = effective_backend.read_terminal_status_from_event(event)
            if (
                pending_progress_text is not None
                and pending_progress_text != last_assistant_text
                and progress_callback
                and _should_send_progress(last_progress_time)
            ):
                await progress_callback(final_session_id, pending_progress_text)
                last_progress_time = time.monotonic()
            pending_progress_text = None
            break

    if terminal_status is None:
        stderr_text = await _read_stderr(claude_process)
        error_text = stderr_text or "Процесс завершился без финального события"
        logger.warning(
            "Процесс CLI завершился без финального события: session_id=%s backend=%s",
            session_id, backend_name.value,
        )
        return SendResult(
            text="",
            session_id=final_session_id,
            is_error=True,
            retries_used=0,
            backend=backend_name,
            error_text=error_text,
        )

    if terminal_status == TerminalStatus.FAILED:
        error_text = (
            effective_backend.read_error_text_from_event(terminal_event)
            if terminal_event is not None else None
        )
        return SendResult(
            text=last_assistant_text,
            session_id=final_session_id,
            is_error=True,
            retries_used=0,
            backend=backend_name,
            error_text=error_text,
        )

    return SendResult(
        text=last_assistant_text,
        session_id=final_session_id,
        is_error=False,
        retries_used=0,
        backend=backend_name,
        error_text=None,
    )
