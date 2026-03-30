"""Транспортный слой Telegram-бота — обработка команд и сообщений.

Принимает сообщения и команды из Telegram, передаёт их в session_manager
и process_manager, форматирует ответы Claude и отправляет обратно
пользователю. Знает о Telegram API, не знает как работает Claude внутри.
"""

import asyncio
import logging
import os
import random
import string
import time
from datetime import datetime
from pathlib import Path

from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from claude_manager import (
    config,
    daily_session_registry,
    message_splitter,
    process_manager,
    session_manager,
    session_reader,
    session_watcher,
)

logger = logging.getLogger(__name__)

# --- Константы ---

# Количество попыток повторной отправки при сетевых ошибках Telegram
SEND_RETRY_COUNT = 3

# Пауза между попытками повторной отправки (секунды)
SEND_RETRY_DELAY_SECONDS = 2

# Имя папки для скачанных фото и документов
RECEIVED_FILES_DIR = "received_files"

# Максимальный возраст файлов в received_files/ (дни)
RECEIVED_FILES_MAX_AGE_DAYS = 7

# Формат временной метки в именах файлов
FILE_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

# Длина случайного суффикса в именах файлов (6 символов = 2 млрд вариантов)
FILE_RANDOM_SUFFIX_LENGTH = 6

# Расширения, которые считаются изображениями
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "svg"}

# Сообщение для пустого ответа Claude
EMPTY_RESPONSE_TEXT = "Claude обработал запрос, но не дал текстовый ответ"

# Служебный ответ Claude, который не пересылается пользователю
NO_RESPONSE_MARKER = "No response requested."

# Команды для меню подсказок в Telegram
BOT_COMMANDS = [
    ("new", "Новая сессия"),
    ("sessions", "Список сессий"),
    ("all", "Мониторинг всех сессий"),
    ("stop", "Остановить Claude"),
]

# Сообщение при попытке написать в режиме /all
MONITORING_MODE_MESSAGE = (
    "Вы в режиме мониторинга. Для отправки сообщений "
    "подключитесь к сессии — нажмите на номер сессии или отправьте /new"
)

# Количество секунд в одном дне (для расчёта возраста файлов)
SECONDS_PER_DAY = 86400

# --- Внутреннее состояние ---

# Ссылка на Application для доступа к bot из функций без context
_application: Application | None = None


# --- Вспомогательные функции ---


def _check_access(update: Update) -> bool:
    """Проверяет, есть ли отправитель в белом списке разрешённых пользователей."""
    user_id = update.effective_user.id
    if user_id in config.ALLOWED_USER_IDS:
        return True
    logger.warning("Неавторизованный доступ: user_id=%d", user_id)
    return False


def _format_session_header(session_number: int, is_final: bool) -> str:
    """Формирует заголовок ответа с номером сессии и статусом."""
    status_icon = "\u2705" if is_final else "\u23f3"
    return f"#{session_number} {status_icon} "


def _format_clickable_session_number(session_number: int) -> str:
    """Форматирует номер сессии как кликабельную команду для Telegram."""
    return f"<b>/{session_number}</b>"


def _is_current_session(chat_id: int, session_id: str) -> bool:
    """Проверяет, является ли сессия текущей активной для данного чата."""
    bound = session_manager.get_bound_session(chat_id)
    return bound == session_id


def _build_file_task(file_path: str, caption: str | None, is_image: bool) -> str:
    """Формирует текстовое задание для Claude на основе скачанного файла."""
    if caption:
        return (
            f"Пользователь отправил файл с подписью: {caption}. "
            f"Файл: {file_path}. "
            "Прочитай файл инструментом Read и выполни задачу из подписи"
        )
    if is_image:
        return (
            "Пользователь отправил фотографию без подписи. "
            f"Файл: {file_path}. "
            "Прочитай файл и опиши, что на фотографии"
        )
    return (
        "Пользователь отправил файл без подписи. "
        f"Файл: {file_path}. "
        "Прочитай файл и опиши его содержимое"
    )


def _generate_file_name(original_name: str | None, extension: str) -> str:
    """Генерирует уникальное имя файла для сохранения в received_files/."""
    timestamp = datetime.now().strftime(FILE_TIMESTAMP_FORMAT)
    alphabet = string.ascii_lowercase + string.digits
    suffix = "".join(random.choices(alphabet, k=FILE_RANDOM_SUFFIX_LENGTH))
    return f"file_{timestamp}_{suffix}.{extension}"


