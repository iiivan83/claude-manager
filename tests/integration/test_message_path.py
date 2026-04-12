"""Интеграционные тесты: путь сообщения от process_manager до message_splitter.

Проверяет цепочку:
- claude_runner.ClaudeProcess получает JSON-события
- process_manager._process_events извлекает текст ответа
- message_splitter.prepare_message конвертирует Markdown в HTML и разбивает

Все тесты мокают subprocess — не запускают настоящий Claude CLI.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import file_sender, message_splitter, process_manager
from claude_manager.claude_runner import ClaudeProcess
from claude_manager.message_splitter import TELEGRAM_MESSAGE_LIMIT


# --- Фейковые данные ---

SESSION_ID = "test-session-001"


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def _reset_process_manager_state() -> None:
    """Сбрасывает внутреннее состояние process_manager перед каждым тестом."""
    process_manager._processes.clear()
    process_manager._busy_flags.clear()
    process_manager._stop_events.clear()


def _make_fake_process(responses: list[dict]) -> MagicMock:
    """Создаёт фейковый asyncio.subprocess.Process с заготовленными ответами."""
    process = MagicMock()

    # Готовим JSON-строки для stdout
    encoded_lines: list[bytes] = []
    for response_dict in responses:
        json_line = json.dumps(response_dict, ensure_ascii=False) + "\n"
        encoded_lines.append(json_line.encode("utf-8"))
    # Пустая строка — конец потока
    encoded_lines.append(b"")

    process.stdout.readline = AsyncMock(side_effect=encoded_lines)
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.returncode = None
    process.pid = 99999
    process.terminate = MagicMock()
    process.kill = MagicMock()
    process.wait = AsyncMock(return_value=0)

    return process


# --- Тесты: полный путь сообщения ---


class TestFullMessagePath:
    """Цепочка: ClaudeProcess -> process_manager -> message_splitter."""

    @pytest.mark.asyncio()
    async def test_simple_text_response_becomes_html(self) -> None:
        """Простой текстовый ответ Claude конвертируется в HTML."""
        # Claude отвечает Markdown-текстом
        events = [
            {
                "type": "assistant",
                "session_id": SESSION_ID,
                "message": {
                    "content": [{"type": "text", "text": "Привет, **мир**!"}],
                },
            },
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": "Привет, **мир**!",
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        # process_manager обрабатывает события
        result = await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=None
        )

        assert result.text == "Привет, **мир**!"
        assert result.is_error is False

        # message_splitter конвертирует в HTML
        html_parts = message_splitter.prepare_message(result.text)
        assert len(html_parts) >= 1
        assert "<b>" in html_parts[0]
        assert "мир" in html_parts[0]

    @pytest.mark.asyncio()
    async def test_code_block_response_wrapped_in_pre(self) -> None:
        """Код в ответе Claude оборачивается в <pre><code>."""
        markdown_with_code = "Вот пример:\n\n```python\nprint('hello')\n```"
        events = [
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": markdown_with_code,
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        result = await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=None
        )

        html_parts = message_splitter.prepare_message(result.text)

        # HTML содержит блок кода
        full_html = "".join(html_parts)
        assert "<pre>" in full_html
        assert "<code" in full_html
        assert "print" in full_html

    @pytest.mark.asyncio()
    async def test_long_response_splits_into_multiple_parts(self) -> None:
        """Длинный ответ Claude разбивается на части до 4096 символов."""
        # Генерируем текст длиннее лимита Telegram
        long_text = "Абзац текста. " * 500
        events = [
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": long_text,
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        result = await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=None
        )

        html_parts = message_splitter.prepare_message(result.text)

        # Ответ разбит на несколько частей
        assert len(html_parts) > 1

        # Каждая часть не превышает лимит Telegram
        for part in html_parts:
            assert len(part) <= TELEGRAM_MESSAGE_LIMIT

    @pytest.mark.asyncio()
    async def test_empty_result_produces_empty_text(self) -> None:
        """Пустой ответ Claude -> пустая строка от process_manager."""
        events = [
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": "",
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        result = await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=None
        )

        assert result.text == ""

    @pytest.mark.asyncio()
    async def test_error_result_marked_as_error(self) -> None:
        """Ответ с is_error=True помечается как ошибка."""
        events = [
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": "Rate limit exceeded",
                "is_error": True,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        result = await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=None
        )

        assert result.is_error is True
        assert result.text == "Rate limit exceeded"


# --- Тесты: session_id обновляется из событий ---


class TestSessionIdFromEvents:
    """ClaudeProcess обновляет session_id из событий stream-json."""

    @pytest.mark.asyncio()
    async def test_session_id_extracted_from_system_event(self) -> None:
        """session_id извлекается из первого события, содержащего его."""
        real_session_id = "uuid-from-claude-111"
        events = [
            {
                "type": "system",
                "session_id": real_session_id,
                "message": "starting",
            },
            {
                "type": "result",
                "session_id": real_session_id,
                "result": "Готово",
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        result = await process_manager._process_events(
            claude_process, "temp-id", progress_callback=None
        )

        # process_manager вернул реальный session_id
        assert result.session_id == real_session_id

        # ClaudeProcess тоже обновился
        assert claude_process.session_id == real_session_id


# --- Тесты: progress_callback ---


class TestProgressCallback:
    """Промежуточные обновления (thinking) передаются через callback."""

    @pytest.mark.asyncio()
    async def test_thinking_event_triggers_progress_callback(self) -> None:
        """Событие thinking вызывает progress_callback."""
        events = [
            {
                "type": "assistant",
                "session_id": SESSION_ID,
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "Размышляю над задачей..."},
                    ],
                },
            },
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": "Готово",
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        progress_calls: list[tuple[str, str]] = []

        async def on_progress(sid: str, text: str) -> None:
            """Записывает каждый вызов progress_callback."""
            progress_calls.append((sid, text))

        await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=on_progress
        )

        # Callback был вызван хотя бы раз
        assert len(progress_calls) >= 1
        assert progress_calls[0][0] == SESSION_ID
        assert "Размышляю" in progress_calls[0][1]


# --- Тесты: No response requested ---


class TestNoResponseMarker:
    """Служебный ответ 'No response requested.' обрабатывается как пустой."""

    @pytest.mark.asyncio()
    async def test_no_response_marker_returns_empty(self) -> None:
        """Ответ 'No response requested.' конвертируется в пустую строку."""
        events = [
            {
                "type": "result",
                "session_id": SESSION_ID,
                "result": "No response requested.",
                "is_error": False,
            },
        ]

        fake_process = _make_fake_process(events)
        claude_process = ClaudeProcess(fake_process)

        result = await process_manager._process_events(
            claude_process, SESSION_ID, progress_callback=None
        )

        # process_manager возвращает пустую строку для этого маркера
        assert result.text == ""


# --- Тесты пути сообщения с файловыми маркерами ---


class TestMessagePathWithFileMarkers:
    """Интеграционный тест: ответ Claude с маркером -> file_sender -> message_splitter."""

    async def test_response_with_send_file_marker_strips_marker(self) -> None:
        """Маркер [SEND_FILE:...] вырезается из текста, путь извлекается корректно."""
        # Имитируем текст ответа Claude с маркером
        claude_response_text = "Вот файл [SEND_FILE:/tmp/readme.md] и пояснение"

        # Шаг 1: извлекаем маркеры
        markers = file_sender.extract_file_markers(claude_response_text)
        assert markers == ["/tmp/readme.md"]

        # Шаг 2: вырезаем маркеры из текста
        cleaned_text = file_sender.strip_file_markers(claude_response_text)
        assert "[SEND_FILE" not in cleaned_text
        assert "Вот файл" in cleaned_text
        assert "пояснение" in cleaned_text

        # Шаг 3: очищенный текст проходит через message_splitter без ошибок
        html_parts = message_splitter.prepare_message(cleaned_text)
        assert len(html_parts) >= 1
        # Маркера нет в HTML-выходе
        for part in html_parts:
            assert "[SEND_FILE" not in part
