"""In-memory state for managed Claude/Codex CLI processes."""

import asyncio
import logging

from claude_manager.coding_agent_backend import BackendName
from claude_manager.claude_runner import BackendSubprocess, ClaudeProcess

# Сохраняем прежнее имя logger после механического выноса state helpers.
logger = logging.getLogger("claude_manager.process_manager")

type ProcessKey = str | tuple[str, BackendName]
type ManagedProcess = ClaudeProcess | BackendSubprocess

# Запущенные процессы: session_id -> ClaudeProcess
_processes: dict[ProcessKey, ManagedProcess] = {}

# Флаги занятости: session_id -> True/False
_busy_flags: dict[ProcessKey, bool] = {}

# Блокировка для атомарных операций над _busy_flags, _processes, _stop_events.
# Захватывается только на короткие критические секции — не на всё время обработки.
_busy_lock: asyncio.Lock = asyncio.Lock()

# События отмены для прерывания ретраев через /stop
_stop_events: dict[ProcessKey, asyncio.Event] = {}

# Алиасы устаревших session_id после temp -> real ремаппинга.
_session_id_aliases: dict[ProcessKey, ProcessKey] = {}


def _make_process_key(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> ProcessKey:
    """Возвращает ключ процесса с совместимостью для старого Claude-only API."""
    if backend == BackendName.CLAUDE:
        return session_id
    return (session_id, backend)


def _make_backend_process_key(session_id: str, backend: BackendName) -> ProcessKey:
    """Возвращает backend-aware ключ без Claude-only compatibility shortcut."""
    return (session_id, backend)


def _split_process_key(key: ProcessKey) -> tuple[str, BackendName]:
    """Возвращает session_id/backend из внутреннего ключа."""
    if isinstance(key, tuple):
        return key
    return key, BackendName.CLAUDE


def _find_registered_alias_key(process_key: ProcessKey) -> ProcessKey | None:
    """Находит ключ в _session_id_aliases с учётом string/tuple-форм CLAUDE."""
    if process_key in _session_id_aliases:
        return process_key
    session_id, backend = _split_process_key(process_key)
    if backend != BackendName.CLAUDE:
        return None
    # CLAUDE-состояние живёт под голой строкой ИЛИ под tuple (sid, CLAUDE) —
    # это одна сессия. Алиас backend-aware ремапа записан под tuple, а вызов
    # мог прийти с голой строкой (и наоборот): пробуем альтернативную форму.
    alternate_key: ProcessKey = (
        (session_id, BackendName.CLAUDE) if isinstance(process_key, str) else session_id
    )
    if alternate_key in _session_id_aliases:
        return alternate_key
    return None


def _resolve_process_key_alias_unlocked(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> ProcessKey:
    """Возвращает актуальный process key по цепочке алиасов."""
    resolved_key = _make_process_key(session_id, backend)
    seen: set[ProcessKey] = set()

    while resolved_key not in seen:
        seen.add(resolved_key)
        alias_source_key = _find_registered_alias_key(resolved_key)
        if alias_source_key is None:
            return resolved_key
        resolved_key = _session_id_aliases[alias_source_key]

    logger.error("Обнаружен цикл алиасов process key: %s", resolved_key)
    return resolved_key


def _prefer_existing_process_key_unlocked(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> ProcessKey:
    """Возвращает существующий ключ, поддерживая tuple-ключи для Claude."""
    process_key = _resolve_process_key_alias_unlocked(session_id, backend)
    if (
        backend == BackendName.CLAUDE
        and process_key not in _processes
        and (session_id, BackendName.CLAUDE) in _processes
    ):
        return (session_id, BackendName.CLAUDE)
    return process_key


def _resolve_session_id_alias_unlocked(session_id: str) -> str:
    """Возвращает актуальный session_id по цепочке алиасов."""
    resolved_key = _resolve_process_key_alias_unlocked(session_id)
    resolved_session_id, _backend = _split_process_key(resolved_key)
    return resolved_session_id


def _remove_session_id_aliases_unlocked(session_ids: set[str]) -> None:
    """Удаляет алиасы, связанные с указанными session_id."""
    if not session_ids:
        return

    aliases_to_remove = [
        alias for alias, target in _session_id_aliases.items()
        if (
            _split_process_key(alias)[0] in session_ids
            or _split_process_key(target)[0] in session_ids
        )
    ]
    for alias in aliases_to_remove:
        _session_id_aliases.pop(alias, None)


def is_busy(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> bool:
    """Проверяет, обрабатывает ли процесс запрос прямо сейчас."""
    process_key = _prefer_existing_process_key_unlocked(session_id, backend)
    return _busy_flags.get(process_key, False)


def has_process(
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> bool:
    """Проверяет, есть ли запущенный процесс для указанной сессии."""
    process_key = _prefer_existing_process_key_unlocked(session_id, backend)
    claude_process = _processes.get(process_key)
    if claude_process is None:
        return False
    return claude_process.is_running()


async def update_session_id(
    old_session_id: str,
    new_session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> None:
    """Обновляет ключ сессии во всех внутренних словарях."""
    # Атомарный перенос: все три словаря обновляются за один захват Lock.
    # Без Lock: другая корутина может прочитать словарь между pop и присвоением
    # нового ключа — и увидеть промежуточное состояние (ключ удалён, но новый не создан).
    async with _busy_lock:
        old_key = _prefer_existing_process_key_unlocked(old_session_id, backend)
        old_session_id, resolved_backend = _split_process_key(old_key)
        # Сохраняем форму ключа: если состояние лежит под tuple-ключом
        # (backend-aware путь), новый ключ тоже обязан быть tuple. Иначе
        # _make_process_key схлопнет его в голую строку для CLAUDE,
        # состояние переедет под строковый ключ, а finally backend-aware
        # пути чистит только tuple-ключи — выставленный stop_event останется
        # висеть и убьёт следующий turn (сессия «не оживает» после /stop).
        if isinstance(old_key, tuple):
            new_key = _make_backend_process_key(new_session_id, resolved_backend)
        else:
            new_key = _make_process_key(new_session_id, resolved_backend)
        new_key = _session_id_aliases.get(new_key, new_key)
        new_session_id, _new_backend = _split_process_key(new_key)
        if old_key == new_key:
            return

        for storage in (_processes, _busy_flags, _stop_events):
            if old_key in storage:
                storage[new_key] = storage.pop(old_key)
        _session_id_aliases[old_key] = new_key
        for alias, target in list(_session_id_aliases.items()):
            if target == old_key:
                _session_id_aliases[alias] = new_key

    logger.info(
        "Session ID обновлён: %s -> %s", old_session_id, new_session_id,
    )
