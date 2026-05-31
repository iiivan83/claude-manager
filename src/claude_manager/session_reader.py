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

from claude_manager.session_request_preview import clean_session_request_preview

logger = logging.getLogger(__name__)

# Относительный путь от домашней директории к папке проектов Claude Code
CLAUDE_PROJECTS_DIR = ".claude/projects"

# Максимум сессий, возвращаемых из get_recent_sessions (из BRD CJM-05)
MAX_RECENT_SESSIONS = 15

# Лимит длины превью: None означает показывать полный очищенный текст без "..."
PREVIEW_MAX_LENGTH: int | None = None

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

# Регулярное выражение для санитации пути проекта — заменяет всё,
# что не буква и не цифра, на дефис. Повторяет sanitizePath() из
# Claude Code CLI (см. claude-code-sourcecode/utils/sessionStoragePortable.ts:311).
SANITIZE_PATH_PATTERN = re.compile(r"[^a-zA-Z0-9]")

# Максимальная длина sanitized-компонента до hash suffix.
# Источник: claude-code-sourcecode/utils/sessionStoragePortable.ts:293.
MAX_SANITIZED_PATH_LENGTH = 200

# Минимальная длина сообщения, чтобы считать его «настоящим»
MIN_MESSAGE_LENGTH = 2


@dataclass
class SessionInfo:
    """Данные об одной сессии Claude Code."""

    session_id: str
    created_at: str
    preview: str


def _to_base36(value: int) -> str:
    """Кодирует положительное целое в base36 как JavaScript Number.toString(36)."""
    if value == 0:
        return "0"

    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    digits = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def _djb2_hash(text: str) -> int:
    """Повторяет signed 32-bit djb2Hash из Claude Code source."""
    hash_value = 0
    utf16 = text.encode("utf-16-le")
    for index in range(0, len(utf16), 2):
        code_unit = int.from_bytes(utf16[index:index + 2], "little")
        hash_value = ((hash_value << 5) - hash_value + code_unit) & 0xFFFFFFFF
        if hash_value >= 0x80000000:
            hash_value -= 0x100000000
    return hash_value


def _sanitize_project_path(project_dir: str) -> str:
    """Заменяет не-буквенно-цифровые символы на дефис."""
    return SANITIZE_PATH_PATTERN.sub("-", project_dir)


def _encode_project_path(project_dir: str) -> str:
    """Кодирует путь проекта в формат имён папок Claude Code."""
    sanitized = _sanitize_project_path(project_dir)
    if len(sanitized) <= MAX_SANITIZED_PATH_LENGTH:
        return sanitized

    hash_suffix = _to_base36(abs(_djb2_hash(project_dir)))
    return f"{sanitized[:MAX_SANITIZED_PATH_LENGTH]}-{hash_suffix}"


def build_sessions_path(project_dir: str) -> str:
    """Строит абсолютный путь к папке сессий проекта."""
    home_dir = os.path.expanduser("~")
    projects_root = os.path.join(home_dir, CLAUDE_PROJECTS_DIR)
    encoded_name = _encode_project_path(project_dir)
    exact_path = os.path.join(projects_root, encoded_name)

    sanitized = _sanitize_project_path(project_dir)
    if len(sanitized) <= MAX_SANITIZED_PATH_LENGTH or os.path.isdir(exact_path):
        return exact_path

    # Claude CLI под Bun использует Bun.hash, а SDK fallback — djb2Hash.
    # Для длинных путей ищем существующую папку по стабильному prefix.
    prefix = sanitized[:MAX_SANITIZED_PATH_LENGTH] + "-"
    try:
        for entry_name in os.listdir(projects_root):
            candidate = os.path.join(projects_root, entry_name)
            if entry_name.startswith(prefix) and os.path.isdir(candidate):
                return candidate
    except OSError:
        return exact_path

    return exact_path


def _is_command_message(text: str) -> bool:
    """Проверяет, содержит ли текст командные XML-теги Claude Code."""
    for tag_name in COMMAND_XML_TAGS:
        if f"<{tag_name}" in text:
            return True
    return False


def _clean_preview(raw_text: str) -> str:
    """Очищает текст превью от XML-тегов и обрезает до максимальной длины."""
    return clean_session_request_preview(raw_text, PREVIEW_MAX_LENGTH)


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

    # session_id: из JSON или из имени файла
    file_basename = os.path.basename(file_path)
    file_name_without_extension = file_basename.removesuffix(".jsonl")
    session_id = parsed_lines[0].get("sessionId", file_name_without_extension)

    # timestamp: обязателен для определения времени создания.
    # Claude CLI начиная с 2.1.96 пишет первой строкой служебные события
    # (permission-mode, file-history-snapshot) без поля timestamp — поэтому
    # ищем первую строку, где timestamp реально присутствует, а не жёстко
    # берём parsed_lines[0].
    timestamp = None
    for line in parsed_lines:
        if line.get("timestamp"):
            timestamp = line["timestamp"]
            break
    if not timestamp:
        logger.warning("Нет timestamp ни в одной строке файла сессии: %s", file_path)
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


async def _list_sorted_session_files(sessions_path: str) -> list[str] | None:
    """Возвращает отсортированные JSONL-файлы или None при ошибке."""
    path_exists = await asyncio.to_thread(os.path.exists, sessions_path)
    if not path_exists:
        logger.warning("Папка сессий не найдена: %s", sessions_path)
        return None

    is_directory = await asyncio.to_thread(os.path.isdir, sessions_path)
    if not is_directory:
        logger.warning("Папка сессий не найдена: %s", sessions_path)
        return None

    try:
        jsonl_files = await asyncio.to_thread(_list_jsonl_files, sessions_path)
    except OSError as error:
        logger.error("Ошибка чтения папки сессий %s: %s", sessions_path, error)
        return None

    if not jsonl_files:
        logger.info("Файлы сессий не найдены в %s", sessions_path)
        return None

    try:
        return await asyncio.to_thread(_sort_files_by_mtime, jsonl_files)
    except OSError as error:
        logger.error("Ошибка сортировки файлов сессий: %s", error)
        return None


async def get_recent_sessions(project_dir: str) -> list[SessionInfo]:
    """Возвращает список последних сессий проекта, новые первые."""
    sessions_path = build_sessions_path(project_dir)
    sorted_files = await _list_sorted_session_files(sessions_path)
    if sorted_files is None:
        return []

    limited_files = sorted_files[:MAX_RECENT_SESSIONS]
    sessions: list[SessionInfo] = []
    for file_path in limited_files:
        session_info = await _read_session_file(file_path)
        if session_info is not None:
            sessions.append(session_info)
    return sessions


async def get_session_messages(session_id: str, project_dir: str) -> list[dict]:
    """Читает все сообщения из файла конкретной сессии."""
    sessions_path = build_sessions_path(project_dir)
    file_path = os.path.join(sessions_path, f"{session_id}.jsonl")

    file_exists = await asyncio.to_thread(os.path.exists, file_path)
    if not file_exists:
        logger.debug("Файл сессии не найден: %s", file_path)
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
