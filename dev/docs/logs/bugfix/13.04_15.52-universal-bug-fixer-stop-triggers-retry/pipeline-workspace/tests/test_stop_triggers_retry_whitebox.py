"""Whitebox-тесты для бага: /stop вызывает retry вместо остановки.

Тестируют три отклонения:
- DEV-1: stop_process() удаляет stop_event из _stop_events сразу после set()
- DEV-2: _restart_process() не воссоздаёт _stop_events и _busy_flags
- DEV-3: handle_stop проверяет has_process() до stop_process()
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager.claude_runner import ClaudeProcess, ClaudeProcessError
from claude_manager.process_manager import (
    ProcessStoppedError,
    SendResult,
    StopResult,
    create_process,
    has_process,
    is_busy,
    send_message,
    stop_process,
)
import claude_manager.process_manager as pm_module


# --- Вспомогательные функции ---


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
    process.stderr.read = AsyncMock(return_value=b"")
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
        raw_lines = []
        for event in events:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            raw_lines.append(line.encode("utf-8"))
        raw_lines.append(b"")
        mock_subprocess.stdout.readline = AsyncMock(side_effect=raw_lines)

    return ClaudeProcess(mock_subprocess)


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


# =============================================================================
# DEV-1: stop_event должен оставаться в _stop_events после stop_process()
# =============================================================================


class TestDev1StopEventSurvivesStopProcess:
    """DEV-1: после stop_process() retry loop должен видеть флаг отмены."""

    async def test_check_stop_requested_raises_after_stop_process(self) -> None:
        """_check_stop_requested() бросает ProcessStoppedError после stop_process().

        Проверяемый сценарий: stop_process() устанавливает флаг отмены,
        а _check_stop_requested() обнаруживает его и бросает исключение.
        До исправления: stop_process() удалял stop_event из словаря,
        и _check_stop_requested() получал None — исключение не бросалось.
        """
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            session_id = await create_process()

        await stop_process(session_id)

        # После stop_process() проверка флага отмены должна бросить исключение
        with pytest.raises(ProcessStoppedError):
            pm_module._check_stop_requested(session_id)

    async def test_wait_with_stop_check_raises_after_stop_process(self) -> None:
        """_wait_with_stop_check() прерывается после stop_process().

        Проверяемый сценарий: retry loop ждёт перед следующей попыткой
        через _wait_with_stop_check(). Если /stop был вызван до или во время
        ожидания, функция должна бросить ProcessStoppedError.
        До исправления: stop_event удалялся из словаря, и ожидание
        продолжалось до конца интервала.
        """
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            session_id = await create_process()

        await stop_process(session_id)

        # Ожидание ретрая должно немедленно прерваться
        with pytest.raises(ProcessStoppedError):
            await pm_module._wait_with_stop_check(session_id, duration_seconds=60)

    async def test_retry_loop_check_sees_stop_flag(self) -> None:
        """_retry_loop проверяет stop_event на каждой итерации — и видит его.

        Проверяемый сценарий:
        1. Retry loop запускает итерацию — первым делом вызывает _check_stop_requested()
        2. stop_event установлен через stop_process() до входа в итерацию
        3. _check_stop_requested() видит флаг и бросает ProcessStoppedError

        До исправления: stop_process() удалял stop_event из словаря,
        поэтому _check_stop_requested() получал None и не бросал исключение.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            await create_process(session_id=session_id)

        # Имитируем вызов stop_process (устанавливает флаг и удаляет записи)
        await stop_process(session_id)

        # Прямая проверка: _retry_loop вызывает _check_stop_requested
        # на каждой итерации. После stop_process флаг должен быть виден.
        with pytest.raises(ProcessStoppedError):
            pm_module._check_stop_requested(session_id)


# =============================================================================
# DEV-2: _restart_process() должен воссоздавать _stop_events и _busy_flags
# =============================================================================


