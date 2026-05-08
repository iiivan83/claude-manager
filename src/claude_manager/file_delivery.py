"""Обработка файловых маркеров и доставка файлов пользователю в Telegram.

Извлекает маркеры [SEND_FILE:path] и [SHOW_FILE:path] из ответа Claude,
отправляет файлы как вложения или рендерит текстовые файлы в чат.
Все функции принимают bot как явный аргумент — модуль не хранит состояние.
"""

import logging
from pathlib import Path

from telegram import Bot, MessageEntity

from claude_manager import file_sender, telegram_sender

logger = logging.getLogger(__name__)

# --- Константы ---

# Расширения, которые считаются изображениями
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "svg"}

# Заголовок перед содержимым файла, отправленным в чат (скрепка + имя файла)
FILE_CONTENT_HEADER_TEMPLATE = "\U0001F4CE {filename}\n\n"


# --- Публичные функции ---


def shift_entity(entity: MessageEntity, offset_delta: int) -> MessageEntity:
    """Создаёт копию MessageEntity со сдвинутым offset."""
    return MessageEntity(
        type=entity.type, offset=entity.offset + offset_delta,
        length=entity.length, url=entity.url, language=entity.language,
    )


async def send_text_file(bot: Bot, chat_id: int, file_path: str) -> None:
    """Читает текстовый файл, рендерит через telegramify-markdown и отправляет в чат."""
    content, error = file_sender.read_file_content(file_path)
    if error:
        await telegram_sender.send_telegram_message(bot, chat_id, error, parse_mode=None)
        return

    filename = Path(file_path).name
    header = FILE_CONTENT_HEADER_TEMPLATE.format(filename=filename)
    # Длина заголовка в UTF-16 code units — резерв, чтобы первый чанк + заголовок
    # не превысил лимит Telegram (4096 UTF-16 code units)
    header_utf16_length = len(header.encode("utf-16-le")) // 2

    chunks = file_sender.render_file_for_telegram(
        content, first_chunk_reserve=header_utf16_length,
    )

    for index, (text, entities) in enumerate(chunks):
        ptb_entities = file_sender.convert_entities(entities)
        # Заголовок с именем файла — только перед первым чанком
        if index == 0:
            text = header + text
            # Сдвигаем offset всех entities на длину заголовка (в UTF-16 code units)
            ptb_entities = [
                shift_entity(entity, header_utf16_length)
                for entity in ptb_entities
            ]
        await bot.send_message(
            chat_id, text, entities=ptb_entities,
        )


async def send_as_document(bot: Bot, chat_id: int, file_path: str) -> None:
    """Отправляет файл как document-вложение через Telegram send_document."""
    error = file_sender.check_binary_file(file_path)
    if error:
        await telegram_sender.send_telegram_message(bot, chat_id, error, parse_mode=None)
        return
    await bot.send_document(chat_id, document=file_path)


async def process_file_markers(bot: Bot, chat_id: int, text: str) -> str:
    """Извлекает маркеры [SEND_FILE:...], отправляет файлы как вложения, возвращает очищенный текст."""
    file_paths = file_sender.extract_file_markers(text)
    if not file_paths:
        return text

    cleaned_text = file_sender.strip_file_markers(text)

    for file_path in file_paths:
        await send_as_document(bot, chat_id, file_path)

    return cleaned_text


async def process_show_file_markers(bot: Bot, chat_id: int, text: str) -> str:
    """Извлекает маркеры [SHOW_FILE:...], показывает содержимое в чате, возвращает очищенный текст."""
    file_paths = file_sender.extract_show_file_markers(text)
    if not file_paths:
        return text

    cleaned_text = file_sender.strip_show_file_markers(text)

    for file_path in file_paths:
        if file_sender.is_text_file(file_path) and not file_sender.is_too_large_for_inline(file_path):
            await send_text_file(bot, chat_id, file_path)
        elif file_sender.is_text_file(file_path):
            await telegram_sender.send_telegram_message(
                bot,
                chat_id,
                f"Файл слишком большой для отображения в чате, отправлен как вложение: {Path(file_path).name}",
                parse_mode=None,
            )
            await send_as_document(bot, chat_id, file_path)
        else:
            await send_as_document(bot, chat_id, file_path)

    return cleaned_text
