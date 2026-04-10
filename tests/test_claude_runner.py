"""Тесты модуля claude_runner — обёртки для запуска Claude Code CLI."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager.claude_runner import (
    STREAM_BUFFER_LIMIT_BYTES,
    ClaudeProcess,
    ClaudeProcessError,
    ClaudeStartError,
    _build_command_args,
    _extract_session_id_from_event,
    _parse_event,
    start_process,
)


# --- Фикстуры ---


@pytest.fixture()
def mock_subprocess():
    """Фейковый asyncio subprocess с stdin, stdout, stderr."""
    process = MagicMock()
    process.pid = 42
    process.returncode = None
    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdout = MagicMock()
    process.stdout.readline = AsyncMock(return_value=b"")
    process.stderr = MagicMock()
    process.wait = AsyncMock(return_value=0)
    process.terminate = MagicMock()
    process.kill = MagicMock()
    return process


@pytest.fixture()
def claude_process(mock_subprocess):
    """Готовый объект ClaudeProcess с фейковым subprocess внутри."""
    return ClaudeProcess(mock_subprocess)


# --- Юнит-тесты: _build_command_args ---


def test_build_command_args_new_session():
    """Аргументы CLI для новой сессии — без --resume."""
    args = _build_command_args(session_id=None)

    # Первый аргумент — путь к claude CLI (может быть полный путь)
    assert args[0].endswith("claude")
    assert "-p" in args
    assert "--output-format" in args
    assert "--dangerously-skip-permissions" in args
    assert "--resume" not in args


def test_build_command_args_resume_session():
    """Аргументы CLI для resume — содержат --resume и session_id."""
    session_id = "84748107-a3de-4314-8c72-4c3b1b6e3605"

    args = _build_command_args(session_id=session_id)

    assert "--resume" in args
    assert session_id in args
    # --resume и session_id идут последними
    resume_index = args.index("--resume")
    assert args[resume_index + 1] == session_id


# --- Юнит-тесты: _parse_event ---


def test_parse_event_valid_json():
    """Корректный JSON разбирается в словарь."""
    raw_line = '{"type": "system", "subtype": "init", "session_id": "abc-123"}'

    result = _parse_event(raw_line)

    assert result == {"type": "system", "subtype": "init", "session_id": "abc-123"}


def test_parse_event_empty_line():
    """Пустая строка возвращает None."""
    result = _parse_event("")

    assert result is None


# --- Юнит-тесты: _extract_session_id_from_event ---


def test_extract_session_id_from_system_event():
    """Извлечение session_id из события system."""
    event = {
        "type": "system",
        "subtype": "init",
        "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605",
    }

    result = _extract_session_id_from_event(event)

    assert result == "84748107-a3de-4314-8c72-4c3b1b6e3605"


def test_extract_session_id_from_result_event():
    """Извлечение session_id из события result."""
    event = {
        "type": "result",
        "subtype": "success",
        "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605",
        "result": "Привет",
    }

    result = _extract_session_id_from_event(event)

    assert result == "84748107-a3de-4314-8c72-4c3b1b6e3605"


def test_extract_session_id_missing():
    """Событие без session_id возвращает None."""
    event = {"type": "rate_limit_event", "rate_limit_info": {}}

    result = _extract_session_id_from_event(event)

    assert result is None


# --- Юнит-тесты: start_process ---


@patch("claude_manager.claude_runner.asyncio.create_subprocess_exec")
async def test_start_process_new_session(mock_exec, mock_subprocess):
    """Запуск нового процесса Claude — subprocess создаётся с правильными аргументами."""
    mock_exec.return_value = mock_subprocess

    result = await start_process(session_id=None)

    assert isinstance(result, ClaudeProcess)
    assert result.process is mock_subprocess

    # Проверяем, что subprocess создан с нужными аргументами
    call_args = mock_exec.call_args
    command_args = call_args[0]
    assert command_args[0].endswith("claude")
    assert "--resume" not in command_args


@patch("claude_manager.claude_runner.asyncio.create_subprocess_exec")
async def test_start_process_resume_session(mock_exec, mock_subprocess):
    """Запуск процесса с resume — аргументы содержат --resume и session_id."""
    mock_exec.return_value = mock_subprocess
    session_id = "84748107-a3de-4314-8c72-4c3b1b6e3605"

    await start_process(session_id=session_id)

    call_args = mock_exec.call_args
    command_args = call_args[0]
    assert "--resume" in command_args
    assert session_id in command_args


@patch("claude_manager.claude_runner.asyncio.create_subprocess_exec")
async def test_start_process_passes_increased_stream_buffer_limit(
    mock_exec, mock_subprocess,
):
    """Регрессия LimitOverrunError: subprocess получает увеличенный лимит буфера.

    Дефолт asyncio.StreamReader — 64 KB на одну строку. Длинные события
    stream-json от Claude CLI (markdown-ответы, tool_result от Read/Bash)
    превышают этот лимит и приводят к LimitOverrunError, который выглядит
    как обрыв процесса. Параметр limit= должен передаваться всегда и быть
    не меньше 16 MB.
    """
    mock_exec.return_value = mock_subprocess

    await start_process(session_id=None)

    call_kwargs = mock_exec.call_args.kwargs
    assert "limit" in call_kwargs, (
        "create_subprocess_exec вызван без параметра limit — "
        "вернётся дефолт 64 KB и снова появится LimitOverrunError"
    )
    assert call_kwargs["limit"] == STREAM_BUFFER_LIMIT_BYTES
    # Защита от случайного уменьшения константы ниже безопасного порога
    assert STREAM_BUFFER_LIMIT_BYTES >= 16 * 1024 * 1024


# --- Юнит-тесты: send_message ---


async def test_send_message_writes_json_to_stdin(claude_process, mock_subprocess):
    """send_message записывает корректный JSON в stdin."""
    text = "Посмотри файл main.py"

    await claude_process.send_message(text)

    # Проверяем, что в stdin записан правильный JSON
    written_data = mock_subprocess.stdin.write.call_args[0][0]
    written_str = written_data.decode("utf-8")
    parsed = json.loads(written_str.strip())
    assert parsed == {
        "type": "user",
        "message": {"role": "user", "content": text},
    }

    # Проверяем, что drain вызван (данные отправлены)
    mock_subprocess.stdin.drain.assert_awaited_once()


# --- Юнит-тесты: read_events ---


async def test_read_events_yields_parsed_events(claude_process, mock_subprocess):
    """read_events возвращает разобранные JSON-события."""
    system_event = {"type": "system", "subtype": "init", "session_id": "abc-123"}
    result_event = {"type": "result", "subtype": "success", "session_id": "abc-123"}

    mock_subprocess.stdout.readline = AsyncMock(side_effect=[
        (json.dumps(system_event) + "\n").encode("utf-8"),
        (json.dumps(result_event) + "\n").encode("utf-8"),
    ])

    events = []
    async for event in claude_process.read_events():
        events.append(event)

    assert len(events) == 2
    assert events[0] == system_event
    assert events[1] == result_event


async def test_read_events_sets_session_id(claude_process, mock_subprocess):
    """session_id устанавливается из первого события с session_id."""
    system_event = {"type": "system", "subtype": "init", "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"}
    result_event = {"type": "result", "subtype": "success", "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"}

    mock_subprocess.stdout.readline = AsyncMock(side_effect=[
        (json.dumps(system_event) + "\n").encode("utf-8"),
        (json.dumps(result_event) + "\n").encode("utf-8"),
    ])

    async for _event in claude_process.read_events():
        pass

    assert claude_process.session_id == "84748107-a3de-4314-8c72-4c3b1b6e3605"


# --- Юнит-тесты: terminate ---


async def test_terminate_sends_sigterm_then_waits(claude_process, mock_subprocess):
    """terminate отправляет SIGTERM и ждёт завершения."""
    await claude_process.terminate()

    mock_subprocess.terminate.assert_called_once()
    mock_subprocess.wait.assert_awaited()


# --- Юнит-тесты: is_running ---


def test_is_running_returns_true_when_active(claude_process, mock_subprocess):
    """is_running возвращает True для работающего процесса."""
    mock_subprocess.returncode = None

    assert claude_process.is_running() is True


def test_is_running_returns_false_when_finished(claude_process, mock_subprocess):
    """is_running возвращает False для завершённого процесса."""
    mock_subprocess.returncode = 0

    assert claude_process.is_running() is False


# --- Граничные случаи ---


async def test_read_events_skips_empty_lines(claude_process, mock_subprocess):
    """Пустые строки в stdout пропускаются."""
    system_event = {"type": "system", "subtype": "init", "session_id": "abc-123"}
    result_event = {"type": "result", "subtype": "success", "session_id": "abc-123"}

    mock_subprocess.stdout.readline = AsyncMock(side_effect=[
        b"\n",
        (json.dumps(system_event) + "\n").encode("utf-8"),
        b"\n",
        (json.dumps(result_event) + "\n").encode("utf-8"),
    ])

    events = []
    async for event in claude_process.read_events():
        events.append(event)

    assert len(events) == 2
    assert events[0]["type"] == "system"
    assert events[1]["type"] == "result"


async def test_read_events_stops_on_empty_bytes(claude_process, mock_subprocess):
    """Итератор завершается при закрытии stdout (пустые байты)."""
    system_event = {"type": "system", "subtype": "init", "session_id": "abc-123"}

    mock_subprocess.stdout.readline = AsyncMock(side_effect=[
        (json.dumps(system_event) + "\n").encode("utf-8"),
        b"",
    ])

    events = []
    async for event in claude_process.read_events():
        events.append(event)

    assert len(events) == 1
    assert events[0]["type"] == "system"


async def test_read_events_stops_on_result_event(claude_process, mock_subprocess):
    """Итератор завершается после события result, не читая дальше."""
    system_event = {"type": "system", "subtype": "init", "session_id": "abc-123"}
    assistant_event = {"type": "assistant", "message": {"content": [{"text": "OK"}]}, "session_id": "abc-123"}
    result_event = {"type": "result", "subtype": "success", "session_id": "abc-123"}
    # Это событие не должно быть прочитано
    next_system_event = {"type": "system", "subtype": "init", "session_id": "def-456"}

    mock_subprocess.stdout.readline = AsyncMock(side_effect=[
        (json.dumps(system_event) + "\n").encode("utf-8"),
        (json.dumps(assistant_event) + "\n").encode("utf-8"),
        (json.dumps(result_event) + "\n").encode("utf-8"),
        (json.dumps(next_system_event) + "\n").encode("utf-8"),
    ])

    events = []
    async for event in claude_process.read_events():
        events.append(event)

    assert len(events) == 3
    assert events[0]["type"] == "system"
    assert events[1]["type"] == "assistant"
    assert events[2]["type"] == "result"


async def test_send_message_with_unicode(claude_process, mock_subprocess):
    """Корректная отправка сообщений с Unicode-символами."""
    text = "Привет \U0001f30d мир"

    await claude_process.send_message(text)

    written_data = mock_subprocess.stdin.write.call_args[0][0]
    written_str = written_data.decode("utf-8")
    parsed = json.loads(written_str.strip())
    assert parsed["message"]["content"] == text


async def test_send_message_with_special_json_characters(claude_process, mock_subprocess):
    """Спецсимволы JSON в тексте сообщения экранируются."""
    text = 'Найди строку "hello" в файле'

    await claude_process.send_message(text)

    written_data = mock_subprocess.stdin.write.call_args[0][0]
    written_str = written_data.decode("utf-8")
    # Проверяем, что кавычки экранированы в JSON
    assert '\\"hello\\"' in written_str
    # И что JSON разбирается обратно корректно
    parsed = json.loads(written_str.strip())
    assert parsed["message"]["content"] == text


async def test_terminate_already_finished_process(claude_process, mock_subprocess):
    """terminate на уже завершённом процессе не вызывает ошибку."""
    mock_subprocess.returncode = 0

    await claude_process.terminate()

    mock_subprocess.terminate.assert_not_called()
    mock_subprocess.kill.assert_not_called()


async def test_session_id_updated_from_later_event(claude_process, mock_subprocess):
    """session_id не перезаписывается из последующих событий."""
    system_event = {"type": "system", "subtype": "init", "session_id": "aaa"}
    result_event = {"type": "result", "subtype": "success", "session_id": "aaa"}

    mock_subprocess.stdout.readline = AsyncMock(side_effect=[
        (json.dumps(system_event) + "\n").encode("utf-8"),
        (json.dumps(result_event) + "\n").encode("utf-8"),
    ])

    async for _event in claude_process.read_events():
        pass

    assert claude_process.session_id == "aaa"


# --- Тесты ошибок ---


@patch("claude_manager.claude_runner.asyncio.create_subprocess_exec")
async def test_start_process_cli_not_found(mock_exec):
    """ClaudeStartError при отсутствии Claude CLI в PATH."""
    mock_exec.side_effect = FileNotFoundError("No such file or directory")

    with pytest.raises(ClaudeStartError, match="Claude Code CLI не найден"):
        await start_process()


@patch("claude_manager.claude_runner.asyncio.create_subprocess_exec")
async def test_start_process_os_error(mock_exec):
    """ClaudeStartError при общей ошибке ОС."""
    mock_exec.side_effect = OSError("Permission denied")

    with pytest.raises(ClaudeStartError, match="Permission denied"):
        await start_process()


async def test_send_message_to_finished_process(claude_process, mock_subprocess):
    """ClaudeProcessError при отправке в завершённый процесс."""
    mock_subprocess.returncode = 1

    with pytest.raises(ClaudeProcessError, match="Процесс Claude уже завершился"):
        await claude_process.send_message("тест")


async def test_send_message_stdin_none(claude_process, mock_subprocess):
    """ClaudeProcessError когда stdin недоступен."""
    mock_subprocess.stdin = None

    with pytest.raises(ClaudeProcessError, match="stdin процесса Claude недоступен"):
        await claude_process.send_message("тест")


async def test_send_message_broken_pipe(claude_process, mock_subprocess):
    """ClaudeProcessError при разрыве pipe."""
    mock_subprocess.stdin.write.side_effect = BrokenPipeError()

    with pytest.raises(ClaudeProcessError, match="Не удалось записать в stdin"):
        await claude_process.send_message("тест")


def test_parse_event_invalid_json():
    """ClaudeProcessError при невалидном JSON."""
    with pytest.raises(ClaudeProcessError, match="Невалидный JSON от Claude"):
        _parse_event("это не json {{{")


async def test_terminate_sigkill_after_timeout(claude_process, mock_subprocess):
    """SIGKILL отправляется после таймаута SIGTERM."""
    # wait() бросает TimeoutError при первом вызове (через wait_for),
    # а при втором (после kill) — возвращает код завершения
    mock_subprocess.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), 0])

    await claude_process.terminate()

    mock_subprocess.terminate.assert_called_once()
    mock_subprocess.kill.assert_called_once()
    # wait вызван дважды: через wait_for и после kill
    assert mock_subprocess.wait.await_count == 2
