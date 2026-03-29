"""Управление жизненным циклом процессов Claude Code.

Запускает процессы, отправляет сообщения, читает потоковые события,
обрабатывает ошибки с автоматическими ретраями и предоставляет
механизм остановки через /stop.
"""

import asyncio
import logging
import time
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

# События отмены для прерывания ретраев через /stop
_stop_events: dict[str, asyncio.Event] = {}

# Счётчик для генерации временных session_id
_temp_session_counter: int = 0


# --- Внутренние функции ---


def _generate_temp_session_id() -> str:
    """Генерирует уникальный временный идентификатор сессии."""
    global _temp_session_counter
    _temp_session_counter += 1
    return f"{TEMP_SESSION_PREFIX}{_temp_session_counter:04d}"


def _extract_progress_text(event: dict) -> str | None:
    """Извлекает текст рассуждений Claude из события assistant."""
    if event.get("type") != EVENT_TYPE_ASSISTANT:
        return None

    content_blocks = event.get("message", {}).get("content", [])

    for block in content_blocks:
        if block.get("type") == "thinking":
            return block.get("text")

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


async def _process_events(
    claude_process: ClaudeProcess,
    session_id: str,
    progress_callback: ProgressCallback | None,
) -> SendResult:
    """Читает поток событий от Claude и собирает результат."""
    last_progress_time = 0.0
    result_text = ""
    final_session_id = session_id
    is_error = False
    got_result = False

    async for event in claude_process.read_events():
        # Проверяем, не запросил ли пользователь отмену
        stop_event = _stop_events.get(session_id)
        if stop_event is not None and stop_event.is_set():
            raise ProcessStoppedError("Запрос прерван командой /stop")

        # Обновляем session_id из события
        event_session_id = event.get("session_id")
        if event_session_id is not None and event_session_id != final_session_id:
            final_session_id = event_session_id

        # Обрабатываем промежуточные обновления (рассуждения Claude)
        progress_text = _extract_progress_text(event)
        if progress_text and progress_callback and _should_send_progress(last_progress_time):
            await progress_callback(session_id, progress_text)
            last_progress_time = time.monotonic()

        # Обрабатываем финальный результат
        if event.get("type") == EVENT_TYPE_RESULT:
            result_text = _extract_result_text(event)
            is_error = _is_error_result(event)
            got_result = True

    if not got_result:
        # Процесс завершился без события result — считаем ошибкой
        logger.warning(
            "Процесс Claude завершился без события result: session_id=%s",
            session_id,
        )
        return SendResult(
            text="",
            session_id=final_session_id,
            is_error=True,
            retries_used=0,
        )

    return SendResult(
        text=result_text,
        session_id=final_session_id,
        is_error=is_error,
        retries_used=0,
    )


async def _retry_loop(
    session_id: str,
    text: str,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
) -> SendResult:
    """Цикл повторных попыток при ошибке от Claude."""
    last_result: SendResult | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        # Проверяем флаг отмены перед каждой попыткой
        stop_event = _stop_events.get(session_id)
        if stop_event is not None and stop_event.is_set():
            raise ProcessStoppedError("Ретрай прерван командой /stop")

        # Уведомляем об очередной попытке
        if retry_callback is not None:
            await retry_callback(session_id, attempt, MAX_RETRIES)

        logger.warning(
            "Повторная попытка %d/%d для сессии %s",
            attempt, MAX_RETRIES, session_id,
        )

        # Ждём интервал, но проверяем отмену каждую секунду
        await _wait_with_stop_check(session_id, RETRY_INTERVAL_SECONDS)

        # Завершаем старый процесс и запускаем новый (resume)
        old_process = _processes.get(session_id)
        if old_process is not None and old_process.is_running():
            await old_process.terminate()
        claude_process = await _restart_process(session_id)

        # Отправляем сообщение и читаем ответ
        try:
            await claude_process.send_message(text)
            result = await _process_events(claude_process, session_id, progress_callback)
        except ProcessStoppedError:
            raise
        except Exception:
            logger.warning(
                "Ошибка при повторной попытке %d для сессии %s",
                attempt, session_id, exc_info=True,
            )
            continue

        # Если ответ успешный — возвращаем с количеством использованных ретраев
        if not result.is_error:
            return SendResult(
                text=result.text,
                session_id=result.session_id,
                is_error=False,
                retries_used=attempt,
            )

        last_result = result

    # Все ретраи исчерпаны
    logger.error(
        "Все %d ретраев исчерпаны для сессии %s", MAX_RETRIES, session_id,
    )

    if last_result is not None:
        return SendResult(
            text=last_result.text,
            session_id=last_result.session_id,
            is_error=True,
            retries_used=MAX_RETRIES,
        )

    # Не должно произойти, но на случай — возвращаем пустой результат с ошибкой
    return SendResult(
        text="",
        session_id=session_id,
        is_error=True,
        retries_used=MAX_RETRIES,
    )


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
    try:
        claude_process = await start_process(session_id)
    except ClaudeStartError as error:
        raise ProcessManagerError(
            f"Не удалось запустить Claude: {error}"
        ) from error

    _processes[session_id] = claude_process

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

    # Запускаем процесс через claude_runner
    # При resume передаём session_id, при новой сессии — None
    try:
        claude_process = await start_process(session_id)
    except ClaudeStartError as error:
        raise ProcessManagerError(
            f"Не удалось запустить Claude: {error}"
        ) from error

    # Сохраняем процесс и инициализируем служебные структуры
    _processes[effective_session_id] = claude_process
    _busy_flags[effective_session_id] = False
    _stop_events[effective_session_id] = asyncio.Event()

    process_type = "resume" if session_id is not None else "новый"
    logger.info(
        "Процесс Claude создан: session_id=%s, тип=%s, PID=%d",
        effective_session_id, process_type, claude_process.process.pid,
    )

    return effective_session_id