class TestDev2RestartProcessCreatesControlStructures:
    """DEV-2: _restart_process() должен восстанавливать управляющие структуры."""

    async def test_restart_creates_stop_event(self) -> None:
        """После _restart_process() для session_id существует stop_event.

        Проверяемый сценарий: retry loop вызывает _restart_process()
        для перезапуска процесса. Новый процесс должен быть управляемым —
        иметь stop_event для прерывания через /stop.
        До исправления: _restart_process() записывал только _processes,
        _stop_events оставался пустым — повторный /stop не работал.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            await pm_module._restart_process(session_id)

        assert session_id in pm_module._stop_events, (
            "_restart_process() должен создать stop_event для перезапущенного процесса"
        )
        assert isinstance(pm_module._stop_events[session_id], asyncio.Event)

    async def test_restart_creates_busy_flag(self) -> None:
        """После _restart_process() для session_id существует busy_flag.

        Проверяемый сценарий: без busy_flag is_busy() всегда возвращает False,
        а send_message не может защитить процесс от конкурентного доступа.
        До исправления: _restart_process() не создавал _busy_flags.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            await pm_module._restart_process(session_id)

        assert session_id in pm_module._busy_flags, (
            "_restart_process() должен создать busy_flag для перезапущенного процесса"
        )

    async def test_restart_stop_event_works_for_subsequent_stop(self) -> None:
        """stop_process() после _restart_process() корректно устанавливает флаг.

        Проверяемый сценарий: retry loop перезапустил процесс через
        _restart_process(). Пользователь отправляет повторный /stop.
        stop_process() должен найти stop_event и установить флаг отмены.
        До исправления: stop_event не существовал — stop_process() не мог
        сигнализировать retry loop.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            await pm_module._restart_process(session_id)

        # Повторный stop_process должен работать
        result = await stop_process(session_id)

        # Процесс был найден и остановлен
        assert result.was_running is True

    async def test_invariant_processes_sync_with_control_structures(self) -> None:
        """Инвариант: каждый процесс в _processes имеет _stop_events и _busy_flags.

        Проверяемый сценарий: после _restart_process() все три словаря
        содержат одинаковые ключи. Нарушение инварианта — процесс-зомби,
        невидимый для механизмов управления.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_manager.start_process", mock_start):
            await pm_module._restart_process(session_id)

        # Все три словаря содержат одинаковые ключи
        process_keys = set(pm_module._processes.keys())
        busy_keys = set(pm_module._busy_flags.keys())
        stop_keys = set(pm_module._stop_events.keys())

        assert session_id in process_keys
        assert process_keys == busy_keys, (
            f"_processes и _busy_flags рассинхронизированы: "
            f"processes={process_keys}, busy={busy_keys}"
        )
        assert process_keys == stop_keys, (
            f"_processes и _stop_events рассинхронизированы: "
            f"processes={process_keys}, stop={stop_keys}"
        )


# =============================================================================
# DEV-3: handle_stop должен работать при retry (без guard has_process())
# =============================================================================


class TestDev3HandleStopDuringRetry:
    """DEV-3: handle_stop должен вызывать stop_process() даже при retry."""

    async def test_stop_process_called_when_busy_but_no_process(self) -> None:
        """stop_process() вызывается, когда процесс busy (retry), но has_process=False.

        Проверяемый сценарий: первый /stop удалил процесс из _processes,
        retry loop ещё не создал новый. has_process() возвращает False,
        но is_busy() — True. handle_stop должен вызвать stop_process(),
        чтобы установить флаг отмены для retry loop.
        До исправления: handle_stop проверял has_process() и при False
        показывал "нечего останавливать" — retry loop продолжал работать.
        """
        from claude_manager import bot, session_manager, process_manager

        # Эмулируем состояние: процесса нет, но busy=True (retry loop активен)
        session_id = "test-session"
        pm_module._busy_flags[session_id] = True
        pm_module._stop_events[session_id] = asyncio.Event()
        # _processes пуст — has_process() вернёт False

        mock_update = MagicMock()
        mock_update.effective_chat.id = 12345
        mock_update.effective_user.id = 12345
        mock_update.message.from_user.id = 12345

        mock_context = MagicMock()

        with (
            patch.object(session_manager, "get_bound_session", return_value=session_id),
            patch.object(bot, "_check_access", return_value=True),
            patch.object(bot, "_send_telegram_message", new_callable=AsyncMock) as mock_send,
            patch.object(process_manager, "stop_process", new_callable=AsyncMock) as mock_stop,
            patch.object(process_manager, "is_busy", return_value=True),
            patch.object(process_manager, "has_process", return_value=False),
        ):
            mock_stop.return_value = StopResult(was_running=False, was_retrying=True)
            await bot.handle_stop(mock_update, mock_context)

            # stop_process должен быть вызван, несмотря на has_process()=False
            mock_stop.assert_called_once_with(session_id)

    async def test_stop_message_confirms_retry_interrupted(self) -> None:
        """Пользователь видит подтверждение остановки при прерывании retry.

        Проверяемый сценарий: пользователь отправляет /stop во время retry.
        Бот должен подтвердить остановку, а не сказать "нечего останавливать".
        """
        from claude_manager import bot, session_manager, process_manager

        session_id = "test-session"

        mock_update = MagicMock()
        mock_update.effective_chat.id = 12345
        mock_update.effective_user.id = 12345
        mock_update.message.from_user.id = 12345

        mock_context = MagicMock()

        with (
            patch.object(session_manager, "get_bound_session", return_value=session_id),
            patch.object(bot, "_check_access", return_value=True),
            patch.object(bot, "_send_telegram_message", new_callable=AsyncMock) as mock_send,
            patch.object(process_manager, "stop_process", new_callable=AsyncMock) as mock_stop,
            patch.object(process_manager, "is_busy", return_value=True),
            patch.object(process_manager, "has_process", return_value=False),
        ):
            mock_stop.return_value = StopResult(was_running=False, was_retrying=True)
            await bot.handle_stop(mock_update, mock_context)

            # Должно быть сообщение об остановке, не "нечего останавливать"
            sent_text = mock_send.call_args[0][1]
            assert "не работает" not in sent_text.lower(), (
                "Пользователь не должен видеть 'нечего останавливать' при активном retry"
            )
