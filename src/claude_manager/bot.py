"""Транспортный слой Telegram-бота — обработка команд и сообщений.

Принимает сообщения и команды из Telegram, передаёт их в session_manager
и process_manager, форматирует ответы Claude и отправляет обратно
пользователю. Знает о Telegram API, не знает как работает Claude внутри.
"""

import asyncio
import logging
import os
import re
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    all_projects_monitor,
    claude_interaction,
    coding_agent_backend,
    config,
    current_backend_registry,
    daily_session_registry,
    file_delivery,
    media_group_handler,
    message_splitter,
    process_manager,
    project_manager,
    session_manager,
    session_reader,
    session_watcher,
    silence_mode_registry,
    telegram_file_downloader,
    telegram_sender,
    unread_buffer,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession

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

# Команды для меню подсказок в Telegram
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

# Маркер, которым в списке проектов помечается текущий активный проект (кружок)
PROJECT_CURRENT_MARKER = "\u25cf"

# Шаблоны сообщений для команды переключения проектов
EMPTY_PROJECTS_TEMPLATE = "Проекты не найдены в папке {root}"
INVALID_PROJECT_NUMBER_TEMPLATE = "Проект #{number} не найден"
PROJECT_SWITCH_SUCCESS_TEMPLATE = "Переключено на проект: {name}"
PROJECT_SWITCH_PENDING_TEMPLATE = "Непрочитанных сообщений: {count}"
PROJECT_SWITCH_ERROR_TEMPLATE = "Ошибка переключения: {error}"
PROJECT_ALREADY_ACTIVE_TEMPLATE = "Уже работаю в проекте: {name}"
ALL_PROJECTS_MODE_LINE = "/all all"
ALL_PROJECTS_MODE_ENABLED_MESSAGE = (
    "Режим all включён: показываю сообщения из всех проектов.\n"
    "Писать агенту отсюда нельзя — сначала выберите проект и сессию."
)
ALL_PROJECTS_MODE_INPUT_WARNING = (
    "Вы в режиме all по всем проектам. Чтобы писать агенту, сначала войдите "
    "в проект и сессию: выберите проект через /projects или нажмите команду "
    "вида /1s2 в сообщении all."
)
PROJECT_SESSION_COMMAND_PATTERN = re.compile(
    r"^/(?P<project>\d+)s(?P<session>\d+)$"
)

# Метка launchd-сервиса бота (используется для самоперезапуска через /restart)
LAUNCHD_SERVICE_LABEL = "com.ivan.claude-manager"

# Задержка перед kickstart — чтобы бот успел ответить пользователю до своей смерти
RESTART_DELAY_BEFORE_KICKSTART_SECONDS = 2

# Маркер-файл для отправки подтверждения после перезапуска через /restart
RESTART_MARKER_PATH = Path("/tmp/claude-manager-restart-chat-id")

# Максимум сессий в /sessions после объединения всех backend-ов.
SESSION_LIST_LIMIT = 15

# --- Внутреннее состояние ---

# Ссылка на Application для доступа к bot из функций без context
_application: Application | None = None


# --- Вспомогательные функции ---