def _is_file_expired(file_path: Path, max_age_seconds: float) -> bool:
    """Проверяет, превысил ли файл максимальный возраст."""
    file_age = time.time() - os.path.getmtime(file_path)
    return file_age > max_age_seconds


async def _clean_old_received_files() -> None:
    """Удаляет файлы старше 7 дней из папки received_files/."""
    files_dir = Path(config.WORKING_DIR) / RECEIVED_FILES_DIR
    if not files_dir.exists():
        return

    max_age_seconds = RECEIVED_FILES_MAX_AGE_DAYS * SECONDS_PER_DAY
    try:
        entries = list(files_dir.iterdir())
    except OSError as error:
        logger.warning("Ошибка чтения папки %s: %s", files_dir, error)
        return

    deleted_count = 0
    for entry in entries:
        if not entry.is_file():
            continue
        try:
            if _is_file_expired(entry, max_age_seconds):
                os.remove(entry)
                deleted_count += 1
        except OSError as error:
            logger.warning("Ошибка удаления файла %s: %s", entry, error)

    if deleted_count > 0:
        logger.info("Удалено %d старых файлов из %s", deleted_count, files_dir)


async def _send_raw(chat_id: int, text: str, parse_mode: str | None, reply_markup) -> None:
    """Вызывает Telegram API для отправки одного сообщения."""
    await _application.bot.send_message(
        chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup,
    )


async def _fallback_to_plain_text(
    chat_id: int, text: str, parse_mode: str | None, reply_markup,
) -> bool:
    """Пробует отправить как plain text при HTML-ошибке. Возвращает True при успехе."""
    if parse_mode != ParseMode.HTML:
        return False
    plain_text = message_splitter.strip_html_tags(text)
    await _send_raw(chat_id, plain_text, parse_mode=None, reply_markup=reply_markup)
    return True


async def _handle_retry_after(
    chat_id: int, text: str, parse_mode: str | None, reply_markup,
    retry_after_seconds: int,
) -> None:
    """Обрабатывает RetryAfter: ждёт указанное Telegram время и повторяет."""
    logger.warning("RetryAfter от Telegram: ждём %d секунд", retry_after_seconds)
    await asyncio.sleep(retry_after_seconds)
    try:
        await _send_raw(chat_id, text, parse_mode, reply_markup)
    except Exception:
        logger.warning("Повторная отправка после RetryAfter не удалась", exc_info=True)


def _handle_network_error(attempt: int, chat_id: int) -> bool:
    """Обрабатывает сетевую ошибку. Возвращает True, если нужно повторить."""
    if attempt < SEND_RETRY_COUNT - 1:
        logger.warning(
            "Сетевая ошибка Telegram (попытка %d/%d), повтор через %d с",
            attempt + 1, SEND_RETRY_COUNT, SEND_RETRY_DELAY_SECONDS,
        )
        return True
    logger.error(
        "Все %d попыток отправки в Telegram исчерпаны (chat_id=%d)",
        SEND_RETRY_COUNT, chat_id,
    )
    return False


async def _send_telegram_message(
    chat_id: int,
    text: str,
    parse_mode: str | None = ParseMode.HTML,
    reply_markup=None,
) -> None:
    """Отправляет одно сообщение в Telegram с обработкой ошибок."""
    for attempt in range(SEND_RETRY_COUNT):
        try:
            await _send_raw(chat_id, text, parse_mode, reply_markup)
            return
        except BadRequest:
            if await _fallback_to_plain_text(chat_id, text, parse_mode, reply_markup):
                return
            raise
        except RetryAfter as error:
            await _handle_retry_after(
                chat_id, text, parse_mode, reply_markup, error.retry_after,
            )
            return
        except (TimedOut, NetworkError):
            if _handle_network_error(attempt, chat_id):
                await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)


