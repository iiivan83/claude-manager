"""Incoming Telegram reply-route handling."""

import logging
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    config,
    process_manager,
    project_manager,
    reply_route_registry,
    telegram_sender,
)
from claude_manager.coding_agent_backend import PermanentErrorKind

logger = logging.getLogger(__name__)

UNKNOWN_ROUTE_MESSAGE = (
    "Не понял, куда передать ответ. "
    "Нажми ссылку на нужную сессию и отправь сообщение там"
)
UNSUPPORTED_ATTACHMENT_REASON = (
    "ответы с вложениями пока не работают, этот функционал ещё не сделали"
)


def _normalize_path(path: str) -> str:
    """Return a comparable absolute project path."""
    return str(Path(path).expanduser().resolve())


def _reply_to_message_id(update: Update) -> int | None:
    """Return message_id of the bot message being replied to."""
    reply_to_message = getattr(update.message, "reply_to_message", None)
    if reply_to_message is None:
        return None
    message_id = getattr(reply_to_message, "message_id", None)
    if isinstance(message_id, int):
        return message_id
    return None


def _route_from_update(
    update: Update,
) -> reply_route_registry.ReplyRouteTarget | None:
    """Resolve incoming Telegram reply to a saved route target."""
    message_id = _reply_to_message_id(update)
    if message_id is None:
        return None
    return reply_route_registry.get_route(update.effective_chat.id, message_id)


async def _full_link(
    target: reply_route_registry.ReplyRouteTarget,
) -> str | None:
    """Return /PsS for a target if project number can be resolved."""
    if target.project_number is not None:
        return f"/{target.project_number}s{target.session_number}"

    target_path = _normalize_path(target.project_path)
    try:
        projects = await project_manager.scan_available_projects()
    except Exception:
        logger.warning("Не удалось просканировать проекты для reply-route", exc_info=True)
        return None

    for project_number, project in enumerate(projects, start=1):
        if _normalize_path(project.absolute_path) == target_path:
            return f"/{project_number}s{target.session_number}"
    return None


async def _route_link(
    chat_id: int,
    target: reply_route_registry.ReplyRouteTarget,
) -> str:
    """Build the short link that should be shown to Ivan."""
    if all_projects_monitor.is_enabled_for_chat(chat_id):
        return await _full_link(target) or f"/{target.session_number}"

    if _normalize_path(config.WORKING_DIR) == _normalize_path(target.project_path):
        return f"/{target.session_number}"

    return await _full_link(target) or f"/{target.session_number}"


async def _target_project_is_available(
    target: reply_route_registry.ReplyRouteTarget,
) -> bool:
    """Return whether the target project is still visible to project manager."""
    target_path = _normalize_path(target.project_path)
    try:
        projects = await project_manager.scan_available_projects()
    except Exception:
        logger.warning("Не удалось проверить доступность проекта", exc_info=True)
        return False
    return any(_normalize_path(project.absolute_path) == target_path for project in projects)


async def _target_session_is_available(
    target: reply_route_registry.ReplyRouteTarget,
) -> bool:
    """Return whether backend can still see the target session in that project."""
    backend = coding_agent_backend.get_backend(target.backend)
    try:
        return await backend.session_file_exists_for_project(
            target.session_id,
            target.project_path,
        )
    except Exception:
        logger.warning(
            "Не удалось проверить сессию reply-route: %s %s",
            target.backend.value,
            target.session_id,
            exc_info=True,
        )
        return False


async def _send_plain(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """Send a plain Telegram message."""
    await telegram_sender.send_telegram_message(
        context.bot,
        chat_id,
        text,
        parse_mode=None,
    )


async def _send_route_error(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    link: str,
    reason: str,
) -> None:
    """Send a route error in the required short format."""
    await _send_plain(context, chat_id, f"Не передал в {link}: {reason}")


def _send_result_error_reason(result: process_manager.SendResult) -> str:
    """Return a user-visible reason for a failed SendResult."""
    if result.permanent_error_kind == PermanentErrorKind.CONTEXT_OVERFLOW:
        return "сессия переполнена. Начни новую через /new"
    if result.permanent_error_kind == PermanentErrorKind.USAGE_LIMIT:
        return "лимит исчерпан, повтор сейчас не поможет"
    if result.permanent_error_kind is not None:
        return "запрос нельзя повторить"
    return result.error_text or result.text or "сессия недоступна"


async def try_handle_text_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Handle a text reply to a routed bot message when possible."""
    chat_id = update.effective_chat.id
    reply_message_id = _reply_to_message_id(update)
    if reply_message_id is None:
        return False

    target = _route_from_update(update)
    if target is None:
        if all_projects_monitor.is_enabled_for_chat(chat_id):
            await _send_plain(context, chat_id, UNKNOWN_ROUTE_MESSAGE)
            return True
        return False

    link = await _route_link(chat_id, target)
    if process_manager.is_busy(target.session_id, target.backend):
        await _send_route_error(
            context,
            chat_id,
            link,
            "сессия занята. Подождите или /stop",
        )
        return True

    if not await _target_project_is_available(target):
        await _send_route_error(context, chat_id, link, "проект недоступен")
        return True

    if not await _target_session_is_available(target):
        await _send_route_error(context, chat_id, link, "сессия недоступна")
        return True

    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception as exc:
        logger.warning("send_chat_action не удался в reply-route: %s", exc)

    try:
        result = await process_manager.send_message(
            target.session_id,
            update.message.text,
            backend=target.backend,
            cwd=target.project_path,
        )
    except process_manager.ProcessManagerError as error:
        logger.warning("Reply-route send failed before acceptance: %s", error)
        error_text = str(error).lower()
        is_busy_error = (
            process_manager.is_busy(target.session_id, target.backend)
            or "busy" in error_text
            or "занят" in error_text
        )
        reason = (
            "сессия занята. Подождите или /stop"
            if is_busy_error
            else "не удалось запустить агент"
        )
        await _send_route_error(context, chat_id, link, reason)
        return True
    except Exception:
        logger.error("Reply-route send failed", exc_info=True)
        await _send_route_error(context, chat_id, link, "не удалось передать")
        return True

    if result.is_error:
        await _send_route_error(
            context,
            chat_id,
            link,
            _send_result_error_reason(result),
        )
        return True

    await _send_plain(context, chat_id, f"Передал в {link}")
    return True


async def try_handle_unsupported_attachment_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Reject photo/document/album replies to routed bot messages in v1."""
    chat_id = update.effective_chat.id
    reply_message_id = _reply_to_message_id(update)
    if reply_message_id is None:
        return False

    target = _route_from_update(update)
    if target is None:
        if all_projects_monitor.is_enabled_for_chat(chat_id):
            await _send_plain(context, chat_id, UNKNOWN_ROUTE_MESSAGE)
            return True
        return False

    link = await _route_link(chat_id, target)
    await _send_route_error(context, chat_id, link, UNSUPPORTED_ATTACHMENT_REASON)
    return True
