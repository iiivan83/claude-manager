"""Управление жизненным циклом процессов Claude Code.

Запускает процессы, отправляет сообщения, читает потоковые события,
обрабатывает ошибки с автоматическими ретраями и предоставляет
механизм остановки через /stop.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from claude_manager.claude_runner import (
    ClaudeProcess,
    ClaudeProcessError,
    ClaudeStartError,
    start_process,
)

logger = logging.getLogger(__name__)

# --- Типы обратных вызовов ---

type ProgressCallback = Callable[[str, str], Awaitable[None]]
type RetryCallback = Callable[[str, int, int], Awaitable[None]]
type SessionIdCallback = Callable[[str, str], Awaitable[None]]

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


@dataclass(frozen=True)
class StopResult:
    """Результат остановки процесса."""

    was_running: bool
    was_retrying: bool


# --- Состояние модуля ---

# Запущенные процессы: session_id -> ClaudeProcess
_processes: dict[str, ClaudeProcess] = {}

# Флаги занятости: session_id -> True/False
_busy_flags: dict[str, bool] = {}

# Блокировка для атомарных операций над _busy_flags, _processes, _stop_events.
# Захватывается только на короткие критические секции — не на всё время обработки.
_busy_lock: asyncio.Lock = asyncio.Lock()

# События отмены для прерывания ретраев через /stop
_stop_events: dict[str, asyncio.Event] = {}

# --- Внутренние функции ---


def _generate_temp_session_id() -> str:
    """Генерирует уникальный временный идентификатор сессии."""
    return f"{TEMP_SESSION_PREFIX}{uuid.uuid4().hex[:12]}"


def _extract_progress_text(event: dict) -> str | None:
    """Извлекает текст рассуждений Claude из события assistant."""
    if event.get("type") != EVENT_TYPE_ASSISTANT:
        return None

    content_blocks = event.get("message", {}).get("content", [])

    for block in content_blocks:
        if block.get("type") == "thinking":
            return block.get("thinking")

    return None


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


def _check_stop_requested(session_id: str) -> None:
    """Проверяет, запросил ли пользователь отмену. Бросает ProcessStoppedError."""
    stop_event = _stop_events.get(session_id)
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


async def _read_stderr(claude_process: ClaudeProcess) -> str:
    """Читает stderr процесса Claude — там может быть причина падения."""
    stderr_max_length = 500
    try:
        if claude_process.process.stderr is None:
            return ""
        raw = await claude_process.process.stderr.read(stderr_max_length)
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


async def _process_events(
    claude_process: ClaudeProcess,
    session_id: str,
    progress_callback: ProgressCallback | None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult:
    """Читает поток событий от Claude и собирает результат."""
    last_progress_time = 0.0
    result_text = ""
    final_session_id = session_id
    is_error = False
    got_result = False
    callback_fired = False

    async for event in claude_process.read_events():
        _check_stop_requested(session_id)

        event_session_id = event.get("session_id")
        if event_session_id is not None and event_session_id != final_session_id:
            old_final = final_session_id
            final_session_id = event_session_id
            if session_id_callback is not None and not callback_fired:
                callback_fired = True
                try:
                    await session_id_callback(old_final, event_session_id)
                except Exception:
                    logger.error(
                        "Ошибка в session_id_callback: %s -> %s",
                        old_final, event_session_id, exc_info=True,
                    )

        last_progress_time = await _handle_progress_event(
            event, session_id, progress_callback, last_progress_time,
        )

        if event.get("type") == EVENT_TYPE_RESULT:
            result_text = _extract_result_text(event)
            is_error = _is_error_result(event)
            got_result = True

    if not got_result:
        # Читаем stderr — там может быть реальная причина падения Claude CLI
        stderr_text = await _read_stderr(claude_process)
        stderr_info = f" stderr: {stderr_text}" if stderr_text else ""
        logger.warning(
            "Процесс Claude завершился без события result: session_id=%s%s",
            session_id, stderr_info,
        )
        # Если есть текст ошибки из stderr — показываем его пользователю
        if stderr_text:
            result_text = stderr_text

    return SendResult(
        text=result_text if got_result else result_text,
        session_id=final_session_id,
        is_error=is_error or not got_result,
        retries_used=0,
    )


async def _execute_single_retry(
    session_id: str,
    text: str,
    attempt: int,
    progress_callback: ProgressCallback | None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult | None:
    """Выполняет одну попытку ретрая. Возвращает результат или None при ошибке."""
    old_process = _processes.get(session_id)
    if old_process is not None and old_process.is_running():
        await old_process.terminate()
    claude_process = await _restart_process(session_id)

    try:
        await claude_process.send_message(text)
        return await _process_events(
            claude_process, session_id, progress_callback, session_id_callback,
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
    last_result: SendResult | None, session_id: str,
) -> SendResult:
    """Формирует результат после исчерпания всех ретраев."""
    logger.error(
        "Все %d ретраев исчерпаны для сессии %s", MAX_RETRIES, session_id,
    )
    if last_result is not None:
        return SendResult(
            text=last_result.text, session_id=last_result.session_id,
            is_error=True, retries_used=MAX_RETRIES,
        )
    return SendResult(
        text="", session_id=session_id,
        is_error=True, retries_used=MAX_RETRIES,
    )


async def _retry_loop(
    session_id: str,
    text: str,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult:
    """Цикл повторных попыток при ошибке от Claude."""
    last_result: SendResult | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        _check_stop_requested(session_id)

        if retry_callback is not None:
            await retry_callback(session_id, attempt, MAX_RETRIES)
        logger.warning(
            "Повторная попытка %d/%d для сессии %s",
            attempt, MAX_RETRIES, session_id,
        )

        await _wait_with_stop_check(session_id, RETRY_INTERVAL_SECONDS)

        result = await _execute_single_retry(
            session_id, text, attempt, progress_callback, session_id_callback,
        )
        if result is None:
            continue

        if not result.is_error:
            return SendResult(
                text=result.text, session_id=result.session_id,
                is_error=False, retries_used=attempt,
            )
        last_result = result

    return _build_exhausted_result(last_result, session_id)


async def _wait_with_stop_check(session_id: str, duration_seconds: float) -> None:
    """Ждёт указанное время, но проверяет флаг отмены каждую секунду."""
    elapsed = 0.0
    while elapsed < duration_seconds:
        stop_event = _stop_events.get(session_id)
        if stop_event is not None and stop_event.is_set():
            raise ProcessStoppedError("Ожидание ретрая прервано командой /stop")

        sleep_time = min(_STOP_CHECK_INTERVAL_SECONDS, duration_seconds - elapsed)
        await asyncio.sleep(sleep_time)
        elapsed += sleep_time


async def _restart_process(session_id: str) -> ClaudeProcess:
    """Перезапускает процесс Claude для указанной сессии (с resume)."""
    # Для временных сессий — запускаем без --resume
    is_temp_session = session_id.startswith(TEMP_SESSION_PREFIX)
    cli_session_id = None if is_temp_session else session_id

    try:
        claude_process = await start_process(cli_session_id)
    except ClaudeStartError as error:
        raise ProcessManagerError(
            f"Не удалось запустить Claude: {error}"
        ) from error

    # Инвариант: все три словаря (_processes, _busy_flags, _stop_events)
    # обновляются атомарно — по аналогии с create_process().
    # Без этого перезапущенный процесс неуправляем: повторный /stop
    # не найдёт stop_event, is_busy() не увидит busy_flag.
    async with _busy_lock:
        _processes[session_id] = claude_process
        _busy_flags[session_id] = True
        _stop_events[session_id] = asyncio.Event()

    logger.info(
        "Процесс Claude перезапущен: session_id=%s, PID=%d",
        session_id, claude_process.process.pid,
    )
    return claude_process


# --- Публичный API ---


async def create_process(session_id: str | None = None) -> str:
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
        claude_process = await start_process(cli_session_id)
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


async def send_message(
    session_id: str,
    text: str,
    progress_callback: ProgressCallback | None = None,
    retry_callback: RetryCallback | None = None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult:
    """Отправляет сообщение в процесс Claude и ожидает ответ."""
    # Критическая секция 1: атомарная проверка «не занят» + установка «занят».
    # Lock защищает от race condition, когда два send_message для одной сессии
    # одновременно проходят проверку и оба устанавливают busy=True.
    async with _busy_lock:
        claude_process = _validate_process_ready(session_id)
        _prepare_for_send(session_id)

    # Lock освобождён — долгая работа идёт без блокировки.
    # stop_process может захватить Lock и очистить busy, пока мы здесь.
    try:
        result = await _execute_send(
            session_id, text, claude_process,
            progress_callback, retry_callback, session_id_callback,
        )
        if result.session_id != session_id:
            # update_session_id захватывает _busy_lock внутри себя.
            # Lock не удерживается здесь — deadlock невозможен.
            await update_session_id(session_id, result.session_id)
            session_id = result.session_id
        return result
    finally:
        # Критическая секция 2: безопасная очистка busy и stop_event.
        # Проверяем наличие ключа, чтобы не создать «зомби» после stop_process.
        # Сценарий без проверки: stop_process.pop удаляет ключ → finally пишет
        # _busy_flags[session_id] = False → ключ воскресает как «зомби».
        # stop_event очищается здесь (а не в stop_process), чтобы retry loop
        # мог обнаружить флаг отмены через _check_stop_requested().
        async with _busy_lock:
            if session_id in _busy_flags:
                _busy_flags[session_id] = False
            _stop_events.pop(session_id, None)


async def _execute_send(
    session_id: str,
    text: str,
    claude_process: ClaudeProcess,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
    session_id_callback: SessionIdCallback | None = None,
) -> SendResult:
    """Выполняет отправку сообщения с обработкой ошибок и ретраями."""
    try:
        await claude_process.send_message(text)
        result = await _process_events(
            claude_process, session_id, progress_callback, session_id_callback,
        )
    except ClaudeProcessError as error:
        # Ошибка отправки или чтения — пробуем ретраи
        logger.warning(
            "Ошибка при взаимодействии с Claude (сессия %s): %s",
            session_id, error,
        )
        return await _retry_loop(
            session_id, text, progress_callback, retry_callback, session_id_callback,
        )

    # Если Claude вернул ошибку — запускаем ретраи
    if result.is_error:
        logger.warning(
            "Claude вернул ошибку (сессия %s): %s", session_id, result.text[:200],
        )
        return await _retry_loop(
            session_id, text, progress_callback, retry_callback, session_id_callback,
        )

    return result


async def stop_process(session_id: str) -> StopResult:
    """Останавливает процесс Claude в указанной сессии."""
    # Критическая секция: читаем состояние, устанавливаем флаг отмены
    # и удаляем записи из _processes/_busy_flags атомарно.
    # Гарантирует, что send_message.finally увидит отсутствие ключа
    # и не создаст зомби-запись после нашего pop.
    async with _busy_lock:
        claude_process = _processes.get(session_id)
        was_retrying = _busy_flags.get(session_id, False)

        # Устанавливаем флаг отмены — прервёт ожидание ретрая
        stop_event = _stop_events.get(session_id)
        if stop_event is not None:
            stop_event.set()

        # Очищаем словари — до terminate, чтобы finally увидел отсутствие ключа.
        # stop_event НЕ удаляется: retry loop проверяет его через
        # _check_stop_requested() и _wait_with_stop_check(). Очистка — в
        # finally блоке send_message() после завершения retry loop.
        _processes.pop(session_id, None)
        _busy_flags.pop(session_id, None)

    # Завершаем процесс вне Lock — terminate может быть долгим (ждёт завершения)
    was_running = False
    if claude_process is not None and claude_process.is_running():
        was_running = True
        await claude_process.terminate()

    logger.info(
        "Процесс остановлен: session_id=%s, was_running=%s, was_retrying=%s",
        session_id, was_running, was_retrying,
    )

    return StopResult(was_running=was_running, was_retrying=was_retrying)


async def stop_all_processes() -> int:
    """Останавливает все запущенные процессы Claude и возвращает количество остановленных."""
    # Копируем ключи в список, чтобы итерация не конфликтовала с модификацией _processes
    session_ids = list(_processes.keys())
    stopped_count = 0

    for session_id in session_ids:
        try:
            await stop_process(session_id)
            stopped_count += 1
        except Exception:
            # Ошибка при остановке одного процесса не должна прерывать остановку остальных
            logger.error(
                "Ошибка при остановке процесса %s", session_id, exc_info=True,
            )

    logger.info("Остановлено процессов Claude: %d", stopped_count)
    return stopped_count


def is_busy(session_id: str) -> bool:
    """Проверяет, обрабатывает ли процесс запрос прямо сейчас."""
    return _busy_flags.get(session_id, False)


def has_process(session_id: str) -> bool:
    """Проверяет, есть ли запущенный процесс для указанной сессии."""
    claude_process = _processes.get(session_id)
    if claude_process is None:
        return False
    return claude_process.is_running()


async def update_session_id(old_session_id: str, new_session_id: str) -> None:
    """Обновляет ключ сессии во всех внутренних словарях."""
    # Атомарный перенос: все три словаря обновляются за один захват Lock.
    # Без Lock: другая корутина может прочитать словарь между pop и присвоением
    # нового ключа — и увидеть промежуточное состояние (ключ удалён, но новый не создан).
    async with _busy_lock:
        for storage in (_processes, _busy_flags, _stop_events):
            if old_session_id in storage:
                storage[new_session_id] = storage.pop(old_session_id)

    logger.info(
        "Session ID обновлён: %s -> %s", old_session_id, new_session_id,
    )
