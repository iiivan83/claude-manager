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
    _process_events,
    _should_send_progress,
    create_process,
    has_process,
    is_busy,
    send_message,
    stop_all_processes,
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
    yield
    pm_module._processes.clear()
    pm_module._busy_flags.clear()
    pm_module._stop_events.clear()


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


def test_generate_temp_session_id_unique():
    """Временные ID уникальны (UUID, не счётчик)."""
    first = _generate_temp_session_id()
    second = _generate_temp_session_id()
    third = _generate_temp_session_id()

    assert first.startswith("_new_")
    assert second.startswith("_new_")
    assert third.startswith("_new_")
    # Все три уникальны
    assert len({first, second, third}) == 3


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
                {"type": "thinking", "thinking": "Сначала прочитаю файл..."},
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
    temp_id = "_new_test0042abc"

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
                    {"type": "thinking", "thinking": "Анализирую файл..."},
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
                "content": [{"type": "thinking", "thinking": "Первая мысль"}],
            },
            "session_id": "abc-123",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "Вторая мысль"}],
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
                "content": [{"type": "thinking", "thinking": "Первая мысль"}],
            },
            "session_id": "abc-123",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "Вторая мысль"}],
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

    # Патчим _should_send_progress напрямую (всегда True), а не time.monotonic.
    # Причина: asyncio.wait_for (добавленный в claude_runner) тоже вызывает
    # time.monotonic() внутри, поэтому глобальный патч time.monotonic ломает asyncio.
    with patch.object(pm_module, "_should_send_progress", return_value=True):
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


# --- Тесты stop_all_processes ---


class TestStopAllProcesses:
    """Тесты массовой остановки всех процессов Claude."""

    @pytest.mark.asyncio()
    async def test_empty_returns_zero(self) -> None:
        """Пустой список процессов — возвращает 0."""
        result = await stop_all_processes()
        assert result == 0

    @pytest.mark.asyncio()
    async def test_single_process_stopped(self) -> None:
        """Один процесс — остановлен, возвращает 1, процесс удалён из _processes."""
        mock_process = _make_claude_process()
        pm_module._processes["sess-1"] = mock_process
        pm_module._busy_flags["sess-1"] = False
        pm_module._stop_events["sess-1"] = asyncio.Event()

        result = await stop_all_processes()

        assert result == 1
        assert "sess-1" not in pm_module._processes

    @pytest.mark.asyncio()
    async def test_multiple_processes_all_stopped(self) -> None:
        """Несколько процессов — все остановлены, возвращает правильный count."""
        for session_id in ["sess-1", "sess-2", "sess-3"]:
            pm_module._processes[session_id] = _make_claude_process()
            pm_module._busy_flags[session_id] = False
            pm_module._stop_events[session_id] = asyncio.Event()

        result = await stop_all_processes()

        assert result == 3
        assert len(pm_module._processes) == 0
        assert len(pm_module._busy_flags) == 0
        assert len(pm_module._stop_events) == 0

    @pytest.mark.asyncio()
    async def test_error_in_one_does_not_block_others(self) -> None:
        """Если stop_process одного процесса бросает, остальные всё равно останавливаются."""
        # Первый процесс будет падать на terminate
        bad_process = _make_claude_process(pid=1)
        bad_process.process.terminate = MagicMock(side_effect=RuntimeError("boom"))
        # terminate через await wait_for вызывает ожидание — упростим: пусть wait тоже падает
        bad_process.process.wait = AsyncMock(side_effect=RuntimeError("boom"))

        good_process = _make_claude_process(pid=2)
        pm_module._processes["sess-bad"] = bad_process
        pm_module._busy_flags["sess-bad"] = False
        pm_module._stop_events["sess-bad"] = asyncio.Event()
        pm_module._processes["sess-good"] = good_process
        pm_module._busy_flags["sess-good"] = False
        pm_module._stop_events["sess-good"] = asyncio.Event()

        result = await stop_all_processes()

        # Хороший процесс должен быть остановлен в любом случае
        assert "sess-good" not in pm_module._processes
        # Результат — минимум 1 успешно остановленный
        assert result >= 1

    @pytest.mark.asyncio()
    async def test_busy_process_stopped_correctly(self) -> None:
        """Процесс, помеченный как занятый, тоже корректно останавливается."""
        mock_process = _make_claude_process()
        pm_module._processes["sess-busy"] = mock_process
        pm_module._busy_flags["sess-busy"] = True
        pm_module._stop_events["sess-busy"] = asyncio.Event()

        result = await stop_all_processes()

        assert result == 1
        assert "sess-busy" not in pm_module._processes


# --- Тесты конкурентного доступа (Lock) ---