async def _send_telegram_message_bridge(
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    """Мост для callback-инъекции в claude_interaction — пробрасывает bot из _application."""
    await telegram_sender.send_telegram_message(
        _application.bot, chat_id, text,
        parse_mode=parse_mode, reply_markup=reply_markup,
    )


def _check_access(update: Update) -> bool:
    """Проверяет, есть ли отправитель в белом списке разрешённых пользователей."""
    user_id = update.effective_user.id
    if user_id in config.ALLOWED_USER_IDS:
        return True
    if config.E2E_TEST_USER_ID is not None and user_id == config.E2E_TEST_USER_ID:
        return True
    logger.warning("Неавторизованный доступ: user_id=%d", user_id)
    return False


def _get_backend_display_name(backend: BackendName) -> str:
    """Возвращает человекочитаемое имя CLI-backend-а."""
    return coding_agent_backend.get_backend(backend).display_name


def _get_backend_plain_name(backend: BackendName) -> str:
    """Возвращает имя backend-а без emoji для середины фразы."""
    display_name = _get_backend_display_name(backend)
    return display_name.split(maxsplit=1)[-1]


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


def _monitoring_mode_message_for_chat(chat_id: int) -> str:
    """Return the right warning for local monitoring or global all mode."""
    if all_projects_monitor.is_enabled_for_chat(chat_id):
        return ALL_PROJECTS_MODE_INPUT_WARNING
    return claude_interaction.MONITORING_MODE_MESSAGE


def _parse_project_session_command(raw_text: str) -> tuple[int, int] | None:
    """Parse an all-mode command like /3s12."""
    match = PROJECT_SESSION_COMMAND_PATTERN.match(raw_text)
    if match is None:
        return None
    return int(match.group("project")), int(match.group("session"))


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


# --- Публичные функции ---


async def send_response(
    chat_id: int,
    text: str,
    session_number: int,
    backend: BackendName | bool = BackendName.CLAUDE,
    is_final: bool | None = None,
    reply_markup=None,
) -> None:
    """Форматирует и отправляет ответ Claude в Telegram."""
    if isinstance(backend, bool) and is_final is None:
        is_final = backend
        backend = BackendName.CLAUDE
    if is_final is None:
        raise TypeError("is_final is required")

    # Пустой ответ или служебный маркер — заменяем на информативное сообщение
    if not text or text == claude_interaction.NO_RESPONSE_MARKER:
        text = claude_interaction.EMPTY_RESPONSE_TEXT

    # Silence mode: подавляем промежуточные сообщения (thinking, progress)
    if not is_final and silence_mode_registry.is_enabled():
        return

    # Обработка файловых маркеров — только для финальных ответов
    if is_final:
        text = await file_delivery.process_file_markers(_application.bot, chat_id, text)
        text = await file_delivery.process_show_file_markers(_application.bot, chat_id, text)

    parts = message_splitter.prepare_message(text)

    # Промежуточные обновления отображаем курсивом
    if not is_final:
        parts = [f"<i>{part}</i>" for part in parts]

    header = _format_session_header(session_number, is_final, backend)
    parts[0] = header + parts[0]

    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        # Кнопки (reply_markup) — только к последней части
        markup = reply_markup if index == last_index else None
        await telegram_sender.send_telegram_message(_application.bot, chat_id, part, reply_markup=markup)


async def send_watcher_message(
    chat_id: int,
    text: str,
    session_id: str,
    backend: BackendName | int = BackendName.CLAUDE,
    session_number: int | None = None,
    is_final: bool | None = None,
) -> None:
    """Отправляет сообщение от watcher (ответ из другой сессии)."""
    if isinstance(backend, int):
        session_number = backend
        backend = BackendName.CLAUDE
    if session_number is None or is_final is None:
        raise TypeError("session_number and is_final are required")

    # Silence mode: подавляем промежуточные сообщения от watcher
    if not is_final and silence_mode_registry.is_enabled():
        return

    # Обработка файловых маркеров — только для финальных ответов
    if is_final:
        text = await file_delivery.process_file_markers(_application.bot, chat_id, text)
        text = await file_delivery.process_show_file_markers(_application.bot, chat_id, text)

    parts = message_splitter.prepare_message(text)

    # Промежуточные обновления отображаем курсивом, как в send_response
    if not is_final:
        parts = [f"<i>{part}</i>" for part in parts]

    if _is_current_session(chat_id, session_id, backend):
        header = _format_session_header(session_number, is_final, backend)
    else:
        header = _format_clickable_session_header(session_number, backend, is_final)

    parts[0] = header + parts[0]

    for part in parts:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, part)


async def send_all_projects_watcher_message(
    chat_id: int,
    *,
    project_number: int,
    session_number: int,
    project_name: str,
    session_id: str,
    backend: BackendName,
    text: str,
    is_final: bool,
) -> None:
    """Send a watcher message from global all-project mode."""
    del session_id

    if not is_final and silence_mode_registry.is_enabled():
        return

    if is_final:
        text = await file_delivery.process_file_markers(_application.bot, chat_id, text)
        text = await file_delivery.process_show_file_markers(_application.bot, chat_id, text)

    parts = message_splitter.prepare_message(text)
    if not is_final:
        parts = [f"<i>{part}</i>" for part in parts]

    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = _get_backend_display_name(backend)
    header = (
        f"/{project_number}s{session_number} "
        f"{project_name} {backend_label} {status_icon} "
    )
    parts[0] = header + parts[0]

    for part in parts:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, part)


