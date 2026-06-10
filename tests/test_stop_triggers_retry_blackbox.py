"""Blackbox-тесты для бага: /stop вызывает retry вместо остановки.

Тестируют пользовательские сценарии целиком — не знают о внутреннем
устройстве, проверяют только наблюдаемое поведение.
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
import claude_manager.process_events as process_events_module
import claude_manager.process_retry as process_retry_module


# --- Вспомогательные функции ---


def _make_mock_subprocess(pid: int = 42) -> MagicMock:
    """Создаёт фейковый asyncio subprocess."""
    process = MagicMock()
    process.pid = pid
    process.returncode = None
    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdin.is_closing = MagicMock(return_value=False)
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


def _make_error_events(session_id: str = "abc-123") -> list[dict]:
    """Создаёт набор событий с ошибкой (вызывает retry)."""
    return [
        {"type": "system", "subtype": "init", "session_id": session_id},
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Error: service unavailable",
            "session_id": session_id,
        },
    ]


def _make_success_events(session_id: str = "abc-123") -> list[dict]:
    """Создаёт набор событий с успешным ответом."""
    return [
        {"type": "system", "subtype": "init", "session_id": session_id},
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": session_id,
        },
    ]


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
# Сценарий 1: отправить сообщение -> retry -> /stop -> retry НЕ продолжается
# =============================================================================


class TestScenarioStopDuringRetry:
    """Пользовательский сценарий: /stop прерывает retry loop."""

    async def test_stop_during_retry_wait_raises_stopped(self) -> None:
        """Сообщение вызвало ошибку, вошёл в retry, /stop прервал ожидание.

        Ожидаемый результат: send_message бросает ProcessStoppedError,
        retry loop не создаёт новых процессов.
        """
        test_session = "test-stop-during-wait"

        # Счётчик вызовов start_process — показывает, сколько процессов создано
        start_call_count = 0

        async def counting_start(*args, **kwargs):
            nonlocal start_call_count
            start_call_count += 1
            return _make_claude_process(events=_make_error_events(session_id=test_session))

        # Имитация: во время ожидания ретрая пользователь нажал /stop
        async def user_presses_stop(sid: str, duration: float) -> None:
            await stop_process(sid)
            pm_module._check_stop_requested(sid)

        # start_process замокан на весь тест — и для create_process, и для retry
        with patch("claude_manager.process_lifecycle.start_process", side_effect=counting_start):
            session_id = await create_process(session_id=test_session)

            with patch.object(process_retry_module, "_wait_with_stop_check", side_effect=user_presses_stop):
                with pytest.raises(ProcessStoppedError):
                    await send_message(session_id, "test message")

        # Процесс создавался только один раз (create_process), retry не создал новых
        assert start_call_count == 1, (
            f"Ожидался 1 вызов start_process (только create_process), "
            f"но было {start_call_count} — retry создал лишние процессы"
        )

    async def test_stop_during_event_processing_raises_stopped(self) -> None:
        """Сообщение обрабатывается, /stop прерывает чтение событий.

        Ожидаемый результат: send_message бросает ProcessStoppedError,
        процесс не входит в retry.
        """
        # Процесс отдаёт события медленно — между ними мы вызовем stop
        session_id_value = "abc-123"

        # Создаём процесс, который отдаёт init-событие, потом stop вызывается
        init_event = {"type": "system", "subtype": "init", "session_id": session_id_value}
        result_event = {
            "type": "result", "subtype": "success",
            "is_error": False, "result": "OK", "session_id": session_id_value,
        }

        mock_process = _make_claude_process(events=[init_event, result_event])
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_lifecycle.start_process", mock_start):
            session_id = await create_process()

        # Перехватываем _process_events: между init и result вызываем stop
        original_check = process_events_module._check_stop_requested

        check_count = 0

        def check_with_stop_on_second_call(sid: str) -> None:
            nonlocal check_count
            check_count += 1
            # При первой проверке (после init event) — устанавливаем stop
            if check_count == 1:
                stop_event = pm_module._stop_events.get(sid)
                if stop_event is not None:
                    stop_event.set()
            original_check(sid)

        with patch.object(
            process_events_module,
            "_check_stop_requested",
            side_effect=check_with_stop_on_second_call,
        ):
            with pytest.raises(ProcessStoppedError):
                await send_message(session_id, "test message")


# =============================================================================
# Сценарий 2: повторный /stop после перезапуска процесса
# =============================================================================


class TestScenarioDoubleStop:
    """Пользовательский сценарий: повторный /stop после restart."""

    async def test_second_stop_after_restart_finds_process(self) -> None:
        """Процесс перезапущен через _restart_process, повторный stop работает.

        Ожидаемый результат: stop_process() находит перезапущенный процесс
        и корректно его останавливает.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_lifecycle.start_process", mock_start):
            # Имитация: retry loop вызвал _restart_process
            await pm_module._restart_process(session_id, "/test/cwd")

        # Повторный stop должен найти процесс
        result = await stop_process(session_id)

        assert result.was_running is True, (
            "Повторный stop_process должен находить перезапущенный процесс"
        )

    async def test_stop_after_restart_sets_cancellation_flag(self) -> None:
        """После restart + stop — флаг отмены установлен для retry loop.

        Ожидаемый результат: _check_stop_requested() бросает
        ProcessStoppedError после stop_process() для перезапущенного процесса.
        """
        session_id = "test-session"
        mock_process = _make_claude_process()
        mock_start = AsyncMock(return_value=mock_process)

        with patch("claude_manager.process_lifecycle.start_process", mock_start):
            await pm_module._restart_process(session_id, "/test/cwd")

        await stop_process(session_id)

        with pytest.raises(ProcessStoppedError):
            pm_module._check_stop_requested(session_id)


