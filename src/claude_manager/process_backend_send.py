"""Backend-aware send flow for Claude/Codex process turns."""

import asyncio
import logging

from claude_manager import config
from claude_manager.coding_agent_backend import (
    BackendName,
    BackendProtocolError,
    get_backend,
)
from claude_manager.claude_runner import BackendSubprocessError
from claude_manager.process_events import _process_events
from claude_manager.process_lifecycle import _restart_process
from claude_manager.process_retry import (
    _classify_permanent_error_result,
    _retry_loop,
)
from claude_manager.process_state import (
    _busy_flags,
    _busy_lock,
    _make_backend_process_key,
    _remove_session_id_aliases_unlocked,
    _stop_events,
)
from claude_manager.process_types import (
    ProcessManagerError,
    ProgressCallback,
    RetryCallback,
    SendResult,
    SessionIdCallback,
)

logger = logging.getLogger("claude_manager.process_manager")


def _validate_effective_backend(
    session_id: str,
    backend: BackendName | None,
) -> BackendName:
    """Проверяет обязательный backend для backend-aware turn-а."""
    if session_id is None:
        raise ProcessManagerError(
            "session_id обязателен; для новой сессии сначала создайте temp-id"
        )
    if backend is None:
        raise ProcessManagerError("backend обязателен для любого turn-а")
    return backend


async def _send_message_backend_aware(
    session_id: str,
    text: str,
    *,
    backend: BackendName | None | object,
    image_paths: list[str] | None,
    cwd: str | None,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
    session_id_callback: SessionIdCallback | None,
) -> SendResult:
    """Backend-aware send_message flow for Claude/Codex adapters."""
    effective_backend = _validate_effective_backend(session_id, backend)
    backend_obj = get_backend(effective_backend)
    effective_cwd = cwd if cwd is not None else config.WORKING_DIR
    effective_image_paths = image_paths if image_paths is not None else []
    current_session_id = session_id
    process_started = False

    async def _track_current_session_id(
        old_id: str,
        new_id: str,
        callback_backend: BackendName,
    ) -> None:
        nonlocal current_session_id
        current_session_id = new_id
        if session_id_callback is not None:
            await session_id_callback(old_id, new_id, callback_backend)

    process_key = _make_backend_process_key(session_id, effective_backend)
    owner_stop_event = asyncio.Event()
    async with _busy_lock:
        if _busy_flags.get(process_key, False):
            raise ProcessManagerError(f"Процесс {process_key} уже занят")
        _busy_flags[process_key] = True
        _stop_events[process_key] = owner_stop_event

    try:
        try:
            claude_process = await _restart_process(
                session_id,
                effective_cwd,
                backend_obj=backend_obj,
                backend_name=effective_backend,
                prompt_text=text,
                image_paths=effective_image_paths,
            )
            process_started = True
            result = await _process_events(
                claude_process,
                current_session_id,
                progress_callback,
                _track_current_session_id,
                backend_obj=backend_obj,
                backend_name=effective_backend,
                session_id_callback_includes_backend=True,
            )
        except BackendProtocolError as error:
            logger.warning(
                "Ошибка протокола backend CLI (сессия %s): %s",
                current_session_id, error,
            )
            result = SendResult(
                text="",
                session_id=current_session_id,
                is_error=True,
                retries_used=0,
                backend=effective_backend,
                error_text=str(error),
            )
        except BackendSubprocessError as error:
            logger.warning(
                "Ошибка backend subprocess (сессия %s): %s",
                current_session_id, error,
            )
            result = SendResult(
                text="",
                session_id=current_session_id,
                is_error=True,
                retries_used=0,
                backend=effective_backend,
                error_text=str(error),
            )

        if result.is_error:
            permanent_result = _classify_permanent_error_result(
                result, effective_backend,
            )
            if permanent_result is not None:
                return permanent_result
            error_reason = (
                (result.error_text or result.text)[:200]
                if (result.error_text or result.text)
                else "неизвестная ошибка"
            )
            return await _retry_loop(
                result.session_id,
                text,
                effective_cwd,
                error_reason,
                progress_callback,
                retry_callback,
                _track_current_session_id,
                backend_obj=backend_obj,
                backend_name=effective_backend,
                image_paths=effective_image_paths,
                session_id_callback_includes_backend=True,
                owner_stop_event=owner_stop_event,
            )
        return result
    finally:
        async with _busy_lock:
            cleanup_keys = {
                _make_backend_process_key(session_id, effective_backend),
                _make_backend_process_key(current_session_id, effective_backend),
            }
            for cleanup_key in cleanup_keys:
                if _stop_events.get(cleanup_key) is not owner_stop_event:
                    continue
                if cleanup_key in _busy_flags:
                    if process_started:
                        _busy_flags[cleanup_key] = False
                    else:
                        _busy_flags.pop(cleanup_key, None)
                _stop_events.pop(cleanup_key, None)
            cleanup_session_ids = {session_id, current_session_id}
            _remove_session_id_aliases_unlocked(cleanup_session_ids)
