"""Тесты модуля process_manager — управление жизненным циклом процессов Claude."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager.claude_runner import ClaudeProcess, ClaudeProcessError, ClaudeStartError
from claude_manager.process_manager import (
    MAX_RETRIES,
    PROGRESS_THROTTLE_SECONDS,
    ProcessManagerError,
    ProcessNotFoundError,
    ProcessStoppedError,
    SendResult,
    StopResult,
    _extract_progress_text,
    _extract_result_text,
    _generate_temp_session_id,
    _is_error_result,
    _should_send_progress,
    create_process,
    has_process,
    is_busy,
    send_message,
    stop_process,
    update_session_id,
)
import claude_manager.process_manager as pm_module


# --- Фикстуры ---


@pytest.fixture(autouse=True)
def reset_module_state():
    """Сбрасывает состояние модуля перед каждым тестом."""
    pm_module._processes.clear()
    pm_module._busy_flags.clear()
    pm_module._stop_events.clear()
    pm_module._temp_session_counter = 0
    yield
    pm_module._processes.clear()
    pm_module._busy_flags.clear()
    pm_module._stop_events.clear()
    pm_module._temp_session_counter = 0


def _make_mock_subprocess(pid: int = 42) -> MagicMock:
    """Создаёт фейковый asyncio subprocess."""
    process = MagicMock()
    process.pid = pid
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


def _make_claude_process(
    pid: int = 42,
    events: list[dict] | None = None,
) -> ClaudeProcess:
    """Создаёт ClaudeProcess с настраиваемыми событиями на stdout."""
    mock_subprocess = _make_mock_subprocess(pid)

    if events is not None:
        # Преобразуем события в байты для stdout.readline
        raw_lines = []
        for event in events:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            raw_lines.append(line.encode("utf-8"))
        # Добавляем пустые байты — сигнал конца потока
        raw_lines.append(b"")
        mock_subprocess.stdout.readline = AsyncMock(side_effect=raw_lines)

    return ClaudeProcess(mock_subprocess)


# --- Юнит-тесты: _generate_temp_session_id ---


def test_generate_temp_session_id_sequential():
    """Временные ID генерируются последовательно."""
    first = _generate_temp_session_id()
    second = _generate_temp_session_id()
    third = _generate_temp_session_id()

    assert first == "_new_0001"
    assert second == "_new_0002"
    assert third == "_new_0003"


# --- Юнит-тесты: _extract_result_text ---


def test_extract_result_text_success():
    """Извлечение текста из успешного result-события."""
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Файл main.py содержит точку входа",
    }

    result = _extract_result_text(event)

    assert result == "Файл main.py содержит точку входа"


def test_extract_result_text_empty():
    """Пустой result возвращает пустую строку."""
    event = {"type": "result", "result": ""}

    result = _extract_result_text(event)

    assert result == ""


def test_extract_result_text_no_response():
    """Служебный ответ фильтруется в пустую строку."""
    event = {"type": "result", "result": "No response requested."}

    result = _extract_result_text(event)

    assert result == ""


def test_extract_result_text_none():
    """None в поле result возвращает пустую строку."""
    event = {"type": "result", "result": None}

    result = _extract_result_text(event)

    assert result == ""


# --- Юнит-тесты: _extract_progress_text ---


def test_extract_progress_text_thinking():
    """Извлечение текста рассуждений из thinking-блока."""
    event = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "text": "Сначала прочитаю файл..."},
            ],
        },
    }

    result = _extract_progress_text(event)

    assert result == "Сначала прочитаю файл..."


def test_extract_progress_text_tool_use_ignored():
    """События tool_use не считаются промежуточными обновлениями."""
    event = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/tmp/test.py"},
                },
            ],
        },
    }

    result = _extract_progress_text(event)

    assert result is None


def test_extract_progress_text_non_assistant():
    """События не-assistant типа возвращают None."""
    event = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result"}],
        },
    }

    result = _extract_progress_text(event)

    assert result is None


# --- Юнит-тесты: _is_error_result ---


def test_is_error_result_true():
    """Определение ошибочного result."""
    event = {"type": "result", "is_error": True, "result": "Error: connection reset"}

    assert _is_error_result(event) is True


def test_is_error_result_false():
    """Определение успешного result."""
    event = {"type": "result", "is_error": False, "result": "Готово"}

    assert _is_error_result(event) is False


# --- Юнит-тесты: _should_send_progress ---


def test_should_send_progress_first_update():
    """Первое обновление всегда отправляется."""
    assert _should_send_progress(0.0) is True


def test_should_send_progress_too_early():
    """Обновление раньше 30 секунд не отправляется."""
    # Берём текущее время как "последнюю отправку" — прошло меньше 30 секунд
    recent_time = time.monotonic()
    assert _should_send_progress(recent_time) is False


def test_should_send_progress_after_interval():
    """Обновление после 30 секунд отправляется."""
    # Имитируем отправку 31 секунду назад
    old_time = time.monotonic() - (PROGRESS_THROTTLE_SECONDS + 1)
    assert _should_send_progress(old_time) is True


# --- Юнит-тесты: create_process ---


@patch("claude_manager.process_manager.start_process")
async def test_create_process_new_session(mock_start):
    """Создание нового процесса без resume."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    assert session_id.startswith("_new_")
    # start_process вызван с None (новая сессия)
    mock_start.assert_awaited_once_with(None)
    # Процесс сохранён в словарях
    assert pm_module._processes[session_id] is mock_process
    assert pm_module._busy_flags[session_id] is False
    assert session_id in pm_module._stop_events


