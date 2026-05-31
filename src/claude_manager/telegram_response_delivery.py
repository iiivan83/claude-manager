"""Доставка ответов Claude в Telegram."""

from telegram.ext import Application

from claude_manager import (
    claude_interaction,
    coding_agent_backend,
    config,
    file_delivery,
    message_splitter,
    reply_anchor_registry,
    reply_route_registry,
    session_manager,
    silence_mode_registry,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName


_application: Application | None = None


def init_application(application: Application) -> None:
    """Сохраняет Telegram Application для доставки сообщений."""
    global _application
    _application = application


async def _send_telegram_message_bridge(
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    """Пробрасывает bot из Application в callback claude_interaction."""
    await telegram_sender.send_telegram_message(
        _application.bot, chat_id, text,
        parse_mode=parse_mode, reply_markup=reply_markup,
    )


def _get_backend_display_name(backend: BackendName) -> str:
    """Возвращает человекочитаемое имя CLI-backend-а."""
    return coding_agent_backend.get_backend(backend).display_name


def _format_session_header(
    session_number: int,
    is_final: bool,
    backend: BackendName = BackendName.CLAUDE,
) -> str:
    """Формирует заголовок ответа с номером сессии и статусом."""
    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = _get_backend_display_name(backend)
    return f"#{session_number} {backend_label} {status_icon} "


def _format_clickable_session_number(session_number: int) -> str:
    """Форматирует номер сессии как кликабельную команду для Telegram."""
    return f"<b>/{session_number}</b>"


def _format_clickable_session_header(
    session_number: int,
    backend: BackendName,
    is_final: bool,
) -> str:
    """Формирует кликабельный заголовок watcher-сообщения."""
    clickable = _format_clickable_session_number(session_number)
    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = _get_backend_display_name(backend)
    return f"{clickable} {backend_label} {status_icon} "


def _is_current_session(
    chat_id: int,
    session_id: str,
    backend: BackendName = BackendName.CLAUDE,
) -> bool:
    """Проверяет, является ли сессия текущей активной для данного чата."""
    active_session = session_manager.get_active_session(chat_id)
    if active_session is None:
        if backend == BackendName.CLAUDE:
            return session_manager.get_bound_session(chat_id) == session_id
        return False
    return (
        active_session.session_id == session_id
        and active_session.backend == backend
    )


def _normalize_response_arguments(
    backend: BackendName | bool,
    is_final: bool | None,
) -> tuple[BackendName, bool]:
    """Сохраняет совместимость со старым позиционным вызовом is_final."""
    if isinstance(backend, bool) and is_final is None:
        return BackendName.CLAUDE, backend
    if is_final is None:
        raise TypeError("is_final is required")
    return backend, is_final


def _normalize_watcher_arguments(
    backend: BackendName | int,
    session_number: int | None,
    is_final: bool | None,
) -> tuple[BackendName, int, bool]:
    """Сохраняет совместимость со старым позиционным session_number."""
    if isinstance(backend, int):
        session_number = backend
        backend = BackendName.CLAUDE
    if session_number is None or is_final is None:
        raise TypeError("session_number and is_final are required")
    return backend, session_number, is_final


def _replace_empty_response(text: str) -> str:
    """Заменяет пустой ответ Claude на понятный текст."""
    if not text or text == claude_interaction.NO_RESPONSE_MARKER:
        return claude_interaction.EMPTY_RESPONSE_TEXT
    return text


async def _process_final_file_markers(
    chat_id: int,
    text: str,
    is_final: bool,
) -> str:
    """Обрабатывает файловые маркеры только для финальных сообщений."""
    if not is_final:
        return text
    text = await file_delivery.process_file_markers(_application.bot, chat_id, text)
    return await file_delivery.process_show_file_markers(
        _application.bot, chat_id, text,
    )


def _prepare_parts(text: str, is_final: bool) -> list[str]:
    """Разбивает сообщение и оформляет промежуточные части курсивом."""
    parts = message_splitter.prepare_message(text)
    if is_final:
        return parts
    return [f"<i>{part}</i>" for part in parts]


def _message_id_from_sent_message(sent_message: object) -> int | None:
    """Return Telegram message_id from a send result if it is available."""
    message_id = getattr(sent_message, "message_id", None)
    if isinstance(message_id, int):
        return message_id
    return None


def _build_route_target(
    *,
    project_path: str | None,
    session_id: str | None,
    backend: BackendName,
    session_number: int,
    project_number: int | None = None,
    project_name: str | None = None,
) -> reply_route_registry.ReplyRouteTarget | None:
    """Build a route target only when the source session is known."""
    if project_path is None or session_id is None:
        return None
    return reply_route_registry.ReplyRouteTarget(
        project_path=project_path,
        session_id=session_id,
        backend=backend,
        session_number=session_number,
        project_number=project_number,
        project_name=project_name,
    )


def _register_sent_route(
    chat_id: int,
    sent_message: object,
    route_target: reply_route_registry.ReplyRouteTarget | None,
) -> None:
    """Register one sent Telegram message as a reply-route source."""
    if route_target is None:
        return
    message_id = _message_id_from_sent_message(sent_message)
    if message_id is None:
        return
    reply_route_registry.register_route(chat_id, message_id, route_target)


async def _send_parts(
    chat_id: int,
    parts: list[str],
    *,
    reply_markup=None,
    attach_reply_markup: bool = False,
    reply_to_message_id: int | None = None,
    route_target: reply_route_registry.ReplyRouteTarget | None = None,
) -> None:
    """Отправляет части сообщения в Telegram."""
    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        part_reply_to_message_id = reply_to_message_id if index == 0 else None
        kwargs = {}
        if part_reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = part_reply_to_message_id
        if attach_reply_markup:
            markup = reply_markup if index == last_index else None
            sent_message = await telegram_sender.send_telegram_message(
                _application.bot,
                chat_id,
                part,
                reply_markup=markup,
                **kwargs,
            )
        else:
            sent_message = await telegram_sender.send_telegram_message(
                _application.bot,
                chat_id,
                part,
                **kwargs,
            )
        _register_sent_route(chat_id, sent_message, route_target)


def _build_watcher_header(
    chat_id: int,
    session_id: str,
    backend: BackendName,
    session_number: int,
    is_final: bool,
) -> str:
    """Выбирает обычный или кликабельный заголовок watcher-сообщения."""
    if _is_current_session(chat_id, session_id, backend):
        return _format_session_header(session_number, is_final, backend)
    return _format_clickable_session_header(session_number, backend, is_final)


def _build_all_projects_header(
    project_number: int,
    session_number: int,
    project_name: str,
    backend: BackendName,
    is_final: bool,
) -> str:
    """Формирует заголовок сообщения из all-project watcher."""
    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = _get_backend_display_name(backend)
    return (
        f"/{project_number}s{session_number} "
        f"{project_name} {backend_label} {status_icon} "
    )


async def send_response(
    chat_id: int,
    text: str,
    session_number: int,
    backend: BackendName | bool = BackendName.CLAUDE,
    is_final: bool | None = None,
    reply_markup=None,
    reply_to_message_id: int | None = None,
    session_id: str | None = None,
) -> None:
    """Форматирует и отправляет ответ Claude в Telegram."""
    backend, is_final = _normalize_response_arguments(backend, is_final)
    project_path = config.WORKING_DIR
    text = _replace_empty_response(text)
    if not is_final and silence_mode_registry.is_enabled():
        return
    text = await _process_final_file_markers(chat_id, text, is_final)
    parts = _prepare_parts(text, is_final)
    header = _format_session_header(session_number, is_final, backend)
    if session_id is not None and not _is_current_session(
        chat_id, session_id, backend,
    ):
        header = _format_clickable_session_header(
            session_number, backend, is_final,
        )
    parts[0] = header + parts[0]
    route_target = _build_route_target(
        project_path=project_path,
        session_id=session_id,
        backend=backend,
        session_number=session_number,
    )
    await _send_parts(
        chat_id,
        parts,
        reply_markup=reply_markup,
        attach_reply_markup=True,
        reply_to_message_id=reply_to_message_id,
        route_target=route_target,
    )


async def send_watcher_message(
    chat_id: int, text: str, session_id: str,
    backend: BackendName | int = BackendName.CLAUDE,
    session_number: int | None = None, is_final: bool | None = None,
) -> None:
    """Отправляет сообщение от watcher (ответ из другой сессии)."""
    backend, session_number, is_final = _normalize_watcher_arguments(
        backend, session_number, is_final,
    )
    project_path = config.WORKING_DIR
    if not is_final and silence_mode_registry.is_enabled():
        return
    text = await _process_final_file_markers(chat_id, text, is_final)
    parts = _prepare_parts(text, is_final)
    header = _build_watcher_header(
        chat_id, session_id, backend, session_number, is_final,
    )
    parts[0] = header + parts[0]
    reply_to_message_id = reply_anchor_registry.get_anchor(
        project_path,
        backend,
        session_id,
    )
    route_target = _build_route_target(
        project_path=project_path,
        session_id=session_id,
        backend=backend,
        session_number=session_number,
    )
    await _send_parts(
        chat_id,
        parts,
        reply_to_message_id=reply_to_message_id,
        route_target=route_target,
    )


async def send_all_projects_watcher_message(
    chat_id: int,
    *,
    project_number: int, session_number: int,
    project_name: str, session_id: str,
    backend: BackendName,
    text: str,
    is_final: bool,
    project_path: str | None = None,
) -> None:
    """Send a watcher message from global all-project mode."""
    if not is_final and silence_mode_registry.is_enabled():
        return
    text = await _process_final_file_markers(chat_id, text, is_final)
    parts = _prepare_parts(text, is_final)
    parts[0] = _build_all_projects_header(
        project_number, session_number, project_name, backend, is_final,
    ) + parts[0]
    reply_to_message_id = None
    if project_path is not None:
        reply_to_message_id = reply_anchor_registry.get_anchor(
            project_path,
            backend,
            session_id,
        )
    route_target = _build_route_target(
        project_path=project_path,
        session_id=session_id,
        backend=backend,
        session_number=session_number,
        project_number=project_number,
        project_name=project_name,
    )
    await _send_parts(
        chat_id,
        parts,
        reply_to_message_id=reply_to_message_id,
        route_target=route_target,
    )
