"""Скачивание и сохранение файлов из Telegram на диск.

Содержит логику скачивания файлов (фото, документы) с retry при таймаутах,
генерацию уникальных имён файлов, очистку старых файлов. Все функции,
зависящие от Telegram Bot API, принимают bot как явный аргумент —
модуль не хранит глобальное состояние.
"""

import asyncio
import logging
import os
import random
import string
import time
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.error import TimedOut

from claude_manager import config

logger = logging.getLogger(__name__)

# Количество попыток повторного скачивания файла при TimedOut.
# Отдельно от SEND_RETRY_COUNT: у отправки сообщений и скачивания файлов
# разные профили отказов (getFile / download_to_drive vs sendMessage),
# поэтому считать их одним числом было бы неверно.
FILE_DOWNLOAD_RETRY_COUNT = 3

# Пауза между попытками скачивания файла (секунды).
# Отдельная от SEND_RETRY_DELAY_SECONDS по той же причине, что и RETRY_COUNT:
# скачивание в среднем тяжелее отправки, а перегруженный HTTP-пул
# разгружается медленнее, чем одиночный sendMessage.
FILE_DOWNLOAD_RETRY_DELAY_SECONDS = 1.5

# Имя папки для скачанных фото и документов
RECEIVED_FILES_DIR = "received_files"

# Максимальный возраст файлов в received_files/ (дни)
RECEIVED_FILES_MAX_AGE_DAYS = 7

# Формат временной метки в именах файлов
FILE_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

# Длина случайного суффикса в именах файлов (6 символов = 2 млрд вариантов)
FILE_RANDOM_SUFFIX_LENGTH = 6

# Количество секунд в одном дне (для расчёта возраста файлов)
SECONDS_PER_DAY = 86400

AUDIO_MIME_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
}


def _extension_from_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return "bin"
    return AUDIO_MIME_EXTENSIONS.get(mime_type.lower(), "bin")


def extract_file_info(update: Update) -> tuple[str, str, str | None]:
    """Извлекает file_id, расширение и оригинальное имя из сообщения Telegram."""
    if update.message.photo:
        photo_size = update.message.photo[-1]
        return photo_size.file_id, "jpg", None

    if update.message.document:
        document = update.message.document
        original_name = document.file_name
        if original_name and "." in original_name:
            extension = original_name.rsplit(".", maxsplit=1)[-1].lower()
        else:
            extension = "bin"
        return document.file_id, extension, original_name

    if update.message.voice:
        voice = update.message.voice
        return voice.file_id, _extension_from_mime_type(voice.mime_type), None

    audio = update.message.audio
    original_name = audio.file_name
    if original_name and "." in original_name:
        extension = original_name.rsplit(".", maxsplit=1)[-1].lower()
    else:
        extension = _extension_from_mime_type(audio.mime_type)
    return audio.file_id, extension, original_name


def generate_file_name(original_name: str | None, extension: str) -> str:
    """Генерирует уникальное имя файла для сохранения в received_files/."""
    timestamp = datetime.now().strftime(FILE_TIMESTAMP_FORMAT)
    alphabet = string.ascii_lowercase + string.digits
    suffix = "".join(random.choices(alphabet, k=FILE_RANDOM_SUFFIX_LENGTH))
    return f"file_{timestamp}_{suffix}.{extension}"


def is_file_expired(file_path: Path, max_age_seconds: float) -> bool:
    """Проверяет, превысил ли файл максимальный возраст."""
    file_age = time.time() - os.path.getmtime(file_path)
    return file_age > max_age_seconds


async def download_file_with_retry(telegram_file, save_path: Path) -> None:
    """Скачивает файл с повторами при TimedOut от Telegram.

    Повторяет только TimedOut — это признак перегрузки сети или HTTP-пула.
    Другие ошибки (BadRequest, OSError) пробрасываются без повтора:
    их смысл не в перегрузке, а в недоступности файла или диска,
    повторы там бесполезны.

    asyncio.CancelledError не ловится — отмена задачи должна штатно
    распространяться вверх по стеку (например, при /stop от пользователя).
    """
    for attempt in range(1, FILE_DOWNLOAD_RETRY_COUNT + 1):
        try:
            await telegram_file.download_to_drive(str(save_path))
            return
        except TimedOut:
            if attempt == FILE_DOWNLOAD_RETRY_COUNT:
                logger.error(
                    "Скачивание исчерпало %d попыток: %s",
                    FILE_DOWNLOAD_RETRY_COUNT,
                    save_path,
                )
                raise
            logger.warning(
                "TimedOut при скачивании (попытка %d/%d), "
                "повтор через %.1f с: %s",
                attempt,
                FILE_DOWNLOAD_RETRY_COUNT,
                FILE_DOWNLOAD_RETRY_DELAY_SECONDS,
                save_path,
            )
            await asyncio.sleep(FILE_DOWNLOAD_RETRY_DELAY_SECONDS)


async def download_and_save_file(update: Update, bot) -> str:
    """Скачивает файл (фото или документ) из Telegram и сохраняет на диск."""
    files_dir = Path(config.WORKING_DIR) / RECEIVED_FILES_DIR
    files_dir.mkdir(exist_ok=True)

    file_id, extension, original_name = extract_file_info(update)
    file_name = generate_file_name(original_name, extension)
    save_path = files_dir / file_name

    telegram_file = await bot.get_file(file_id)
    await download_file_with_retry(telegram_file, save_path)

    absolute_path = str(save_path.resolve())
    logger.info("Файл сохранён: %s", absolute_path)
    return absolute_path


async def clean_old_received_files() -> None:
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
            if is_file_expired(entry, max_age_seconds):
                os.remove(entry)
                deleted_count += 1
        except OSError as error:
            logger.warning("Ошибка удаления файла %s: %s", entry, error)

    if deleted_count > 0:
        logger.info("Удалено %d старых файлов из %s", deleted_count, files_dir)