def _extract_file_info(update: Update) -> tuple[str, str, str | None]:
    """Извлекает file_id, расширение и оригинальное имя из сообщения Telegram."""
    if update.message.photo:
        photo_size = update.message.photo[-1]
        return photo_size.file_id, "jpg", None

    document = update.message.document
    original_name = document.file_name
    if original_name and "." in original_name:
        extension = original_name.rsplit(".", maxsplit=1)[-1].lower()
    else:
        extension = "bin"
    return document.file_id, extension, original_name


async def _download_and_save_file(update: Update) -> str:
    """Скачивает файл (фото или документ) из Telegram и сохраняет на диск."""
    files_dir = Path(config.WORKING_DIR) / RECEIVED_FILES_DIR
    files_dir.mkdir(exist_ok=True)

    file_id, extension, original_name = _extract_file_info(update)
    file_name = _generate_file_name(original_name, extension)
    save_path = files_dir / file_name

    telegram_file = await _application.bot.get_file(file_id)
    await telegram_file.download_to_drive(str(save_path))

    absolute_path = str(save_path.resolve())
    logger.info("Файл сохранён: %s", absolute_path)
    return absolute_path


async def _find_session_by_number(day_number: int) -> str | None:
    """Ищет сессию по дневному номеру в реестре и среди видимых сессий."""
    # Шаг 1: ищем в дневном реестре
    session_id = await daily_session_registry.get_session_id_by_number(day_number)
    if session_id is not None:
        return session_id

    # Шаг 2: регистрируем все видимые сессии и ищем повторно
    sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)
    for session in sessions:
        await daily_session_registry.register_session(session.session_id)

    return await daily_session_registry.get_session_id_by_number(day_number)


async def _on_progress(session_id: str, progress_text: str) -> None:
    """Callback для промежуточных обновлений от process_manager."""
    # Находим chat_id по session_id через привязки
    all_bindings = session_manager.get_all_bindings()
    for chat_id, bound_session in all_bindings.items():
        if bound_session == session_id:
            day_number = await daily_session_registry.register_session(session_id)
            await send_response(
                chat_id, progress_text, day_number, is_final=False
            )
            break


async def _on_retry(session_id: str, attempt: int, max_attempts: int) -> None:
    """Callback для уведомления о ретраях от process_manager."""
    all_bindings = session_manager.get_all_bindings()
    for chat_id, bound_session in all_bindings.items():
        if bound_session == session_id:
            await _send_telegram_message(
                chat_id,
                f"Ошибка Claude, повтор {attempt}/{max_attempts}...",
                parse_mode=None,
            )
            break


async def _ensure_process_running(chat_id: int, session_id: str) -> bool:
    """Создаёт процесс Claude, если он не запущен. Возвращает True при успехе."""
    if process_manager.has_process(session_id):
        return True
    try:
        await process_manager.create_process(session_id)
        return True
    except process_manager.ProcessManagerError as error:
        logger.error("Не удалось создать процесс: %s", error)
        await _send_telegram_message(
            chat_id, "Не удалось запустить Claude. Попробуйте ещё раз",
            parse_mode=None,
        )
        return False


async def _handle_claude_result(
    chat_id: int, session_id: str, result: process_manager.SendResult,
) -> str:
    """Обрабатывает результат от Claude: обновляет ID, отправляет ответ."""
    if result.session_id != session_id:
        old_id = session_id
        new_id = result.session_id
        await session_manager.update_session_id(chat_id, old_id, new_id)
        session_watcher.update_session_id(old_id, new_id)
        session_id = new_id

    day_number = await daily_session_registry.register_session(session_id)

    if result.is_error:
        error_text = result.text if result.text else "Неизвестная ошибка Claude"
        await _send_telegram_message(
            chat_id, f"Ошибка Claude: {error_text}", parse_mode=None,
        )
    else:
        await send_response(chat_id, result.text, day_number, is_final=True)

    return session_id