@patch("claude_manager.process_manager.start_process")
async def test_create_process_with_temp_id_starts_without_resume(mock_start):
    """Процесс с временным ID (_new_XXXX) запускается без --resume."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process
    temp_id = "_new_0042"

    session_id = await create_process(session_id=temp_id)

    assert session_id == temp_id
    # start_process получает None — без --resume, хотя session_id задан
    mock_start.assert_awaited_once_with(None)
    assert pm_module._processes[temp_id] is mock_process


@patch("claude_manager.process_manager.start_process")
async def test_create_process_resume(mock_start):
    """Создание процесса с resume существующей сессии."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process
    existing_id = "84748107-a3de-4314-8c72-4c3b1b6e3605"

    session_id = await create_process(session_id=existing_id)

    assert session_id == existing_id
    mock_start.assert_awaited_once_with(existing_id)
    assert pm_module._processes[existing_id] is mock_process


@patch("claude_manager.process_manager.start_process")
async def test_create_process_claude_not_found(mock_start):
    """ProcessManagerError при отсутствии Claude CLI."""
    mock_start.side_effect = ClaudeStartError("Claude Code CLI не найден")

    with pytest.raises(ProcessManagerError, match="Не удалось запустить Claude"):
        await create_process()


# --- Юнит-тесты: send_message ---


@patch("claude_manager.process_manager.start_process")
async def test_send_message_success(mock_start):
    """Успешная отправка сообщения и получение ответа."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Привет!",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    await create_process(session_id=None)
    session_id = list(pm_module._processes.keys())[0]

    result = await send_message(session_id, "Привет")

    assert result.text == "Привет!"
    assert result.session_id == "abc-123"
    assert result.is_error is False
    assert result.retries_used == 0


@patch("claude_manager.process_manager.start_process")
async def test_send_message_with_progress(mock_start):
    """Промежуточные обновления передаются через progress_callback."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "text": "Анализирую файл..."},
                ],
            },
            "session_id": "abc-123",
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Готово",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    progress_mock = AsyncMock()

    result = await send_message(session_id, "Посмотри файл", progress_callback=progress_mock)

    # progress_callback должен быть вызван с текстом рассуждений
    progress_mock.assert_awaited_once()
    call_args = progress_mock.call_args[0]
    assert call_args[1] == "Анализирую файл..."
    assert result.text == "Готово"


# --- Юнит-тесты: stop_process ---


