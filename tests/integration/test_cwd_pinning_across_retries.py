"""Интеграционный тест: cwd фиксируется в send_message и не перечитывается при ретраях.

Сценарий: пользователь отправляет сообщение в проект A, Claude возвращает ошибку,
бот начинает ретрай. Между отправкой и ретраем пользователь переключает проект на B.
Ретрай должен перезапустить Claude в директории A (оригинальный cwd), а не B.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import config, process_lifecycle, process_manager, process_retry
from claude_manager.claude_runner import ClaudeProcessError
from claude_manager.process_manager import SendResult, send_message


SESSION_ID = "test-cwd-pinning"
ORIGINAL_CWD = "/projects/alpha"
SWITCHED_CWD = "/projects/beta"


@pytest.fixture(autouse=True)
def _reset_state():
    """Сбрасывает состояние process_manager и восстанавливает config."""
    original_working_dir = config.WORKING_DIR
    process_manager._processes.clear()
    process_manager._busy_flags.clear()
    process_manager._stop_events.clear()
    yield
    process_manager._processes.clear()
    process_manager._busy_flags.clear()
    process_manager._stop_events.clear()
    config.WORKING_DIR = original_working_dir


def _make_failing_process() -> MagicMock:
    """Процесс, который падает при send_message — триггерит путь ретрая."""
    process = MagicMock()
    process.is_running.return_value = True
    process.send_message = AsyncMock(
        side_effect=ClaudeProcessError("connection lost"),
    )
    process.terminate = AsyncMock()
    return process


def _make_retry_process() -> MagicMock:
    """Процесс для ретрая — send_message проходит успешно."""
    process = MagicMock()
    process.is_running.return_value = True
    process.send_message = AsyncMock()
    process.terminate = AsyncMock()
    process.process.pid = 54321
    return process


class TestCwdPinningAcrossRetries:
    """cwd из момента send_message доходит до start_process при ретрае."""

    async def test_retry_uses_original_cwd_after_project_switch(self) -> None:
        """start_process при ретрае получает cwd из момента отправки, не текущий."""
        failing = _make_failing_process()
        process_manager._processes[SESSION_ID] = failing
        process_manager._busy_flags[SESSION_ID] = False
        process_manager._stop_events[SESSION_ID] = asyncio.Event()

        config.WORKING_DIR = ORIGINAL_CWD

        captured_cwds: list[str | None] = []
        retry_proc = _make_retry_process()

        async def fake_start_process(
            session_id: str | None = None, cwd: str | None = None,
        ) -> MagicMock:
            captured_cwds.append(cwd)
            return retry_proc

        async def fake_wait_switches_project(
            session_id: str, duration: float,
        ) -> None:
            """Имитация: пользователь переключил проект пока бот ждёт перед ретраем."""
            config.WORKING_DIR = SWITCHED_CWD

        success_result = SendResult(
            text="ok", session_id=SESSION_ID, is_error=False, retries_used=0,
        )

        with (
            patch.object(
                process_lifecycle, "start_process",
                side_effect=fake_start_process,
            ),
            patch.object(
                process_retry, "_process_events",
                new_callable=AsyncMock, return_value=success_result,
            ),
            patch.object(
                process_retry, "_wait_with_stop_check",
                side_effect=fake_wait_switches_project,
            ),
            patch.object(process_retry, "MAX_RETRIES", 1),
        ):
            result = await send_message(SESSION_ID, "hello")

        assert len(captured_cwds) == 1
        assert captured_cwds[0] == ORIGINAL_CWD
        assert config.WORKING_DIR == SWITCHED_CWD
        assert not result.is_error
