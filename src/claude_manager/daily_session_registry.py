"""Дневная нумерация сессий Claude (#1, #2, #3...).

Присваивает каждой сессии порядковый номер в рамках текущего дня,
сбрасывает счётчик в полночь и позволяет найти сессию по номеру.
Хранит реестр в файле daily_sessions.json с атомарной записью.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from inspect import isawaitable
from pathlib import Path

from claude_manager import config
from claude_manager.coding_agent_backend import BackendName, get_backend

logger = logging.getLogger(__name__)

# Имя файла реестра на диске
REGISTRY_FILENAME = "daily_sessions.json"

# Суффикс временного файла при атомарной записи
REGISTRY_TEMP_SUFFIX = ".tmp"

# Формат даты для ключей реестра (ISO 8601 — однозначный, сортируемый)
DATE_FORMAT = "%Y-%m-%d"

# Количество попыток чтения файла при транзиентной OS-ошибке (EDEADLK и т.п.)
LOAD_RETRY_COUNT = 5

# Пауза между попытками чтения (секунды)
LOAD_RETRY_DELAY_SECONDS = 1

# Максимум одновременных stat-проверок при чистке записей-сирот.
# Без параллелизации проверка существования файла на каждую запись реестра
# превращается в N последовательных I/O операций и тормозит переключение проектов.
MAX_CONCURRENT_ORPHAN_CHECKS = 16

# Backend старых строковых записей. До миграции существовал только Claude.
DEFAULT_BACKEND_FOR_LEGACY_ENTRIES = BackendName.CLAUDE


@dataclass(frozen=True, eq=False)
class DailySessionEntry:
    """Session id, backend ownership, and optional short user-facing summary."""

    session_id: str
    backend: BackendName
    summary: str = ""

    def __eq__(self, other: object) -> bool:
        """Compare entries, with temporary compatibility for old string tests."""
        if isinstance(other, DailySessionEntry):
            return (
                self.session_id == other.session_id
                and self.backend == other.backend
                and self.summary == other.summary
            )
        if isinstance(other, str):
            return self.session_id == other
        return NotImplemented

    def __hash__(self) -> int:
        """Hash entry by its logical ownership pair."""
        return hash((self.session_id, self.backend))

# Внутреннее состояние модуля
# Ключ — дата "YYYY-MM-DD", значение — словарь {номер_строкой: DailySessionEntry}
_registry: dict[str, dict[str, DailySessionEntry]] = {}

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


def _coerce_value_to_entry(raw_value: object) -> DailySessionEntry | None:
    """Convert old and new registry values into a DailySessionEntry."""
    if isinstance(raw_value, DailySessionEntry):
        return raw_value
    if isinstance(raw_value, str):
        return DailySessionEntry(raw_value, DEFAULT_BACKEND_FOR_LEGACY_ENTRIES)
    if isinstance(raw_value, dict):
        session_id = raw_value.get("session_id")
        raw_backend = raw_value.get("backend")
        raw_summary = raw_value.get("summary", "")
        if not isinstance(session_id, str) or not isinstance(raw_backend, str):
            logger.warning("Повреждённая запись дневного реестра: %r", raw_value)
            return None
        if not isinstance(raw_summary, str):
            logger.warning("Некорректное summary в дневном реестре: %r", raw_value)
            raw_summary = ""
        try:
            return DailySessionEntry(
                session_id,
                BackendName(raw_backend),
                raw_summary,
            )
        except ValueError:
            logger.warning("Неизвестный backend в дневном реестре: %r", raw_backend)
            return None
    if raw_value is not None:
        logger.warning("Неподдерживаемая запись дневного реестра: %r", raw_value)
    return None


def _serialize_registry_to_json_dict(
    registry: dict[str, dict[str, object]],
) -> dict[str, dict[str, dict[str, str]]]:
    """Serialize the in-memory registry to the JSON file shape."""
    serialized: dict[str, dict[str, dict[str, str]]] = {}
    for day_key, day_entries in registry.items():
        serialized_day: dict[str, dict[str, str]] = {}
        for number_str, raw_entry in day_entries.items():
            entry = _coerce_value_to_entry(raw_entry)
            if entry is None:
                continue
            serialized_day[number_str] = {
                "session_id": entry.session_id,
                "backend": entry.backend.value,
                "summary": entry.summary,
            }
        serialized[day_key] = serialized_day
    return serialized


def _migrate_registry_to_new_format(
    raw_registry: object,
) -> tuple[dict[str, dict[str, DailySessionEntry]], bool]:
    """Migrate raw JSON registry values to DailySessionEntry objects."""
    if not isinstance(raw_registry, dict):
        return {}, True

    migrated: dict[str, dict[str, DailySessionEntry]] = {}
    had_migrations = False
    for day_key, raw_day_entries in raw_registry.items():
        if not isinstance(day_key, str) or not isinstance(raw_day_entries, dict):
            had_migrations = True
            continue
        migrated_day: dict[str, DailySessionEntry] = {}
        for number_str, raw_value in raw_day_entries.items():
            if not isinstance(number_str, str):
                had_migrations = True
                continue
            entry = _coerce_value_to_entry(raw_value)
            if entry is None:
                had_migrations = True
                continue
            if not isinstance(raw_value, DailySessionEntry):
                had_migrations = True
            migrated_day[number_str] = entry
        migrated[day_key] = migrated_day
    return migrated, had_migrations


async def _save_registry() -> None:
    """Сохраняет реестр на диск атомарно (tmp + rename)."""
    if not _loaded_from_disk:
        logger.warning("Запись реестра заблокирована — данные не были загружены с диска")
        return

    if _registry_path is None:
        raise OSError("Путь к файлу реестра не задан — вызовите load_registry()")

    json_content = json.dumps(
        _serialize_registry_to_json_dict(_registry),
        indent=2,
        ensure_ascii=False,
    )
    temp_path = _registry_path.with_name(REGISTRY_FILENAME + REGISTRY_TEMP_SUFFIX)

    # Запись в файл — блокирующая операция, выносим в поток
    await asyncio.to_thread(temp_path.write_text, json_content, "utf-8")

    # Атомарное переименование (на macOS — безопасная замена)
    await asyncio.to_thread(os.replace, str(temp_path), str(_registry_path))


async def register_session(
    session_id: str,
    backend: BackendName = DEFAULT_BACKEND_FOR_LEGACY_ENTRIES,
) -> int:
    """Регистрирует сессию и возвращает её дневной номер (начиная с 1)."""
    async with _lock:
        _ensure_today_registry()

        today_key = _get_today_key()
        today_entries = _registry[today_key]

        # Если сессия уже зарегистрирована — возвращаем существующий номер
        for number_str, existing_entry in today_entries.items():
            entry = _coerce_value_to_entry(existing_entry)
            if (
                entry is not None
                and entry.session_id == session_id
                and entry.backend == backend
            ):
                return int(number_str)

        # Новая сессия — присваиваем следующий номер
        day_number = _next_day_number()
        today_entries[str(day_number)] = DailySessionEntry(session_id, backend)
        await _save_registry()

        logger.info(
            "Сессия %s (%s) зарегистрирована как #%d",
            session_id,
            backend.value,
            day_number,
        )
        return day_number


async def lookup_by_number(day_number: int) -> DailySessionEntry | None:
    """Ищет сессию по дневному номеру. Возвращает entry или None."""
    async with _lock:
        _ensure_today_registry()

        today_key = _get_today_key()
        today_entries = _registry.get(today_key, {})
        return _coerce_value_to_entry(today_entries.get(str(day_number)))


async def get_session_id_by_number(day_number: int) -> str | None:
    """Compatibility wrapper returning only session_id for old consumers."""
    entry = await lookup_by_number(day_number)
    return entry.session_id if entry is not None else None


async def get_backend_for_session(session_id: str) -> BackendName | None:
    """Find the backend for a known session id across all days."""
    async with _lock:
        matches: set[BackendName] = set()
        for day_entries in _registry.values():
            for raw_entry in day_entries.values():
                entry = _coerce_value_to_entry(raw_entry)
                if entry is not None and entry.session_id == session_id:
                    matches.add(entry.backend)
        if len(matches) == 1:
            return next(iter(matches))
        if len(matches) > 1:
            logger.warning("Неоднозначный backend для session_id %s", session_id)
        return None


async def update_session_id(old_session_id: str, new_session_id: str) -> None:
    """Заменяет временный ID сессии на реальный во всех записях реестра."""
    async with _lock:
        found = False

        # Ищем во всех днях — ID мог быть зарегистрирован вчера
        for day_entries in _registry.values():
            for number_str, current_id in day_entries.items():
                current_entry = _coerce_value_to_entry(current_id)
                if (
                    current_entry is not None
                    and current_entry.session_id == old_session_id
                ):
                    day_entries[number_str] = DailySessionEntry(
                        new_session_id,
                        current_entry.backend,
                        current_entry.summary,
                    )
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


async def update_session_summary(
    session_id: str,
    backend: BackendName,
    summary: str,
) -> None:
    """Stores a generated short summary for a known session."""
    normalized_summary = summary.strip()
    if not normalized_summary:
        return

    async with _lock:
        found = False
        for day_entries in _registry.values():
            for number_str, raw_entry in day_entries.items():
                entry = _coerce_value_to_entry(raw_entry)
                if (
                    entry is not None
                    and entry.session_id == session_id
                    and entry.backend == backend
                ):
                    day_entries[number_str] = DailySessionEntry(
                        entry.session_id,
                        entry.backend,
                        normalized_summary,
                    )
                    found = True

        if not found:
            logger.debug(
                "Session summary skipped: session %s (%s) is not registered",
                session_id,
                backend.value,
            )
            return

        await _save_registry()
        logger.info(
            "Session summary обновлён: %s (%s)",
            session_id,
            backend.value,
        )


async def get_session_summary(
    session_id: str,
    backend: BackendName,
) -> str:
    """Returns a generated session summary, or an empty string if missing."""
    async with _lock:
        for day_entries in _registry.values():
            for raw_entry in day_entries.values():
                entry = _coerce_value_to_entry(raw_entry)
                if (
                    entry is not None
                    and entry.session_id == session_id
                    and entry.backend == backend
                    and entry.summary.strip()
                ):
                    return entry.summary
        return ""


async def get_all_today_sessions() -> dict[int, DailySessionEntry]:
    """Возвращает все сессии текущего дня: {номер: session_id}."""
    async with _lock:
        _ensure_today_registry()

        today_key = _get_today_key()
        today_entries = _registry.get(today_key, {})

        # Конвертируем строковые ключи в числовые и возвращаем копию
        return {
            int(number): entry
            for number, raw_entry in today_entries.items()
            if (entry := _coerce_value_to_entry(raw_entry)) is not None
        }


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
            number for number, raw_entry in day_entries.items()
            if (
                (entry := _coerce_value_to_entry(raw_entry)) is not None
                and entry.session_id.startswith("_new_")
            )
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
        # Группируем номера по паре (session_id, backend)
        numbers_by_session: dict[tuple[str, BackendName], list[int]] = {}
        for number_str, raw_entry in day_entries.items():
            entry = _coerce_value_to_entry(raw_entry)
            if entry is None:
                continue
            key = (entry.session_id, entry.backend)
            numbers_by_session.setdefault(key, []).append(int(number_str))

        # Находим session_id с несколькими номерами и удаляем лишние
        for (session_id, backend), numbers in numbers_by_session.items():
            if len(numbers) < 2:
                continue

            numbers.sort()
            kept_number = numbers[0]
            duplicate_numbers = numbers[1:]

            for duplicate_number in duplicate_numbers:
                del day_entries[str(duplicate_number)]
                logger.info(
                    "Удалён дубликат: день %s, session_id %s — убран #%d (оставлен #%d)",
                    day_key, f"{session_id}/{backend.value}", duplicate_number, kept_number,
                )

            total_removed += len(duplicate_numbers)

    return total_removed


async def _remove_orphan_entries() -> int:
    """Удаляет записи с session_id, для которых нет .jsonl файла на диске."""
    candidates_to_check, immediate_orphans = _classify_registry_entries()

    if not candidates_to_check:
        _delete_orphan_entries(immediate_orphans)
        return len(immediate_orphans)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ORPHAN_CHECKS)

    async def check_one(
        day_key: str, number_str: str, entry: DailySessionEntry,
    ) -> tuple[str, str, bool] | None:
        async with semaphore:
            try:
                backend = get_backend(entry.backend)
                file_exists = await backend.session_file_exists_for_project(
                    entry.session_id,
                    config.WORKING_DIR,
                )
            except Exception as error:
                logger.warning(
                    "Не удалось проверить файл сессии %s (%s): %s",
                    entry.session_id,
                    entry.backend.value,
                    error,
                )
                return None
        return day_key, number_str, file_exists

    check_results = await asyncio.gather(
        *(
            check_one(day_key, number_str, entry)
            for day_key, number_str, entry in candidates_to_check
        )
    )

    orphan_keys = list(immediate_orphans)
    for (day_key, number_str, entry), result in zip(
        candidates_to_check, check_results, strict=True,
    ):
        if result is None:
            continue
        _, _, file_exists = result
        if not file_exists:
            orphan_keys.append((day_key, number_str))
            logger.info(
                "Запись-сирота: день %s, #%s -> %s (%s, файл не найден)",
                day_key, number_str, entry.session_id, entry.backend.value,
            )

    _delete_orphan_entries(orphan_keys)
    return len(orphan_keys)


def _classify_registry_entries() -> tuple[
    list[tuple[str, str, DailySessionEntry]],
    list[tuple[str, str]],
]:
    """Разделяет записи реестра на «требуют проверки на диске» и «удалить сразу»."""
    candidates: list[tuple[str, str, DailySessionEntry]] = []
    immediate_orphans: list[tuple[str, str]] = []
    for day_key, day_entries in _registry.items():
        for number_str, raw_entry in day_entries.items():
            entry = _coerce_value_to_entry(raw_entry)
            if entry is None:
                immediate_orphans.append((day_key, number_str))
                continue
            # Записи с _new_ — временные ID, для них файлов нет по определению
            if entry.session_id.startswith("_new_"):
                continue
            candidates.append((day_key, number_str, entry))
    return candidates, immediate_orphans


def _delete_orphan_entries(keys_to_delete: list[tuple[str, str]]) -> None:
    """Удаляет указанные записи реестра по парам (день, номер)."""
    for day_key, number_str in keys_to_delete:
        day_entries = _registry.get(day_key)
        if day_entries is not None and number_str in day_entries:
            del day_entries[number_str]


async def load_registry() -> None:
    """Загружает реестр из файла daily_sessions.json в память."""
    global _registry, _registry_path, _loaded_from_disk

    _registry_path = Path(config.WORKING_DIR) / REGISTRY_FILENAME

    result = await _read_registry_file()

    had_migrations = False
    if result is not None:
        _registry, had_migrations = _migrate_registry_to_new_format(result)
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
    orphan_cleanup_result = _remove_orphan_entries()
    if isawaitable(orphan_cleanup_result):
        orphan_count = await orphan_cleanup_result
    else:
        orphan_count = orphan_cleanup_result
    if orphan_count > 0:
        logger.info("Удалено %d записей-сирот без файлов на диске", orphan_count)

    # Если были удаления — сохраняем очищенный реестр
    if _loaded_from_disk and (
        had_migrations
        or phantom_count > 0
        or duplicate_count > 0
        or orphan_count > 0
    ):
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