@patch("claude_manager.process_manager.start_process")
async def test_stop_process_running(mock_start):
    """Остановка работающего процесса."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    result = await stop_process(session_id)

    assert result.was_running is True
    assert result.was_retrying is False
    # Процесс удалён из словарей
    assert session_id not in pm_module._processes


@patch("claude_manager.process_manager.start_process")
async def test_stop_process_already_stopped(mock_start):
    """Остановка уже завершённого процесса."""
    mock_process = _make_claude_process()
    # Имитируем завершённый процесс
    mock_process.process.returncode = 0
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    result = await stop_process(session_id)

    assert result.was_running is False
    assert result.was_retrying is False


async def test_stop_nonexistent_session():
    """Остановка несуществующей сессии."""
    result = await stop_process("nonexistent")

    assert result.was_running is False
    assert result.was_retrying is False


# --- Юнит-тесты: is_busy ---


@patch("claude_manager.process_manager.start_process")
async def test_is_busy_after_completion(mock_start):
    """is_busy возвращает False после завершения запроса."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Готово",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)
    await send_message(session_id, "Привет")

    # После send_message, session_id мог обновиться на "abc-123"
    assert is_busy("abc-123") is False


def test_is_busy_nonexistent_session():
    """is_busy для несуществующей сессии возвращает False."""
    assert is_busy("nonexistent") is False


# --- Юнит-тесты: has_process ---


@patch("claude_manager.process_manager.start_process")
async def test_has_process_existing(mock_start):
    """Наличие запущенного процесса."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    assert has_process(session_id) is True


def test_has_process_nonexistent():
    """Отсутствие процесса."""
    assert has_process("nonexistent") is False


@patch("claude_manager.process_manager.start_process")
async def test_has_process_finished(mock_start):
    """has_process для завершившегося процесса возвращает False."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    # Имитируем завершение процесса
    mock_process.process.returncode = 0

    assert has_process(session_id) is False


# --- Юнит-тесты: update_session_id ---


@patch("claude_manager.process_manager.start_process")
async def test_update_session_id(mock_start):
    """Обновление ключа сессии во всех словарях."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process

    old_id = await create_process(session_id=None)

    new_id = "abc-123"
    await update_session_id(old_id, new_id)

    # Новый ключ существует
    assert pm_module._processes[new_id] is mock_process
    assert new_id in pm_module._busy_flags
    assert new_id in pm_module._stop_events

    # Старый ключ удалён
    assert old_id not in pm_module._processes
    assert old_id not in pm_module._busy_flags
    assert old_id not in pm_module._stop_events


# --- Граничные случаи ---


@patch("claude_manager.process_manager.start_process")
async def test_send_message_empty_result(mock_start):
    """Обработка пустого ответа от Claude."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)
    result = await send_message(session_id, "Привет")

    assert result.text == ""
    assert result.is_error is False


@patch("claude_manager.process_manager.start_process")
async def test_send_message_no_response_requested(mock_start):
    """Фильтрация служебного ответа."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "No response requested.",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)
    result = await send_message(session_id, "Привет")

    assert result.text == ""
    assert result.is_error is False


@patch("claude_manager.process_manager.start_process")
async def test_progress_throttle_blocks_fast_updates(mock_start):
    """Промежуточные обновления не отправляются чаще раза в 30 секунд."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "text": "Первая мысль"}],
            },
            "session_id": "abc-123",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "text": "Вторая мысль"}],
            },
            "session_id": "abc-123",
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Готово",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)
    progress_mock = AsyncMock()

    await send_message(session_id, "Привет", progress_callback=progress_mock)

    # Первое обновление отправляется, второе — нет (меньше 30 секунд)
    assert progress_mock.await_count == 1
    call_args = progress_mock.call_args_list[0][0]
    assert call_args[1] == "Первая мысль"


@patch("claude_manager.process_manager.start_process")
async def test_progress_throttle_allows_after_interval(mock_start):
    """Обновление отправляется через 30 секунд."""
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "text": "Первая мысль"}],
            },
            "session_id": "abc-123",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "text": "Вторая мысль"}],
            },
            "session_id": "abc-123",
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Готово",
            "session_id": "abc-123",
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)
    progress_mock = AsyncMock()

    # Подменяем time.monotonic, чтобы имитировать прошедшие 31 секунду
    # _should_send_progress(0.0) -> True (первый раз)
    # time.monotonic() при обновлении last_progress_time -> 1000.0
    # _should_send_progress(1000.0) -> проверяет monotonic() - 1000.0 >= 30
    #   -> monotonic() возвращает 1031.0 -> True
    # time.monotonic() при обновлении last_progress_time -> 1031.0
    monotonic_values = iter([1000.0, 1031.0, 1031.0])

    with patch("claude_manager.process_manager.time.monotonic", side_effect=monotonic_values):
        await send_message(session_id, "Привет", progress_callback=progress_mock)

    # Оба обновления должны быть отправлены
    assert progress_mock.await_count == 2


