"""Чтение файлов сессий Claude Code с диска.

Извлекает метаданные (идентификатор сессии, время создания, первое
сообщение пользователя) и возвращает список последних сессий проекта,
отсортированный по времени. Единственный источник данных о сессиях
для всех модулей.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Относительный путь от домашней директории к папке проектов Claude Code
CLAUDE_PROJECTS_DIR = ".claude/projects"

# Максимум сессий, возвращаемых из get_recent_sessions (из BRD CJM-05)
MAX_RECENT_SESSIONS = 15

# Максимальная длина превью первого сообщения в символах (из BRD CJM-05)
PREVIEW_MAX_LENGTH = 120

# Сколько строк JSONL-файла читать при поиске первого сообщения пользователя
MAX_LINES_FOR_PREVIEW = 50

# XML-теги, обозначающие системные/командные сообщения Claude Code
COMMAND_XML_TAGS = {
    "command-name",
    "command-message",
    "command-args",
    "local-command-stdout",
    "local-command-caveat",
}

# Регулярное выражение для удаления XML-тегов из превью
XML_TAG_PATTERN = re.compile(r"<[^>]+>")

# Регулярное выражение для замены множественных пробелов на один
WHITESPACE_PATTERN = re.compile(r"\s+")

# Минимальная длина сообщения, чтобы считать его «настоящим»
MIN_MESSAGE_LENGTH = 2


@dataclass
class SessionInfo:
    """Данные об одной сессии Claude Code."""

    session_id: str
    created_at: str
    preview: str


def _encode_project_path(project_dir: str) -> str:
    """Кодирует путь проекта в формат имён папок Claude Code."""
    return project_dir.replace("/", "-").replace(" ", "-")


def _build_sessions_path(project_dir: str) -> str:
    """Строит абсолютный путь к папке сессий проекта."""
    home_dir = os.path.expanduser("~")
    encoded_name = _encode_project_path(project_dir)
    return os.path.join(home_dir, CLAUDE_PROJECTS_DIR, encoded_name)


def _is_command_message(text: str) -> bool:
    """Проверяет, содержит ли текст командные XML-теги Claude Code."""
    for tag_name in COMMAND_XML_TAGS:
        if f"<{tag_name}" in text:
            return True
    return False


def _clean_preview(raw_text: str) -> str:
    """Очищает текст превью от XML-тегов и обрезает до максимальной длины."""
    without_tags = XML_TAG_PATTERN.sub("", raw_text)
    collapsed = WHITESPACE_PATTERN.sub(" ", without_tags).strip()
    if len(collapsed) > PREVIEW_MAX_LENGTH:
        return collapsed[:PREVIEW_MAX_LENGTH] + "..."
    return collapsed


def _extract_text_from_content(content: str | list) -> str:
    """Извлекает текст из поля content сообщения (строка или список)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return item.get("text", "")
    return ""


def _extract_first_user_message(lines: list[dict]) -> str:
    """Находит первое настоящее сообщение пользователя в списке строк JSONL."""
    for line in lines:
        if line.get("type") != "user":
            continue
        if line.get("isMeta"):
            continue
        message = line.get("message", {})
        content = message.get("content", "")
        text = _extract_text_from_content(content)
        if not text.strip() or len(text.strip()) < MIN_MESSAGE_LENGTH:
            continue
        if _is_command_message(text):
            continue
        return text
    return ""


def _parse_jsonl_lines(raw_lines: list[str], file_path: str) -> list[dict]:
    """Парсит список строк JSONL в список словарей, пропуская невалидные."""
    parsed = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            parsed.append(json.loads(stripped))
        except json.JSONDecodeError:
            logger.warning(
                "Ошибка чтения сессии %s: невалидный JSON на строке %d",
                file_path,
                line_number,
            )
    return parsed


def _read_file_lines(file_path: str, max_lines: int | None = None) -> list[str]:
    """Читает строки из файла (блокирующая операция для asyncio.to_thread)."""
    with open(file_path, encoding="utf-8") as file_handle:
        if max_lines is None:
            return file_handle.readlines()
        lines = []
        for _ in range(max_lines):
            line = file_handle.readline()
            if not line:
                break
            lines.append(line)
        return lines


