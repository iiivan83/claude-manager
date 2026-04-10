"""Буфер непрочитанных сообщений при переключении проектов.

Хранит снапшоты состояния watcher (количество обработанных строк JSONL)
для каждого проекта на момент ухода. При возврате в проект сканирует
JSONL-файлы сессий, находит сообщения, написанные после ухода,
фильтрует по TTL и возвращает для доставки пользователю.

Сами сообщения НЕ хранятся в памяти — JSONL-файлы на диске уже
являются «буфером». Этот модуль хранит только снапшоты счётчиков.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from claude_manager import config, session_reader

logger = logging.getLogger(__name__)

# Служебные ответы Claude, которые не нужно отправлять пользователю.
# Продублировано из session_watcher — чтобы не создавать горизонтальную
# зависимость между модулями одного слоя (бизнес-логика).
NO_RESPONSE_MARKERS = frozenset({"No response requested."})


@dataclass
class ProjectSnapshot:
    """Снапшот состояния watcher на момент ухода из проекта."""

    seen_counts: dict[str, int]
    switch_time: datetime


@dataclass(frozen=True)
class PendingMessage:
    """Одно непрочитанное сообщение из фоновой сессии."""

    session_id: str
    text: str


# Снапшоты: ключ — абсолютный путь к проекту
_snapshots: dict[str, ProjectSnapshot] = {}


def _is_empty_response(text: str | None) -> bool:
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


def _is_snapshot_expired(snapshot: ProjectSnapshot) -> bool:
    """Проверяет, просрочен ли снапшот по TTL."""
    ttl = timedelta(hours=config.UNREAD_BUFFER_TTL_HOURS)
    return datetime.now() - snapshot.switch_time > ttl


def save_snapshot(project_path: str, seen_counts: dict[str, int]) -> None:
    """Сохраняет снапшот watcher при уходе из проекта."""
    _snapshots[project_path] = ProjectSnapshot(
        seen_counts=dict(seen_counts),
        switch_time=datetime.now(),
    )
    logger.info(
        "Снапшот сохранён для проекта %s (%d сессий)",
        project_path, len(seen_counts),
    )


async def get_pending_messages(project_path: str) -> list[PendingMessage]:
    """Сканирует JSONL-файлы и возвращает новые сообщения после снапшота."""
    snapshot = _snapshots.get(project_path)
    if snapshot is None:
        return []

    if _is_snapshot_expired(snapshot):
        logger.info(
            "Снапшот для %s просрочен (TTL %d ч), пропускаем",
            project_path, config.UNREAD_BUFFER_TTL_HOURS,
        )
        del _snapshots[project_path]
        return []

    result: list[PendingMessage] = []

    # Проверяем сессии из снапшота — ищем новые сообщения
    for session_id, seen_count in snapshot.seen_counts.items():
        pending = await _collect_from_session(
            session_id, project_path, seen_count,
        )
        result.extend(pending)

    # Обнаружение новых сессий, которых не было в снапшоте
    new_session_pending = await _collect_from_new_sessions(
        project_path, snapshot.seen_counts,
    )
    result.extend(new_session_pending)

    logger.info(
        "Найдено %d непрочитанных сообщений для проекта %s",
        len(result), project_path,
    )
    return result


async def _collect_from_session(
    session_id: str, project_path: str, seen_count: int,
) -> list[PendingMessage]:
    """Собирает новые сообщения из одной сессии по снапшоту."""
    all_messages = await session_reader.get_session_messages(
        session_id, project_path,
    )

    if len(all_messages) <= seen_count:
        return []

    new_messages = all_messages[seen_count:]
    result: list[PendingMessage] = []

    for message in new_messages:
        text = _extract_message_text(message)
        if text is not None and not _is_empty_response(text):
            result.append(PendingMessage(session_id=session_id, text=text))

    return result


async def _collect_from_new_sessions(
    project_path: str, known_session_ids: dict[str, int],
) -> list[PendingMessage]:
    """Обнаруживает сессии, появившиеся после ухода, и собирает их сообщения."""
    recent_sessions = await session_reader.get_recent_sessions(project_path)
    known_ids = set(known_session_ids.keys())
    result: list[PendingMessage] = []

    for session in recent_sessions:
        if session.session_id in known_ids:
            continue

        # Новая сессия — все assistant-сообщения считаются непрочитанными
        pending = await _collect_from_session(
            session.session_id, project_path, seen_count=0,
        )
        result.extend(pending)

    return result


def clear_snapshot(project_path: str) -> None:
    """Удаляет снапшот после успешной доставки сообщений."""
    removed = _snapshots.pop(project_path, None)
    if removed is not None:
        logger.debug("Снапшот удалён для проекта %s", project_path)


def has_pending(project_path: str) -> bool:
    """Проверяет, есть ли для проекта непросроченный снапшот."""
    snapshot = _snapshots.get(project_path)
    if snapshot is None:
        return False
    if _is_snapshot_expired(snapshot):
        del _snapshots[project_path]
        return False
    return True


def cleanup_expired() -> None:
    """Удаляет все просроченные снапшоты."""
    expired_paths = [
        path for path, snapshot in _snapshots.items()
        if _is_snapshot_expired(snapshot)
    ]
    for path in expired_paths:
        del _snapshots[path]

    if expired_paths:
        logger.info("Удалено %d просроченных снапшотов", len(expired_paths))