# --- Обработчики команд ---


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

    # Восстанавливаем привязки сессий после перезапуска
    try:
        await session_manager.load_bindings()
    except Exception:
        logger.error(
            "Ошибка при восстановлении состояния — начинаю с чистого",
            exc_info=True,
        )

    # Восстанавливаем silence mode после перезапуска
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

    # Если реестр дневных сессий не загрузился — сообщаем пользователю
    if not daily_session_registry.is_registry_loaded():
        for chat_id in config.ALLOWED_USER_IDS:
            if chat_id == config.E2E_TEST_USER_ID:
                continue
            await telegram_sender.send_telegram_message(_application.bot,
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

    # Запускаем фоновый мониторинг сессий из терминала
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
    await send_watcher_message(
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
    session_id: str,
    backend: BackendName,
    text: str,
    is_final: bool,
) -> None:
    """Callback for the global all-project monitor."""
    await send_all_projects_watcher_message(
        chat_id,
        project_number=project_number,
        session_number=session_number,
        project_name=project_name,
        session_id=session_id,
        backend=backend,
        text=text,
        is_final=is_final,
    )


async def _get_current_session_async(chat_id: int) -> ActiveSession | None:
    """Возвращает привязанную сессию для watcher (async-обёртка)."""
    return session_manager.get_active_session(chat_id)


async def handle_new(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /new — создаёт новую сессию Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id

    if all_projects_monitor.is_enabled_for_chat(chat_id):
        await telegram_sender.send_telegram_message(
            _application.bot,
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

        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            f"Создана новая сессия #{day_number} ({display_name})",
            parse_mode=None,
        )
    except Exception:
        logger.error("Ошибка создания сессии (chat_id=%d)", chat_id, exc_info=True)
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            "Не удалось создать сессию. Попробуйте ещё раз",
            parse_mode=None,
        )


async def handle_sessions(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /sessions — показывает список последних сессий."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    sessions_with_backend = []
    error_lines: list[str] = []
    for backend in coding_agent_backend.get_all_backends():
        try:
            sessions = await backend.list_session_files_for_project(
                config.WORKING_DIR
            )
        except Exception:
            logger.warning(
                "Не удалось прочитать список сессий backend-а %s",
                backend.name.value,
                exc_info=True,
            )
            error_lines.append(f"{backend.display_name}: не удалось прочитать список сессий")
            continue
        for session in sessions:
            sessions_with_backend.append((session, backend))

    if not sessions_with_backend and not error_lines:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, "Нет сессий", parse_mode=None)
        return

    lines: list[str] = []
    sessions_with_backend.sort(
        key=lambda item: item[0].last_modified_at,
        reverse=True,
    )
    for session, backend in sessions_with_backend[:SESSION_LIST_LIMIT]:
        day_number = await daily_session_registry.register_session(
            session.session_id,
            backend.name,
        )
        session_summary = await daily_session_registry.get_session_summary(
            session.session_id,
            backend.name,
        )
        session_label = session_summary or session.preview
        lines.append(f"/{day_number} {backend.display_name} {session_label}")

    lines.extend(error_lines)
    if not lines:
        lines.append("Нет сессий")

    text = "\n".join(lines)
    # Отправляем без HTML, чтобы /1 /2 /3 были кликабельными командами
    await telegram_sender.send_telegram_message(_application.bot, chat_id, text, parse_mode=None)


async def handle_agent(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик команды /agent — показывает выбор CLI-агента."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    current_backend = current_backend_registry.get_current()
    display_name = _get_backend_display_name(current_backend)
    reply_markup = _build_agent_keyboard(current_backend)

    await telegram_sender.send_telegram_message(
        _application.bot,
        chat_id,
        f"Текущий агент: {display_name}",
        parse_mode=None,
        reply_markup=reply_markup,
    )


async def handle_agent_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик inline-кнопок /agent."""
    if not _check_access(update):
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


async def handle_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /stop — останавливает текущий процесс Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    active_session = session_manager.get_active_session(chat_id)
    if active_session is None:
        legacy_session_id = session_manager.get_bound_session(chat_id)
        if legacy_session_id is not None:
            active_session = ActiveSession(legacy_session_id, BackendName.CLAUDE)

    if active_session is None:
        await telegram_sender.send_telegram_message(_application.bot,
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
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            f"{display_name} сейчас не работает, нечего останавливать",
            parse_mode=None,
        )
        return

    await process_manager.stop_process(session_id, backend)
    await telegram_sender.send_telegram_message(_application.bot,
        chat_id, f"{display_name} остановлен", parse_mode=None
    )


async def handle_all(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /all — включает глобальный мониторинг всех проектов."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    await session_manager.unbind_session(chat_id)
    await all_projects_monitor.enable_for_chat(chat_id)
    await telegram_sender.send_telegram_message(_application.bot,
        chat_id,
        ALL_PROJECTS_MODE_ENABLED_MESSAGE,
        parse_mode=None,
    )


def _format_project_line(
    project: project_manager.ProjectInfo,
    number: int,
    *,
    suppress_current_marker: bool = False,
) -> str:
    """Форматирует одну строку списка проектов с номером и маркером текущего."""
    marker = (
        PROJECT_CURRENT_MARKER + " "
        if project.is_current and not suppress_current_marker
        else ""
    )
    return f"{marker}/p{number} {project.name}"


async def handle_projects(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /projects — показывает список доступных проектов."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    projects = await project_manager.scan_available_projects()

    if not projects:
        message = EMPTY_PROJECTS_TEMPLATE.format(root=config.PROJECTS_ROOT_DIR)
        await telegram_sender.send_telegram_message(_application.bot, chat_id, message, parse_mode=None)
        return

    # Каждая строка — кликабельная команда. parse_mode=None сохраняет кликабельность
    all_mode_enabled = all_projects_monitor.is_enabled_for_chat(chat_id)
    all_marker = PROJECT_CURRENT_MARKER + " " if all_mode_enabled else ""
    lines = [f"{all_marker}{ALL_PROJECTS_MODE_LINE}"]
    lines.extend(
        _format_project_line(
            project,
            number,
            suppress_current_marker=all_mode_enabled,
        )
        for number, project in enumerate(projects, start=1)
    )
    text = "\n".join(lines)
    await telegram_sender.send_telegram_message(_application.bot, chat_id, text, parse_mode=None)


async def _resolve_project_by_number(
    project_number: int,
) -> project_manager.ProjectInfo | None:
    """Находит проект по порядковому номеру в отсортированном списке."""
    projects = await project_manager.scan_available_projects()
    if project_number < 1 or project_number > len(projects):
        return None
    return projects[project_number - 1]


def _format_switch_result_message(
    result: project_manager.SwitchResult, project_name: str
) -> str:
    """Формирует текст ответа пользователю по результату переключения проекта."""
    if result.already_active:
        return PROJECT_ALREADY_ACTIVE_TEMPLATE.format(name=project_name)
    if result.success:
        text = PROJECT_SWITCH_SUCCESS_TEMPLATE.format(name=project_name)
        visible_pending_count = _count_visible_pending_messages(result)
        if visible_pending_count > 0:
            text += "\n" + PROJECT_SWITCH_PENDING_TEMPLATE.format(
                count=visible_pending_count,
            )
        return text
    return PROJECT_SWITCH_ERROR_TEMPLATE.format(error=result.error_message)


def _pending_message_is_visible_now(pending_message: object) -> bool:
    """Проверяет, будет ли pending-сообщение видно пользователю прямо сейчас."""
    is_final = getattr(pending_message, "is_final", True)
    return bool(is_final) or not silence_mode_registry.is_enabled()


def _get_visible_pending_messages(pending_messages: list) -> list:
    """Возвращает pending-сообщения, которые не будут подавлены silence mode."""
    return [
        pending_message
        for pending_message in pending_messages
        if _pending_message_is_visible_now(pending_message)
    ]


def _count_visible_pending_messages(
    result: project_manager.SwitchResult,
) -> int:
    """Считает pending-сообщения, которые реально будут показаны пользователю."""
    if not result.pending_messages:
        return result.pending_messages_count
    return len(_get_visible_pending_messages(result.pending_messages))


async def _deliver_pending_messages(
    chat_id: int, pending_messages: list,
) -> None:
    """Доставляет пропущенные сообщения из буфера после переключения проекта."""
    for pending in _get_visible_pending_messages(pending_messages):
        backend = getattr(pending, "backend", BackendName.CLAUDE)
        is_final = getattr(pending, "is_final", True)
        try:
            day_number = await daily_session_registry.register_session(
                pending.session_id,
                backend,
            )
        except Exception:
            logger.error(
                "Ошибка регистрации сессии %s при доставке буфера",
                pending.session_id, exc_info=True,
            )
            continue

        await send_response(
            chat_id, pending.text, day_number, backend, is_final=is_final,
        )
        unread_buffer.clear_snapshot_for_session_backend_pair(
            pending.session_id,
            backend,
        )


async def _include_pending_for_all_mode_same_project(
    result: project_manager.SwitchResult,
    target_project: project_manager.ProjectInfo,
    was_all_projects_mode: bool,
) -> project_manager.SwitchResult:
    """Collect pending messages when all mode exits into the already active project."""
    if not was_all_projects_mode or not result.success or not result.already_active:
        return result

    pending_count, pending_messages = await project_manager.collect_pending_messages_for_project(
        target_project.absolute_path
    )
    return project_manager.SwitchResult(
        success=True,
        already_active=False,
        old_path=result.old_path,
        new_path=result.new_path,
        pending_messages_count=pending_count,
        pending_messages=pending_messages,
        error_message=result.error_message,
    )


async def _restore_all_projects_mode_after_failed_project_switch(
    chat_id: int,
    result: project_manager.SwitchResult,
    was_all_projects_mode: bool,
) -> bool:
    """Restore global all-project monitoring after an unsuccessful project switch."""
    if result.success or not was_all_projects_mode:
        return False

    await all_projects_monitor.enable_for_chat(chat_id)
    return True


async def handle_switch_project(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /pN — переключает бот на проект по номеру."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    was_all_projects_mode = False
    all_projects_mode_restored_after_switch_failure = False
    # Отбрасываем префикс "/p" и парсим число. Регулярное выражение в хендлере
    # гарантирует, что текст имеет вид /p<digits> — ошибки int() быть не может
    project_number = int(update.message.text[2:])

    try:
        target_project = await _resolve_project_by_number(project_number)
        if target_project is None:
            message = INVALID_PROJECT_NUMBER_TEMPLATE.format(number=project_number)
            await telegram_sender.send_telegram_message(_application.bot, chat_id, message, parse_mode=None)
            return

        was_all_projects_mode = all_projects_monitor.disable_for_chat(chat_id)
        result = await project_manager.switch_project(target_project.absolute_path)
        all_projects_mode_restored_after_switch_failure = (
            await _restore_all_projects_mode_after_failed_project_switch(
                chat_id,
                result,
                was_all_projects_mode,
            )
        )
        result = await _include_pending_for_all_mode_same_project(
            result,
            target_project,
            was_all_projects_mode,
        )
        response_text = _format_switch_result_message(result, target_project.name)
        await telegram_sender.send_telegram_message(_application.bot, chat_id, response_text, parse_mode=None)

        # Доставка пропущенных сообщений после переключения
        if result.success and result.pending_messages_count > 0:
            await _deliver_pending_messages(chat_id, result.pending_messages)
    finally:
        if (
            was_all_projects_mode
            and not all_projects_mode_restored_after_switch_failure
            and not all_projects_monitor.has_enabled_chats()
        ):
            session_watcher.resume_all()


async def handle_switch_project_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик команды из all-режима: /<project>s<session>."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    parsed_command = _parse_project_session_command(update.message.text)
    if parsed_command is None:
        return
    project_number, session_number = parsed_command

    link_target = all_projects_monitor.resolve_link(
        project_number,
        session_number,
    )
    was_all_projects_mode = False
    all_projects_mode_restored_after_switch_failure = False

    try:
        target_project = await _resolve_project_by_number(project_number)
        if link_target is not None:
            target_project = project_manager.ProjectInfo(
                name=link_target.project_name,
                absolute_path=link_target.project_path,
                is_current=target_project.is_current if target_project else False,
            )

        if target_project is None:
            message = INVALID_PROJECT_NUMBER_TEMPLATE.format(number=project_number)
            await telegram_sender.send_telegram_message(
                _application.bot,
                chat_id,
                message,
                parse_mode=None,
            )
            return

        was_all_projects_mode = all_projects_monitor.disable_for_chat(chat_id)
        result = await project_manager.switch_project(target_project.absolute_path)
        all_projects_mode_restored_after_switch_failure = (
            await _restore_all_projects_mode_after_failed_project_switch(
                chat_id,
                result,
                was_all_projects_mode,
            )
        )
        result = await _include_pending_for_all_mode_same_project(
            result,
            target_project,
            was_all_projects_mode,
        )

        if not result.success:
            response_text = _format_switch_result_message(result, target_project.name)
            await telegram_sender.send_telegram_message(
                _application.bot,
                chat_id,
                response_text,
                parse_mode=None,
            )
            return

        if link_target is not None:
            bound_number = await session_manager.set_active_session(
                chat_id,
                link_target.session_id,
                link_target.backend,
            )
            display_name = _get_backend_display_name(link_target.backend)
            session_found = True
        else:
            switch_session_result = await session_manager.switch_to_session(
                chat_id,
                session_number,
            )
            session_found = switch_session_result.found
            bound_number = switch_session_result.day_number
            display_name = _get_backend_display_name(switch_session_result.backend)

        if not session_found:
            await telegram_sender.send_telegram_message(
                _application.bot,
                chat_id,
                f"Сессия #{session_number} не найдена в проекте {target_project.name}",
                parse_mode=None,
            )
            return

        response_text = (
            f"Переключено на проект: {target_project.name}\n"
            f"Подключён к сессии #{bound_number} ({display_name})"
        )
        visible_pending_count = _count_visible_pending_messages(result)
        if visible_pending_count > 0:
            response_text += "\n" + PROJECT_SWITCH_PENDING_TEMPLATE.format(
                count=visible_pending_count,
            )
        await telegram_sender.send_telegram_message(
            _application.bot,
            chat_id,
            response_text,
            parse_mode=None,
        )

        if result.pending_messages_count > 0:
            await _deliver_pending_messages(chat_id, result.pending_messages)
    finally:
        if (
            was_all_projects_mode
            and not all_projects_mode_restored_after_switch_failure
            and not all_projects_monitor.has_enabled_chats()
        ):
            session_watcher.resume_all()


async def handle_switch_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /N — переключает на сессию по номеру."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    day_number = int(update.message.text[1:])

    result = await session_manager.switch_to_session(chat_id, day_number)

    if not result.found:
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            f"Сессия #{day_number} не найдена",
            parse_mode=None,
        )
        return

    display_name = _get_backend_display_name(result.backend)
    preview_text = f": {result.preview}" if result.preview else ""
    await telegram_sender.send_telegram_message(_application.bot,
        chat_id,
        f"Подключён к сессии #{day_number} ({display_name}){preview_text}",
        parse_mode=None,
    )


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик текстовых сообщений — отправляет текст в Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    text = update.message.text

    # Перехват текстовых команд silence mode — ДО отправки в Claude
    normalized_text = text.strip().lower()
    if normalized_text == "silence on":
        silence_mode_registry.enable()
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            "Silence mode включён — буду присылать только финальные ответы",
            parse_mode=None,
        )
        return
    if normalized_text == "silence off":
        silence_mode_registry.disable()
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            "Silence mode выключен — промежуточные сообщения снова доставляются",
            parse_mode=None,
        )
        return

    if session_manager.is_monitoring_mode(chat_id):
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id, _monitoring_mode_message_for_chat(chat_id), parse_mode=None
        )
        return

    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception as exc:
        logger.warning("send_chat_action не удался в handle_message: %s", exc)
    await claude_interaction.send_to_claude_and_respond(chat_id, text)


async def _handle_single_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обрабатывает одиночное фото (вне альбома).

    Скачивает файл, формирует задание для Claude и шлёт ответ.
    Предполагает, что проверки доступа и режима мониторинга уже сделаны
    в вызывающем коде (handle_photo).
    """
    chat_id = update.effective_chat.id

    # Guard: Claude занят — отвечаем сразу, не тратим HTTP-пул на скачивание.
    # Проверка — fast-path оптимизация, атомарная защита живёт в
    # process_manager.send_message под _busy_lock
    busy_message = claude_interaction.build_busy_message_if_busy(chat_id)
    if busy_message is not None:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, busy_message, parse_mode=None)
        return

    try:
        file_path = await telegram_file_downloader.download_and_save_file(update, _application.bot)
    except Exception:
        logger.error("Ошибка скачивания фото (chat_id=%d)", chat_id, exc_info=True)
        await telegram_sender.send_telegram_message(_application.bot,
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
    await claude_interaction.send_to_claude_and_respond(chat_id, task_text)


async def handle_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик фотографий — роутит между одиночным фото и медиа-группой.

    Если фото часть альбома (media_group_id != None) — отдаём в агрегатор,
    который соберёт всю группу и вызовет _finalize_photo_group.
    Если фото одиночное — обрабатываем немедленно.
    """
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id

    if session_manager.is_monitoring_mode(chat_id):
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id, _monitoring_mode_message_for_chat(chat_id), parse_mode=None
        )
        return

    media_group_id = update.message.media_group_id
    if media_group_id is not None:
        # Часть альбома — в агрегатор. Реальная обработка произойдёт
        # через MEDIA_GROUP_DEBOUNCE_SECONDS после последнего фото группы.
        # Callback — bot'овский _finalize_photo_group, а не media_group_handler
        # напрямую, чтобы тесты могли мокать через claude_manager.bot.*
        await media_group_handler.media_group_aggregator.add_update(
            media_group_id, update, media_group_handler.finalize_photo_group,
        )
        return

    # Одиночное фото — обрабатываем немедленно
    await _handle_single_photo(update, context)


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик документов — скачивает файл и формирует задание для Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id

    if session_manager.is_monitoring_mode(chat_id):
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id, _monitoring_mode_message_for_chat(chat_id), parse_mode=None
        )
        return

    # media_group_id намеренно игнорируется — документы как одиночные (v1, см. спеку 2.12)
    # Guard: Claude занят → выходим до скачивания.
    # Проверка — fast-path оптимизация, атомарная защита в process_manager.send_message.
    busy_message = claude_interaction.build_busy_message_if_busy(chat_id)
    if busy_message is not None:
        await telegram_sender.send_telegram_message(_application.bot, chat_id, busy_message, parse_mode=None)
        return

    try:
        file_path = await telegram_file_downloader.download_and_save_file(update, _application.bot)
    except Exception:
        logger.error(
            "Ошибка скачивания документа (chat_id=%d)", chat_id, exc_info=True
        )
        await telegram_sender.send_telegram_message(_application.bot,
            chat_id,
            "Не удалось скачать файл. Попробуйте отправить ещё раз",
            parse_mode=None,
        )
        return

    caption = update.message.caption
    # Определяем тип файла: изображение или нет
    extension = Path(file_path).suffix.lstrip(".").lower()
    is_image = extension in file_delivery.IMAGE_EXTENSIONS
    task_text = claude_interaction.build_file_task(file_path, caption, is_image)

    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception as exc:
        logger.warning("send_chat_action не удался в handle_document: %s", exc)
    await claude_interaction.send_to_claude_and_respond(chat_id, task_text)


async def handle_restart(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /restart — самоперезапуск бота через launchd.

    Запускает отвязанный процесс, который через задержку выполнит
    launchctl kickstart. Задержка нужна, чтобы бот успел отправить
    подтверждение пользователю до того, как его убьёт launchd.
    """
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        f"Перезапускаюсь через {RESTART_DELAY_BEFORE_KICKSTART_SECONDS} сек...",
    )
    RESTART_MARKER_PATH.write_text(str(chat_id))
    logger.info("Запущен самоперезапуск через /restart")

    # Отвязанный процесс (start_new_session=True) — чтобы kickstart -k,
    # убивающий дерево процессов бота, не убил сам себя вместе с ботом.
    # DEVNULL для stdout/stderr — чтобы не держать пайпы открытыми.
    kickstart_command = (
        f"sleep {RESTART_DELAY_BEFORE_KICKSTART_SECONDS} && "
        f"launchctl kickstart -k 'gui/{os.getuid()}/{LAUNCHD_SERVICE_LABEL}'"
    )
    await asyncio.create_subprocess_exec(
        "bash", "-c", kickstart_command,
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def handle_silence_on(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик /silence_on — включает режим тишины."""
    if not _check_access(update):
        return
    silence_mode_registry.enable()
    await telegram_sender.send_telegram_message(_application.bot,
        update.effective_chat.id,
        "Silence mode включён — буду присылать только финальные ответы",
        parse_mode=None,
    )


async def handle_silence_off(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработчик /silence_off — выключает режим тишины."""
    if not _check_access(update):
        return
    silence_mode_registry.disable()
    await telegram_sender.send_telegram_message(_application.bot,
        update.effective_chat.id,
        "Silence mode выключен — промежуточные сообщения снова доставляются",
        parse_mode=None,
    )


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

    # Инъекция callback-зависимостей в claude_interaction.
    # Разрывает циклическую зависимость: claude_interaction не импортирует bot.
    # Передаём пары (модуль, имя_атрибута) — getattr при каждом вызове
    # позволяет unittest.mock.patch на bot.send_response подхватываться автоматически.
    import claude_manager.bot as _self_module
    claude_interaction.init_callbacks(
        send_response_module=_self_module,
        send_response_attr="send_response",
        send_telegram_message_module=_self_module,
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
