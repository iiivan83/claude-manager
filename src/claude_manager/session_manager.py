"""Управление привязкой Telegram-чатов к сессиям Claude.

Привязывает chat_id к session_id, переключает между сессиями,
управляет режимом мониторинга (/all). Сохраняет привязки
в файл sessions.json с атомарной записью.
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from claude_manager import config, daily_session_registry, session_reader
from claude_manager.coding_agent_backend import BackendName

logger = logging.getLogger(__name__)

# Имя файла привязок на диске
BINDINGS_FILENAME = "sessions.json"

# Суффикс временного файла при атомарной записи
BINDINGS_TEMP_SUFFIX = ".tmp"

# Префикс временных session_id (для новых сессий до получения реального ID)
TEMP_SESSION_PREFIX = "_new_"

# Длина hex-части временного session_id
TEMP_SESSION_HEX_LENGTH = 12

# Количество попыток чтения файла привязок при транзиентной OS-ошибке (EDEADLK и т.п.)
LOAD_RETRY_COUNT = 5

# Пауза между попытками чтения (секунды)
LOAD_RETRY_DELAY_SECONDS = 1

# Backend старых строковых записей. До миграции существовал только Claude.
DEFAULT_BACKEND_FOR_LEGACY_BINDINGS = BackendName.CLAUDE

# Внутреннее состояние: привязки {chat_id: ActiveSession}
_bindings: dict[int, "ActiveSession"] = {}

# Защита от параллельного чтения/записи
_lock = asyncio.Lock()

# Путь к файлу привязок (заполняется при load_bindings)
_bindings_path: Path | None = None

# Были ли привязки успешно загружены с диска.
# False блокирует запись, чтобы transient ошибка чтения не затёрла sessions.json.
_bindings_loaded_from_disk: bool = False


@dataclass(frozen=True, eq=False)
class ActiveSession:
    """Active session id plus the backend that owns it."""

    session_id: str
    backend: BackendName

    def __eq__(self, other: object) -> bool:
        """Compare active sessions, with temporary compatibility for strings."""
        if isinstance(other, ActiveSession):
            return (
                self.session_id == other.session_id
                and self.backend == other.backend
            )
        if isinstance(other, str):
            return self.session_id == other
        return NotImplemented

    def __hash__(self) -> int:
        """Hash active session by its ownership pair."""
        return hash((self.session_id, self.backend))


@dataclass
class SwitchResult:
    """Результат переключения на сессию по дневному номеру."""

    found: bool
    session_id: str
    day_number: int
    preview: str
    backend: BackendName = DEFAULT_BACKEND_FOR_LEGACY_BINDINGS


@dataclass
class NewSessionResult:
    """Результат создания новой сессии."""

    session_id: str
    day_number: int
    backend: BackendName = DEFAULT_BACKEND_FOR_LEGACY_BINDINGS


def _generate_temp_session_id() -> str:
    """Генерирует уникальный временный ID вида _new_<uuid>."""
    return f"{TEMP_SESSION_PREFIX}{uuid.uuid4().hex[:TEMP_SESSION_HEX_LENGTH]}"


def _coerce_binding_value(raw_value: object) -> ActiveSession | None:
    """Convert old and new sessions.json values to ActiveSession."""
    if isinstance(raw_value, ActiveSession):
        return raw_value
    if isinstance(raw_value, str):
        return ActiveSession(raw_value, DEFAULT_BACKEND_FOR_LEGACY_BINDINGS)
    if not isinstance(raw_value, dict):
        logger.warning("Неподдерживаемая запись привязки: %r", raw_value)
        return None

    session_id = raw_value.get("session_id")
    raw_backend = raw_value.get("backend")
    if not isinstance(session_id, str) or not isinstance(raw_backend, str):
        logger.warning("Повреждённая запись привязки: %r", raw_value)
        return None

    try:
        backend = BackendName(raw_backend)
    except ValueError:
        logger.warning("Неизвестный backend в sessions.json: %r", raw_backend)
        return None

    return ActiveSession(session_id, backend)


def _serialize_bindings_to_json_dict() -> dict[str, dict[str, str]]:
    """Serialize in-memory bindings to the sessions.json shape."""
    serializable: dict[str, dict[str, str]] = {}
    for chat_id, raw_active_session in _bindings.items():
        active_session = _coerce_binding_value(raw_active_session)
        if active_session is None:
            continue
        serializable[str(chat_id)] = {
            "session_id": active_session.session_id,
            "backend": active_session.backend.value,
        }
    return serializable


async def _save_bindings() -> None:
    """Сохраняет привязки на диск атомарно (tmp + rename)."""
    if _bindings_path is None:
        raise RuntimeError(
            "Путь к файлу привязок не задан — вызовите load_bindings() перед записью"
        )
    if not _bindings_loaded_from_disk:
        raise RuntimeError(
            "Привязки не загружены с диска — запись sessions.json заблокирована, "
            "чтобы не затереть существующие данные"
        )

    # JSON не поддерживает числовые ключи — конвертируем chat_id в строки
    json_content = json.dumps(
        _serialize_bindings_to_json_dict(),
        indent=2,
        ensure_ascii=False,
    )

    temp_path = _bindings_path.with_name(BINDINGS_FILENAME + BINDINGS_TEMP_SUFFIX)

    # Запись в файл — блокирующая операция, выносим в поток
    await asyncio.to_thread(temp_path.write_text, json_content, "utf-8")

    # Атомарное переименование (на macOS — безопасная замена)
    await asyncio.to_thread(os.replace, str(temp_path), str(_bindings_path))


async def _find_session_among_visible(
    day_number: int,
) -> tuple[str, BackendName, str] | None:
    """Ищет сессию по дневному номеру среди всех видимых сессий на диске."""
    sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)

    for session in sessions:
        assigned_number = await daily_session_registry.register_session(
            session.session_id,
            BackendName.CLAUDE,
        )
        if assigned_number == day_number:
            return (session.session_id, BackendName.CLAUDE, session.preview)

    return None


async def set_active_session(
    chat_id: int,
    session_id: str,
    backend: BackendName,
) -> int:
    """Привязывает Telegram-чат к backend-aware сессии."""
    async with _lock:
        _bindings[chat_id] = ActiveSession(session_id, backend)
        day_number = await daily_session_registry.register_session(session_id, backend)
        await _save_bindings()
        logger.info(
            "Чат %d привязан к сессии %s (#%d, backend=%s)",
            chat_id,
            session_id,
            day_number,
            backend.value,
        )
    return day_number


async def bind_session(chat_id: int, session_id: str) -> int:
    """Compatibility wrapper binding a Claude session by session_id only."""
    return await set_active_session(
        chat_id,
        session_id,
        DEFAULT_BACKEND_FOR_LEGACY_BINDINGS,
    )


async def clear_active_session(chat_id: int) -> None:
    """Отвязывает чат от сессии (переводит в режим /all мониторинга)."""
    async with _lock:
        _bindings.pop(chat_id, None)
        await _save_bindings()
        logger.info("Чат %d переведён в режим мониторинга (/all)", chat_id)


async def unbind_session(chat_id: int) -> None:
    """Compatibility wrapper clearing the active session."""
    await clear_active_session(chat_id)


def get_active_session(chat_id: int) -> ActiveSession | None:
    """Возвращает активную backend-aware сессию или None."""
    if chat_id not in _bindings:
        return None
    return _coerce_binding_value(_bindings.get(chat_id))


def get_active_session_id(chat_id: int) -> str | None:
    """Возвращает только session_id активной сессии или None."""
    active_session = get_active_session(chat_id)
    if active_session is None:
        return None
    return active_session.session_id


def get_bound_session(chat_id: int) -> str | None:
    """Compatibility wrapper returning only the bound session_id."""
    return get_active_session_id(chat_id)


def find_chat_by_session_id(session_id: str, backend: BackendName) -> int | None:
    """Возвращает chat_id владельца пары session_id/backend."""
    for chat_id, raw_active_session in _bindings.items():
        active_session = _coerce_binding_value(raw_active_session)
        if active_session is None:
            continue
        if (
            active_session.session_id == session_id
            and active_session.backend == backend
        ):
            return chat_id
    return None


def get_chat_id_for_session(session_id: str) -> int | None:
    """Compatibility wrapper returning a chat for any backend with this session_id."""
    for chat_id, raw_active_session in _bindings.items():
        active_session = _coerce_binding_value(raw_active_session)
        if active_session is None:
            continue
        if active_session.session_id == session_id:
            return chat_id
    return None


def is_monitoring_mode(chat_id: int) -> bool:
    """Проверяет, находится ли чат в режиме /all мониторинга."""
    return chat_id not in _bindings


async def switch_to_session(chat_id: int, day_number: int) -> SwitchResult:
    """Переключает чат на сессию по дневному номеру."""
    # Шаг 1: ищем в дневном реестре
    daily_entry = await daily_session_registry.lookup_by_number(day_number)

    if daily_entry is not None:
        # Сессия найдена в реестре — получаем превью из списка видимых сессий
        preview = await _get_preview_for_session(daily_entry.session_id)
        await set_active_session(
            chat_id,
            daily_entry.session_id,
            daily_entry.backend,
        )
        return SwitchResult(
            found=True,
            session_id=daily_entry.session_id,
            day_number=day_number,
            preview=preview,
            backend=daily_entry.backend,
        )

    # Шаг 2: ищем среди всех видимых сессий на диске
    visible_result = await _find_session_among_visible(day_number)

    if visible_result is not None:
        found_session_id, found_backend, found_preview = visible_result
        await set_active_session(chat_id, found_session_id, found_backend)
        return SwitchResult(
            found=True,
            session_id=found_session_id,
            day_number=day_number,
            preview=found_preview,
            backend=found_backend,
        )

    # Сессия не найдена нигде
    return SwitchResult(
        found=False,
        session_id="",
        day_number=day_number,
        preview="",
        backend=DEFAULT_BACKEND_FOR_LEGACY_BINDINGS,
    )


async def _get_preview_for_session(session_id: str) -> str:
    """Получает превью первого сообщения сессии из списка видимых сессий."""
    sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)
    for session in sessions:
        if session.session_id == session_id:
            return session.preview
    return ""


async def update_session_id(chat_id: int, old_session_id: str, new_session_id: str) -> None:
    """Обновляет session_id в привязках и дневном реестре."""
    async with _lock:
        active_session = _bindings.get(chat_id)
        # Обновляем привязку чата, только если он привязан к старому ID
        if active_session is not None and active_session.session_id == old_session_id:
            _bindings[chat_id] = ActiveSession(
                new_session_id,
                active_session.backend,
            )
        else:
            logger.debug(
                "Чат %d не привязан к сессии %s — привязка не обновлена",
                chat_id,
                old_session_id,
            )

        # В дневном реестре обновляем всегда
        await daily_session_registry.update_session_id(old_session_id, new_session_id)
        await _save_bindings()
        logger.info("Session ID обновлён в привязках: %s → %s", old_session_id, new_session_id)


async def create_new_session(
    chat_id: int,
    backend: BackendName = DEFAULT_BACKEND_FOR_LEGACY_BINDINGS,
) -> NewSessionResult:
    """Создаёт новую сессию с временным ID и привязывает к чату."""
    temp_session_id = _generate_temp_session_id()
    day_number = await set_active_session(chat_id, temp_session_id, backend)
    logger.info(
        "Создана новая сессия %s (#%d) для чата %d, backend=%s",
        temp_session_id,
        day_number,
        chat_id,
        backend.value,
    )
    return NewSessionResult(
        session_id=temp_session_id,
        day_number=day_number,
        backend=backend,
    )


async def load_bindings() -> None:
    """Загружает привязки из sessions.json и дневной реестр при запуске бота."""
    global _bindings, _bindings_path, _bindings_loaded_from_disk

    _bindings_path = Path(config.WORKING_DIR) / BINDINGS_FILENAME
    _bindings_loaded_from_disk = False

    # Загружаем дневной реестр
    await daily_session_registry.load_registry()

    # Читаем файл привязок с повторными попытками при транзиентных OS-ошибках
    raw_data = None
    for attempt in range(1, LOAD_RETRY_COUNT + 1):
        try:
            content = await asyncio.to_thread(_bindings_path.read_text, "utf-8")
            raw_data = json.loads(content)
            _bindings_loaded_from_disk = True
            break
        except FileNotFoundError:
            _bindings = {}
            _bindings_loaded_from_disk = True
            logger.info("Файл привязок не найден, начинаю с чистого состояния")
            return
        except json.JSONDecodeError:
            _bindings = {}
            _bindings_loaded_from_disk = True
            logger.warning("Файл привязок повреждён, начинаю с чистого состояния")
            return
        except Exception as error:
            logger.warning(
                "Попытка чтения привязок %d/%d: %s",
                attempt, LOAD_RETRY_COUNT, error,
            )
            if attempt < LOAD_RETRY_COUNT:
                await asyncio.sleep(LOAD_RETRY_DELAY_SECONDS)

    if raw_data is None:
        logger.error(
            "Не удалось прочитать привязки после %d попыток, начинаю с чистого состояния",
            LOAD_RETRY_COUNT,
        )
        _bindings = {}
        _bindings_loaded_from_disk = False
        return

    if not isinstance(raw_data, dict):
        logger.warning("Файл привязок имеет неожиданный формат, начинаю с чистого состояния")
        _bindings = {}
        _bindings_loaded_from_disk = True
        return

    # Конвертируем строковые ключи JSON в числовые chat_id
    _bindings = {}
    for raw_key, raw_value in raw_data.items():
        try:
            chat_id = int(raw_key)
        except ValueError:
            logger.warning("Невалидный ключ chat_id в sessions.json: '%s' — пропущен", raw_key)
            continue

        active_session = _coerce_binding_value(raw_value)
        if active_session is None:
            continue
        _bindings[chat_id] = active_session

    logger.info("Загружено %d привязок из sessions.json", len(_bindings))


def get_all_bindings() -> dict[int, ActiveSession]:
    """Возвращает копию всех backend-aware привязок."""
    return {
        chat_id: active_session
        for chat_id, raw_active_session in _bindings.items()
        if (active_session := _coerce_binding_value(raw_active_session)) is not None
    }


async def reset_state() -> None:
    """Сбрасывает привязки и путь к файлу, перезагружает данные из нового WORKING_DIR."""
    global _bindings, _bindings_path, _bindings_loaded_from_disk

    # Очищаем состояние под блокировкой, чтобы не конфликтовать с параллельными операциями.
    # load_bindings будет вызвана ниже без блокировки — она сама не использует _lock,
    # а save_bindings внутри неё не вызывается (load только читает).
    async with _lock:
        _bindings = {}
        # Сбрасываем кэшированный путь — иначе load_bindings продолжит читать старый файл
        _bindings_path = None
        _bindings_loaded_from_disk = False

    # Повторно загружаем привязки — функция пересчитает _bindings_path из текущего config.WORKING_DIR
    await load_bindings()
    logger.info("Состояние session_manager сброшено и перезагружено")
