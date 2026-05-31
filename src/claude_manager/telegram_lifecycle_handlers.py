"""Telegram application lifecycle and service command handlers."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    config,
    current_backend_registry,
    daily_session_registry,
    reply_route_registry,
    session_manager,
    session_watcher,
    silence_mode_registry,
    telegram_file_downloader,
    telegram_response_delivery,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    ("new", "Новая сессия"),
    ("agent", "Выбор CLI-агента"),
    ("sessions", "Список сессий"),
    ("all", "Мониторинг всех проектов"),
    ("all_projects", "Мониторинг всех проектов"),
    ("stop", "Остановить активного агента"),
    ("projects", "Список проектов для переключения"),
    ("silence_on", "Режим тишины: вкл"),
    ("silence_off", "Режим тишины: выкл"),
    ("restart", "Перезапуск бота"),
]

# Задержка перед systemctl restart — чтобы бот успел ответить пользователю
# до того, как systemd пришлёт ему SIGTERM.
RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS = 2

# Маркер-файл для отправки подтверждения после перезапуска через /restart.
# Новый процесс читает chat_id из этого файла в post_init и шлёт «готов».
RESTART_MARKER_PATH = Path("/tmp/claude-manager-restart-chat-id")

_ApplicationGetter = Callable[[], Application | None]
_AccessChecker = Callable[[Update], bool]
_application_getter: _ApplicationGetter | None = None
_access_checker: _AccessChecker | None = None


def init_callbacks(
    application_getter: _ApplicationGetter,
    access_checker: _AccessChecker,
) -> None:
    """Inject bot-owned callbacks needed by lifecycle handlers."""
    global _application_getter, _access_checker
    _application_getter = application_getter
    _access_checker = access_checker


def _get_application() -> Application:
    if _application_getter is None:
        raise RuntimeError("telegram lifecycle handlers are not initialized")
    application = _application_getter()
    if application is None:
        raise RuntimeError("telegram application is not initialized")
    return application


def _has_access(update: Update) -> bool:
    if _access_checker is None:
        raise RuntimeError("telegram lifecycle access checker is not initialized")
    return _access_checker(update)


async def _notify_restart_complete(application: Application) -> None:
    """Отправляет подтверждение после перезапуска через /restart."""
    if not RESTART_MARKER_PATH.exists():
        return
    try:
        chat_id = int(RESTART_MARKER_PATH.read_text().strip())
        await application.bot.send_message(chat_id, "Перезапуск завершён, снова на связи.")
        logger.info("Отправлено подтверждение перезапуска в chat_id=%d", chat_id)
    except Exception:
        logger.warning("Не удалось отправить подтверждение перезапуска", exc_info=True)
    finally:
        RESTART_MARKER_PATH.unlink(missing_ok=True)


async def post_init(application: Application) -> None:
    """Инициализация после запуска: очистка файлов, восстановление состояния, меню команд."""
    await telegram_file_downloader.clean_old_received_files()

    try:
        await session_manager.load_bindings()
    except Exception:
        logger.error(
            "Ошибка при восстановлении состояния — начинаю с чистого",
            exc_info=True,
        )

    try:
        silence_mode_registry.load_state()
    except Exception:
        logger.error(
            "Ошибка при загрузке silence mode — режим выключен по умолчанию",
            exc_info=True,
        )

    try:
        current_backend_registry.load_state()
    except Exception:
        logger.error(
            "Ошибка при загрузке current backend — используется Claude",
            exc_info=True,
        )

    try:
        reply_route_registry.load_routes()
    except Exception:
        logger.error(
            "Ошибка при загрузке reply-route registry — старые reply не восстановлены",
            exc_info=True,
        )

    if not daily_session_registry.is_registry_loaded():
        for chat_id in config.ALLOWED_USER_IDS:
            if chat_id == config.E2E_TEST_USER_ID:
                continue
            await telegram_sender.send_telegram_message(
                application.bot,
                chat_id,
                "Не удалось загрузить реестр дневных сессий после 10 попыток. "
                "Нумерация сессий может начаться заново. "
                "Попробуй перезапустить бота.",
                parse_mode=None,
            )

    bindings = session_manager.get_all_bindings()
    if bindings:
        logger.info("Восстановлено %d привязок к сессиям", len(bindings))
    else:
        logger.info("Привязок нет — бот в режиме /all (мониторинг)")

    try:
        commands = [
            BotCommand(command, description)
            for command, description in BOT_COMMANDS
        ]
        await application.bot.set_my_commands(commands)
        logger.info("Меню команд установлено")
    except Exception:
        logger.warning("Не удалось установить меню команд", exc_info=True)

    asyncio.create_task(
        session_watcher.start(_watcher_callback, _get_current_session_async)
    )
    asyncio.create_task(
        all_projects_monitor.start(_all_projects_watcher_callback)
    )

    await _notify_restart_complete(application)


async def _watcher_callback(
    chat_id: int,
    session_id: str,
    backend: BackendName,
    day_number: int,
    text: str,
    is_current: bool,
    is_final: bool,
) -> None:
    """Callback для session_watcher — пересылает ответ Claude из мониторинга."""
    await telegram_response_delivery.send_watcher_message(
        chat_id,
        text,
        session_id,
        backend,
        day_number,
        is_final,
    )


async def _all_projects_watcher_callback(
    chat_id: int,
    project_number: int,
    session_number: int,
    project_name: str,
    project_path: str,
    session_id: str,
    backend: BackendName,
    text: str,
    is_final: bool,
) -> None:
    """Callback for the global all-project monitor."""
    await telegram_response_delivery.send_all_projects_watcher_message(
        chat_id,
        project_number=project_number,
        session_number=session_number,
        project_name=project_name,
        project_path=project_path,
        session_id=session_id,
        backend=backend,
        text=text,
        is_final=is_final,
    )


async def _get_current_session_async(chat_id: int) -> ActiveSession | None:
    """Возвращает привязанную сессию для watcher (async-обёртка)."""
    return session_manager.get_active_session(chat_id)


async def handle_restart(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /restart — самоперезапуск бота через systemd."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        f"Перезапускаюсь через {RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS} сек...",
    )
    RESTART_MARKER_PATH.write_text(str(chat_id))
    logger.info("Запущен самоперезапуск через /restart")

    restart_command = (
        f"sleep {RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS} && "
        "systemctl --user restart claude-manager.service"
    )
    await asyncio.create_subprocess_exec(
        "bash", "-c", restart_command,
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def handle_silence_on(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик /silence_on — включает режим тишины."""
    if not _has_access(update):
        return
    silence_mode_registry.enable()
    await telegram_sender.send_telegram_message(
        _get_application().bot,
        update.effective_chat.id,
        "Silence mode включён — буду присылать только финальные ответы",
        parse_mode=None,
    )


async def handle_silence_off(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик /silence_off — выключает режим тишины."""
    if not _has_access(update):
        return
    silence_mode_registry.disable()
    await telegram_sender.send_telegram_message(
        _get_application().bot,
        update.effective_chat.id,
        "Silence mode выключен — промежуточные сообщения снова доставляются",
        parse_mode=None,
    )
