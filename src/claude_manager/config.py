"""Загрузка и проверка настроек приложения из файла .env.

Читает переменные окружения, проверяет обязательные параметры
и предоставляет их остальным модулям через константы уровня модуля.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Имена переменных окружения (вынесены в константы, чтобы не дублировать строки)
_ENV_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
_ENV_ALLOWED_IDS = "ALLOWED_USER_IDS"
_ENV_WORKING_DIR = "CLAUDE_WORKING_DIR"
_ENV_PROJECTS_ROOT = "PROJECTS_ROOT_DIR"

# Значение PROJECTS_ROOT_DIR по умолчанию — папка, где у пользователя лежат проекты.
# Используется, если переменная окружения не задана. Бот ищет проекты здесь для команды /projects.
DEFAULT_PROJECTS_ROOT = "/Users/ivan/Desktop/claude-sandbox"

# Имя файла, в котором бот запоминает последний выбранный проект.
# Файл лежит в домашней папке пользователя — не зависит от рабочей директории.
# Используется для восстановления выбранного проекта после перезапуска бота.
LAST_PROJECT_FILE: Path = Path.home() / ".claude-manager-current-project"

# Константы модуля — заполняются после вызова load_config()
BOT_TOKEN: str = ""
ALLOWED_USER_IDS: set[int] = set()
WORKING_DIR: str = ""
PROJECTS_ROOT_DIR: str = ""


class ConfigError(Exception):
    """Ошибка конфигурации: обязательный параметр отсутствует или некорректен."""


def _parse_allowed_user_ids(raw_value: str) -> set[int]:
    """Разбирает строку с Telegram-ID пользователей в множество чисел."""
    parts = raw_value.split(",")
    stripped_parts = [part.strip() for part in parts]
    non_empty_parts = [part for part in stripped_parts if part]

    result: set[int] = set()
    for part in non_empty_parts:
        try:
            result.add(int(part))
        except ValueError:
            raise ConfigError(
                f"{_ENV_ALLOWED_IDS} содержит нечисловое значение: '{part}'"
            )

    if not result:
        raise ConfigError(
            f"{_ENV_ALLOWED_IDS} не содержит ни одного корректного ID"
        )

    return result


def _resolve_working_dir(raw_value: str | None) -> str:
    """Определяет абсолютный путь к рабочей директории."""
    if not raw_value:
        return os.getcwd()

    resolved_path = os.path.abspath(raw_value)

    if not os.path.isdir(resolved_path):
        raise ConfigError(
            f"{_ENV_WORKING_DIR} указывает на несуществующую директорию: "
            f"'{resolved_path}'"
        )

    return resolved_path


def _resolve_projects_root(raw_value: str | None) -> str:
    """Определяет абсолютный путь к корневой папке со всеми проектами."""
    # Пустое значение трактуется как «не задано» — используем значение по умолчанию
    path_to_resolve = raw_value if raw_value else DEFAULT_PROJECTS_ROOT

    resolved_path = os.path.abspath(path_to_resolve)

    if not os.path.isdir(resolved_path):
        raise ConfigError(
            f"{_ENV_PROJECTS_ROOT} указывает на несуществующую директорию: "
            f"'{resolved_path}'"
        )

    return resolved_path


def load_config() -> None:
    """Загружает настройки из .env и записывает их в константы модуля."""
    global BOT_TOKEN, ALLOWED_USER_IDS, WORKING_DIR, PROJECTS_ROOT_DIR

    # override=True — значения из .env перезаписывают системные переменные
    load_dotenv(override=True)

    bot_token = os.environ.get(_ENV_BOT_TOKEN, "")
    if not bot_token:
        raise ConfigError(
            f"{_ENV_BOT_TOKEN} не задан. Укажите токен бота в файле .env"
        )

    raw_allowed_ids = os.environ.get(_ENV_ALLOWED_IDS, "")
    if not raw_allowed_ids:
        raise ConfigError(
            f"{_ENV_ALLOWED_IDS} не задан. "
            "Укажите Telegram-ID разрешённых пользователей в файле .env"
        )

    allowed_ids = _parse_allowed_user_ids(raw_allowed_ids)
    working_dir = _resolve_working_dir(os.environ.get(_ENV_WORKING_DIR))
    projects_root = _resolve_projects_root(os.environ.get(_ENV_PROJECTS_ROOT))

    BOT_TOKEN = bot_token
    ALLOWED_USER_IDS = allowed_ids
    WORKING_DIR = working_dir
    PROJECTS_ROOT_DIR = projects_root

    logger.info(
        "Конфигурация загружена: рабочая директория=%s, корень проектов=%s, "
        "пользователей в белом списке=%d",
        WORKING_DIR,
        PROJECTS_ROOT_DIR,
        len(ALLOWED_USER_IDS),
    )
