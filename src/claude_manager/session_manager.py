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

logger = logging.getLogger(__name__)

# Имя файла привязок на диске
BINDINGS_FILENAME = "sessions.json"

# Суффикс временного файла при атомарной записи
BINDINGS_TEMP_SUFFIX = ".tmp"

# Префикс временных session_id (для новых сессий до получения реального ID)
TEMP_SESSION_PREFIX = "_new_"

# Внутреннее состояние: привязки {chat_id: session_id}
_bindings: dict[int, str] = {}

# Защита от параллельного чтения/записи
_lock = asyncio.Lock()

# Путь к файлу привязок (заполняется при load_bindings)
_bindings_path: Path | None = None

@dataclass
class SwitchResult:
    """Результат переключения на сессию по дневному номеру."""

    found: bool
    session_id: str
    day_number: int
    preview: str


@dataclass
class NewSessionResult:
    """Результат создания новой сессии."""

    session_id: str
    day_number: int


def _generate_temp_session_id() -> str:
    """Генерирует уникальный временный ID вида _new_<uuid>."""
    return f"{TEMP_SESSION_PREFIX}{uuid.uuid4().hex[:12]}"


async def _save_bindings() -> None:
    """Сохраняет привязки на диск атомарно (tmp + rename)."""
    if _bindings_path is None:
        raise OSError("Путь к файлу привязок не задан — вызовите load_bindings()")

    # JSON не поддерживает числовые ключи — конвертируем chat_id в строки
    serializable = {str(chat_id): session_id for chat_id, session_id in _bindings.items()}
    json_content = json.dumps(serializable, indent=2, ensure_ascii=False)

    temp_path = _bindings_path.with_name(BINDINGS_FILENAME + BINDINGS_TEMP_SUFFIX)

    # Запись в файл — блокирующая операция, выносим в поток
    await asyncio.to_thread(temp_path.write_text, json_content, "utf-8")

    # Атомарное переименование (на macOS — безопасная замена)
    await asyncio.to_thread(os.replace, str(temp_path), str(_bindings_path))


async def _find_session_among_visible(day_number: int) -> tuple[str, str] | None:
    """Ищет сессию по дневному номеру среди всех видимых сессий на диске."""
    sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)

    for session in sessions:
        assigned_number = await daily_session_registry.register_session(session.session_id)
        if assigned_number == day_number:
            return (session.session_id, session.preview)

    return None


async def bind_session(chat_id: int, session_id: str) -> int:
    """Привязывает Telegram-чат к сессии Claude и возвращает дневной номер."""
    async with _lock:
        _bindings[chat_id] = session_id
        day_number = await daily_session_registry.register_session(session_id)
        await _save_bindings()
        logger.info("Чат %d привязан к сессии %s (#%d)", chat_id, session_id, day_number)
    return day_number


async def unbind_session(chat_id: int) -> None:
    """Отвязывает чат от сессии (переводит в режим /all мониторинга)."""
    async with _lock:
        _bindings.pop(chat_id, None)
        await _save_bindings()
        logger.info("Чат %d переведён в режим мониторинга (/all)", chat_id)


def get_bound_session(chat_id: int) -> str | None:
    """Возвращает session_id привязанной сессии или None (режим /all)."""
    return _bindings.get(chat_id)


def is_monitoring_mode(chat_id: int) -> bool:
    """Проверяет, находится ли чат в режиме /all мониторинга."""
    return chat_id not in _bindings


async def switch_to_session(chat_id: int, day_number: int) -> SwitchResult:
    """Переключает чат на сессию по дневному номеру."""
    # Шаг 1: ищем в дневном реестре
    session_id = await daily_session_registry.get_session_id_by_number(day_number)

    if session_id is not None:
        # Сессия найдена в реестре — получаем превью из списка видимых сессий
        preview = await _get_preview_for_session(session_id)
        await bind_session(chat_id, session_id)
        return SwitchResult(found=True, session_id=session_id, day_number=day_number, preview=preview)

    # Шаг 2: ищем среди всех видимых сессий на диске
    visible_result = await _find_session_among_visible(day_number)

    if visible_result is not None:
        found_session_id, found_preview = visible_result
        await bind_session(chat_id, found_session_id)
        return SwitchResult(
            found=True, session_id=found_session_id, day_number=day_number, preview=found_preview
        )

    # Сессия не найдена нигде
    return SwitchResult(found=False, session_id="", day_number=day_number, preview="")


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
        # Обновляем привязку чата, только если он привязан к старому ID
        if _bindings.get(chat_id) == old_session_id:
            _bindings[chat_id] = new_session_id
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


async def create_new_session(chat_id: int) -> NewSessionResult:
    """Создаёт новую сессию с временным ID и привязывает к чату."""
    async with _lock:
        temp_session_id = _generate_temp_session_id()

    # bind_session захватывает Lock самостоятельно
    day_number = await bind_session(chat_id, temp_session_id)
    logger.info(
        "Создана новая сессия %s (#%d) для чата %d",
        temp_session_id,
        day_number,
        chat_id,
    )
    return NewSessionResult(session_id=temp_session_id, day_number=day_number)


async def load_bindings() -> None:
    """Загружает привязки из sessions.json и дневной реестр при запуске бота."""
    global _bindings, _bindings_path

    _bindings_path = Path(config.WORKING_DIR) / BINDINGS_FILENAME

    # Загружаем дневной реестр
    await daily_session_registry.load_registry()

    # Читаем файл привязок
    try:
        content = await asyncio.to_thread(_bindings_path.read_text, "utf-8")
        raw_data = json.loads(content)
    except FileNotFoundError:
        _bindings = {}
        logger.info("Файл привязок не найден, начинаю с чистого состояния")
        return
    except json.JSONDecodeError:
        _bindings = {}
        logger.warning("Файл привязок повреждён, начинаю с чистого состояния")
        return

    # Конвертируем строковые ключи JSON в числовые chat_id
    _bindings = {}
    for raw_key, session_id in raw_data.items():
        try:
            chat_id = int(raw_key)
            _bindings[chat_id] = session_id
        except ValueError:
            logger.warning("Невалидный ключ chat_id в sessions.json: '%s' — пропущен", raw_key)

    logger.info("Загружено %d привязок из sessions.json", len(_bindings))


def get_all_bindings() -> dict[int, str]:
    """Возвращает копию всех привязок {chat_id: session_id}."""
    return dict(_bindings)


async def reset_state() -> None:
    """Сбрасывает привязки и путь к файлу, перезагружает данные из нового WORKING_DIR."""
    global _bindings, _bindings_path

    # Очищаем состояние под блокировкой, чтобы не конфликтовать с параллельными операциями.
    # load_bindings будет вызвана ниже без блокировки — она сама не использует _lock,
    # а save_bindings внутри неё не вызывается (load только читает).
    async with _lock:
        _bindings = {}
        # Сбрасываем кэшированный путь — иначе load_bindings продолжит читать старый файл
        _bindings_path = None

    # Повторно загружаем привязки — функция пересчитает _bindings_path из текущего config.WORKING_DIR
    await load_bindings()
    logger.info("Состояние session_manager сброшено и перезагружено")
