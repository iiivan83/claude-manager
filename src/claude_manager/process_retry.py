"""Retry loop helpers for CLI process turns."""

import asyncio
import logging

from claude_manager.coding_agent_backend import (
    BackendName,
    CodingAgentBackend,
    get_backend,
)
from claude_manager.claude_runner import ClaudeProcess
from claude_manager.process_events import (
    _abort_if_send_superseded,
    _check_stop_requested,
    _process_events,
    _send_superseded_or_stopped,
)
from claude_manager.process_lifecycle import _restart_process
from claude_manager.process_state import (
    _make_backend_process_key,
    _prefer_existing_process_key_unlocked,
    _processes,
    _resolve_process_key_alias_unlocked,
    _resolve_session_id_alias_unlocked,
    _stop_events,
)
from claude_manager.process_stop import _apply_backend_stop_strategy
from claude_manager.process_types import (
    MAX_RETRIES,
    RETRY_INTERVAL_SECONDS,
    STOP_CHECK_INTERVAL_SECONDS,
    ProcessStoppedError,
    ProgressCallback,
    RetryCallback,
    SendResult,
    SessionIdCallback,
)

logger = logging.getLogger("claude_manager.process_manager")


async def _execute_single_retry(
    session_id: str,
    text: str,
    attempt: int,
    cwd: str,
    progress_callback: ProgressCallback | None,
    session_id_callback: SessionIdCallback | None = None,
    backend_obj: CodingAgentBackend | None = None,
    backend_name: BackendName = BackendName.CLAUDE,
    image_paths: list[str] | None = None,
    session_id_callback_includes_backend: bool = False,
) -> SendResult | None:
    """Выполняет одну попытку ретрая. Возвращает результат или None при ошибке."""
    process_key = (
        _make_backend_process_key(session_id, backend_name)
        if backend_obj is not None
        else _prefer_existing_process_key_unlocked(session_id, backend_name)
    )
    try:
        # Рестарт внутри try: транзиентный сбой спавна (CodingAgentStartError и
        # т.п.) на одной попытке уводит в except → return None → _retry_loop
        # делает continue, а не рвёт весь цикл из MAX_RETRIES попыток (P2-16).
        # ProcessStoppedError из рестарта пробрасывается ниже отдельным except.
        old_process = _processes.get(process_key)
        if old_process is not None and old_process.is_running():
            await _apply_backend_stop_strategy(old_process, backend_name)
        claude_process = await _restart_process(
            session_id,
            cwd,
            backend_obj=backend_obj,
            backend_name=backend_name,
            prompt_text=text,
            image_paths=image_paths or [],
        )
        if isinstance(claude_process, ClaudeProcess):
            await claude_process.send_message(text)
        return await _process_events(
            claude_process,
            session_id,
            progress_callback,
            session_id_callback,
            backend_obj=backend_obj,
            backend_name=backend_name,
            session_id_callback_includes_backend=session_id_callback_includes_backend,
        )
    except ProcessStoppedError:
        raise
    except Exception:
        logger.warning(
            "Ошибка при повторной попытке %d для сессии %s",
            attempt, session_id, exc_info=True,
        )
        return None


