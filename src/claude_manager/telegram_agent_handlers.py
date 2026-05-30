"""Telegram handlers for CLI-agent backend selection."""

import logging
from collections.abc import Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    coding_agent_backend,
    current_backend_registry,
    daily_session_registry,
    session_manager,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName

logger = logging.getLogger(__name__)

_ApplicationGetter = Callable[[], Application | None]
_AccessChecker = Callable[[Update], bool]
_application_getter: _ApplicationGetter | None = None
_access_checker: _AccessChecker | None = None


def init_callbacks(
    application_getter: _ApplicationGetter,
    access_checker: _AccessChecker,
) -> None:
    """Inject bot-owned callbacks needed by agent handlers."""
    global _application_getter, _access_checker
    _application_getter = application_getter
    _access_checker = access_checker


def _get_application() -> Application:
    if _application_getter is None:
        raise RuntimeError("telegram agent handlers are not initialized")
    application = _application_getter()
    if application is None:
        raise RuntimeError("telegram application is not initialized")
    return application


def _has_access(update: Update) -> bool:
    if _access_checker is None:
        raise RuntimeError("telegram agent access checker is not initialized")
    return _access_checker(update)


def _get_backend_display_name(backend: BackendName) -> str:
    """Возвращает человекочитаемое имя CLI-backend-а."""
    return coding_agent_backend.get_backend(backend).display_name


def _get_backend_plain_name(backend: BackendName) -> str:
    """Возвращает имя backend-а без emoji для середины фразы."""
    display_name = _get_backend_display_name(backend)
    return display_name.split(maxsplit=1)[-1]


def _build_agent_keyboard(current_backend: BackendName) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру выбора CLI-agent backend-а."""
    keyboard = []
    for backend in coding_agent_backend.get_all_backends():
        label = backend.display_name
        if backend.name == current_backend:
            label = f"✓ {label}"
        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=f"agent:{backend.name.value}",
            )
        ])
    return InlineKeyboardMarkup(keyboard)


def _parse_agent_callback_data(raw_data: object) -> BackendName | None:
    """Читает backend из callback data вида agent:<backend>."""
    if not isinstance(raw_data, str):
        return None
    prefix, separator, raw_backend = raw_data.partition(":")
    if prefix != "agent" or separator != ":":
        return None
    try:
        return BackendName(raw_backend)
    except ValueError:
        return None


async def _build_agent_switch_confirmation(
    chat_id: int,
    target_backend: BackendName,
) -> str:
    """Формирует подтверждение переключения backend-а для новых сессий."""
    display_name = _get_backend_display_name(target_backend)
    lines = [
        f"Теперь новые сессии будут создаваться через {display_name}.",
    ]

    active_session = session_manager.get_active_session(chat_id)
    if active_session is not None:
        active_session_number = await daily_session_registry.register_session(
            active_session.session_id,
            active_session.backend,
        )
        active_display_name = _get_backend_display_name(active_session.backend)
        lines.append(
            f"Текущая сессия #{active_session_number} остаётся на "
            f"{active_display_name}."
        )
        plain_backend_name = _get_backend_plain_name(target_backend)
        lines.append(
            f"Чтобы начать новую {plain_backend_name}-сессию, отправьте /new."
        )
    else:
        lines.append("Чтобы начать новую сессию, отправьте /new.")

    return "\n".join(lines)


async def handle_agent(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик команды /agent — показывает выбор CLI-агента."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    current_backend = current_backend_registry.get_current()
    display_name = _get_backend_display_name(current_backend)
    reply_markup = _build_agent_keyboard(current_backend)

    await telegram_sender.send_telegram_message(
        _get_application().bot,
        chat_id,
        f"Текущий агент: {display_name}",
        parse_mode=None,
        reply_markup=reply_markup,
    )


async def handle_agent_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик inline-кнопок /agent."""
    if not _has_access(update):
        return

    query = update.callback_query
    target_backend = _parse_agent_callback_data(query.data)
    if target_backend is None:
        logger.warning("Неизвестный callback выбора агента: %r", query.data)
        await query.answer("Неизвестный агент", show_alert=True)
        return

    await query.answer()
    current_backend = current_backend_registry.get_current()
    target_display_name = _get_backend_display_name(target_backend)

    if target_backend == current_backend:
        await query.edit_message_text(
            text=f"Уже выбран: {target_display_name}.",
            parse_mode=None,
        )
        return

    try:
        current_backend_registry.set_current(target_backend)
    except (RuntimeError, OSError) as error:
        logger.error("Не удалось переключить агента", exc_info=True)
        await query.edit_message_text(
            text=f"Не удалось переключить агента: {error}",
            parse_mode=None,
        )
        return

    chat_id = update.effective_chat.id
    confirmation = await _build_agent_switch_confirmation(
        chat_id,
        target_backend,
    )
    await query.edit_message_text(text=confirmation, parse_mode=None)