async def _read_session_file(file_path: str) -> SessionInfo | None:
    """Читает один JSONL-файл сессии и извлекает метаданные."""
    try:
        raw_lines = await asyncio.to_thread(
            _read_file_lines, file_path, MAX_LINES_FOR_PREVIEW
        )
    except PermissionError:
        logger.error("Нет доступа к файлу сессии: %s", file_path)
        return None
    except OSError as error:
        logger.error("Ошибка чтения файла сессии %s: %s", file_path, error)
        return None

    parsed_lines = _parse_jsonl_lines(raw_lines, file_path)
    if not parsed_lines:
        return None

    first_line = parsed_lines[0]

    # session_id: из JSON или из имени файла
    file_basename = os.path.basename(file_path)
    file_name_without_extension = file_basename.removesuffix(".jsonl")
    session_id = first_line.get("sessionId", file_name_without_extension)

    # timestamp: обязателен для определения времени создания
    timestamp = first_line.get("timestamp")
    if not timestamp:
        logger.warning("Нет timestamp в файле сессии: %s", file_path)
        return None

    raw_message = _extract_first_user_message(parsed_lines)
    preview = _clean_preview(raw_message)

    return SessionInfo(
        session_id=session_id,
        created_at=timestamp,
        preview=preview,
    )


def _list_jsonl_files(sessions_path: str) -> list[str]:
    """Возвращает список JSONL-файлов в папке (блокирующая операция)."""
    entries = os.listdir(sessions_path)
    jsonl_files = []
    for entry in entries:
        full_path = os.path.join(sessions_path, entry)
        if entry.endswith(".jsonl") and os.path.isfile(full_path):
            jsonl_files.append(full_path)
    return jsonl_files


def _sort_files_by_mtime(file_paths: list[str]) -> list[str]:
    """Сортирует файлы по времени модификации (новые первые)."""
    return sorted(file_paths, key=os.path.getmtime, reverse=True)


async def get_recent_sessions(project_dir: str) -> list[SessionInfo]:
    """Возвращает список последних сессий проекта, новые первые."""
    sessions_path = _build_sessions_path(project_dir)

    path_exists = await asyncio.to_thread(os.path.exists, sessions_path)
    if not path_exists:
        logger.warning("Папка сессий не найдена: %s", sessions_path)
        return []

    is_directory = await asyncio.to_thread(os.path.isdir, sessions_path)
    if not is_directory:
        logger.warning("Папка сессий не найдена: %s", sessions_path)
        return []

    try:
        jsonl_files = await asyncio.to_thread(_list_jsonl_files, sessions_path)
    except OSError as error:
        logger.error("Ошибка чтения папки сессий %s: %s", sessions_path, error)
        return []

    if not jsonl_files:
        logger.info("Файлы сессий не найдены в %s", sessions_path)
        return []

    try:
        sorted_files = await asyncio.to_thread(_sort_files_by_mtime, jsonl_files)
    except OSError as error:
        logger.error("Ошибка сортировки файлов сессий: %s", error)
        return []

    # Берём только первые MAX_RECENT_SESSIONS файлов
    limited_files = sorted_files[:MAX_RECENT_SESSIONS]

    # Читаем файлы последовательно, чтобы не нагружать диск
    sessions: list[SessionInfo] = []
    for file_path in limited_files:
        session_info = await _read_session_file(file_path)
        if session_info is not None:
            sessions.append(session_info)

    return sessions


async def get_session_messages(session_id: str, project_dir: str) -> list[dict]:
    """Читает все сообщения из файла конкретной сессии."""
    sessions_path = _build_sessions_path(project_dir)
    file_path = os.path.join(sessions_path, f"{session_id}.jsonl")

    file_exists = await asyncio.to_thread(os.path.exists, file_path)
    if not file_exists:
        logger.warning("Файл сессии не найден: %s", file_path)
        return []

    try:
        raw_lines = await asyncio.to_thread(_read_file_lines, file_path)
    except PermissionError:
        logger.error("Нет доступа к файлу сессии: %s", file_path)
        return []
    except OSError as error:
        logger.error("Ошибка чтения файла сессии %s: %s", file_path, error)
        return []

    return _parse_jsonl_lines(raw_lines, file_path)