@patch("claude_manager.process_manager.start_process")
async def test_concurrent_two_sends_same_session(mock_start):
    """Два одновременных send_message для одной сессии — ровно один получает ошибку."""
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

    # Замедляем _process_events, чтобы первый send_message удерживал busy=True
    # пока второй пытается захватить Lock
    original_process_events = pm_module._process_events

    async def slow_process_events(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await original_process_events(*args, **kwargs)

    results = []
    errors = []

    async def safe_send(label: str):
        try:
            result = await send_message(session_id, f"Привет от {label}")
            results.append(result)
        except (ProcessManagerError, ProcessNotFoundError) as error:
            errors.append(error)

    with patch.object(pm_module, "_process_events", side_effect=slow_process_events):
        await asyncio.gather(safe_send("first"), safe_send("second"))

    # Ровно один успех и ровно одна ошибка «уже занят»
    assert len(results) + len(errors) == 2
    assert len(errors) == 1
    assert "уже занят" in str(errors[0])


@patch("claude_manager.process_manager.start_process")
async def test_concurrent_send_and_stop(mock_start):
    """Одновременный send_message + stop_process — нет зомби в _busy_flags."""
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

    # Замедляем _process_events, чтобы stop_process успел вызваться во время send_message
    original_process_events = pm_module._process_events

    async def slow_process_events(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await original_process_events(*args, **kwargs)

    send_result = None
    send_error = None

    async def do_send():
        nonlocal send_result, send_error
        try:
            send_result = await send_message(session_id, "Привет")
        except (ProcessManagerError, ProcessStoppedError, ProcessNotFoundError) as error:
            send_error = error

    async def do_stop():
        # Небольшая задержка, чтобы send_message успел захватить Lock первым
        await asyncio.sleep(0.01)
        await stop_process(session_id)

    with patch.object(pm_module, "_process_events", side_effect=slow_process_events):
        await asyncio.gather(do_send(), do_stop())

    # Главная проверка: после обоих операций — session_id НЕТ в _busy_flags (нет зомби)
    assert session_id not in pm_module._busy_flags


async def test_stop_does_not_leave_zombie_busy_flag():
    """stop_process.pop удаляет ключ — имитация finally не воскрешает зомби."""
    session_id = "zombie-test"

    # Напрямую устанавливаем состояние, как если бы send_message работал
    mock_process = _make_claude_process()
    pm_module._processes[session_id] = mock_process
    pm_module._busy_flags[session_id] = True
    pm_module._stop_events[session_id] = asyncio.Event()

    # stop_process удаляет ключ из _busy_flags через pop
    await stop_process(session_id)

    # Ключ удалён
    assert session_id not in pm_module._busy_flags

    # Имитация finally-блока send_message: проверяем наличие перед записью
    # Это повторяет логику: async with _busy_lock: if session_id in _busy_flags: ...
    async with pm_module._busy_lock:
        if session_id in pm_module._busy_flags:
            pm_module._busy_flags[session_id] = False

    # Ключ НЕ воскрес — зомби не создан
    assert session_id not in pm_module._busy_flags


@patch("claude_manager.process_manager.start_process")
async def test_update_session_id_atomic_under_lock(mock_start):
    """update_session_id переносит ключи атомарно во всех трёх словарях."""
    mock_process = _make_claude_process()
    mock_start.return_value = mock_process

    old_id = await create_process(session_id=None)
    new_id = "new-session-uuid"

    # Запоминаем оригинальные объекты до переноса
    original_process = pm_module._processes[old_id]
    original_stop_event = pm_module._stop_events[old_id]

    # Проверяем промежуточное состояние: запускаем update и конкурентную проверку
    observed_states = []

    original_update = pm_module.update_session_id

    async def check_consistency_after_update():
        # Даём update_session_id время начать
        await asyncio.sleep(0.001)
        # После update — проверяем консистентность словарей
        async with pm_module._busy_lock:
            has_old_in_processes = old_id in pm_module._processes
            has_old_in_busy = old_id in pm_module._busy_flags
            has_old_in_events = old_id in pm_module._stop_events
            has_new_in_processes = new_id in pm_module._processes
            has_new_in_busy = new_id in pm_module._busy_flags
            has_new_in_events = new_id in pm_module._stop_events
            observed_states.append({
                "old_gone": not has_old_in_processes and not has_old_in_busy and not has_old_in_events,
                "new_present": has_new_in_processes and has_new_in_busy and has_new_in_events,
            })

    await asyncio.gather(
        update_session_id(old_id, new_id),
        check_consistency_after_update(),
    )

    # Старый ключ отсутствует во всех трёх словарях
    assert old_id not in pm_module._processes
    assert old_id not in pm_module._busy_flags
    assert old_id not in pm_module._stop_events

    # Новый ключ присутствует во всех трёх словарях с правильными значениями
    assert pm_module._processes[new_id] is original_process
    assert pm_module._busy_flags[new_id] is False
    assert pm_module._stop_events[new_id] is original_stop_event

    # Конкурентная проверка: после завершения update — состояние консистентно
    if observed_states:
        state = observed_states[0]
        assert state["old_gone"] is True
        assert state["new_present"] is True


# --- Тесты session_id_callback ---


async def test_session_id_callback_called_on_new_id():
    """Callback вызывается при обнаружении нового session_id в потоке событий."""
    old_id = "temp-session-001"
    new_id = "real-uuid-abc-123"
    events = [
        {"type": "system", "subtype": "init", "session_id": new_id},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": new_id,
        },
    ]
    claude_process = _make_claude_process(events=events)
    callback_mock = AsyncMock()

    result = await _process_events(
        claude_process, old_id, progress_callback=None,
        session_id_callback=callback_mock,
    )

    callback_mock.assert_awaited_once_with(old_id, new_id)
    assert result.session_id == new_id


async def test_session_id_callback_called_once_despite_multiple_events():
    """Callback вызывается ровно один раз, даже если session_id повторяется в нескольких событиях."""
    old_id = "temp-session-002"
    new_id = "real-uuid-def-456"
    # Три события с одним и тем же новым session_id
    events = [
        {"type": "system", "subtype": "init", "session_id": new_id},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "Думаю..."}],
            },
            "session_id": new_id,
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": new_id,
        },
    ]
    claude_process = _make_claude_process(events=events)
    callback_mock = AsyncMock()

    await _process_events(
        claude_process, old_id, progress_callback=None,
        session_id_callback=callback_mock,
    )

    # Callback вызван ровно 1 раз (не 3), благодаря флагу callback_fired.
    # При ретрае идемпотентность обеспечивается модулями-потребителями callback,
    # а не флагом callback_fired (он сбрасывается при новом вызове _process_events).
    assert callback_mock.await_count == 1


