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

from claude_manager import config, session_reader

logger = logging.getLogger(__name__)

# Имя файла реестра на диске
REGISTRY_FILENAME = "daily_sessions.json"

# Суффикс временного файла при атомарной записи
REGISTRY_TEMP_SUFFIX = ".tmp"

# Формат даты для ключей реестра (ISO 8601 — однозначный, сортируемый)
DATE_FORMAT = "%Y-%m-%d"

# Количество попыток чтения файла при непредвиденной ошибке
LOAD_RETRY_COUNT = 10

# Пауза между попытками чтения (секунды)
LOAD_RETRY_DELAY_SECONDS = 2

# Внутреннее состояние модуля
# Ключ — дата "YYYY-MM-DD", значение — словарь {номер_строкой: session_id}
_registry: dict[str, dict[str, str]] = {}

# Защита от параллельного чтения/записи
_lock = asyncio.Lock()

# Путь к файлу реестра (заполняется при load_registry)
_registry_path: Path | None = None

# Был ли реестр успешно загружен (защита от затирания данных при записи)
_loaded_from_disk: bool = False


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
    if not _loaded_from_disk:
        logger.warning("Запись реестра заблокирована — данные не были загружены с диска")
        return

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


def is_registry_loaded() -> bool:
    """Проверяет, был ли реестр успешно загружен с диска."""
    return _loaded_from_disk


async def _read_registry_file() -> dict | None:
    """Читает файл реестра с повторными попытками при непредвиденных ошибках.

    Возвращает словарь с данными или None если все попытки исчерпаны.
    """
    for attempt in range(1, LOAD_RETRY_COUNT + 1):
        try:
            content = await asyncio.to_thread(_registry_path.read_text, "utf-8")
            return json.loads(content)
        except FileNotFoundError:
            logger.info("Файл реестра не найден, создаю пустой реестр")
            return {}
        except json.JSONDecodeError:
            logger.warning("Файл реестра повреждён, создаю пустой реестр")
            return {}
        except Exception as error:
            logger.warning("Попытка чтения реестра %d/%d: %s", attempt, LOAD_RETRY_COUNT, error)
            if attempt < LOAD_RETRY_COUNT:
                await asyncio.sleep(LOAD_RETRY_DELAY_SECONDS)

    logger.error("Не удалось прочитать реестр после %d попыток", LOAD_RETRY_COUNT)
    return None


def _remove_phantom_entries() -> int:
    """Удаляет записи с временными session_id (префикс '_new_') из реестра."""
    total_removed = 0
    for day_key, day_entries in _registry.items():
        phantom_keys = [
            number for number, session_id in day_entries.items()
            if session_id.startswith("_new_")
        ]
        for key in phantom_keys:
            del day_entries[key]
        total_removed += len(phantom_keys)
    return total_removed


def _remove_duplicate_entries() -> int:
    """Удаляет дубликаты session_id внутри каждого дня, оставляя запись с наименьшим номером.

    Race condition между watcher и обработчиком сообщений может привести к тому,
    что один UUID регистрируется под двумя разными номерами. Эта функция находит
    такие дубликаты и оставляет только первую регистрацию (наименьший номер).
    """
    total_removed = 0

    for day_key, day_entries in _registry.items():
        # Группируем номера по session_id
        numbers_by_session: dict[str, list[int]] = {}
        for number_str, session_id in day_entries.items():
            numbers_by_session.setdefault(session_id, []).append(int(number_str))

        # Находим session_id с несколькими номерами и удаляем лишние
        for session_id, numbers in numbers_by_session.items():
            if len(numbers) < 2:
                continue

            numbers.sort()
            kept_number = numbers[0]
            duplicate_numbers = numbers[1:]

            for duplicate_number in duplicate_numbers:
                del day_entries[str(duplicate_number)]
                logger.info(
                    "Удалён дубликат: день %s, session_id %s — убран #%d (оставлен #%d)",
                    day_key, session_id, duplicate_number, kept_number,
                )

            total_removed += len(duplicate_numbers)

    return total_removed


def _remove_orphan_entries() -> int:
    """Удаляет записи с session_id, для которых нет .jsonl файла на диске."""
    sessions_path = session_reader.build_sessions_path(config.WORKING_DIR)
    total_removed = 0

    for day_key, day_entries in _registry.items():
        orphan_keys = []
        for number_str, session_id in day_entries.items():
            # Записи с _new_ — временные ID, для них файлов нет по определению
            if session_id.startswith("_new_"):
                continue

            file_path = os.path.join(sessions_path, f"{session_id}.jsonl")
            if not os.path.exists(file_path):
                orphan_keys.append(number_str)
                logger.info(
                    "Запись-сирота: день %s, #%s -> %s (файл не найден)",
                    day_key, number_str, session_id,
                )

        for key in orphan_keys:
            del day_entries[key]
        total_removed += len(orphan_keys)

    return total_removed


async def load_registry() -> None:
    """Загружает реестр из файла daily_sessions.json в память."""
    global _registry, _registry_path, _loaded_from_disk

    _registry_path = Path(config.WORKING_DIR) / REGISTRY_FILENAME

    result = await _read_registry_file()

    if result is not None:
        _registry = result
        _loaded_from_disk = True
        if result:
            logger.info("Реестр дневных сессий загружен из %s", _registry_path)
    else:
        _registry = {}
        _loaded_from_disk = False

    # Удаляем фантомные записи с временными ID (префикс _new_).
    # Они появляются, когда сессия была зарегистрирована, но Claude CLI
    # не вернул реальный session_id. Watcher пытается найти файлы
    # для таких сессий и генерирует тысячи предупреждений.
    phantom_count = _remove_phantom_entries()
    if phantom_count > 0:
        logger.info("Удалено %d фантомных записей с префиксом _new_", phantom_count)

    # Удаляем дубликаты — один session_id, зарегистрированный под несколькими номерами.
    # Возникают из-за race condition между watcher и обработчиком сообщений.
    duplicate_count = _remove_duplicate_entries()
    if duplicate_count > 0:
        logger.info("Удалено %d дублированных записей session_id", duplicate_count)

    # Удаляем записи-сироты — session_id, для которых нет .jsonl файла
    # в текущем проекте. Появляются из-за race condition при переключении
    # проектов: watcher регистрирует сессию из нового проекта в реестр старого.
    orphan_count = _remove_orphan_entries()
    if orphan_count > 0:
        logger.info("Удалено %d записей-сирот без файлов на диске", orphan_count)

    # Если были удаления — сохраняем очищенный реестр
    if _loaded_from_disk and (phantom_count > 0 or duplicate_count > 0 or orphan_count > 0):
        await _save_registry()

    _ensure_today_registry()


async def reset_state() -> None:
    """Сбрасывает реестр и путь к файлу, перезагружает данные из нового WORKING_DIR."""
    global _registry, _registry_path, _loaded_from_disk

    # Сбрасываем состояние под блокировкой.
    # Флаг _loaded_from_disk критично сбросить в False: иначе _save_registry
    # продолжит считать, что данные корректны, и запишет пустой реестр в новый файл.
    async with _lock:
        _registry = {}
        _registry_path = None
        _loaded_from_disk = False

    # Повторно загружаем реестр — пересчитает _registry_path и установит _loaded_from_disk
    await load_registry()
    logger.info("Состояние daily_session_registry сброшено и перезагружено")
