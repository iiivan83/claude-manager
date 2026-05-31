"""Facade Telegram-бота: сборка Application и регистрация handlers."""

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claude_manager import (
    claude_interaction,
    config,
    media_group_handler,
    telegram_agent_handlers,
    telegram_input_handlers,
    telegram_lifecycle_handlers,
    telegram_project_handlers,
    telegram_session_handlers,
    telegram_response_delivery,
    telegram_sender,
)

logger = logging.getLogger(__name__)

# --- Константы ---

# Таймаут установления TCP+TLS-соединения с Telegram API (секунды).
# Дефолт httpx 5 с — мало при дрожащей сети и параллельных запросах альбомов.
HTTP_CONNECT_TIMEOUT_SECONDS = 30

# Таймаут чтения ответа от Telegram API (секунды).
# Критичен для getFile на крупных фото — Telegram может отдавать медленно.
HTTP_READ_TIMEOUT_SECONDS = 30

# Таймаут отправки тела запроса в Telegram API (секунды).
HTTP_WRITE_TIMEOUT_SECONDS = 30

# Размер пула HTTP-соединений к Telegram API.
# 32 хватает и на альбом из 10 фото с параллельными retry,
# оставляя запас на send_message и send_chat_action.
HTTP_CONNECTION_POOL_SIZE = 32

ALL_PROJECTS_MODE_INPUT_WARNING = telegram_input_handlers.ALL_PROJECTS_MODE_INPUT_WARNING

# --- Внутреннее состояние ---

# Ссылка на Application для доступа к bot из функций без context
_application: Application | None = None


# --- Вспомогательные функции ---


def _check_access(update: Update) -> bool:
    """Проверяет, есть ли отправитель в белом списке разрешённых пользователей."""
    user_id = update.effective_user.id
    if user_id in config.ALLOWED_USER_IDS:
        return True
    if config.E2E_TEST_USER_ID is not None and user_id == config.E2E_TEST_USER_ID:
        return True
    logger.warning("Неавторизованный доступ: user_id=%d", user_id)
    return False


def _get_application_for_handlers() -> Application | None:
    return _application


def _has_access_for_handlers(update: Update) -> bool:
    return _check_access(update)


def _init_handler_callbacks() -> None:
    telegram_agent_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )
    telegram_session_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )
    telegram_input_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )
    telegram_lifecycle_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )
    telegram_project_handlers.init_callbacks(
        _get_application_for_handlers,
        _has_access_for_handlers,
    )


# --- Публичные функции ---


ALL_PROJECTS_MODE_LINE = telegram_project_handlers.ALL_PROJECTS_MODE_LINE
EMPTY_PROJECTS_TEMPLATE = telegram_project_handlers.EMPTY_PROJECTS_TEMPLATE
INVALID_PROJECT_NUMBER_TEMPLATE = (
    telegram_project_handlers.INVALID_PROJECT_NUMBER_TEMPLATE
)
PROJECT_ALREADY_ACTIVE_TEMPLATE = (
    telegram_project_handlers.PROJECT_ALREADY_ACTIVE_TEMPLATE
)
PROJECT_CURRENT_MARKER = telegram_project_handlers.PROJECT_CURRENT_MARKER
PROJECT_SWITCH_ERROR_TEMPLATE = telegram_project_handlers.PROJECT_SWITCH_ERROR_TEMPLATE
PROJECT_SWITCH_PENDING_TEMPLATE = (
    telegram_project_handlers.PROJECT_SWITCH_PENDING_TEMPLATE
)
PROJECT_SWITCH_SUCCESS_TEMPLATE = (
    telegram_project_handlers.PROJECT_SWITCH_SUCCESS_TEMPLATE
)
PROJECT_SESSION_COMMAND_PATTERN = (
    telegram_project_handlers.PROJECT_SESSION_COMMAND_PATTERN
)
handle_projects = telegram_project_handlers.handle_projects
handle_switch_project = telegram_project_handlers.handle_switch_project
handle_switch_project_session = (
    telegram_project_handlers.handle_switch_project_session
)
handle_agent = telegram_agent_handlers.handle_agent
handle_agent_callback = telegram_agent_handlers.handle_agent_callback
ALL_PROJECTS_MODE_ENABLED_MESSAGE = (
    telegram_session_handlers.ALL_PROJECTS_MODE_ENABLED_MESSAGE
)
SESSION_LIST_LIMIT = telegram_session_handlers.SESSION_LIST_LIMIT
handle_new = telegram_session_handlers.handle_new
handle_sessions = telegram_session_handlers.handle_sessions
handle_stop = telegram_session_handlers.handle_stop
handle_all = telegram_session_handlers.handle_all
handle_switch_session = telegram_session_handlers.handle_switch_session
handle_message = telegram_input_handlers.handle_message
handle_photo = telegram_input_handlers.handle_photo
handle_document = telegram_input_handlers.handle_document
BOT_COMMANDS = telegram_lifecycle_handlers.BOT_COMMANDS
RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS = (
    telegram_lifecycle_handlers.RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS
)
RESTART_MARKER_PATH = telegram_lifecycle_handlers.RESTART_MARKER_PATH
post_init = telegram_lifecycle_handlers.post_init
handle_restart = telegram_lifecycle_handlers.handle_restart
handle_silence_on = telegram_lifecycle_handlers.handle_silence_on
handle_silence_off = telegram_lifecycle_handlers.handle_silence_off
_get_backend_display_name = telegram_agent_handlers._get_backend_display_name


