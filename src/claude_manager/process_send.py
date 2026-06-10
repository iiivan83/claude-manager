"""Legacy send flow and dispatcher for managed CLI processes."""

import logging

from claude_manager import config
from claude_manager.coding_agent_backend import BackendName
from claude_manager.claude_runner import (
    ClaudeProcess,
    ClaudeProcessError,
)
from claude_manager.process_backend_send import (
    _send_message_backend_aware,
    _validate_effective_backend,
)
from claude_manager.process_events import _process_events
from claude_manager.process_retry import (
    _classify_permanent_error_result,
    _retry_loop,
)
from claude_manager.process_state import (
    _busy_flags,
    _busy_lock,
    _processes,
    _remove_session_id_aliases_unlocked,
    _stop_events,
    update_session_id,
)
from claude_manager.process_types import (
    ProcessManagerError,
    ProcessNotFoundError,
    ProgressCallback,
    RetryCallback,
    SendResult,
    SessionIdCallback,
)

logger = logging.getLogger("claude_manager.process_manager")

_BACKEND_NOT_PROVIDED = object()


def _validate_process_ready(session_id: str) -> ClaudeProcess:
    """Проверяет, что процесс существует и не занят. Возвращает процесс."""
    claude_process = _processes.get(session_id)
    if claude_process is None:
        raise ProcessNotFoundError(
            f"Нет запущенного процесса для сессии '{session_id}'"
        )
    if _busy_flags.get(session_id, False):
        logger.warning("Процесс для сессии %s уже обрабатывает запрос", session_id)
        raise ProcessManagerError(
            f"Процесс для сессии '{session_id}' уже занят другим запросом"
        )
    return claude_process


def _prepare_for_send(session_id: str) -> None:
    """Устанавливает флаг занятости и сбрасывает флаг отмены."""
    _busy_flags[session_id] = True
    stop_event = _stop_events.get(session_id)
    if stop_event is not None:
        stop_event.clear()


async def send_message(
    session_id: str,
    text: str,
    progress_callback: ProgressCallback | None = None,
    retry_callback: RetryCallback | None = None,
    session_id_callback: SessionIdCallback | None = None,
    *,
    backend: BackendName | None | object = _BACKEND_NOT_PROVIDED,
    image_paths: list[str] | None = None,
    cwd: str | None = None,
) -> SendResult:
    """Отправляет сообщение в CLI-процесс и ожидает ответ."""
    if backend is _BACKEND_NOT_PROVIDED:
        return await _send_message_legacy_claude(
            session_id,
            text,
            progress_callback,
            retry_callback,
            session_id_callback,
        )
    return await _send_message_backend_aware(
        session_id,
        text,
        backend=backend,
        image_paths=image_paths,
        cwd=cwd,
        progress_callback=progress_callback,
        retry_callback=retry_callback,
        session_id_callback=session_id_callback,
    )


async def _send_message_legacy_claude(
    session_id: str,
    text: str,
    progress_callback: ProgressCallback | None = None,
    retry_callback: RetryCallback | None = None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult:
    """Старый Claude-only путь для текущего Telegram-слоя до Task 13."""
    current_session_id = session_id

    async def _track_current_session_id(
        old_id: str, new_id: str,
    ) -> None:
        nonlocal current_session_id
        current_session_id = new_id
        if session_id_callback is not None:
            await session_id_callback(old_id, new_id)

    effective_session_id_callback: SessionIdCallback = _track_current_session_id

    async with _busy_lock:
        claude_process = _validate_process_ready(session_id)
        _prepare_for_send(session_id)

    cwd = config.WORKING_DIR

    try:
        result = await _execute_send(
            session_id, text, claude_process, cwd,
            progress_callback, retry_callback, effective_session_id_callback,
        )
        if result.session_id != session_id:
            await update_session_id(session_id, result.session_id)
            session_id = result.session_id
            current_session_id = result.session_id
        return result
    finally:
        async with _busy_lock:
            cleanup_session_ids = {session_id, current_session_id}
            for cleanup_session_id in cleanup_session_ids:
                if cleanup_session_id in _busy_flags:
                    _busy_flags[cleanup_session_id] = False
                _stop_events.pop(cleanup_session_id, None)
            _remove_session_id_aliases_unlocked(cleanup_session_ids)


async def _execute_send(
    session_id: str,
    text: str,
    claude_process: ClaudeProcess,
    cwd: str,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult:
    """Выполняет отправку сообщения с обработкой ошибок и ретраями."""
    current_session_id = session_id

    if session_id_callback is not None:
        original_callback = session_id_callback

        async def _tracking_session_id_callback(
            old_id: str, new_id: str,
        ) -> None:
            nonlocal current_session_id
            current_session_id = new_id
            await original_callback(old_id, new_id)

        effective_callback: SessionIdCallback | None = (
            _tracking_session_id_callback
        )
    else:
        effective_callback = None

    try:
        await claude_process.send_message(text)
        result = await _process_events(
            claude_process, current_session_id,
            progress_callback, effective_callback,
        )
    except ClaudeProcessError as error:
        logger.warning(
            "Ошибка при взаимодействии с Claude (сессия %s): %s",
            current_session_id, error,
        )
        error_reason = str(error)[:200]
        return await _retry_loop(
            current_session_id, text, cwd, error_reason,
            progress_callback, retry_callback, effective_callback,
        )

    if result.is_error:
        permanent_result = _classify_permanent_error_result(
            result, BackendName.CLAUDE,
        )
        if permanent_result is not None:
            return permanent_result
        logger.warning(
            "Claude вернул ошибку (сессия %s): %s",
            current_session_id, result.text[:200],
        )
        error_reason = result.text[:200] if result.text else "неизвестная ошибка"
        return await _retry_loop(
            current_session_id, text, cwd, error_reason,
            progress_callback, retry_callback, effective_callback,
        )

    return result
