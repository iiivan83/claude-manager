"""E2E tests for choosing Claude or Codex through /agent.

These tests avoid long-running CLI turns where possible. They verify the Telegram
UI path, session ownership, and stop command routing through user-visible text.
"""

import re

from tests.e2e.test_client import TelegramTestClient

BOT_COMMAND_TIMEOUT_SECONDS = 20
SESSION_NUMBER_PATTERN = re.compile(r"#(\d+)")


async def _open_agent_menu(
    telegram_client: TelegramTestClient,
) -> str:
    """Open /agent and wait for the current-agent message."""
    await telegram_client.send_command("/agent")
    return await telegram_client.wait_for_matching_response(
        "Текущий агент",
        timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )


async def _switch_agent_to(
    telegram_client: TelegramTestClient,
    backend_name: str,
) -> str:
    """Switch current agent by pressing an inline button."""
    await _open_agent_menu(telegram_client)
    await telegram_client.click_last_button_containing(backend_name)
    return await telegram_client.wait_for_regex_response(
        r"(?:Теперь новые сессии|Уже выбран)",
        timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )


def _extract_session_number(response_text: str) -> str:
    """Extract #N from a bot response."""
    match = SESSION_NUMBER_PATTERN.search(response_text)
    assert match, f"Не найден номер сессии в ответе: {response_text}"
    return match.group(1)


async def test_agent_menu_shows_current_backend_and_switches_to_codex(
    telegram_client: TelegramTestClient,
) -> None:
    """/agent shows the current backend and can switch new sessions to Codex."""
    try:
        menu_text = await _open_agent_menu(telegram_client)
        assert "Текущий агент:" in menu_text
        assert "Claude" in menu_text or "Codex" in menu_text

        switch_text = await _switch_agent_to(telegram_client, "Codex")
        assert "Codex" in switch_text
    finally:
        await _switch_agent_to(telegram_client, "Claude")


async def test_new_session_after_codex_selection_is_codex(
    telegram_client: TelegramTestClient,
) -> None:
    """/new after selecting Codex creates a Codex-owned session."""
    try:
        await _switch_agent_to(telegram_client, "Codex")

        await telegram_client.send_command("/new")
        response = await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        assert "Codex" in response
    finally:
        await _switch_agent_to(telegram_client, "Claude")


async def test_existing_claude_session_keeps_backend_after_codex_selection(
    telegram_client: TelegramTestClient,
) -> None:
    """A Claude session opened by /N remains Claude after /agent selects Codex."""
    try:
        await _switch_agent_to(telegram_client, "Claude")
        await telegram_client.send_command("/new")
        new_session_text = await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        session_number = _extract_session_number(new_session_text)
        assert "Claude" in new_session_text

        await _switch_agent_to(telegram_client, "Codex")
        await telegram_client.send_command(f"/{session_number}")
        switch_text = await telegram_client.wait_for_matching_response(
            "Подключён",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        assert f"#{session_number}" in switch_text
        assert "Claude" in switch_text
    finally:
        await _switch_agent_to(telegram_client, "Claude")


async def test_stop_uses_active_codex_backend(
    telegram_client: TelegramTestClient,
) -> None:
    """/stop text names Codex when the active session belongs to Codex."""
    try:
        await _switch_agent_to(telegram_client, "Codex")
        await telegram_client.send_command("/new")
        await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        await telegram_client.send_command("/stop")
        response = await telegram_client.wait_for_matching_response(
            "Codex",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        assert "не работает" in response or "остановлен" in response
    finally:
        await _switch_agent_to(telegram_client, "Claude")
