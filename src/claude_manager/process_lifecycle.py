"""Process creation and restart helpers for Claude/Codex CLI processes."""

import asyncio
import logging
import uuid

from claude_manager.coding_agent_backend import (
    BackendBinaryNotFoundError,
    BackendName,
    CodingAgentBackend,
)
from claude_manager.claude_runner import (
    BackendSubprocess,
    BackendSubprocessError,
    BackendSubprocessStartError,
    ClaudeStartError,
    start_subprocess_for_backend,
    start_process,
)
from claude_manager.process_state import (
    ManagedProcess,
    _busy_flags,
    _busy_lock,
    _make_backend_process_key,
    _prefer_existing_process_key_unlocked,
    _processes,
    _stop_events,
)
from claude_manager.process_stop import _apply_backend_stop_strategy
from claude_manager.process_types import (
    TEMP_SESSION_PREFIX,
    CodingAgentStartError,
    ProcessStoppedError,
)

logger = logging.getLogger("claude_manager.process_manager")


def _generate_temp_session_id() -> str:
    """Генерирует уникальный временный идентификатор сессии."""
    return f"{TEMP_SESSION_PREFIX}{uuid.uuid4().hex[:12]}"


async def _start_subprocess_for_backend_turn(
    backend_obj: CodingAgentBackend,
    session_id: str,
    cwd: str,
    prompt_text: str,
    image_paths: list[str],
) -> BackendSubprocess:
    """Запускает subprocess через backend adapter и нормализует ошибки запуска."""
    try:
        return await start_subprocess_for_backend(
            backend_obj, session_id, cwd, prompt_text, image_paths,
        )
    except (
        BackendBinaryNotFoundError,
        BackendSubprocessStartError,
        BackendSubprocessError,
        OSError,
    ) as error:
        logger.error(
            "Не удалось запустить backend CLI: backend=%s session_id=%s",
            backend_obj.name.value, session_id, exc_info=True,
        )
        raise CodingAgentStartError(
            f"Не удалось запустить CLI: {error}"
        ) from error


async def _restart_process(
    session_id: str,
    cwd: str,
    *,
    backend_obj: CodingAgentBackend | None = None,
    backend_name: BackendName = BackendName.CLAUDE,
    prompt_text: str = "",
    image_paths: list[str] | None = None,
) -> ManagedProcess:
    """Перезапускает процесс CLI для указанной сессии."""
    process_key = (
        _make_backend_process_key(session_id, backend_name)
        if backend_obj is not None
        else _prefer_existing_process_key_unlocked(session_id, backend_name)
    )
    async with _busy_lock:
        stop_event = _stop_events.get(process_key)
        if stop_event is not None and stop_event.is_set():
            raise ProcessStoppedError("Перезапуск прерван командой /stop")

    if backend_obj is None:
        is_temp_session = session_id.startswith(TEMP_SESSION_PREFIX)
        cli_session_id = None if is_temp_session else session_id
        try:
            claude_process: ManagedProcess = await start_process(cli_session_id, cwd=cwd)
        except ClaudeStartError as error:
            raise CodingAgentStartError(
                f"Не удалось запустить Claude: {error}"
            ) from error
    else:
        claude_process = await _start_subprocess_for_backend_turn(
            backend_obj,
            session_id,
            cwd,
            prompt_text,
            image_paths or [],
        )

    should_abort_restart = False
    orphan_to_kill: ManagedProcess | None = None
    async with _busy_lock:
        stop_event = _stop_events.get(process_key)
        if stop_event is not None and stop_event.is_set():
            should_abort_restart = True
        else:
            previous_process = _processes.get(process_key)
            if previous_process is not None and previous_process.is_running():
                orphan_to_kill = previous_process
            _processes[process_key] = claude_process
            _busy_flags[process_key] = True
            _stop_events[process_key] = stop_event or asyncio.Event()

    if orphan_to_kill is not None:
        logger.warning(
            "Обнаружен orphan-процесс при перезапуске: "
            "session_id=%s backend=%s old_pid=%d new_pid=%d",
            session_id, backend_name.value,
            orphan_to_kill.process.pid, claude_process.process.pid,
        )
        try:
            await _apply_backend_stop_strategy(orphan_to_kill, backend_name)
        except (ProcessLookupError, OSError) as stop_error:
            logger.warning(
                "Ошибка при остановке orphan-процесса PID=%d: %s",
                orphan_to_kill.process.pid, stop_error,
            )

    if should_abort_restart:
        if claude_process.is_running():
            await _apply_backend_stop_strategy(claude_process, backend_name)
        raise ProcessStoppedError("Перезапуск прерван командой /stop")

    logger.info(
        "Процесс CLI перезапущен: session_id=%s backend=%s PID=%d",
        session_id, backend_name.value, claude_process.process.pid,
    )
    return claude_process


async def create_process(session_id: str | None = None, cwd: str | None = None) -> str:
    """Запускает новый процесс Claude."""
    if session_id is not None:
        effective_session_id = session_id
    else:
        effective_session_id = _generate_temp_session_id()

    is_temp_session = effective_session_id.startswith(TEMP_SESSION_PREFIX)
    cli_session_id = None if is_temp_session else session_id

    try:
        claude_process = await start_process(cli_session_id, cwd=cwd)
    except ClaudeStartError as error:
        raise CodingAgentStartError(
            f"Не удалось запустить Claude: {error}"
        ) from error

    async with _busy_lock:
        _processes[effective_session_id] = claude_process
        _busy_flags[effective_session_id] = False
        _stop_events[effective_session_id] = asyncio.Event()

    process_type = "resume" if session_id is not None else "новый"
    logger.info(
        "Процесс Claude создан: session_id=%s, тип=%s, PID=%d",
        effective_session_id, process_type, claude_process.process.pid,
    )

    return effective_session_id