async def _send_to_claude_and_respond(chat_id: int, text: str) -> None:
    """Отправляет сообщение в Claude и обрабатывает ответ."""
    session_id = session_manager.get_bound_session(chat_id)
    if session_id is None:
        await _send_telegram_message(
            chat_id, MONITORING_MODE_MESSAGE, parse_mode=None
        )
        return

    if not await _ensure_process_running(chat_id, session_id):
        return

    session_watcher.pause_session(session_id)

    try:
        result = await process_manager.send_message(
            session_id, text,
            progress_callback=_on_progress, retry_callback=_on_retry,
        )
        session_id = await _handle_claude_result(chat_id, session_id, result)
    except process_manager.ProcessStoppedError:
        logger.info("Запрос прерван командой /stop: session_id=%s", session_id)
    except process_manager.ProcessNotFoundError:
        await _send_telegram_message(
            chat_id, "Процесс Claude не найден. Попробуйте /new",
            parse_mode=None,
        )
    except Exception:
        logger.error(
            "Ошибка при взаимодействии с Claude (chat_id=%d)", chat_id,
            exc_info=True,
        )
        await _send_telegram_message(
            chat_id, "Произошла ошибка. Попробуйте ещё раз",
            parse_mode=None,
        )
    finally:
        await session_watcher.resume_session(session_id)


# --- Публичные функции ---


async def send_response(
    chat_id: int,
    text: str,
    session_number: int,
    is_final: bool,
    reply_markup=None,
) -> None:
    """Форматирует и отправляет ответ Claude в Telegram."""
    # Пустой ответ или служебный маркер — заменяем на информативное сообщение
    if not text or text == NO_RESPONSE_MARKER:
        text = EMPTY_RESPONSE_TEXT

    parts = message_splitter.prepare_message(text)

    # Промежуточные обновления отображаем курсивом
    if not is_final:
        parts = [f"<i>{part}</i>" for part in parts]

    header = _format_session_header(session_number, is_final)
    parts[0] = header + parts[0]

    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        # Кнопки (reply_markup) — только к последней части
        markup = reply_markup if index == last_index else None
        await _send_telegram_message(chat_id, part, reply_markup=markup)


async def send_watcher_message(
    chat_id: int,
    text: str,
    session_id: str,
    session_number: int,
    is_final: bool,
) -> None:
    """Отправляет сообщение от watcher (ответ из другой сессии)."""
    parts = message_splitter.prepare_message(text)

    # Промежуточные обновления отображаем курсивом, как в send_response
    if not is_final:
        parts = [f"<i>{part}</i>" for part in parts]

    if _is_current_session(chat_id, session_id):
        header = _format_session_header(session_number, is_final)
    else:
        clickable = _format_clickable_session_number(session_number)
        status_icon = "\u2705" if is_final else "\u23f3"
        header = f"{clickable} {status_icon} "

    parts[0] = header + parts[0]

    for part in parts:
        await _send_telegram_message(chat_id, part)


# --- Обработчики команд ---