async def send_message(
    session_id: str,
    text: str,
    progress_callback: ProgressCallback | None = None,
    retry_callback: RetryCallback | None = None,
) -> SendResult:
    """Отправляет сообщение в процесс Claude и ожидает ответ."""
    # Проверяем наличие процесса
    claude_process = _processes.get(session_id)
    if claude_process is None:
        raise ProcessNotFoundError(
            f"Нет запущенного процесса для сессии '{session_id}'"
        )

    # Проверяем, что процесс не занят другим запросом
    if _busy_flags.get(session_id, False):
        logger.warning(
            "Процесс для сессии %s уже обрабатывает запрос", session_id,
        )
        raise ProcessManagerError(
            f"Процесс для сессии '{session_id}' уже занят другим запросом"
        )

    # Устанавливаем флаг занятости и сбрасываем флаг отмены
    _busy_flags[session_id] = True
    stop_event = _stop_events.get(session_id)
    if stop_event is not None:
        stop_event.clear()

    try:
        result = await _execute_send(
            session_id, text, claude_process, progress_callback, retry_callback,
        )
        # Обновляем session_id, если Claude вернул новый
        if result.session_id != session_id:
            await update_session_id(session_id, result.session_id)
            session_id = result.session_id

        return result

    finally:
        # Снимаем флаг занятости в любом случае
        # session_id мог измениться, поэтому используем актуальный
        _busy_flags[session_id] = False


async def _execute_send(
    session_id: str,
    text: str,
    claude_process: ClaudeProcess,
    progress_callback: ProgressCallback | None,
    retry_callback: RetryCallback | None,
) -> SendResult:
    """Выполняет отправку сообщения с обработкой ошибок и ретраями."""
    try:
        await claude_process.send_message(text)
        result = await _process_events(claude_process, session_id, progress_callback)
    except ClaudeProcessError as error:
        # Ошибка отправки или чтения — пробуем ретраи
        logger.warning(
            "Ошибка при взаимодействии с Claude (сессия %s): %s",
            session_id, error,
        )
        return await _retry_loop(session_id, text, progress_callback, retry_callback)

    # Если Claude вернул ошибку — запускаем ретраи
    if result.is_error:
        logger.warning(
            "Claude вернул ошибку (сессия %s): %s", session_id, result.text[:200],
        )
        return await _retry_loop(session_id, text, progress_callback, retry_callback)

    return result


async def stop_process(session_id: str) -> StopResult:
    """Останавливает процесс Claude в указанной сессии."""
    claude_process = _processes.get(session_id)
    was_running = False
    was_retrying = _busy_flags.get(session_id, False)

    # Устанавливаем флаг отмены — прервёт ожидание ретрая
    stop_event = _stop_events.get(session_id)
    if stop_event is not None:
        stop_event.set()

    # Завершаем процесс, если он работает
    if claude_process is not None and claude_process.is_running():
        was_running = True
        await claude_process.terminate()

    # Очищаем словари
    _processes.pop(session_id, None)
    _busy_flags.pop(session_id, None)
    _stop_events.pop(session_id, None)

    logger.info(
        "Процесс остановлен: session_id=%s, was_running=%s, was_retrying=%s",
        session_id, was_running, was_retrying,
    )

    return StopResult(was_running=was_running, was_retrying=was_retrying)


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
    # Переносим данные по новому ключу
    for storage in (_processes, _busy_flags, _stop_events):
        if old_session_id in storage:
            storage[new_session_id] = storage.pop(old_session_id)

    logger.info(
        "Session ID обновлён: %s -> %s", old_session_id, new_session_id,
    )
