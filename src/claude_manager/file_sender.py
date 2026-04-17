"""Парсинг маркеров [SEND_FILE:path], определение типа файла и рендеринг через telegramify-markdown.

Утилитный модуль без состояния (как message_splitter). Отвечает за три задачи:
1) парсинг маркеров [SEND_FILE:path] из текста
2) определение типа файла — текстовый или бинарный
3) рендеринг текстовых файлов через telegramify-markdown с разбивкой на чанки
"""

import logging
import os
import re
from pathlib import Path

import telegramify_markdown
from telegram import MessageEntity

logger = logging.getLogger(__name__)

# --- Константы ---

# Регулярное выражение для извлечения путей из маркеров [SEND_FILE:/path/to/file]
SEND_FILE_PATTERN = r"\[SEND_FILE:([^\]]+)\]"

# Регулярное выражение для вырезки целых маркеров из текста (с опциональным пробелом после)
SEND_FILE_PATTERN_WITH_BRACKETS = r"\[SEND_FILE:[^\]]+\]\s*"

# Расширения файлов, которые считаются текстовыми
TEXT_EXTENSIONS = frozenset({
    "md", "txt", "json", "py", "sh", "js", "ts", "jsx", "tsx",
    "html", "css", "yml", "yaml", "toml", "cfg", "ini", "csv",
    "xml", "log", "env", "gitignore", "dockerignore", "makefile",
    "rst", "tex", "sql", "rb", "go", "rs", "java", "kt", "swift",
    "c", "cpp", "h", "hpp",
})

# Имена файлов без расширения, которые считаются текстовыми
TEXT_FILE_NAMES = frozenset({
    "makefile", "dockerfile", ".gitignore", ".dockerignore",
    ".env", ".env.example", ".editorconfig",
})

# Максимальный размер текстового файла для рендеринга в чате (1 МБ)
MAX_TEXT_FILE_SIZE_BYTES = 1_000_000

# Максимальный размер бинарного файла для отправки через Telegram Bot API (50 МБ)
MAX_BINARY_FILE_SIZE_BYTES = 50_000_000

# Максимальная длина одного сообщения Telegram (в UTF-16 code units)
TELEGRAM_MESSAGE_LIMIT = 4096

# Текст-заглушка для пустых файлов
EMPTY_FILE_PLACEHOLDER = "(пустой файл)"


# --- Публичные функции ---


def extract_file_markers(text: str) -> list[str]:
    """Находит все маркеры [SEND_FILE:/path] в тексте и возвращает список путей."""
    raw_paths = re.findall(SEND_FILE_PATTERN, text)
    return [path.strip() for path in raw_paths]


def strip_file_markers(text: str) -> str:
    """Вырезает все маркеры [SEND_FILE:...] из текста и схлопывает лишние пустые строки."""
    result = re.sub(SEND_FILE_PATTERN_WITH_BRACKETS, "", text).strip()
    # Не больше двух переносов строки подряд (одна пустая строка между абзацами)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def is_text_file(file_path: str) -> bool:
    """Определяет, является ли файл текстовым по расширению или имени."""
    path = Path(file_path)
    # Проверяем по известным именам файлов (Makefile, .gitignore и т.д.)
    if path.name.lower() in TEXT_FILE_NAMES:
        return True
    # Проверяем по расширению
    extension = path.suffix.lstrip(".").lower()
    if not extension:
        return False
    return extension in TEXT_EXTENSIONS


def read_file_content(file_path: str) -> tuple[str, str | None]:
    """Читает текстовый файл с диска. Возвращает (content, error)."""
    try:
        file_size = os.path.getsize(file_path)
    except FileNotFoundError:
        message = f"Файл не найден: {file_path}"
        logger.warning(message)
        return ("", message)
    except PermissionError:
        message = f"Нет доступа к файлу: {file_path}"
        logger.warning(message)
        return ("", message)
    except OSError as error:
        message = f"Ошибка доступа к файлу: {file_path} ({error})"
        logger.warning(message)
        return ("", message)

    if file_size > MAX_TEXT_FILE_SIZE_BYTES:
        message = f"Файл слишком большой для отображения в чате: {file_path}"
        logger.warning(message)
        return ("", message)

    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        message = f"Файл не является текстовым: {file_path}"
        logger.warning(message)
        return ("", message)
    except OSError as error:
        message = f"Ошибка чтения файла: {file_path} ({error})"
        logger.warning(message)
        return ("", message)

    return (content, None)


def render_file_for_telegram(
    content: str, first_chunk_reserve: int = 0,
) -> list[tuple[str, list]]:
    """Рендерит текстовое содержимое через telegramify-markdown и разбивает на чанки.

    first_chunk_reserve — количество UTF-16 code units, которое нужно зарезервировать
    под заголовок перед первым чанком. Уменьшает лимит для ВСЕХ чанков (split_entities
    не поддерживает отдельный лимит для первого). Потеря места в чанках 2+ минимальна.
    """
    if not content:
        return [(EMPTY_FILE_PLACEHOLDER, [])]

    effective_limit = TELEGRAM_MESSAGE_LIMIT
    if first_chunk_reserve > 0:
        if first_chunk_reserve >= TELEGRAM_MESSAGE_LIMIT:
            logger.warning(
                "first_chunk_reserve (%d) >= TELEGRAM_MESSAGE_LIMIT (%d), "
                "используем fallback (половина лимита)",
                first_chunk_reserve, TELEGRAM_MESSAGE_LIMIT,
            )
            effective_limit = TELEGRAM_MESSAGE_LIMIT // 2
        else:
            effective_limit = TELEGRAM_MESSAGE_LIMIT - first_chunk_reserve

    text, entities = telegramify_markdown.convert(content)
    chunks = telegramify_markdown.split_entities(
        text, entities, effective_limit,
    )
    return chunks


def convert_entities(entities: list) -> list[MessageEntity]:
    """Конвертирует MessageEntity из telegramify-markdown в telegram.MessageEntity."""
    return [
        MessageEntity(
            type=entity.type,
            offset=entity.offset,
            length=entity.length,
            url=entity.url,
            language=entity.language,
        )
        for entity in entities
    ]


def check_binary_file(file_path: str) -> str | None:
    """Проверяет бинарный файл перед отправкой. Возвращает None при успехе, строку с ошибкой при проблеме."""
    try:
        file_size = os.path.getsize(file_path)
    except FileNotFoundError:
        message = f"Файл не найден: {file_path}"
        logger.warning(message)
        return message
    except PermissionError:
        message = f"Нет доступа к файлу: {file_path}"
        logger.warning(message)
        return message
    except OSError as error:
        message = f"Ошибка доступа к файлу: {file_path} ({error})"
        logger.warning(message)
        return message

    if file_size > MAX_BINARY_FILE_SIZE_BYTES:
        message = (
            f"Файл слишком большой для отправки через Telegram "
            f"(лимит 50 МБ): {file_path}"
        )
        logger.warning(message)
        return message

    return None