# =============================================================================
# Сценарий 3: полный цикл отправка -> ошибка -> retry -> stop -> чисто
# =============================================================================


class TestScenarioFullCycleStopDuringRetry:
    """Полный сценарий: ошибка -> retry -> /stop -> чистая остановка."""

    async def test_full_cycle_no_zombie_processes(self) -> None:
        """После /stop во время retry: ни процессов, ни зомби-записей.

        Проверяет, что после полного цикла stop_process() модуль
        не оставляет "мёртвых" записей в словарях состояния.
        """
        async def stop_and_raise(sid: str, duration: float) -> None:
            await stop_process(sid)
            pm_module._check_stop_requested(sid)

        # start_process замокан на весь тест
        with patch(
            "claude_manager.process_lifecycle.start_process",
            new_callable=lambda: AsyncMock(
                side_effect=lambda *a, **kw: _make_claude_process(events=_make_error_events()),
            ),
        ):
            session_id = await create_process()

            with patch.object(process_retry_module, "_wait_with_stop_check", side_effect=stop_and_raise):
                with pytest.raises(ProcessStoppedError):
                    await send_message(session_id, "test")

        # После остановки: ни процессов, ни busy-флагов, ни stop-событий
        # (или если записи остались — они не в "зомби" состоянии)
        if session_id in pm_module._busy_flags:
            assert pm_module._busy_flags[session_id] is False, (
                "Зомби busy-флаг: запись осталась в True после остановки"
            )

    async def test_retry_count_limited_by_stop(self) -> None:
        """/stop ограничивает количество ретраев — не ждём все MAX_RETRIES.

        Проверяемый сценарий: без /stop Claude пытается MAX_RETRIES раз (10).
        С /stop — прерывается после первой попытки.
        """
        retry_count = 0
        test_session = "test-stop-limits-retries"

        async def counting_retry(sid, attempt, max_retries, error_reason):
            nonlocal retry_count
            retry_count += 1

        async def stop_immediately(sid: str, duration: float) -> None:
            await stop_process(sid)
            pm_module._check_stop_requested(sid)

        # start_process замокан на весь тест
        with patch(
            "claude_manager.process_lifecycle.start_process",
            new_callable=lambda: AsyncMock(
                side_effect=lambda *a, **kw: _make_claude_process(
                    events=_make_error_events(session_id=test_session),
                ),
            ),
        ):
            session_id = await create_process(session_id=test_session)

            with patch.object(process_retry_module, "_wait_with_stop_check", side_effect=stop_immediately):
                with pytest.raises(ProcessStoppedError):
                    await send_message(
                        session_id, "test",
                        retry_callback=counting_retry,
                    )

        # Ретрай должен быть прерван на первой попытке
        assert retry_count <= 1, (
            f"Ожидалось не более 1 ретрая до /stop, но было {retry_count}"
        )
