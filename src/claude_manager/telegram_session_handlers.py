"""Telegram handlers for session commands and all-project mode."""

import logging
from collections.abc import Callable

from telegram import Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    config,
    current_backend_registry,
    daily_session_registry,
    process_manager,
    recent_sessions_refresh,
    reply_anchor_registry,
    session_manager,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.recent_sessions_store import RecentSessionHeader
from claude_manager.session_manager import ActiveSession
from claude_manager.session_request_preview import clean_session_request_preview

logger = logging.getLogger(__name__)

SESSION_LIST_LIMIT = 15
ALL_PROJECTS_MODE_ENABLED_MESSAGE = (
    "Режим all включён: показываю сообщения из всех проектов.\n"
    "Писать агенту отсюда нельзя — сначала выберите проект и сессию."
)
ALL_PROJECTS_MODE_INPUT_WARNING = (
    "Вы в режиме all по всем проектам. Чтобы писать агенту, сначала войдите "
    "в проект и сессию: выберите проект через /projects или нажмите команду "
    "вида /1s2 в сообщении all."
)
CODEX_BOOTSTRAP_AGENTS_PREFIX = "# AGENTS.md instructions for "
CODEX_BOOTSTRAP_INSTRUCTIONS_MARKER = "<INSTRUCTIONS>"

_ApplicationGetter = Callable[[], Application | None]
_AccessChecker = Callable[[Update], bool]
_application_getter: _ApplicationGetter | None = None
_access_checker: _AccessChecker | None = None


def init_callbacks(
    application_getter: _ApplicationGetter,
    access_checker: _AccessChecker,
) -> None:
    """Inject bot-owned callbacks needed by session handlers."""
    global _application_getter, _access_checker
    _application_getter = application_getter
    _access_checker = access_checker


def _get_application() -> Application:
    if _application_getter is None:
        raise RuntimeError("telegram session handlers are not initialized")
    application = _application_getter()
    if application is None:
        raise RuntimeError("telegram application is not initialized")
    return application


def _has_access(update: Update) -> bool:
    if _access_checker is None:
        raise RuntimeError("telegram session access checker is not initialized")
    return _access_checker(update)


def _get_backend_display_name(backend: BackendName) -> str:
    """Возвращает человекочитаемое имя CLI-backend-а."""
    return coding_agent_backend.get_backend(backend).display_name


def _get_backend_list_marker(backend: BackendName) -> str:
    """Возвращает короткий маркер backend-а для компактного списка сессий."""
    display_name = _get_backend_display_name(backend)
    marker, separator, _tail = display_name.partition(" ")
    return marker if separator else ""


def _format_session_list_line(
    day_number: int,
    backend: BackendName,
    session_label: str,
) -> str:
    """Форматирует одну строку списка /sessions без имени backend-а."""
    backend_marker = _get_backend_list_marker(backend)
    prefix = f"/{day_number}"
    if backend_marker:
        prefix = f"{prefix} {backend_marker}"
    return f"{prefix} {session_label}".rstrip()


def _is_codex_bootstrap_request(text: str) -> bool:
    """Проверяет служебный AGENTS-запрос Codex, не являющийся темой сессии."""
    return (
        text.startswith(CODEX_BOOTSTRAP_AGENTS_PREFIX)
        and CODEX_BOOTSTRAP_INSTRUCTIONS_MARKER in text
    )


async def _resolve_session_list_label(
    row: RecentSessionHeader,
    session_summary: str,
) -> str:
    """Возвращает полный заголовок сессии для списка /sessions."""
    if session_summary or not row.preview.endswith("..."):
        return session_summary or row.preview

    try:
        backend = coding_agent_backend.get_backend(row.backend)
        messages = await backend.read_messages_from_session_file(row.file_path)
    except Exception:
        logger.debug(
            "Не удалось дочитать полный preview сессии %s (%s)",
            row.session_id,
            row.backend.value,
            exc_info=True,
        )
        return row.preview

    for message in messages:
        if message.role == "user" and message.text:
            if _is_codex_bootstrap_request(message.text):
                continue
            return clean_session_request_preview(message.text)
    return row.preview