@patch("claude_manager.process_manager.start_process")
async def test_session_id_updated_from_event(mock_start):
    """Обновление session_id из потока событий (временный -> настоящий)."""
    real_uuid = "84748107-a3de-4314-8c72-4c3b1b6e3605"
    events = [
        {"type": "system", "subtype": "init", "session_id": real_uuid},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": real_uuid,
        },
    ]
    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    temp_id = await create_process(session_id=None)
    result = await send_message(temp_id, "Привет")

    assert result.session_id == real_uuid


@patch("claude_manager.process_manager.start_process")
async def test_busy_flag_cleared_on_error(mock_start):
    """Флаг занятости снимается даже при ошибке."""
    mock_process = _make_claude_process()
    # send_message бросит ClaudeProcessError при попытке отправить
    mock_process.process.stdin.write.side_effect = BrokenPipeError()
    # Процесс "завершился" — returncode не None
    mock_process.process.returncode = 1
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    # Патчим _retry_loop, чтобы не запускать реальные ретраи
    with patch.object(pm_module, "_retry_loop") as mock_retry:
        error_result = SendResult(
            text="Error", session_id=session_id, is_error=True, retries_used=MAX_RETRIES,
        )
        mock_retry.return_value = error_result
        await send_message(session_id, "Привет")

    assert is_busy(session_id) is False


# --- Тесты ошибок ---


async def test_send_message_no_process():
    """Ошибка при отправке в несуществующую сессию."""
    with pytest.raises(ProcessNotFoundError, match="nonexistent"):
        await send_message("nonexistent", "Привет")


@patch("claude_manager.process_manager.start_process")
async def test_send_message_retry_on_error(mock_start):
    """Автоматический ретрай при ошибке от Claude."""
    error_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Error: service unavailable",
            "session_id": "abc-123",
        },
    ]
    success_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Ответ после ретрая",
            "session_id": "abc-123",
        },
    ]

    initial_process = _make_claude_process(events=error_events)
    retry_process = _make_claude_process(events=success_events)

    # 1-й вызов: create_process, 2-й вызов: _restart_process в retry_loop
    mock_start.side_effect = [initial_process, retry_process]

    with patch.object(pm_module, "_wait_with_stop_check", new_callable=AsyncMock):
        session_id = await create_process(session_id=None)
        result = await send_message(session_id, "Привет")

    assert result.text == "Ответ после ретрая"
    assert result.is_error is False
    assert result.retries_used == 1


@patch("claude_manager.process_manager.start_process")
async def test_send_message_all_retries_exhausted(mock_start):
    """Исчерпание всех ретраев."""
    def make_error_events():
        return [
            {"type": "system", "subtype": "init", "session_id": "abc-123"},
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "result": "Error: service unavailable",
                "session_id": "abc-123",
            },
        ]

    # 1 (create_process) + 10 (_restart_process в каждом ретрае) = 11 процессов
    processes = [
        _make_claude_process(events=make_error_events())
        for _ in range(MAX_RETRIES + 1)
    ]
    mock_start.side_effect = processes

    with patch.object(pm_module, "_wait_with_stop_check", new_callable=AsyncMock):
        session_id = await create_process(session_id=None)
        result = await send_message(session_id, "Привет")

    assert result.is_error is True
    assert result.retries_used == MAX_RETRIES


