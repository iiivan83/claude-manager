"""E2E tests for choosing Claude or Codex through /agent.

Fast tests verify the Telegram UI path and session ownership. Slow tests run real
CLI turns where the Codex integration is only observable after subprocess output.
"""

import asyncio
import re
import shutil
from pathlib import Path

import pytest

from tests.e2e.test_client import (
    TelegramTestClient,
    build_current_session_final_response_pattern,
)

BOT_COMMAND_TIMEOUT_SECONDS = 20
CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90
CODEX_RESPONSE_TIMEOUT_SECONDS = 180
CODEX_PROCESS_STARTUP_SECONDS = 3
CODEX_STOP_CLEANUP_SECONDS = 3
CODEX_DEFAULT_BINARY_PATH = Path("~/.npm-global/bin/codex").expanduser()
CODEX_LONG_RUNNING_PROMPT = (
    "Сначала выполни shell-команду sleep 45. "
    "После завершения ответь только словом: готово."
)
SESSION_NUMBER_PATTERN = re.compile(r"#(\d+)")
SESSION_LIST_CODEX_ENTRY_PATTERN_TEMPLATE = r"(?m)^/{session_number}\s+.*Codex\b"


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


def _codex_cli_is_available() -> bool:
    """Return whether a Codex CLI binary is available for live E2E turns."""
    return shutil.which("codex") is not None or CODEX_DEFAULT_BINARY_PATH.exists()


def _skip_if_codex_cli_unavailable() -> None:
    """Skip live Codex tests when this machine cannot run Codex CLI."""
    if not _codex_cli_is_available():
        pytest.skip("Codex CLI не найден — live Codex E2E пропущен")


def _first_line(response_text: str) -> str:
    """Return the first visible line of a Telegram response."""
    return response_text.splitlines()[0] if response_text.splitlines() else response_text


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


async def test_agent_reselecting_current_backend_reports_already_selected(
    telegram_client: TelegramTestClient,
) -> None:
    """/agent reports a no-op when the user clicks the already selected backend."""
    try:
        await _switch_agent_to(telegram_client, "Claude")
        await _open_agent_menu(telegram_client)
        await telegram_client.click_last_button_containing("Claude")

        response = await telegram_client.wait_for_matching_response(
            "Уже выбран",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        assert "Claude" in response
    finally:
        await _switch_agent_to(telegram_client, "Claude")


async def test_agent_switch_without_active_session_keeps_monitoring_mode(
    telegram_client: TelegramTestClient,
) -> None:
    """/agent in /all changes only future sessions and keeps text blocked."""
    try:
        await telegram_client.send_command("/all")
        await telegram_client.wait_for_matching_response(
            "Режим мониторинга",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        response = await _switch_agent_to(telegram_client, "Codex")

        assert "Теперь новые сессии" in response
        assert "Codex" in response
        assert "Текущая сессия" not in response

        await telegram_client.send_message("Текст без активной сессии")
        monitoring_response = await telegram_client.wait_for_matching_response(
            "мониторинг",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        assert "/new" in monitoring_response or "сесси" in monitoring_response.lower()
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


async def test_codex_text_turn_returns_codex_header_and_sessions_entry(
    telegram_client: TelegramTestClient,
) -> None:
    """A real Codex text turn returns a Codex header and appears in /sessions."""
    _skip_if_codex_cli_unavailable()

    try:
        await _switch_agent_to(telegram_client, "Codex")

        await telegram_client.send_command("/new")
        new_session_text = await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        session_number = _extract_session_number(new_session_text)
        assert "Codex" in new_session_text

        await telegram_client.send_message("Ответь только словом: искра")
        final_response = await telegram_client.wait_for_regex_response(
            build_current_session_final_response_pattern(session_number),
            timeout=CODEX_RESPONSE_TIMEOUT_SECONDS,
        )
        response_header = _first_line(final_response)

        assert "Codex" in response_header
        assert "Claude" not in response_header

        await telegram_client.send_command("/sessions")
        session_entry_pattern = SESSION_LIST_CODEX_ENTRY_PATTERN_TEMPLATE.format(
            session_number=re.escape(session_number),
        )
        sessions_response = await telegram_client.wait_for_regex_response(
            session_entry_pattern,
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )

        assert f"/{session_number}" in sessions_response
        assert "Codex" in sessions_response
    finally:
        await _switch_agent_to(telegram_client, "Claude")


async def test_codex_uploaded_file_uses_codex_session_header(
    telegram_client: TelegramTestClient,
    tmp_path: Path,
) -> None:
    """A file sent to a Codex-owned session is answered with a Codex header."""
    _skip_if_codex_cli_unavailable()
    uploaded_file_path = tmp_path / "codex-e2e-uploaded-note.txt"
    uploaded_file_path.write_text(
        "Кодовое слово для ответа: бирюза\n",
        encoding="utf-8",
    )

    try:
        await _switch_agent_to(telegram_client, "Codex")

        await telegram_client.send_command("/new")
        new_session_text = await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        session_number = _extract_session_number(new_session_text)
        assert "Codex" in new_session_text

        await telegram_client.send_file(
            str(uploaded_file_path),
            caption="Прочитай файл и ответь только кодовым словом.",
        )
        final_response = await telegram_client.wait_for_regex_response(
            build_current_session_final_response_pattern(session_number),
            timeout=CODEX_RESPONSE_TIMEOUT_SECONDS,
        )
        response_header = _first_line(final_response)

        assert "Codex" in response_header
        assert "Claude" not in response_header
        assert "бирюза" in final_response.lower()
    finally:
        await _switch_agent_to(telegram_client, "Claude")


async def test_existing_claude_session_keeps_backend_after_codex_selection(
    telegram_client: TelegramTestClient,
) -> None:
    """A Claude session opened by /N runs through Claude after selecting Codex."""
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

        await telegram_client.send_message("Ответь только словом: кедр")
        final_response = await telegram_client.wait_for_regex_response(
            build_current_session_final_response_pattern(session_number),
            timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
        )
        response_header = _first_line(final_response)

        assert "Claude" in response_header
        assert "Codex" not in response_header
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


async def test_active_codex_session_busy_message_and_stop_use_codex_name(
    telegram_client: TelegramTestClient,
) -> None:
    """Busy and /stop during a real Codex turn both name Codex."""
    _skip_if_codex_cli_unavailable()
    stop_confirmed = False

    try:
        await _switch_agent_to(telegram_client, "Codex")
        await telegram_client.send_command("/new")
        new_session_text = await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        assert "Codex" in new_session_text

        await telegram_client.send_message(CODEX_LONG_RUNNING_PROMPT)
        await asyncio.sleep(CODEX_PROCESS_STARTUP_SECONDS)

        await telegram_client.send_message("Второй запрос во время работы")
        busy_response = await telegram_client.wait_for_matching_response(
            "обрабатывает",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        assert "Codex" in busy_response

        await telegram_client.send_command("/stop")
        stop_response = await telegram_client.wait_for_matching_response(
            "остановлен",
            timeout=BOT_COMMAND_TIMEOUT_SECONDS,
        )
        stop_confirmed = True

        assert "Codex" in stop_response
    finally:
        if not stop_confirmed:
            await telegram_client.send_command("/stop")
            try:
                await telegram_client.wait_for_regex_response(
                    r"(?:остановлен|не работает)",
                    timeout=BOT_COMMAND_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                pass
        await asyncio.sleep(CODEX_STOP_CLEANUP_SECONDS)
        await _switch_agent_to(telegram_client, "Claude")
