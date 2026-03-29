"""Дневная нумерация сессий Claude (#1, #2, #3...).

Присваивает каждой сессии порядковый номер в рамках текущего дня,
сбрасывает счётчик в полночь и позволяет найти сессию по номеру.
Хранит реестр в файле daily_sessions.json с атомарной записью.
"""

import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path

from claude_manager import config

logger = logging.getLogger(__name__)

# Имя файла реестра на диске
REGISTRY_FILENAME = "daily_sessions.json"

# Суффикс временного файла при атомарной записи
REGISTRY_TEMP_SUFFIX = ".tmp"

# Формат даты для ключей реестра (ISO 8601 — однозначный, сортируемый)
DATE_FORMAT = "%Y-%m-%d"

# Внутреннее состояние модуля
# Ключ — дата "YYYY-MM-DD", значение — словарь {номер_строкой: session_id}
_registry: dict[str, dict[str, str]] = {}

# Защита от параллельного чтения/записи
_lock = asyncio.Lock()

# Путь к файлу реестра (заполняется при load_registry)
_registry_path: Path | None = None


def _get_today_key() -> str:
    """Возвращает ключ текущего дня в формате 'YYYY-MM-DD'."""
    return date.today().strftime(DATE_FORMAT)


def _ensure_today_registry() -> None:
    """Создаёт пустую секцию для текущего дня, если её ещё нет."""
    today_key = _get_today_key()
    if today_key not in _registry:
        _registry[today_key] = {}


def _next_day_number() -> int:
    """Возвращает следующий свободный номер для текущего дня."""
    today_key = _get_today_key()
    today_entries = _registry.get(today_key, {})

    if not today_entries:
        return 1

    # Ключи хранятся как строки — преобразуем в числа для поиска максимума
    existing_numbers = [int(number) for number in today_entries]
    return max(existing_numbers) + 1


async def _save_registry() -> None:
    """Сохраняет реестр на диск атомарно (tmp + rename)."""
    if _registry_path is None:
        raise OSError("Путь к файлу реестра не задан — вызовите load_registry()")

    json_content = json.dumps(_registry, indent=2, ensure_ascii=False)
    temp_path = _registry_path.with_name(REGISTRY_FILENAME + REGISTRY_TEMP_SUFFIX)

    # Запись в файл — блокирующая операция, выносим в поток
    await asyncio.to_thread(temp_path.write_text, json_content, "utf-8")

    # Атомарное переименование (на macOS — безопасная замена)
    await asyncio.to_thread(os.replace, str(temp_path), str(_registry_path))


async def register_session(session_id: str) -> int:
    """Регистрирует сессию и возвращает её дневной номер (начиная с 1)."""
    async with _lock:
        _ensure_today_registry()

        today_key = _get_today_key()
        today_entries = _registry[today_key]

        # Если сессия уже зарегистрирована — возвращаем существующий номер
        for number_str, existing_id in today_entries.items():
            if existing_id == session_id:
                return int(number_str)

        # Новая сессия — присваиваем следующий номер
        day_number = _next_day_number()
        today_entries[str(day_number)] = session_id
        await _save_registry()

        logger.info("Сессия %s зарегистрирована как #%d", session_id, day_number)
        return day_number


async def get_session_id_by_number(day_number: int) -> str | None:
    """Ищет сессию по дневному номеру. Возвращает session_id или None."""
    async with _lock:
        _ensure_today_registry()

        today_key = _get_today_key()
        today_entries = _registry.get(today_key, {})
        return today_entries.get(str(day_number))


async def update_session_id(old_session_id: str, new_session_id: str) -> None:
    """Заменяет временный ID сессии на реальный во всех записях реестра."""
    async with _lock:
        found = False

        # Ищем во всех днях — ID мог быть зарегистрирован вчера
        for day_entries in _registry.values():
            for number_str, current_id in day_entries.items():
                if current_id == old_session_id:
                    day_entries[number_str] = new_session_id
                    found = True
                    break
            if found:
                break

        if not found:
            logger.debug(
                "Session ID %s не найден в реестре — пропускаем обновление",
                old_session_id,
            )
            return

        await _save_registry()
        logger.info("Session ID обновлён: %s → %s", old_session_id, new_session_id)


async def get_all_today_sessions() -> dict[int, str]:
    """Возвращает все сессии текущего дня: {номер: session_id}."""
    async with _lock:
        _ensure_today_registry()

        today_key = _get_today_key()
        today_entries = _registry.get(today_key, {})

        # Конвертируем строковые ключи в числовые и возвращаем копию
        return {int(number): session_id for number, session_id in today_entries.items()}


async def load_registry() -> None:
    """Загружает реестр из файла daily_sessions.json в память."""
    global _registry, _registry_path

    # Путь к файлу реестра — рядом с sessions.json в рабочей директории
    _registry_path = Path(config.WORKING_DIR) / REGISTRY_FILENAME

    try:
        content = await asyncio.to_thread(_registry_path.read_text, "utf-8")
        _registry = json.loads(content)
        logger.info("Реестр дневных сессий загружен из %s", _registry_path)
    except FileNotFoundError:
        _registry = {}
        logger.info("Файл реестра не найден, создаю пустой реестр")
    except json.JSONDecodeError:
        _registry = {}
        logger.warning("Файл реестра повреждён, создаю пустой реестр")

    _ensure_today_registry()