@patch("claude_manager.process_manager.start_process")
async def test_stop_interrupts_retry_loop(mock_start):
    """Прерывание цикла ретраев командой /stop."""
    error_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Error",
            "session_id": "abc-123",
        },
    ]

    mock_process = _make_claude_process(events=error_events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    # Имитируем вызов stop_process во время _wait_with_stop_check
    async def fake_wait(sid, duration):
        raise ProcessStoppedError("Ожидание ретрая прервано командой /stop")

    with patch.object(pm_module, "_wait_with_stop_check", side_effect=fake_wait):
        with pytest.raises(ProcessStoppedError):
            await send_message(session_id, "Привет")


@patch("claude_manager.process_manager.start_process")
async def test_retry_callback_called(mock_start):
    """retry_callback вызывается перед каждой повторной попыткой."""
    error_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Error",
            "session_id": "abc-123",
        },
    ]
    success_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": "abc-123",
        },
    ]

    initial_process = _make_claude_process(events=error_events)
    retry_process = _make_claude_process(events=success_events)
    # 1-й: create_process, 2-й: _restart_process в retry_loop
    mock_start.side_effect = [initial_process, retry_process]

    retry_mock = AsyncMock()

    with patch.object(pm_module, "_wait_with_stop_check", new_callable=AsyncMock):
        session_id = await create_process(session_id=None)
        await send_message(
            session_id, "Привет", retry_callback=retry_mock,
        )

    # retry_callback вызван один раз (одна повторная попытка)
    retry_mock.assert_awaited_once()
    call_args = retry_mock.call_args[0]
    assert call_args[0] == session_id  # session_id
    assert call_args[1] == 1  # номер попытки
    assert call_args[2] == MAX_RETRIES  # максимум попыток


@patch("claude_manager.process_manager.start_process")
async def test_process_crash_during_events(mock_start):
    """Обработка неожиданного завершения процесса (нет события result)."""
    # Процесс завершается без result (stdout закрывается)
    crash_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
    ]
    success_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK после перезапуска",
            "session_id": "abc-123",
        },
    ]

    initial_process = _make_claude_process(events=crash_events)
    retry_process = _make_claude_process(events=success_events)
    # 1-й: create_process, 2-й: _restart_process в retry_loop
    mock_start.side_effect = [initial_process, retry_process]

    with patch.object(pm_module, "_wait_with_stop_check", new_callable=AsyncMock):
        session_id = await create_process(session_id=None)
        result = await send_message(session_id, "Привет")

    assert result.text == "OK после перезапуска"
    assert result.is_error is False
    assert result.retries_used == 1


@patch("claude_manager.process_manager.start_process")
async def test_broken_pipe_triggers_retry(mock_start):
    """BrokenPipeError при отправке приводит к ретраю."""
    broken_process = _make_claude_process()
    broken_process.process.stdin.write.side_effect = BrokenPipeError()

    success_events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": "abc-123",
        },
    ]
    retry_process = _make_claude_process(events=success_events)

    # 1-й: create_process (broken_pipe), 2-й: _restart_process
    mock_start.side_effect = [broken_process, retry_process]

    with patch.object(pm_module, "_wait_with_stop_check", new_callable=AsyncMock):
        session_id = await create_process(session_id=None)
        result = await send_message(session_id, "Привет")

    assert result.is_error is False
    assert result.retries_used == 1


# --- Тесты: is_busy во время обработки запроса ---


@patch("claude_manager.process_manager.start_process")
async def test_is_busy_during_request(mock_start):
    """is_busy возвращает True во время обработки запроса."""
    busy_checked = False

    # Используем события, между которыми проверяем is_busy
    events = [
        {"type": "system", "subtype": "init", "session_id": "abc-123"},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": "abc-123",
        },
    ]

    mock_process = _make_claude_process(events=events)
    mock_start.return_value = mock_process

    session_id = await create_process(session_id=None)

    # Оборачиваем _process_events, чтобы проверить is_busy внутри
    original_process_events = pm_module._process_events

    async def wrapped_process_events(*args, **kwargs):
        nonlocal busy_checked
        # Внутри обработки — флаг должен быть True
        assert is_busy(session_id) is True
        busy_checked = True
        return await original_process_events(*args, **kwargs)

    with patch.object(pm_module, "_process_events", side_effect=wrapped_process_events):
        await send_message(session_id, "Привет")

    assert busy_checked is True
