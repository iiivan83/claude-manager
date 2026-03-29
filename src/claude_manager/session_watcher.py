"""Мониторинг файлов сессий Claude Code в реальном времени.

Каждые 2 секунды проверяет файлы сессий на диске, обнаруживает
новые ответы Claude и передаёт их в callback-функцию для отправки
пользователю. Координируется с обработчиком сообщений через
механизм паузы, чтобы не дублировать ответы.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from claude_manager import config, daily_session_registry, session_reader

logger = logging.getLogger(__name__)

# Интервал между проверками файлов сессий (секунды).
# Из BRD CJM-07: «каждые 2 секунды проверяет файлы сессий»
POLL_INTERVAL_SECONDS = 2

# Задержка после непредвиденной ошибки перед повтором (секунды).
# 10 секунд — достаточно, чтобы временная проблема разрешилась
ERROR_RETRY_DELAY_SECONDS = 10

# Служебные ответы Claude, которые не нужно отправлять пользователю
NO_RESPONSE_MARKERS = frozenset({"No response requested."})

# Типы-алиасы для callback-функций
# callback(chat_id, session_id, day_number, message_text, is_current_session, is_final)
MessageCallback = Callable[[int, str, int, str, bool, bool], Awaitable[None]]
# get_current_session(chat_id) -> session_id или None
CurrentSessionGetter = Callable[[int], Awaitable[str | None]]

# Внутреннее состояние модуля
# Для каждой сессии хранит количество уже обработанных строк JSONL-файла
_seen_message_counts: dict[str, int] = {}

# Сессии, мониторинг которых приостановлен (ответ отправляет обработчик)
_paused_sessions: set[str] = set()

# Ссылки на callback-функции (заполняются при вызове start)
_callback: MessageCallback | None = None
_get_current_session: CurrentSessionGetter | None = None


def _is_empty_response(text: str) -> bool:
    """Проверяет, является ли ответ Claude пустым или служебным."""
    if not text or not text.strip():
        return True
    return text.strip() in NO_RESPONSE_MARKERS


def _extract_message_text(message: dict) -> str | None:
    """Извлекает текст из одного сообщения Claude.

    Обрабатывает два формата поля content:
    строка (возвращает как есть) или список (склеивает текстовые блоки).
    """
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
    all_messages: list[dict], already_seen_count: int
) -> list[str]:
    """Извлекает тексты новых ответов Claude, пропуская уже обработанные."""
    new_messages = all_messages[already_seen_count:]
    result: list[str] = []

    for message in new_messages:
        text = _extract_message_text(message)
        if text is not None:
            result.append(text)

    return result


async def _get_sessions_to_monitor() -> list[str]:
    """Получает список session_id всех сессий для мониторинга.

    Объединяет сессии из session_reader (файлы на диске)
    и daily_session_registry (реестр текущего дня) без дубликатов.
    """
    recent_sessions = await session_reader.get_recent_sessions(
        config.WORKING_DIR
    )
    session_ids = [session.session_id for session in recent_sessions]

    # Добавляем сессии из дневного реестра, которых нет в списке с диска
    today_sessions = await daily_session_registry.get_all_today_sessions()
    existing_ids = set(session_ids)
    for session_id in today_sessions.values():
        if session_id not in existing_ids:
            session_ids.append(session_id)
            existing_ids.add(session_id)

    return session_ids


async def _check_session(session_id: str) -> None:
    """Проверяет одну сессию на наличие новых сообщений Claude."""
    if session_id in _paused_sessions:
        return

    all_messages = await session_reader.get_session_messages(
        session_id, config.WORKING_DIR
    )
    current_count = len(all_messages)
    already_seen = _seen_message_counts.get(session_id, 0)

    if current_count <= already_seen:
        return

    new_texts = _extract_assistant_messages(all_messages, already_seen)

    # Обновляем счётчик до актуального значения
    _seen_message_counts[session_id] = current_count

    last_index = len(new_texts) - 1
    for index, text in enumerate(new_texts):
        if _is_empty_response(text):
            continue

        # Все сообщения кроме последнего — точно промежуточные.
        # Последнее — помечаем финальным (если Claude ещё думает,
        # следующий цикл через 2 сек найдёт новое сообщение)
        is_final = (index == last_index)

        try:
            day_number = await daily_session_registry.register_session(
                session_id
            )
        except Exception:
            logger.error(
                "Ошибка регистрации сессии %s в дневном реестре",
                session_id,
                exc_info=True,
            )
            continue

        # Отправляем сообщение каждому разрешённому пользователю
        for chat_id in config.ALLOWED_USER_IDS:
            current_session = await _get_current_session(chat_id)
            is_current = current_session == session_id

            try:
                await _callback(
                    chat_id, session_id, day_number, text,
                    is_current, is_final,
                )
            except Exception:
                logger.error(
                    "Ошибка при отправке сообщения из сессии %s: %s",
                    session_id,
                    session_id,
                    exc_info=True,
                )


async def _poll_sessions() -> None:
    """Выполняет один цикл проверки всех сессий."""
    session_ids = await _get_sessions_to_monitor()

    # Удаляем устаревшие записи — сессий, которых больше нет
    active_ids = set(session_ids)
    stale_keys = [
        key for key in _seen_message_counts if key not in active_ids
    ]
    for key in stale_keys:
        del _seen_message_counts[key]

    # Проверяем каждую сессию последовательно (не нагружаем диск)
    for session_id in session_ids:
        try:
            await _check_session(session_id)
        except Exception:
            logger.error(
                "Ошибка проверки сессии %s", session_id, exc_info=True
            )


def pause_session(session_id: str) -> None:
    """Приостанавливает мониторинг конкретной сессии."""
    _paused_sessions.add(session_id)
    logger.debug("Мониторинг сессии %s приостановлен", session_id)


async def resume_session(session_id: str) -> None:
    """Возобновляет мониторинг сессии, обновляя счётчик обработанных сообщений."""
    _paused_sessions.discard(session_id)

    # Перечитываем файл сессии и обновляем счётчик,
    # чтобы не отправлять сообщения, которые уже отправил обработчик
    messages = await session_reader.get_session_messages(
        session_id, config.WORKING_DIR
    )
    count = len(messages)
    _seen_message_counts[session_id] = count

    logger.debug(
        "Мониторинг сессии %s возобновлён, счётчик обновлён до %d",
        session_id,
        count,
    )


def update_session_id(old_session_id: str, new_session_id: str) -> None:
    """Переносит внутреннее состояние со старого session_id на новый."""
    if old_session_id not in _seen_message_counts and old_session_id not in _paused_sessions:
        logger.debug(
            "Watcher: session_id %s не найден — пропускаем обновление",
            old_session_id,
        )
        return

    # Переносим счётчик обработанных сообщений
    if old_session_id in _seen_message_counts:
        _seen_message_counts[new_session_id] = _seen_message_counts.pop(
            old_session_id
        )

    # Переносим статус паузы
    if old_session_id in _paused_sessions:
        _paused_sessions.discard(old_session_id)
        _paused_sessions.add(new_session_id)

    logger.info(
        "Watcher: session_id обновлён %s → %s", old_session_id, new_session_id
    )


async def start(
    callback: MessageCallback,
    get_current_session: CurrentSessionGetter,
) -> None:
    """Запускает бесконечный цикл мониторинга сессий.

    Каждые 2 секунды проверяет файлы сессий на диске
    и вызывает callback при обнаружении новых сообщений Claude.
    """
    global _callback, _get_current_session, _seen_message_counts, _paused_sessions

    _callback = callback
    _get_current_session = get_current_session
    _seen_message_counts = {}
    _paused_sessions = set()

    # Первоначальное сканирование — запоминаем текущее количество сообщений,
    # чтобы не отправлять старые сообщения при первом запуске
    initial_sessions = await _get_sessions_to_monitor()
    for session_id in initial_sessions:
        messages = await session_reader.get_session_messages(
            session_id, config.WORKING_DIR
        )
        _seen_message_counts[session_id] = len(messages)

    logger.info("Мониторинг сессий запущен (%d сессий)", len(initial_sessions))

    try:
        while True:
            try:
                await _poll_sessions()
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
