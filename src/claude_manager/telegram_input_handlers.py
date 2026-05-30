"""Telegram handlers for user text, photo, and document input."""

import logging
from collections.abc import Callable
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    claude_interaction,
    file_delivery,
    media_group_handler,
    session_manager,
    silence_mode_registry,
    telegram_file_downloader,
    telegram_sender,
)

logger = logging.getLogger(__name__)

ALL_PROJECTS_MODE_INPUT_WARNING = (
    "Вы в режиме all по всем проектам. Чтобы писать агенту, сначала войдите "
    "в проект и сессию: выберите проект через /projects или нажмите команду "
    "вида /1s2 в сообщении all."
)

_ApplicationGetter = Callable[[], Application | None]
_AccessChecker = Callable[[Update], bool]
_application_getter: _ApplicationGetter | None = None
_access_checker: _AccessChecker | None = None


def init_callbacks(
    application_getter: _ApplicationGetter,
    access_checker: _AccessChecker,
) -> None:
    """Inject bot-owned callbacks needed by input handlers."""
    global _application_getter, _access_checker
    _application_getter = application_getter
    _access_checker = access_checker


def _get_application() -> Application:
    if _application_getter is None:
        raise RuntimeError("telegram input handlers are not initialized")
    application = _application_getter()
    if application is None:
        raise RuntimeError("telegram application is not initialized")
    return application


def _has_access(update: Update) -> bool:
    if _access_checker is None:
        raise RuntimeError("telegram input access checker is not initialized")
    return _access_checker(update)


def _monitoring_mode_message_for_chat(chat_id: int) -> str:
    """Return the right warning for local monitoring or global all mode."""
    if all_projects_monitor.is_enabled_for_chat(chat_id):
        return ALL_PROJECTS_MODE_INPUT_WARNING
    return claude_interaction.MONITORING_MODE_MESSAGE


def _reply_anchor_kwargs(update: Update) -> dict[str, int]:
    """Return send kwargs for a real Telegram message_id anchor candidate."""
    message_id = update.message.message_id
    if not isinstance(message_id, int):
        return {}
    return {"reply_to_message_id": message_id}


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик текстовых сообщений — отправляет текст в Claude."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    text = update.message.text

    # Перехват текстовых команд silence mode — ДО отправки в Claude
    normalized_text = text.strip().lower()
    if normalized_text == "silence on":
        silence_mode_registry.enable()
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Silence mode включён — буду присылать только финальные ответы",
            parse_mode=None,
        )
        return
    if normalized_text == "silence off":
        silence_mode_registry.disable()
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Silence mode выключен — промежуточные сообщения снова доставляются",
            parse_mode=None,
        )
        return

    if session_manager.is_monitoring_mode(chat_id):
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            _monitoring_mode_message_for_chat(chat_id),
            parse_mode=None,
        )
        return

    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception as exc:
        logger.warning("send_chat_action не удался в handle_message: %s", exc)
    await claude_interaction.send_to_claude_and_respond(
        chat_id,
        text,
        **_reply_anchor_kwargs(update),
    )


async def _handle_single_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обрабатывает одиночное фото вне альбома."""
    chat_id = update.effective_chat.id

    # Guard: Claude занят — отвечаем сразу, не тратим HTTP-пул на скачивание.
    # Проверка — fast-path оптимизация, атомарная защита живёт в
    # process_manager.send_message под _busy_lock
    busy_message = claude_interaction.build_busy_message_if_busy(chat_id)
    if busy_message is not None:
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            busy_message,
            parse_mode=None,
        )
        return

    try:
        file_path = await telegram_file_downloader.download_and_save_file(
            update,
            _get_application().bot,
        )
    except Exception:
        logger.error("Ошибка скачивания фото (chat_id=%d)", chat_id, exc_info=True)
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Не удалось скачать файл. Попробуйте отправить ещё раз",
            parse_mode=None,
        )
        return

    caption = update.message.caption
    task_text = claude_interaction.build_file_task(file_path, caption, is_image=True)

    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception as exc:
        logger.warning("send_chat_action не удался в _handle_single_photo: %s", exc)
    await claude_interaction.send_to_claude_and_respond(
        chat_id,
        task_text,
        **_reply_anchor_kwargs(update),
    )


async def handle_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик фотографий — роутит одиночное фото или медиа-группу."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id

    if session_manager.is_monitoring_mode(chat_id):
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            _monitoring_mode_message_for_chat(chat_id),
            parse_mode=None,
        )
        return

    media_group_id = update.message.media_group_id
    if media_group_id is not None:
        await media_group_handler.media_group_aggregator.add_update(
            media_group_id,
            update,
            media_group_handler.finalize_photo_group,
        )
        return

    await _handle_single_photo(update, context)


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик документов — скачивает файл и формирует задание для Claude."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id

    if session_manager.is_monitoring_mode(chat_id):
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            _monitoring_mode_message_for_chat(chat_id),
            parse_mode=None,
        )
        return

    busy_message = claude_interaction.build_busy_message_if_busy(chat_id)
    if busy_message is not None:
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            busy_message,
            parse_mode=None,
        )
        return

    try:
        file_path = await telegram_file_downloader.download_and_save_file(
            update,
            _get_application().bot,
        )
    except Exception:
        logger.error(
            "Ошибка скачивания документа (chat_id=%d)", chat_id, exc_info=True
        )
        await telegram_sender.send_telegram_message(
            _get_application().bot,
            chat_id,
            "Не удалось скачать файл. Попробуйте отправить ещё раз",
            parse_mode=None,
        )
        return

    caption = update.message.caption
    extension = Path(file_path).suffix.lstrip(".").lower()
    is_image = extension in file_delivery.IMAGE_EXTENSIONS
    task_text = claude_interaction.build_file_task(file_path, caption, is_image)

    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception as exc:
        logger.warning("send_chat_action не удался в handle_document: %s", exc)
    await claude_interaction.send_to_claude_and_respond(
        chat_id,
        task_text,
        **_reply_anchor_kwargs(update),
    )
