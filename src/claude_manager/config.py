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
_ENV_E2E_TEST_USER_ID = "E2E_TEST_USER_ID"

# Значение PROJECTS_ROOT_DIR по умолчанию — папка, где у пользователя лежат проекты.
# Используется, если переменная окружения не задана. Бот ищет проекты здесь для команды /projects.
DEFAULT_PROJECTS_ROOT = "/Users/ivan/Desktop/claude-sandbox"

# Имя файла, в котором бот запоминает последний выбранный проект.
# Файл лежит в домашней папке пользователя — не зависит от рабочей директории.
# Используется для восстановления выбранного проекта после перезапуска бота.
LAST_PROJECT_FILE: Path = Path.home() / ".claude-manager-current-project"

# Максимальное время хранения снапшота непрочитанных сообщений (часы).
# Сообщения старше этого возраста не доставляются при возврате в проект.
UNREAD_BUFFER_TTL_HOURS: int = 3

# Константы модуля — заполняются после вызова load_config()
BOT_TOKEN: str = ""
ALLOWED_USER_IDS: set[int] = set()
WORKING_DIR: str = ""
PROJECTS_ROOT_DIR: str = ""
E2E_TEST_USER_ID: int | None = None


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
    global BOT_TOKEN, ALLOWED_USER_IDS, WORKING_DIR, PROJECTS_ROOT_DIR, E2E_TEST_USER_ID

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

    # E2E_TEST_USER_ID — необязательная переменная для изоляции тестового аккаунта
    raw_e2e_id = os.environ.get(_ENV_E2E_TEST_USER_ID, "")
    if raw_e2e_id.strip():
        try:
            E2E_TEST_USER_ID = int(raw_e2e_id.strip())
        except ValueError:
            raise ConfigError(
                f"{_ENV_E2E_TEST_USER_ID} содержит нечисловое значение: '{raw_e2e_id}'"
            )
        logger.info("E2E тестовый аккаунт настроен: user_id=%d", E2E_TEST_USER_ID)
        if E2E_TEST_USER_ID in allowed_ids:
            logger.warning(
                "E2E_TEST_USER_ID (%d) совпадает с одним из ALLOWED_USER_IDS — "
                "это нарушает изоляцию тестового аккаунта. "
                "Уберите этот ID из ALLOWED_USER_IDS",
                E2E_TEST_USER_ID,
            )
    else:
        E2E_TEST_USER_ID = None

    if len(allowed_ids) > 1:
        logger.warning(
            "В ALLOWED_USER_IDS указано %d ID. Бот рассчитан на одного пользователя — "
            "несколько ID поддерживаются только для одного человека с разных устройств. "
            "Добавление ID другого человека может вызвать дублирование сообщений "
            "и конфликты состояния",
            len(allowed_ids),
        )

    logger.info(
        "Конфигурация загружена: рабочая директория=%s, корень проектов=%s, "
        "пользователей в белом списке=%d",
        WORKING_DIR,
        PROJECTS_ROOT_DIR,
        len(ALLOWED_USER_IDS),
    )
