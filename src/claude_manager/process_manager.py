"""Управление жизненным циклом процессов Claude Code.

Запускает процессы, отправляет сообщения, читает потоковые события,
обрабатывает ошибки с автоматическими ретраями и предоставляет
механизм остановки через /stop.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from claude_manager import config
from claude_manager.coding_agent_backend import (
    BackendBinaryNotFoundError,
    BackendName,
    BackendProtocolError,
    CodingAgentBackend,
    PermanentErrorKind,
    StopStrategy,
    TerminalStatus,
    UnifiedEvent,
    get_backend,
)
from claude_manager.claude_runner import (
    BackendSubprocess,
    BackendSubprocessError,
    BackendSubprocessStartError,
    ClaudeProcess,
    ClaudeProcessError,
    ClaudeStartError,
    start_subprocess_for_backend,
    start_process,
)
from claude_manager.process_state import (
    ManagedProcess,
    ProcessKey,
    _busy_flags,
    _busy_lock,
    _make_backend_process_key,
    _make_process_key,
    _prefer_existing_process_key_unlocked,
    _processes,
    _remove_session_id_aliases_unlocked,
    _resolve_process_key_alias_unlocked,
    _resolve_session_id_alias_unlocked,
    _session_id_aliases,
    _split_process_key,
    _stop_events,
    has_process,
    is_busy,
    update_session_id,
)

logger = logging.getLogger(__name__)

# --- Типы обратных вызовов ---

type ProgressCallback = Callable[[str, str], Awaitable[None]]
type RetryCallback = Callable[[str, int, int, str], Awaitable[None]]
type SessionIdCallback = Callable[..., Awaitable[None]]

# --- Константы ---

# Максимальное количество повторных попыток при ошибке от Claude
MAX_RETRIES = 10

# Интервал между повторными попытками (секунды)
RETRY_INTERVAL_SECONDS = 60

# Минимальный интервал между промежуточными обновлениями (секунды)
PROGRESS_THROTTLE_SECONDS = 30

# Тип финального события от Claude
EVENT_TYPE_RESULT = "result"

# Тип события с ответом или рассуждением Claude
EVENT_TYPE_ASSISTANT = "assistant"

# Типы контент-блоков внутри assistant-события
CONTENT_BLOCK_TEXT = "text"
CONTENT_BLOCK_THINKING = "thinking"

# Префикс временных идентификаторов сессий
TEMP_SESSION_PREFIX = "_new_"

# Служебный ответ Claude, который не нужно пересылать пользователю
EMPTY_RESPONSE_MARKER = "No response requested."

# Интервал проверки флага отмены внутри ожидания ретрая (секунды)
_STOP_CHECK_INTERVAL_SECONDS = 1


# --- Исключения ---


class ProcessManagerError(Exception):
    """Общая ошибка process_manager (не удалось запустить процесс)."""


class ProcessNotFoundError(Exception):
    """Для указанного session_id нет запущенного процесса."""


class ProcessStoppedError(Exception):
    """Запрос прерван командой /stop."""


# --- Результаты ---


@dataclass(frozen=True)
class SendResult:
    """Результат отправки сообщения в Claude."""

    text: str
    session_id: str
    is_error: bool
    retries_used: int
    backend: BackendName = BackendName.CLAUDE
    error_text: str | None = None
    # Заполняется, если ошибка постоянная (повтор бессмыслен): транспортный
    # слой по этому полю показывает понятное сообщение вместо «повтор N/10».
    permanent_error_kind: PermanentErrorKind | None = None


@dataclass(frozen=True)
class StopResult:
    """Результат остановки процесса."""

    was_running: bool
    was_retrying: bool
    backend: BackendName = BackendName.CLAUDE


# --- Внутренние функции ---


def _generate_temp_session_id() -> str:
    """Генерирует уникальный временный идентификатор сессии."""
    return f"{TEMP_SESSION_PREFIX}{uuid.uuid4().hex[:12]}"


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
    # Первое обновление — всегда отправляем
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


async def _handle_progress_event(
    event: dict,
    session_id: str,
    progress_callback: ProgressCallback | None,
    last_progress_time: float,
) -> float:
    """Обрабатывает промежуточное обновление. Возвращает обновлённое время."""
    progress_text = _extract_progress_text(event)
    if progress_text and progress_callback and _should_send_progress(last_progress_time):
        await progress_callback(session_id, progress_text)
        return time.monotonic()
    return last_progress_time


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
    # Look-ahead против дубля «промежуточное+финальное»: текст из assistant-события
    # сначала кладётся в pending, а уходит к пользователю как progress только если
    # позже мы убедились, что он НЕ совпадает с финальным result.result.
    # Совпадает → подавляем (финал уйдёт один раз через SendResult).
    # Не совпадает → это был thinking или промежуточный текст до tool_use → flush.
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
            # Если pending не совпадает с финалом — это был thinking/промежуточный
            # текст, его надо доставить пользователю как progress.
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
        # Читаем stderr — там может быть реальная причина падения CLI
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
        raise ProcessManagerError(
            f"Не удалось запустить CLI: {error}"
        ) from error


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

    try:
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
    """Возвращает готовый результат, если повторять ошибку бессмысленно.

    Спрашивает у backend-контракта, постоянная ли это ошибка (переполнение
    контекста, исчерпанный лимит). Если да — повтор не нужен: собираем финальный
    результат с пометкой permanent_error_kind, чтобы транспортный слой показал
    пользователю понятное сообщение вместо «повтор N/10». None — ошибка может
    быть временной, повтор имеет смысл.
    """
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
) -> SendResult:
    """Цикл повторных попыток при ошибке от backend CLI."""
    last_result: SendResult | None = None

    for attempt in range(1, MAX_RETRIES + 1):
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
                session_id, RETRY_INTERVAL_SECONDS, backend_name,
            )
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
) -> None:
    """Ждёт указанное время, но проверяет флаг отмены каждую секунду."""
    elapsed = 0.0
    while elapsed < duration_seconds:
        process_key = _resolve_process_key_alias_unlocked(session_id, backend)
        if process_key not in _stop_events and backend == BackendName.CLAUDE:
            process_key = _resolve_session_id_alias_unlocked(session_id)
        stop_event = _stop_events.get(process_key)
        if stop_event is not None and stop_event.is_set():
            raise ProcessStoppedError("Ожидание ретрая прервано командой /stop")

        sleep_time = min(_STOP_CHECK_INTERVAL_SECONDS, duration_seconds - elapsed)
        await asyncio.sleep(sleep_time)
        elapsed += sleep_time


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
        # Для временных сессий — запускаем без --resume.
        is_temp_session = session_id.startswith(TEMP_SESSION_PREFIX)
        cli_session_id = None if is_temp_session else session_id
        try:
            claude_process: ManagedProcess = await start_process(cli_session_id, cwd=cwd)
        except ClaudeStartError as error:
            raise ProcessManagerError(
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

    # Инвариант: все три словаря (_processes, _busy_flags, _stop_events)
    # обновляются атомарно — по аналогии с create_process().
    # Без этого перезапущенный процесс неуправляем: повторный /stop
    # не найдёт stop_event, is_busy() не увидит busy_flag.
    should_abort_restart = False
    orphan_to_kill: ManagedProcess | None = None
    async with _busy_lock:
        stop_event = _stop_events.get(process_key)
        if stop_event is not None and stop_event.is_set():
            should_abort_restart = True
        else:
            # Защита от orphan: subprocess из предыдущего turn'а
            # мог не умереть сам после result event (медленный shutdown,
            # зависший pipe, ошибка cleanup внутри CLI). Если ссылка
            # на него ещё в _processes — остановим его до перезаписи.
            # Без этого старый PID становится orphan'ом: stop_process
            # будет видеть только новую ссылку.
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


# --- Публичный API ---


async def create_process(session_id: str | None = None, cwd: str | None = None) -> str:
    """Запускает новый процесс Claude."""
    # Определяем идентификатор сессии
    if session_id is not None:
        effective_session_id = session_id
    else:
        effective_session_id = _generate_temp_session_id()

    # Для новых сессий (временный ID типа _new_XXXX) — запускаем без --resume,
    # чтобы Claude создал свежую сессию. Для существующих — передаём ID для resume.
    is_temp_session = effective_session_id.startswith(TEMP_SESSION_PREFIX)
    cli_session_id = None if is_temp_session else session_id

    try:
        claude_process = await start_process(cli_session_id, cwd=cwd)
    except ClaudeStartError as error:
        raise ProcessManagerError(
            f"Не удалось запустить Claude: {error}"
        ) from error

    # Сохраняем процесс и инициализируем служебные структуры.
    # Lock гарантирует, что другая корутина не увидит процесс без busy-флага
    # (промежуточное состояние между записью в _processes и _busy_flags).
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


_BACKEND_NOT_PROVIDED = object()


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

    # Критическая секция 1: атомарная проверка «не занят» + установка «занят».
    # Lock защищает от race condition, когда два send_message для одной сессии
    # одновременно проходят проверку и оба устанавливают busy=True.
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
            # update_session_id захватывает _busy_lock внутри себя.
            # Lock не удерживается здесь — deadlock невозможен.
            await update_session_id(session_id, result.session_id)
            session_id = result.session_id
            current_session_id = result.session_id
        return result
    finally:
        # Критическая секция 2: безопасная очистка busy и stop_event.
        # Проверяем наличие ключа, чтобы не создать «зомби» после stop_process.
        # Сценарий без проверки: stop_process.pop удаляет ключ → finally пишет
        # _busy_flags[session_id] = False → ключ воскресает как «зомби».
        # stop_event очищается здесь (а не в stop_process), чтобы retry loop
        # мог обнаружить флаг отмены через _check_stop_requested().
        async with _busy_lock:
            cleanup_session_ids = {session_id, current_session_id}
            for cleanup_session_id in cleanup_session_ids:
                if cleanup_session_id in _busy_flags:
                    _busy_flags[cleanup_session_id] = False
                _stop_events.pop(cleanup_session_id, None)
            _remove_session_id_aliases_unlocked(cleanup_session_ids)


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
    async with _busy_lock:
        if _busy_flags.get(process_key, False):
            raise ProcessManagerError(f"Процесс {process_key} уже занят")
        _busy_flags[process_key] = True
        _stop_events[process_key] = asyncio.Event()

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
            )
        return result
    finally:
        async with _busy_lock:
            cleanup_keys = {
                _make_backend_process_key(session_id, effective_backend),
                _make_backend_process_key(current_session_id, effective_backend),
            }
            for cleanup_key in cleanup_keys:
                if cleanup_key in _busy_flags:
                    if process_started:
                        _busy_flags[cleanup_key] = False
                    else:
                        _busy_flags.pop(cleanup_key, None)
                _stop_events.pop(cleanup_key, None)
            cleanup_session_ids = {session_id, current_session_id}
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
    # Критическая секция: читаем состояние, устанавливаем флаг отмены
    # и удаляем записи из _processes/_busy_flags атомарно.
    # Гарантирует, что send_message.finally увидит отсутствие ключа
    # и не создаст зомби-запись после нашего pop.
    async with _busy_lock:
        original_session_id = session_id
        process_key = _prefer_existing_process_key_unlocked(session_id, backend)
        session_id, resolved_backend = _split_process_key(process_key)
        claude_process = _processes.get(process_key)
        was_retrying = _busy_flags.get(process_key, False)

        # Устанавливаем флаг отмены — прервёт ожидание ретрая
        stop_event = _stop_events.get(process_key)
        if stop_event is not None:
            stop_event.set()

        # Очищаем словари — до terminate, чтобы finally увидел отсутствие ключа.
        # stop_event НЕ удаляется: retry loop проверяет его через
        # _check_stop_requested() и _wait_with_stop_check(). Очистка — в
        # finally блоке send_message() после завершения retry loop.
        _processes.pop(process_key, None)
        _busy_flags.pop(process_key, None)
        if original_session_id != session_id:
            logger.info(
                "Session ID для остановки разрешён через алиас: %s -> %s",
                original_session_id, session_id,
            )

    # Завершаем процесс вне Lock — terminate может быть долгим (ждёт завершения)
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
    # Копируем ключи в список, чтобы итерация не конфликтовала с модификацией _processes
    process_keys = list(_processes.keys())
    stopped_count = 0

    for process_key in process_keys:
        session_id, backend = _split_process_key(process_key)
        try:
            await stop_process(session_id, backend)
            stopped_count += 1
        except Exception:
            # Ошибка при остановке одного процесса не должна прерывать остановку остальных
            logger.error(
                "Ошибка при остановке процесса %s", session_id, exc_info=True,
            )

    logger.info("Остановлено процессов Claude: %d", stopped_count)
    return stopped_count
