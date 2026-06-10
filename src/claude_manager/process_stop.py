"""Stop helpers for managed CLI processes."""

import asyncio
import logging

from claude_manager.coding_agent_backend import (
    BackendName,
    StopStrategy,
    get_backend,
)
from claude_manager.process_state import (
    ManagedProcess,
    _busy_flags,
    _busy_lock,
    _prefer_existing_process_key_unlocked,
    _processes,
    _split_process_key,
    _stop_events,
)
from claude_manager.process_types import StopResult

logger = logging.getLogger("claude_manager.process_manager")


async def _apply_stop_strategy(
    managed_process: ManagedProcess,
    strategy: StopStrategy,
) -> None:
    """Останавливает subprocess через последовательность сигналов strategy."""
    process = managed_process.process
    if process.returncode is not None:
        return

    if not strategy.steps:
        process.kill()
        await process.wait()
        return

    *initial_steps, final_step = strategy.steps
    for step in initial_steps:
        if process.returncode is not None:
            return
        process.send_signal(step.signal_to_send)
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=step.wait_seconds_before_next,
            )
            return
        except asyncio.TimeoutError:
            continue

    if process.returncode is None:
        process.send_signal(final_step.signal_to_send)
        await process.wait()


async def _apply_backend_stop_strategy(
    claude_process: ManagedProcess,
    backend: BackendName,
) -> None:
    """Останавливает процесс через backend-specific stop strategy."""
    strategy = get_backend(backend).get_stop_strategy()
    await _apply_stop_strategy(claude_process, strategy)


async def stop_process(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> StopResult:
    """Останавливает процесс Claude в указанной сессии."""
    async with _busy_lock:
        original_session_id = session_id
        process_key = _prefer_existing_process_key_unlocked(session_id, backend)
        session_id, resolved_backend = _split_process_key(process_key)
        claude_process = _processes.get(process_key)
        was_retrying = _busy_flags.get(process_key, False)

        stop_event = _stop_events.get(process_key)
        if stop_event is not None:
            stop_event.set()

        _processes.pop(process_key, None)
        _busy_flags.pop(process_key, None)
        if original_session_id != session_id:
            logger.info(
                "Session ID для остановки разрешён через алиас: %s -> %s",
                original_session_id, session_id,
            )

    was_running = False
    if claude_process is not None and claude_process.is_running():
        was_running = True
        await _apply_backend_stop_strategy(claude_process, resolved_backend)

    logger.info(
        "Процесс остановлен: session_id=%s, was_running=%s, was_retrying=%s",
        session_id, was_running, was_retrying,
    )

    return StopResult(
        was_running=was_running,
        was_retrying=was_retrying,
        backend=resolved_backend,
    )


async def stop_all_processes() -> int:
    """Останавливает все запущенные процессы Claude и возвращает количество остановленных."""
    process_keys = list(_processes.keys())
    stopped_count = 0

    for process_key in process_keys:
        session_id, backend = _split_process_key(process_key)
        try:
            await stop_process(session_id, backend)
            stopped_count += 1
        except Exception:
            logger.error(
                "Ошибка при остановке процесса %s", session_id, exc_info=True,
            )

    logger.info("Остановлено процессов Claude: %d", stopped_count)
    return stopped_count