async def post_init(application: Application) -> None:
    """Инициализация после запуска: очистка файлов, восстановление состояния, меню команд."""
    await _clean_old_received_files()

    # Восстанавливаем привязки сессий после перезапуска
    try:
        await session_manager.load_bindings()
    except Exception:
        logger.error(
            "Ошибка при восстановлении состояния — начинаю с чистого",
            exc_info=True,
        )

    # Если реестр дневных сессий не загрузился — сообщаем пользователю
    if not daily_session_registry.is_registry_loaded():
        for chat_id in config.ALLOWED_USER_IDS:
            await _send_telegram_message(
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


async def _watcher_callback(
    chat_id: int,
    session_id: str,
    day_number: int,
    text: str,
    is_current: bool,
    is_final: bool,
) -> None:
    """Callback для session_watcher — пересылает ответ Claude из мониторинга."""
    await send_watcher_message(chat_id, text, session_id, day_number, is_final)


async def _get_current_session_async(chat_id: int) -> str | None:
    """Возвращает привязанную сессию для watcher (async-обёртка)."""
    return session_manager.get_bound_session(chat_id)


async def handle_new(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /new — создаёт новую сессию Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id

    try:
        new_result = await session_manager.create_new_session(chat_id)
        session_id = new_result.session_id
        day_number = new_result.day_number

        await _send_telegram_message(
            chat_id,
            f"Создана новая сессия #{day_number}",
            parse_mode=None,
        )
    except Exception:
        logger.error("Ошибка создания сессии (chat_id=%d)", chat_id, exc_info=True)
        await _send_telegram_message(
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
    sessions = await session_reader.get_recent_sessions(config.WORKING_DIR)

    if not sessions:
        await _send_telegram_message(chat_id, "Нет сессий", parse_mode=None)
        return

    lines: list[str] = []
    for session in sessions:
        day_number = await daily_session_registry.register_session(
            session.session_id
        )
        lines.append(f"/{day_number} {session.preview}")

    text = "\n".join(lines)
    # Отправляем без HTML, чтобы /1 /2 /3 были кликабельными командами
    await _send_telegram_message(chat_id, text, parse_mode=None)


async def handle_stop(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /stop — останавливает текущий процесс Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    session_id = session_manager.get_bound_session(chat_id)

    if session_id is None:
        await _send_telegram_message(
            chat_id,
            "Команда /stop работает только внутри сессии. "
            "Подключитесь к сессии через /sessions",
            parse_mode=None,
        )
        return

    if not process_manager.has_process(session_id):
        await _send_telegram_message(
            chat_id,
            "Claude сейчас не работает, нечего останавливать",
            parse_mode=None,
        )
        return

    await process_manager.stop_process(session_id)
    await _send_telegram_message(
        chat_id, "Claude остановлен", parse_mode=None
    )


async def handle_all(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /all — переводит в режим мониторинга."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id
    await session_manager.unbind_session(chat_id)
    await _send_telegram_message(
        chat_id,
        "Режим мониторинга всех сессий",
        parse_mode=None,
    )


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
        await _send_telegram_message(
            chat_id,
            f"Сессия #{day_number} не найдена",
            parse_mode=None,
        )
        return

    preview_text = f": {result.preview}" if result.preview else ""
    await _send_telegram_message(
        chat_id,
        f"Подключён к сессии #{day_number}{preview_text}",
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

    if session_manager.is_monitoring_mode(chat_id):
        await _send_telegram_message(
            chat_id, MONITORING_MODE_MESSAGE, parse_mode=None
        )
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    await _send_to_claude_and_respond(chat_id, text)


async def handle_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик фотографий — скачивает фото и формирует задание для Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id

    if session_manager.is_monitoring_mode(chat_id):
        await _send_telegram_message(
            chat_id, MONITORING_MODE_MESSAGE, parse_mode=None
        )
        return

    try:
        file_path = await _download_and_save_file(update)
    except Exception:
        logger.error("Ошибка скачивания фото (chat_id=%d)", chat_id, exc_info=True)
        await _send_telegram_message(
            chat_id,
            "Не удалось скачать файл. Попробуйте отправить ещё раз",
            parse_mode=None,
        )
        return

    caption = update.message.caption
    task_text = _build_file_task(file_path, caption, is_image=True)

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    await _send_to_claude_and_respond(chat_id, task_text)


async def handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик документов — скачивает файл и формирует задание для Claude."""
    if not _check_access(update):
        return

    chat_id = update.effective_chat.id

    if session_manager.is_monitoring_mode(chat_id):
        await _send_telegram_message(
            chat_id, MONITORING_MODE_MESSAGE, parse_mode=None
        )
        return

    try:
        file_path = await _download_and_save_file(update)
    except Exception:
        logger.error(
            "Ошибка скачивания документа (chat_id=%d)", chat_id, exc_info=True
        )
        await _send_telegram_message(
            chat_id,
            "Не удалось скачать файл. Попробуйте отправить ещё раз",
            parse_mode=None,
        )
        return

    caption = update.message.caption
    # Определяем тип файла: изображение или нет
    extension = Path(file_path).suffix.lstrip(".").lower()
    is_image = extension in IMAGE_EXTENSIONS
    task_text = _build_file_task(file_path, caption, is_image)

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    await _send_to_claude_and_respond(chat_id, task_text)


# --- Настройка бота ---


def _register_handlers(application: Application) -> None:
    """Регистрирует все обработчики команд и сообщений."""
    application.add_handler(CommandHandler("new", handle_new))
    application.add_handler(CommandHandler("sessions", handle_sessions))
    application.add_handler(CommandHandler("stop", handle_stop))
    application.add_handler(CommandHandler("all", handle_all))
    application.add_handler(
        MessageHandler(filters.Regex(r"^/\d+$"), handle_switch_session)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(
        MessageHandler(filters.Document.ALL, handle_document)
    )


def setup_bot() -> Application:
    """Создаёт и настраивает экземпляр Telegram-бота."""
    global _application

    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    _application = application
    _register_handlers(application)

    return application
