"""E2E tests for Telegram reply anchors."""

import asyncio
import re

from tests.e2e.test_client import (
    TelegramTestClient,
    build_current_session_final_response_pattern,
)


CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90
BOT_COMMAND_TIMEOUT_SECONDS = 15
PROCESS_STARTUP_SECONDS = 3
STOP_CLEANUP_SECONDS = 3


def _extract_session_number(response: str) -> str:
    """Extract #N session number from a bot response."""
    match = re.search(r"#(\d+)", response)
    assert match, f"Не найден номер сессии (#N) в ответе: {response}"
    return match.group(1)


async def test_reply_anchor_happy_path(
    telegram_client: TelegramTestClient,
) -> None:
    """Final answer replies to the accepted user message."""
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    session_number = _extract_session_number(response)

    sent = await telegram_client.send_message("Ответь одним словом: якорь")
    bot_message = await telegram_client.wait_for_regex_response_message(
        build_current_session_final_response_pattern(session_number),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    assert bot_message.reply_to_msg_id == sent.id


async def test_busy_message_does_not_steal_reply_anchor(
    telegram_client: TelegramTestClient,
) -> None:
    """A busy message must not replace the active turn reply anchor."""
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    session_number = _extract_session_number(response)

    sent_a = await telegram_client.send_message(
        "Прочитай файл src/claude_manager/bot.py и ответь числом функций."
    )
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)
    sent_b = await telegram_client.send_message("Это сообщение должно получить busy")
    busy = await telegram_client.wait_for_matching_response_message(
        "обрабатывает",
        timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )
    final_a = await telegram_client.wait_for_regex_response_message(
        build_current_session_final_response_pattern(session_number),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    assert busy.reply_to_msg_id in (None, sent_b.id)
    assert final_a.reply_to_msg_id == sent_a.id


async def test_new_request_after_stop_uses_new_reply_anchor(
    telegram_client: TelegramTestClient,
) -> None:
    """A request after /stop uses its own fresh reply anchor."""
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    session_number = _extract_session_number(response)

    sent_a = await telegram_client.send_message(
        "Прочитай файл src/claude_manager/config.py и перечисли переменные окружения."
    )
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)
    await telegram_client.send_command("/stop")
    await telegram_client.wait_for_matching_response(
        "остановлен",
        timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )
    await asyncio.sleep(STOP_CLEANUP_SECONDS)

    sent_b = await telegram_client.send_message("Скажи одним словом: после")
    final_b = await telegram_client.wait_for_regex_response_message(
        build_current_session_final_response_pattern(session_number),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    assert sent_a.id != sent_b.id
    assert final_b.reply_to_msg_id == sent_b.id