async def test_session_id_callback_none_no_error():
    """При session_id_callback=None смена ID обрабатывается без ошибок (обратная совместимость)."""
    old_id = "temp-session-003"
    new_id = "real-uuid-ghi-789"
    events = [
        {"type": "system", "subtype": "init", "session_id": new_id},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": new_id,
        },
    ]
    claude_process = _make_claude_process(events=events)

    result = await _process_events(
        claude_process, old_id, progress_callback=None,
        session_id_callback=None,
    )

    assert result.session_id == new_id
    assert result.text == "OK"
    assert result.is_error is False


async def test_session_id_callback_error_does_not_break_events():
    """Ошибка в callback не прерывает чтение потока событий."""
    old_id = "temp-session-004"
    new_id = "real-uuid-jkl-012"
    events = [
        {"type": "system", "subtype": "init", "session_id": new_id},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Ответ получен",
            "session_id": new_id,
        },
    ]
    claude_process = _make_claude_process(events=events)

    # Callback бросает RuntimeError — _process_events должен перехватить и продолжить
    failing_callback = AsyncMock(side_effect=RuntimeError("Ошибка в callback"))

    result = await _process_events(
        claude_process, old_id, progress_callback=None,
        session_id_callback=failing_callback,
    )

    # События дочитаны до конца — пользователь получил ответ
    assert result.text == "Ответ получен"
    assert result.session_id == new_id
    assert result.is_error is False


async def test_session_id_callback_not_called_when_id_unchanged():
    """Callback не вызывается, если session_id в событиях совпадает с переданным."""
    session_id = "same-session-id"
    events = [
        {"type": "system", "subtype": "init", "session_id": session_id},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": session_id,
        },
    ]
    claude_process = _make_claude_process(events=events)
    callback_mock = AsyncMock()

    await _process_events(
        claude_process, session_id, progress_callback=None,
        session_id_callback=callback_mock,
    )

    callback_mock.assert_not_awaited()


@patch("claude_manager.process_manager.start_process")
async def test_send_message_passes_callback_to_execute_send(mock_start):
    """send_message пробрасывает session_id_callback через цепочку вызовов."""
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
    callback_mock = AsyncMock()

    # Мокаем _execute_send, чтобы проверить что callback передаётся
    with patch.object(pm_module, "_execute_send", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = SendResult(
            text="OK", session_id="abc-123", is_error=False, retries_used=0,
        )
        await send_message(
            session_id, "Привет", session_id_callback=callback_mock,
        )

    # Проверяем, что session_id_callback передан в _execute_send
    call_kwargs = mock_execute.call_args
    # Аргументы: session_id, text, claude_process, progress_callback, retry_callback, session_id_callback
    assert call_kwargs[0][5] is callback_mock or call_kwargs[1].get("session_id_callback") is callback_mock