async def handle_new(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /new — создаёт новую сессию Claude."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id

    if all_projects_monitor.is_enabled_for_chat(chat_id):
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            ALL_PROJECTS_MODE_INPUT_WARNING,
            parse_mode=None,
        )
        return

    try:
        backend = current_backend_registry.get_current()
        new_result = await session_manager.create_new_session(chat_id, backend)
        day_number = new_result.day_number
        display_name = _get_backend_display_name(new_result.backend)

        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            f"Создана новая сессия #{day_number} ({display_name})",
            parse_mode=None,
        )
    except Exception:
        logger.error("Ошибка создания сессии (chat_id=%d)", chat_id, exc_info=True)
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Не удалось создать сессию. Попробуйте ещё раз",
            parse_mode=None,
        )


async def handle_sessions(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /sessions — показывает список последних сессий."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    try:
        result = await recent_sessions_refresh.get_project_recent_sessions(
            config.WORKING_DIR,
            limit=SESSION_LIST_LIMIT,
            refresh_on_hit=True,
        )
    except Exception:
        logger.warning("Не удалось прочитать список сессий", exc_info=True)
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Не удалось прочитать список сессий. Попробуйте ещё раз",
            parse_mode=None,
        )
        return

    lines: list[str] = []
    for row in result.rows[:SESSION_LIST_LIMIT]:
        day_number = await daily_session_registry.register_session(
            row.session_id,
            row.backend,
        )
        session_summary = await daily_session_registry.get_session_summary(
            row.session_id,
            row.backend,
        )
        session_label = await _resolve_session_list_label(row, session_summary)
        lines.append(_format_session_list_line(day_number, row.backend, session_label))

    if not lines:
        lines.append("Нет сессий")
    lines.extend(result.degraded_messages)

    text = "\n".join(lines)
    # Отправляем без HTML, чтобы /1 /2 /3 были кликабельными командами
    await telegram_sender.send_telegram_message(
        _get_application().bot,
        chat_id,
        text,
        parse_mode=None,
    )


async def handle_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /stop — останавливает текущий процесс Claude."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    active_session = session_manager.get_active_session(chat_id)
    if active_session is None:
        legacy_session_id = session_manager.get_bound_session(chat_id)
        if legacy_session_id is not None:
            active_session = ActiveSession(legacy_session_id, BackendName.CLAUDE)

    if active_session is None:
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Команда /stop работает только внутри сессии. "
            "Подключитесь к сессии через /sessions",
            parse_mode=None,
        )
        return

    session_id = active_session.session_id
    backend = active_session.backend
    display_name = _get_backend_display_name(backend)

    # Процесс может отсутствовать в _processes (has_process=False),
    # но retry loop ещё активен (is_busy=True). Первый /stop удаляет
    # процесс из _processes, retry loop ещё не создал новый.
    # В этом случае /stop должен установить флаг отмены для retry loop.
    if (
        not process_manager.has_process(session_id, backend)
        and not process_manager.is_busy(session_id, backend)
    ):
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            f"{display_name} сейчас не работает, нечего останавливать",
            parse_mode=None,
        )
        return

    await process_manager.stop_process(session_id, backend)
    reply_anchor_registry.clear_anchor(config.WORKING_DIR, backend, session_id)
    await telegram_sender.send_telegram_message(
        _get_application().bot,
        chat_id,
        f"{display_name} остановлен",
        parse_mode=None,
    )


async def handle_all(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /all — включает глобальный мониторинг всех проектов."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    enable_result = await all_projects_monitor.enable_for_chat(chat_id)
    if enable_result.enabled:
        await session_manager.unbind_session(chat_id)
    await telegram_sender.send_telegram_message(
        _get_application().bot,
        chat_id,
        enable_result.message,
        parse_mode=None,
    )


async def handle_switch_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /N — переключает на сессию по номеру."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    day_number = int(update.message.text[1:])

    result = await session_manager.switch_to_session(chat_id, day_number)

    if not result.found:
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            f"Сессия #{day_number} не найдена",
            parse_mode=None,
        )
        return

    display_name = _get_backend_display_name(result.backend)
    preview_text = f": {result.preview}" if result.preview else ""
    await telegram_sender.send_telegram_message(
        _get_application().bot,
        chat_id,
        f"Подключён к сессии #{day_number} ({display_name}){preview_text}",
        parse_mode=None,
    )