_init_handler_callbacks()

# --- Настройка бота ---


def _register_handlers(application: Application) -> None:
    """Регистрирует все обработчики команд и сообщений."""
    application.add_handler(CommandHandler("new", handle_new))
    application.add_handler(CommandHandler("agent", handle_agent))
    application.add_handler(
        CallbackQueryHandler(handle_agent_callback, pattern=r"^agent:(claude|codex)$")
    )
    application.add_handler(CommandHandler("sessions", handle_sessions))
    application.add_handler(CommandHandler("stop", handle_stop))
    application.add_handler(CommandHandler(["all", "all_projects"], handle_all))
    application.add_handler(CommandHandler("projects", handle_projects))
    application.add_handler(CommandHandler("silence_on", handle_silence_on))
    application.add_handler(CommandHandler("silence_off", handle_silence_off))
    application.add_handler(CommandHandler("restart", handle_restart))
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^/\d+s\d+$"),
            handle_switch_project_session,
        )
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^/\d+$"), handle_switch_session)
    )
    # Обработчик /pN — переключение проекта. Должен быть зарегистрирован
    # до общего TEXT-обработчика, чтобы не перехватывался handle_message
    application.add_handler(
        MessageHandler(filters.Regex(r"^/p\d+$"), handle_switch_project)
    )
    # Copied watcher replies can start with clickable headers like "/8 ...".
    # Telegram marks "/8" as a command entity, so route these prompts before
    # the broad text handler excludes commands.
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^/(?:\d+s\d+|\d+)\s+"),
            handle_message,
        )
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(
        MessageHandler(filters.Document.ALL, handle_document)
    )


async def _global_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Последний рубеж для необработанных исключений в обработчиках."""
    logger.error(
        "Необработанное исключение в обработчике: %s", context.error, exc_info=context.error
    )
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ Внутренняя ошибка. Попробуй ещё раз.",
            )
        except Exception:
            logger.warning("Не удалось уведомить пользователя об ошибке")


def setup_bot() -> Application:
    """Создаёт и настраивает экземпляр Telegram-бота."""
    global _application

    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(256)
        .connect_timeout(HTTP_CONNECT_TIMEOUT_SECONDS)
        .read_timeout(HTTP_READ_TIMEOUT_SECONDS)
        .write_timeout(HTTP_WRITE_TIMEOUT_SECONDS)
        .pool_timeout(HTTP_CONNECT_TIMEOUT_SECONDS)
        .connection_pool_size(HTTP_CONNECTION_POOL_SIZE)
        .build()
    )
    _application = application
    telegram_response_delivery.init_application(application)
    _init_handler_callbacks()

    # Инъекция callback-зависимостей в claude_interaction.
    # Разрывает циклическую зависимость: claude_interaction не импортирует bot.
    # Передаём пары (модуль, имя_атрибута) — getattr при каждом вызове
    # позволяет unittest.mock.patch на delivery-модуле подхватываться автоматически.
    claude_interaction.init_callbacks(
        send_response_module=telegram_response_delivery,
        send_response_attr="send_response",
        send_telegram_message_module=telegram_response_delivery,
        send_telegram_message_attr="_send_telegram_message_bridge",
    )

    # Инъекция callback-зависимостей в media_group_handler.
    # Разрывает циклическую зависимость: media_group_handler не импортирует bot.
    async def _send_chat_action_for_media_group(chat_id: int) -> None:
        await _application.bot.send_chat_action(chat_id, ChatAction.TYPING)

    async def _send_telegram_message_for_media_group(
        chat_id: int, text: str, parse_mode: str | None,
    ) -> None:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, text, parse_mode=parse_mode)

    media_group_handler.init_callbacks(
        send_to_claude=claude_interaction.send_to_claude_and_respond,
        build_busy_message=claude_interaction.build_busy_message_if_busy,
        send_telegram_message=_send_telegram_message_for_media_group,
        send_chat_action=_send_chat_action_for_media_group,
    )

    _register_handlers(application)
    application.add_error_handler(_global_error_handler)

    return application