def _build_exhausted_result(
    last_result: SendResult | None,
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> SendResult:
    """Формирует результат после исчерпания всех ретраев."""
    logger.error(
        "Все %d ретраев исчерпаны для сессии %s", MAX_RETRIES, session_id,
    )
    if last_result is not None:
        return SendResult(
            text=last_result.text, session_id=last_result.session_id,
            is_error=True, retries_used=MAX_RETRIES,
            backend=backend, error_text=last_result.error_text,
        )
    return SendResult(
        text="", session_id=session_id,
        is_error=True, retries_used=MAX_RETRIES,
        backend=backend, error_text=None,
    )


def _classify_permanent_error_result(
    error_result: SendResult,
    backend_name: BackendName,
) -> SendResult | None:
    """Возвращает готовый результат, если повторять ошибку бессмысленно."""
    permanent_error_kind = get_backend(backend_name).classify_permanent_error(
        error_result.error_text or error_result.text
    )
    if permanent_error_kind is None:
        return None
    logger.warning(
        "Постоянная ошибка backend=%s session_id=%s kind=%s — повтор пропущен",
        backend_name.value, error_result.session_id, permanent_error_kind.value,
    )
    return SendResult(
        text=error_result.text,
        session_id=error_result.session_id,
        is_error=True,
        retries_used=0,
        backend=backend_name,
        error_text=error_result.error_text,
        permanent_error_kind=permanent_error_kind,
    )


async def _retry_loop(
    session_id: str,
    text: str,
    cwd: str,
    error_reason: str,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
    session_id_callback: SessionIdCallback | None = None,
    backend_obj: CodingAgentBackend | None = None,
    backend_name: BackendName = BackendName.CLAUDE,
    image_paths: list[str] | None = None,
    session_id_callback_includes_backend: bool = False,
    owner_stop_event: asyncio.Event | None = None,
) -> SendResult:
    """Цикл повторных попыток при ошибке от backend CLI."""
    last_result: SendResult | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        process_key = _make_backend_process_key(session_id, backend_name)
        _abort_if_send_superseded(process_key, owner_stop_event)
        _check_stop_requested(session_id, backend_name)

        if retry_callback is not None:
            await retry_callback(session_id, attempt, MAX_RETRIES, error_reason)
        logger.warning(
            "Повторная попытка %d/%d для сессии %s backend=%s",
            attempt, MAX_RETRIES, session_id, backend_name.value,
        )

        if backend_obj is None:
            await _wait_with_stop_check(session_id, RETRY_INTERVAL_SECONDS)
        else:
            await _wait_with_stop_check(
                session_id, RETRY_INTERVAL_SECONDS, backend_name, owner_stop_event,
            )
        _abort_if_send_superseded(process_key, owner_stop_event)
        _check_stop_requested(session_id, backend_name)

        result = await _execute_single_retry(
            session_id,
            text,
            attempt,
            cwd,
            progress_callback,
            session_id_callback,
            backend_obj=backend_obj,
            backend_name=backend_name,
            image_paths=image_paths,
            session_id_callback_includes_backend=session_id_callback_includes_backend,
        )
        if result is None:
            continue

        if result.session_id != session_id:
            session_id = result.session_id

        if not result.is_error:
            return SendResult(
                text=result.text, session_id=result.session_id,
                is_error=False, retries_used=attempt,
                backend=backend_name, error_text=None,
            )
        permanent_result = _classify_permanent_error_result(result, backend_name)
        if permanent_result is not None:
            return permanent_result
        logger.warning(
            "Ретрай %d вернул ошибку (сессия %s): %s",
            attempt, session_id, (result.error_text or result.text)[:200],
        )
        error_reason = (
            (result.error_text or result.text)[:200]
            if (result.error_text or result.text)
            else "неизвестная ошибка"
        )
        last_result = result

    return _build_exhausted_result(last_result, session_id, backend_name)


async def _wait_with_stop_check(
    session_id: str,
    duration_seconds: float,
    backend: BackendName = BackendName.CLAUDE,
    owner_stop_event: asyncio.Event | None = None,
) -> None:
    """Ждёт указанное время, но проверяет флаг отмены каждую секунду."""
    elapsed = 0.0
    while elapsed < duration_seconds:
        if _send_superseded_or_stopped(
            _make_backend_process_key(session_id, backend), owner_stop_event,
        ):
            raise ProcessStoppedError("Ожидание ретрая прервано командой /stop")
        process_key = _resolve_process_key_alias_unlocked(session_id, backend)
        if process_key not in _stop_events and backend == BackendName.CLAUDE:
            process_key = _resolve_session_id_alias_unlocked(session_id)
        stop_event = _stop_events.get(process_key)
        if stop_event is not None and stop_event.is_set():
            raise ProcessStoppedError("Ожидание ретрая прервано командой /stop")

        sleep_time = min(STOP_CHECK_INTERVAL_SECONDS, duration_seconds - elapsed)
        await asyncio.sleep(sleep_time)
        elapsed += sleep_time
