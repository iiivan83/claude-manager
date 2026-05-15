"""Низкоуровневая отправка сообщений в Telegram с обработкой ошибок.

Содержит retry-логику, fallback на plain text при ошибках HTML-парсинга,
обработку RetryAfter и сетевых ошибок. Все функции принимают bot как
явный аргумент — модуль не хранит глобальное состояние.
"""

import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from claude_manager import message_splitter

logger = logging.getLogger(__name__)

# Количество попыток повторной отправки при сетевых ошибках Telegram
SEND_RETRY_COUNT = 3

# Пауза между попытками повторной отправки (секунды)
SEND_RETRY_DELAY_SECONDS = 2


async def send_raw(
    bot: Bot, chat_id: int, text: str, parse_mode: str | None, reply_markup,
) -> None:
    """Вызывает Telegram API для отправки одного сообщения."""
    await bot.send_message(
        chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup,
    )


async def fallback_to_plain_text(
    bot: Bot, chat_id: int, text: str, parse_mode: str | None, reply_markup,
) -> bool:
    """Пробует отправить как plain text при HTML-ошибке. Возвращает True при успехе."""
    if parse_mode != ParseMode.HTML:
        return False
    plain_text = message_splitter.strip_html_tags(text)
    await send_raw(bot, chat_id, plain_text, parse_mode=None, reply_markup=reply_markup)
    return True


async def handle_retry_after(
    bot: Bot, chat_id: int, text: str, parse_mode: str | None, reply_markup,
    retry_after_seconds: int,
) -> None:
    """Обрабатывает RetryAfter: ждёт указанное Telegram время и повторяет."""
    logger.warning("RetryAfter от Telegram: ждём %d секунд", retry_after_seconds)
    await asyncio.sleep(retry_after_seconds)
    try:
        await send_raw(bot, chat_id, text, parse_mode, reply_markup)
    except Exception:
        logger.warning("Повторная отправка после RetryAfter не удалась", exc_info=True)


def handle_network_error(attempt: int, chat_id: int) -> bool:
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


async def send_telegram_message(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: str | None = ParseMode.HTML,
    reply_markup=None,
) -> None:
    """Отправляет одно сообщение в Telegram с обработкой ошибок."""
    for attempt in range(SEND_RETRY_COUNT):
        try:
            await send_raw(bot, chat_id, text, parse_mode, reply_markup)
            return
        except BadRequest:
            if await fallback_to_plain_text(bot, chat_id, text, parse_mode, reply_markup):
                return
            raise
        except RetryAfter as error:
            await handle_retry_after(
                bot, chat_id, text, parse_mode, reply_markup, error.retry_after,
            )
            return
        except (TimedOut, NetworkError):
            if handle_network_error(attempt, chat_id):
                await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
