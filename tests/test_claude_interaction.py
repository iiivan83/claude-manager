"""Тесты модуля claude_interaction — взаимодействие с Claude CLI."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import (
    current_backend_registry,
    daily_session_registry,
    process_manager,
    session_manager,
    session_reader,
    session_watcher,
    unread_buffer,
)
from claude_manager.coding_agent_backend import BackendName, SessionUnreadState
from claude_manager.claude_interaction import (
    AGENT_SILENCE_TIMEOUT_SECONDS,
    EMPTY_RESPONSE_TEXT,
    MONITORING_MODE_MESSAGE,
    NO_RESPONSE_MARKER,
    agent_silence_watchdog,
    build_busy_message_if_busy,
    build_file_task,
    cancel_agent_silence_watchdog,
    ensure_process_running,
    find_session_by_number,
    handle_claude_result,
    reset_watchdog_on_progress,
    send_to_claude_and_respond,
    start_agent_silence_watchdog,
    watchdog_tasks,
)
import claude_manager.claude_interaction as ci_module
import claude_manager.config as config_module
from claude_manager.process_manager import (
    ProcessManagerError,
    ProcessNotFoundError,
    ProcessStoppedError,
    SendResult,
)
from claude_manager.session_manager import ActiveSession
from claude_manager.session_reader import SessionInfo


# --- Фикстуры ---


ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345
TEST_SESSION_ID = "abc-def-111"
TEST_SESSION_ID_2 = "abc-def-222"


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для всех тестов."""
    original_allowed = config_module.ALLOWED_USER_IDS
    original_working_dir = config_module.WORKING_DIR
    config_module.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    config_module.WORKING_DIR = "/tmp/test_working_dir"
    yield
    config_module.ALLOWED_USER_IDS = original_allowed
    config_module.WORKING_DIR = original_working_dir


@pytest.fixture(autouse=True)
def _cleanup_watchdog_tasks():
    """Очищает словарь watchdog-тасков между тестами."""
    original_tasks = watchdog_tasks.copy()
    watchdog_tasks.clear()
    yield
    for task in watchdog_tasks.values():
        if not task.done():
            task.cancel()
    watchdog_tasks.clear()
    watchdog_tasks.update(original_tasks)


@pytest.fixture(autouse=True)
def _setup_application():
    """Устанавливает фейковый Application и инициализирует callbacks."""
    import claude_manager.bot as bot_module
    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    mock_app.bot.send_chat_action = AsyncMock()
    original = bot_module._application
    bot_module._application = mock_app

    # Инициализируем callbacks для claude_interaction
    from claude_manager import claude_interaction
    claude_interaction.init_callbacks(
        send_response_module=bot_module,
        send_response_attr="send_response",
        send_telegram_message_module=bot_module,
        send_telegram_message_attr="_send_telegram_message_bridge",
    )
    yield mock_app
    bot_module._application = original




# --- Тесты задания для Claude ---


class TestBuildFileTask:
    """Тесты формирования текстовых заданий для Claude."""

    def test_build_file_task_with_caption(self) -> None:
        """Задание с подписью включает текст подписи."""
        result = build_file_task(
            "/tmp/received_files/file_20260328_143022_abc123.jpg",
            "Что здесь не так?",
            is_image=True,
        )
        assert "подписью" in result
        assert "Что здесь не так?" in result
        assert "/tmp/received_files/file_20260328_143022_abc123.jpg" in result
        assert "Прочитай файл" in result

    def test_build_file_task_image_no_caption(self) -> None:
        """Задание для фото без подписи предлагает описать содержимое."""
        result = build_file_task("/tmp/photo.jpg", None, is_image=True)
        assert "фотографию без подписи" in result
        assert "/tmp/photo.jpg" in result
        assert "опиши, что на фотографии" in result

    def test_build_file_task_document_no_caption(self) -> None:
        """Задание для документа без подписи предлагает описать содержимое."""
        result = build_file_task("/tmp/report.pdf", None, is_image=False)
        assert "файл без подписи" in result
        assert "/tmp/report.pdf" in result
        assert "опиши его содержимое" in result


# --- Тесты генерации имён файлов ---





# --- Тесты _build_busy_message_if_busy ---


class TestBuildBusyMessageIfBusy:
    """Тесты проверки занятости Claude перед отправкой нового сообщения."""

    @patch.object(process_manager, "is_busy", return_value=True)
    @patch.object(session_manager, "get_active_session")
    def test_busy_message_uses_active_backend(
        self,
        mock_get_active: MagicMock,
        mock_is_busy: MagicMock,
    ) -> None:
        """Busy-текст и проверка занятости используют backend активной сессии."""
        mock_get_active.return_value = ActiveSession(
            TEST_SESSION_ID,
            BackendName.CODEX,
        )

        result = build_busy_message_if_busy(TEST_CHAT_ID)

        mock_is_busy.assert_called_once_with(TEST_SESSION_ID, BackendName.CODEX)
        assert result is not None
        assert "Codex" in result

    @patch.object(process_manager, "is_busy", return_value=True)
    @patch.object(session_manager, "get_bound_session", return_value=TEST_SESSION_ID)
    def test_returns_busy_text_when_claude_processing(
        self,
        mock_get_bound: MagicMock,
        mock_is_busy: MagicMock,
    ) -> None:
        """Возвращает текст о занятости, если Claude обрабатывает предыдущее сообщение."""
        result = build_busy_message_if_busy(TEST_CHAT_ID)
        assert result is not None
        assert "обрабатывает" in result

    @patch.object(process_manager, "is_busy", return_value=False)
    @patch.object(session_manager, "get_bound_session", return_value=TEST_SESSION_ID)
    def test_returns_none_when_claude_free(
        self,
        mock_get_bound: MagicMock,
        mock_is_busy: MagicMock,
    ) -> None:
        """Возвращает None, если Claude свободен."""
        result = build_busy_message_if_busy(TEST_CHAT_ID)
        assert result is None

    @patch.object(session_manager, "get_bound_session", return_value=None)
    def test_returns_none_when_no_session_bound(
        self,
        mock_get_bound: MagicMock,
    ) -> None:
        """Возвращает None, если у чата нет привязанной сессии."""
        result = build_busy_message_if_busy(TEST_CHAT_ID)
        assert result is None
        # is_busy даже не вызывается — нет session_id для проверки


# --- Тесты _ensure_process_running ---





# --- Тесты _ensure_process_running ---


class TestEnsureProcessRunning:
    """Тесты создания процесса Claude при отсутствии."""

    @pytest.mark.asyncio()
    @patch.object(process_manager, "has_process", return_value=True)
    async def test_returns_true_when_process_exists(
        self,
        mock_has: MagicMock,
    ) -> None:
        """Возвращает True без создания процесса, если он уже есть."""
        result = await ensure_process_running(TEST_CHAT_ID, TEST_SESSION_ID)
        assert result is True

    @pytest.mark.asyncio()
    @patch.object(process_manager, "create_process", new_callable=AsyncMock)
    @patch.object(process_manager, "has_process", return_value=False)
    async def test_creates_process_and_returns_true_on_success(
        self,
        mock_has: MagicMock,
        mock_create: AsyncMock,
    ) -> None:
        """Создаёт процесс и возвращает True при успешном создании."""
        result = await ensure_process_running(TEST_CHAT_ID, TEST_SESSION_ID)
        assert result is True
        mock_create.assert_awaited_once_with(TEST_SESSION_ID)

    @pytest.mark.asyncio()
    @patch.object(
        process_manager, "create_process",
        new_callable=AsyncMock,
        side_effect=ProcessManagerError("Ошибка запуска"),
    )
    @patch.object(process_manager, "has_process", return_value=False)
    async def test_returns_false_and_notifies_on_creation_failure(
        self,
        mock_has: MagicMock,
        mock_create: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Возвращает False и отправляет ошибку пользователю при сбое создания."""
        result = await ensure_process_running(TEST_CHAT_ID, TEST_SESSION_ID)
        assert result is False
        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "запустить" in sent_text.lower() or "claude" in sent_text.lower()


# --- Тесты _find_session_by_number (дополнение: не найдена) ---





# --- Тесты _find_session_by_number ---


class TestFindSessionByNumber:
    """Тесты поиска сессии по дневному номеру."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "get_session_id_by_number", new_callable=AsyncMock)
    async def test_find_in_registry(
        self, mock_get: AsyncMock
    ) -> None:
        """Сессия найдена в дневном реестре."""
        mock_get.return_value = TEST_SESSION_ID


        result = await find_session_by_number(3)
        assert result == TEST_SESSION_ID

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "get_session_id_by_number", new_callable=AsyncMock)
    async def test_find_session_by_number_in_visible_sessions(
        self,
        mock_get_by_number: AsyncMock,
        mock_get_sessions: AsyncMock,
        mock_register: AsyncMock,
    ) -> None:
        """Сессия не в реестре, но среди видимых сессий."""
        # Первый вызов — не найдено, второй — найдено (после регистрации)
        mock_get_by_number.side_effect = [None, TEST_SESSION_ID]
        mock_get_sessions.return_value = [
            SessionInfo(TEST_SESSION_ID, "2026-03-30T10:00:00", "Тест"),
        ]
        mock_register.return_value = 5


        result = await find_session_by_number(5)
        assert result == TEST_SESSION_ID
        mock_register.assert_called()


# --- Тесты команды /projects ---





# --- Тесты _find_session_by_number (дополнение: не найдена) ---


class TestFindSessionByNumberNotFound:
    """Тест: сессия не найдена ни в реестре, ни среди видимых."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_reader, "get_recent_sessions", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "get_session_id_by_number", new_callable=AsyncMock)
    async def test_returns_none_when_session_not_found_anywhere(
        self,
        mock_get_by_number: AsyncMock,
        mock_get_sessions: AsyncMock,
        mock_register: AsyncMock,
    ) -> None:
        """Возвращает None, если сессия не найдена ни в реестре, ни после регистрации видимых."""
        # Оба вызова get_session_id_by_number возвращают None
        mock_get_by_number.side_effect = [None, None]
        mock_get_sessions.return_value = [
            SessionInfo("some-other-id", "2026-03-30T10:00:00", "Другая"),
        ]
        mock_register.return_value = 7

        result = await find_session_by_number(99)
        assert result is None
        # get_session_id_by_number вызвана дважды (до и после регистрации)
        assert mock_get_by_number.await_count == 2


# --- Тесты _handle_claude_result (дополнение: success и error) ---





# --- Тесты _agent_silence_watchdog ---


class TestAgentSilenceWatchdog:
    """Тесты watchdog'а разблокировки watcher при тишине stdout Claude."""

    @pytest.mark.asyncio()
    @patch.object(session_watcher, "resume_session", new_callable=AsyncMock)
    async def test_resumes_session_after_timeout(
        self,
        mock_resume: AsyncMock,
    ) -> None:
        """По истечении таймаута вызывает resume_session для разблокировки watcher."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 0.01):
            await agent_silence_watchdog(TEST_SESSION_ID)
        mock_resume.assert_awaited_once_with(TEST_SESSION_ID, BackendName.CLAUDE)

    @pytest.mark.asyncio()
    @patch.object(session_watcher, "resume_session", new_callable=AsyncMock)
    async def test_cancellation_before_timeout_does_not_resume(
        self,
        mock_resume: AsyncMock,
    ) -> None:
        """Отмена до таймаута — resume_session не вызывается."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 10):
            task = asyncio.create_task(agent_silence_watchdog(TEST_SESSION_ID))
            await asyncio.sleep(0.01)
            task.cancel()
            # Ждём завершения таска — CancelledError перехватывается внутри watchdog (return)
            await task
        mock_resume.assert_not_awaited()


# --- Тесты _start_agent_silence_watchdog ---





# --- Тесты _start_agent_silence_watchdog ---


class TestStartAgentSilenceWatchdog:
    """Тесты запуска watchdog-таска."""

    @pytest.mark.asyncio()
    async def test_creates_watchdog_task_in_registry(self) -> None:
        """Создаёт таск в словаре _watchdog_tasks по session_id."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100):
            start_agent_silence_watchdog(TEST_SESSION_ID)
        assert TEST_SESSION_ID in watchdog_tasks
        task = watchdog_tasks[TEST_SESSION_ID]
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        task.cancel()

    @pytest.mark.asyncio()
    async def test_replaces_previous_watchdog_task(self) -> None:
        """Замещает предыдущий watchdog для той же сессии, отменяя старый."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100):
            start_agent_silence_watchdog(TEST_SESSION_ID)
            first_task = watchdog_tasks[TEST_SESSION_ID]
            start_agent_silence_watchdog(TEST_SESSION_ID)
            second_task = watchdog_tasks[TEST_SESSION_ID]
        assert first_task is not second_task
        # Даём event loop обработать отмену
        await asyncio.sleep(0)
        assert first_task.cancelled()
        assert not second_task.done()
        second_task.cancel()

    @pytest.mark.asyncio()
    async def test_different_sessions_have_independent_watchdogs(self) -> None:
        """Разные session_id имеют независимые watchdog-таски."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100):
            start_agent_silence_watchdog(TEST_SESSION_ID)
            start_agent_silence_watchdog(TEST_SESSION_ID_2)
        assert TEST_SESSION_ID in watchdog_tasks
        assert TEST_SESSION_ID_2 in watchdog_tasks
        assert watchdog_tasks[TEST_SESSION_ID] is not watchdog_tasks[TEST_SESSION_ID_2]
        watchdog_tasks[TEST_SESSION_ID].cancel()
        watchdog_tasks[TEST_SESSION_ID_2].cancel()


# --- Тесты _cancel_agent_silence_watchdog ---





# --- Тесты _cancel_agent_silence_watchdog ---


class TestCancelAgentSilenceWatchdog:
    """Тесты отмены watchdog-таска."""

    @pytest.mark.asyncio()
    async def test_cancels_existing_watchdog_and_removes_from_registry(self) -> None:
        """Отменяет таск и удаляет его из словаря."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100):
            start_agent_silence_watchdog(TEST_SESSION_ID)
            task = watchdog_tasks[TEST_SESSION_ID]
        cancel_agent_silence_watchdog(TEST_SESSION_ID)
        assert TEST_SESSION_ID not in watchdog_tasks
        # Даём event loop обработать отмену
        await asyncio.sleep(0)
        assert task.cancelled()

    def test_no_error_when_no_watchdog_for_session(self) -> None:
        """Не падает, если для сессии нет watchdog-таска."""
        cancel_agent_silence_watchdog("nonexistent-session-id")
        # Просто не падает — нет исключения

    def test_no_error_when_task_already_done(self) -> None:
        """Не падает, если таск уже завершился."""
        done_task = asyncio.get_event_loop().create_future()
        done_task.set_result(None)
        watchdog_tasks[TEST_SESSION_ID] = done_task
        cancel_agent_silence_watchdog(TEST_SESSION_ID)
        assert TEST_SESSION_ID not in watchdog_tasks


# --- Тесты _reset_watchdog_on_progress ---





# --- Тесты _reset_watchdog_on_progress ---


class TestResetWatchdogOnProgress:
    """Тесты перезапуска watchdog при новом progress-событии."""

    @pytest.mark.asyncio()
    @patch.object(session_watcher, "pause_session")
    async def test_pauses_session_and_restarts_watchdog(
        self,
        mock_pause: MagicMock,
    ) -> None:
        """Progress-событие: ставит watcher обратно на паузу и перезапускает watchdog."""
        with patch("claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100):
            start_agent_silence_watchdog(TEST_SESSION_ID)
            old_task = watchdog_tasks[TEST_SESSION_ID]
            await reset_watchdog_on_progress(TEST_SESSION_ID)
        mock_pause.assert_called_once_with(TEST_SESSION_ID, BackendName.CLAUDE)
        new_task = watchdog_tasks[TEST_SESSION_ID]
        assert old_task is not new_task
        # Даём event loop обработать отмену старого таска
        await asyncio.sleep(0)
        assert old_task.cancelled()
        new_task.cancel()

    @pytest.mark.asyncio()
    @patch.object(session_watcher, "pause_session")
    async def test_ignores_progress_when_no_watchdog_for_session(
        self,
        mock_pause: MagicMock,
    ) -> None:
        """Не делает ничего, если для сессии нет watchdog (progress от другой сессии)."""
        await reset_watchdog_on_progress("unknown-session-id")
        mock_pause.assert_not_called()


# --- Тесты _send_to_claude_and_respond (полный набор) ---





# --- Тесты _send_to_claude_and_respond (полный набор) ---


class TestSendToClaudeAndRespondBehavior:
    """Тесты основного сценария отправки сообщения в Claude и обработки ответа."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.send_response", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_active_session")
    async def test_send_to_agent_uses_active_session_backend(
        self,
        mock_get_active: MagicMock,
        mock_register: AsyncMock,
        mock_send_response: AsyncMock,
    ) -> None:
        """Сообщение уходит в backend активной сессии, а не в глобальный выбор."""
        mock_get_active.return_value = ActiveSession(
            TEST_SESSION_ID,
            BackendName.CODEX,
        )
        mock_register.return_value = 1

        with patch.object(
            current_backend_registry,
            "get_current",
            return_value=BackendName.CLAUDE,
        ), patch.object(
            process_manager,
            "send_message",
            new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher,
            "pause_session",
        ) as mock_pause, patch.object(
            session_watcher,
            "resume_session",
            new_callable=AsyncMock,
        ) as mock_resume:
            mock_send.return_value = SendResult(
                text="Codex response",
                session_id=TEST_SESSION_ID,
                is_error=False,
                retries_used=0,
                backend=BackendName.CODEX,
            )

            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

        assert mock_send.call_args.kwargs["backend"] == BackendName.CODEX
        mock_pause.assert_called_once_with(TEST_SESSION_ID, BackendName.CODEX)
        mock_resume.assert_awaited_once_with(TEST_SESSION_ID, BackendName.CODEX)
        mock_send_response.assert_awaited_once()
        assert mock_send_response.call_args.args[3] == BackendName.CODEX

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.send_response", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_happy_path_sends_response(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        mock_send_response: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Happy path: сообщение отправлено, ответ доставлен пользователю."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume:
            mock_send.return_value = SendResult(
                text="Привет!", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            mock_send_response.assert_awaited_once()
            # resume_session вызван в finally
            mock_resume.assert_awaited_once_with(TEST_SESSION_ID, BackendName.CLAUDE)

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_monitoring_mode_sends_warning(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Без привязанной сессии — отправляет сообщение о режиме мониторинга."""
        mock_get_bound.return_value = None

        await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_project_changed_suppresses_delivery(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Если проект сменился во время обработки — доставка подавляется."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        async def fake_send_that_changes_project(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            # Имитируем смену проекта во время обработки
            config_module.WORKING_DIR = "/tmp/another_project"
            return SendResult(
                text="Ответ", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_that_changes_project,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume, patch(
            "claude_manager.bot.send_response", new_callable=AsyncMock,
        ) as mock_send_response:
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # Ответ Claude НЕ доставлен — проект сменился
            mock_send_response.assert_not_awaited()
            # resume_session НЕ вызван — проект уже не тот
            mock_resume.assert_not_awaited()

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_process_stopped_error_logs_silently(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """ProcessStoppedError (от /stop) — подавляется, ошибка не отправляется."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=ProcessStoppedError("Прервано"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # send_message НЕ вызывается для ошибки пользователю
            sent = _setup_application.bot.send_message
            sent.assert_not_called()

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_process_not_found_error_sends_hint(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """ProcessNotFoundError — отправляет пользователю подсказку /new."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=ProcessNotFoundError("Нет процесса"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            sent = _setup_application.bot.send_message
            sent.assert_called()
            sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
            assert "/new" in sent_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_process_manager_error_sends_busy_message(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """ProcessManagerError (процесс занят) — отправляет сообщение о занятости."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=ProcessManagerError("Занят"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            sent = _setup_application.bot.send_message
            sent.assert_called()
            sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
            assert "обрабатывает" in sent_text or "подожди" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_generic_error_sends_generic_message(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Неизвестная ошибка — отправляет общее сообщение об ошибке."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=RuntimeError("Что-то непредвиденное"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            sent = _setup_application.bot.send_message
            sent.assert_called()
            sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
            assert "ошибка" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_pause_and_resume_watcher_around_send(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Watcher приостанавливается перед отправкой и возобновляется после."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ) as mock_pause, patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume:
            mock_send.return_value = SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            mock_pause.assert_called_once_with(TEST_SESSION_ID, BackendName.CLAUDE)
            mock_resume.assert_awaited_once_with(TEST_SESSION_ID, BackendName.CLAUDE)

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_watchdog_cancelled_in_finally(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Watchdog отменяется в finally-блоке, даже при ошибке."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=RuntimeError("Бум"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ), patch(
            "claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # Watchdog должен быть отменён — нет таска в реестре
            assert TEST_SESSION_ID not in watchdog_tasks

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_ensure_process_failure_returns_early(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Backend-aware flow делегирует запуск процесса в process_manager.send_message."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=False,
        ), patch.object(
            process_manager, "create_process", new_callable=AsyncMock,
            side_effect=ProcessManagerError("Сбой"),
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            mock_send.return_value = SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            assert mock_send.call_args.kwargs["backend"] == BackendName.CLAUDE


@pytest.mark.asyncio()
@patch("claude_manager.bot.send_response", new_callable=AsyncMock)
@patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
@patch.object(session_manager, "get_bound_session")
async def test_chat_in_all_projects_mode_does_not_receive_plain_session_response_from_earlier_request(
    mock_get_bound: MagicMock,
    mock_register: AsyncMock,
    mock_send_response: AsyncMock,
) -> None:
    """All-project mode suppresses a plain session reply from an older request."""
    mock_get_bound.return_value = TEST_SESSION_ID
    mock_register.return_value = 35
    previous_seen_position = SessionUnreadState(
        raw_record_count=10,
        last_delivered_idx=3,
    )
    unread_buffer._snapshots.clear()

    try:
        with patch(
            "claude_manager.claude_interaction.all_projects_monitor.is_enabled_for_chat",
            return_value=True,
        ), patch.object(
            session_watcher,
            "get_seen_counts_snapshot",
            return_value={TEST_SESSION_ID: previous_seen_position},
        ), patch.object(
            process_manager,
            "send_message",
            new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher,
            "pause_session",
        ), patch.object(
            session_watcher,
            "resume_session",
            new_callable=AsyncMock,
        ):
            mock_send.return_value = SendResult(
                text="Ответ из старого запроса",
                session_id=TEST_SESSION_ID,
                is_error=False,
                retries_used=0,
            )

            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

        mock_send_response.assert_not_awaited()
        assert unread_buffer.restore_snapshot(
            TEST_SESSION_ID,
            BackendName.CLAUDE,
        ) == previous_seen_position
    finally:
        unread_buffer._snapshots.clear()


# --- Тесты замыканий _on_progress, _on_retry, _on_session_id_changed ---





# --- Тесты _send_to_claude_and_respond (полный набор) ---


class TestSendToClaudeAndRespondBehavior:
    """Тесты основного сценария отправки сообщения в Claude и обработки ответа."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.send_response", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_happy_path_sends_response(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        mock_send_response: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Happy path: сообщение отправлено, ответ доставлен пользователю."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume:
            mock_send.return_value = SendResult(
                text="Привет!", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            mock_send_response.assert_awaited_once()
            # resume_session вызван в finally
            mock_resume.assert_awaited_once_with(TEST_SESSION_ID, BackendName.CLAUDE)

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_monitoring_mode_sends_warning(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Без привязанной сессии — отправляет сообщение о режиме мониторинга."""
        mock_get_bound.return_value = None

        await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "мониторинг" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_project_changed_suppresses_delivery(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Если проект сменился во время обработки — доставка подавляется."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        async def fake_send_that_changes_project(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            # Имитируем смену проекта во время обработки
            config_module.WORKING_DIR = "/tmp/another_project"
            return SendResult(
                text="Ответ", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_that_changes_project,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume, patch(
            "claude_manager.bot.send_response", new_callable=AsyncMock,
        ) as mock_send_response:
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # Ответ Claude НЕ доставлен — проект сменился
            mock_send_response.assert_not_awaited()
            # resume_session НЕ вызван — проект уже не тот
            mock_resume.assert_not_awaited()

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_process_stopped_error_logs_silently(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """ProcessStoppedError (от /stop) — подавляется, ошибка не отправляется."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=ProcessStoppedError("Прервано"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # send_message НЕ вызывается для ошибки пользователю
            sent = _setup_application.bot.send_message
            sent.assert_not_called()

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_process_not_found_error_sends_hint(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """ProcessNotFoundError — отправляет пользователю подсказку /new."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=ProcessNotFoundError("Нет процесса"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            sent = _setup_application.bot.send_message
            sent.assert_called()
            sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
            assert "/new" in sent_text

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_process_manager_error_sends_busy_message(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """ProcessManagerError (процесс занят) — отправляет сообщение о занятости."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=ProcessManagerError("Занят"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            sent = _setup_application.bot.send_message
            sent.assert_called()
            sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
            assert "обрабатывает" in sent_text or "подожди" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_generic_error_sends_generic_message(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Неизвестная ошибка — отправляет общее сообщение об ошибке."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=RuntimeError("Что-то непредвиденное"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            sent = _setup_application.bot.send_message
            sent.assert_called()
            sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
            assert "ошибка" in sent_text.lower()

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_pause_and_resume_watcher_around_send(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Watcher приостанавливается перед отправкой и возобновляется после."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ) as mock_pause, patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume:
            mock_send.return_value = SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            mock_pause.assert_called_once_with(TEST_SESSION_ID, BackendName.CLAUDE)
            mock_resume.assert_awaited_once_with(TEST_SESSION_ID, BackendName.CLAUDE)

    @pytest.mark.asyncio()
    @patch.object(session_manager, "get_bound_session")
    async def test_watchdog_cancelled_in_finally(
        self,
        mock_get_bound: MagicMock,
        _setup_application: MagicMock,
    ) -> None:
        """Watchdog отменяется в finally-блоке, даже при ошибке."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
            side_effect=RuntimeError("Бум"),
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ), patch(
            "claude_manager.claude_interaction.AGENT_SILENCE_TIMEOUT_SECONDS", 100,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # Watchdog должен быть отменён — нет таска в реестре
            assert TEST_SESSION_ID not in watchdog_tasks

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_ensure_process_failure_returns_early(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Backend-aware flow делегирует запуск процесса в process_manager.send_message."""
        mock_get_bound.return_value = TEST_SESSION_ID

        with patch.object(
            process_manager, "has_process", return_value=False,
        ), patch.object(
            process_manager, "create_process", new_callable=AsyncMock,
            side_effect=ProcessManagerError("Сбой"),
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            mock_send.return_value = SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            assert mock_send.call_args.kwargs["backend"] == BackendName.CLAUDE


# --- Тесты замыканий _on_progress, _on_retry, _on_session_id_changed ---





# --- Тесты замыканий _on_progress, _on_retry, _on_session_id_changed ---


class TestSendToClaudeClosures:
    """Тесты замыканий callback'ов внутри _send_to_claude_and_respond."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.send_response", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_on_progress_callback_sends_non_final_response(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        mock_send_response: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Замыкание _on_progress отправляет промежуточное обновление с is_final=False."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 2

        async def fake_send_message(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            # Вызываем progress callback
            if progress_callback:
                await progress_callback(session_id, "Думаю...")
            return SendResult(
                text="Готово", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_message,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # Проверяем, что send_response вызывался с is_final=False (progress)
            progress_calls = [
                c for c in mock_send_response.await_args_list
                if c.kwargs.get("is_final") is False or (len(c.args) >= 4 and c.args[3] is False)
            ]
            assert len(progress_calls) >= 1

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_on_retry_callback_sends_retry_notification(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Замыкание _on_retry отправляет уведомление о ретрае с номером попытки."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 3

        async def fake_send_with_retry(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            if retry_callback:
                await retry_callback(session_id, 1, 3, "Connection reset")
            return SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=1,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_with_retry,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # Ищем вызов send_message с уведомлением о ретрае
            sent = _setup_application.bot.send_message
            retry_calls = [
                c for c in sent.call_args_list
                if "повтор" in str(c).lower() or "1/3" in str(c)
            ]
            assert len(retry_calls) >= 1

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "update_session_id", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_on_session_id_changed_callback_when_project_changed(
        self,
        mock_get_bound: MagicMock,
        mock_sm_update: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """При смене проекта — callback НЕ обновляет session_manager и session_watcher."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1
        new_id = "real-uuid-after-switch"

        async def fake_send_with_project_switch(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            # Имитируем: callback вызывается, но проект уже сменился
            config_module.WORKING_DIR = "/tmp/switched_project"
            if session_id_callback:
                await session_id_callback(TEST_SESSION_ID, new_id)
            return SendResult(
                text="OK", session_id=new_id, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_with_project_switch,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "update_session_id",
        ) as mock_watcher_update, patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # session_watcher.update_session_id НЕ вызван — проект сменился
            mock_watcher_update.assert_not_called()
            # session_manager.update_session_id НЕ вызван — проект сменился
            mock_sm_update.assert_not_awaited()

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_on_progress_suppressed_when_project_changed(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Progress callback подавляется, если проект сменился."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        async def fake_send_with_progress_after_switch(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            config_module.WORKING_DIR = "/tmp/switched"
            if progress_callback:
                await progress_callback(session_id, "Промежуточный текст")
            return SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_with_progress_after_switch,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ), patch(
            "claude_manager.bot.send_response", new_callable=AsyncMock,
        ) as mock_send_response:
            await send_to_claude_and_respond(TEST_CHAT_ID, "Тест")

            # send_response НЕ вызван — ни progress, ни финальный
            mock_send_response.assert_not_awaited()


# --- Тесты констант claude-interaction ---





# --- Тесты session_id_callback (раннее обновление привязок) ---


class TestSessionIdCallback:
    """Тесты механизма раннего уведомления о смене session_id через callback."""

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_send_to_claude_passes_session_id_callback(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """_send_to_claude_and_respond передаёт session_id_callback в process_manager.send_message."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", new_callable=AsyncMock,
        ) as mock_send, patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            mock_send.return_value = SendResult(
                text="OK", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
            )
            await send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # Проверяем, что session_id_callback передан и является callable
            call_kwargs = mock_send.call_args
            assert "session_id_callback" in call_kwargs.kwargs
            assert callable(call_kwargs.kwargs["session_id_callback"])

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "update_session_id", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_on_session_id_changed_updates_watcher_and_session_manager(
        self,
        mock_get_bound: MagicMock,
        mock_sm_update: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Callback _on_session_id_changed вызывает update_session_id в watcher и session_manager."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1
        new_session_id = "real-uuid-xyz-789"

        # Мокаем send_message так, чтобы он захватил и вызвал session_id_callback
        async def fake_send_message(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            # Вызываем callback, имитируя обнаружение нового session_id
            if session_id_callback is not None:
                await session_id_callback(TEST_SESSION_ID, new_session_id)
            return SendResult(
                text="OK", session_id=new_session_id, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_message,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "update_session_id",
        ) as mock_watcher_update, patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ):
            await send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # session_watcher.update_session_id вызван с правильными аргументами
            mock_watcher_update.assert_called_once_with(
                TEST_SESSION_ID,
                new_session_id,
                BackendName.CLAUDE,
            )
            # session_manager.update_session_id вызван с chat_id и обоими ID
            mock_sm_update.assert_awaited_once_with(
                TEST_CHAT_ID, TEST_SESSION_ID, new_session_id,
            )

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "update_session_id", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_resume_uses_updated_session_id_after_callback(
        self,
        mock_get_bound: MagicMock,
        mock_sm_update: AsyncMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """После callback обновления, finally вызывает resume_session с НОВЫМ session_id."""
        mock_get_bound.return_value = TEST_SESSION_ID
        mock_register.return_value = 1
        new_session_id = "real-uuid-resume-test"

        async def fake_send_message(
            session_id, text, progress_callback=None,
            retry_callback=None, session_id_callback=None, **_kwargs,
        ):
            if session_id_callback is not None:
                await session_id_callback(TEST_SESSION_ID, new_session_id)
            return SendResult(
                text="OK", session_id=new_session_id, is_error=False, retries_used=0,
            )

        with patch.object(
            process_manager, "has_process", return_value=True,
        ), patch.object(
            process_manager, "send_message", side_effect=fake_send_message,
        ), patch.object(
            session_watcher, "pause_session",
        ), patch.object(
            session_watcher, "update_session_id",
        ), patch.object(
            session_watcher, "resume_session", new_callable=AsyncMock,
        ) as mock_resume:
            await send_to_claude_and_respond(TEST_CHAT_ID, "Привет")

            # resume_session вызван с НОВЫМ session_id, а не со старым
            mock_resume.assert_awaited_once_with(
                new_session_id,
                BackendName.CLAUDE,
            )

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    @patch.object(session_manager, "get_bound_session")
    async def test_handle_claude_result_no_longer_calls_update_session_id(
        self,
        mock_get_bound: MagicMock,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """_handle_claude_result не вызывает update_session_id — обновление теперь через callback."""
        mock_register.return_value = 1
        different_session_id = "different-uuid-999"

        # Результат с session_id, отличающимся от переданного
        result = SendResult(
            text="OK", session_id=different_session_id, is_error=False, retries_used=0,
        )

        with patch.object(
            session_manager, "update_session_id", new_callable=AsyncMock,
        ) as mock_sm_update, patch.object(
            session_watcher, "update_session_id",
        ) as mock_watcher_update:
            returned_id = await handle_claude_result(
                TEST_CHAT_ID, TEST_SESSION_ID, result,
            )

            # update_session_id НЕ вызывается в _handle_claude_result
            mock_sm_update.assert_not_awaited()
            mock_watcher_update.assert_not_called()
            # Возвращает actual_session_id из результата
            assert returned_id == different_session_id


# --- Тесты _build_photo_group_task ---





# --- Тесты _handle_claude_result (дополнение: success и error) ---


class TestHandleClaudeResultBehavior:
    """Тесты обработки результата от Claude: успешный ответ и ошибка."""

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.send_response", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    async def test_success_result_sends_response_with_final_flag(
        self,
        mock_register: AsyncMock,
        mock_send_response: AsyncMock,
    ) -> None:
        """Успешный результат отправляется через send_response с is_final=True."""
        mock_register.return_value = 3
        result = SendResult(
            text="Ответ Claude", session_id=TEST_SESSION_ID, is_error=False, retries_used=0,
        )

        returned_id = await handle_claude_result(TEST_CHAT_ID, TEST_SESSION_ID, result)

        assert returned_id == TEST_SESSION_ID
        mock_send_response.assert_awaited_once_with(
            TEST_CHAT_ID,
            "Ответ Claude",
            3,
            BackendName.CLAUDE,
            is_final=True,
        )

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    async def test_error_result_sends_error_message(
        self,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Результат с is_error=True отправляется как текст ошибки."""
        mock_register.return_value = 2
        result = SendResult(
            text="Процесс упал", session_id=TEST_SESSION_ID, is_error=True, retries_used=1,
        )

        returned_id = await handle_claude_result(TEST_CHAT_ID, TEST_SESSION_ID, result)

        assert returned_id == TEST_SESSION_ID
        sent = _setup_application.bot.send_message
        sent.assert_called()
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Ошибка" in sent_text
        assert "Процесс упал" in sent_text

    @pytest.mark.asyncio()
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    async def test_error_result_without_text_shows_unknown_error(
        self,
        mock_register: AsyncMock,
        _setup_application: MagicMock,
    ) -> None:
        """Результат с is_error=True без текста показывает 'Неизвестная ошибка'."""
        mock_register.return_value = 2
        result = SendResult(
            text="", session_id=TEST_SESSION_ID, is_error=True, retries_used=0,
        )

        await handle_claude_result(TEST_CHAT_ID, TEST_SESSION_ID, result)

        sent = _setup_application.bot.send_message
        sent_text = sent.call_args[1].get("text", sent.call_args[0][1])
        assert "Неизвестная ошибка" in sent_text

    @pytest.mark.asyncio()
    @patch("claude_manager.bot.send_response", new_callable=AsyncMock)
    @patch.object(daily_session_registry, "register_session", new_callable=AsyncMock)
    async def test_uses_actual_session_id_from_result(
        self,
        mock_register: AsyncMock,
        mock_send_response: AsyncMock,
    ) -> None:
        """Использует session_id из результата, а не переданный аргументом."""
        mock_register.return_value = 5
        real_session_id = "real-uuid-from-result"
        result = SendResult(
            text="OK", session_id=real_session_id, is_error=False, retries_used=0,
        )

        returned_id = await handle_claude_result(TEST_CHAT_ID, "temp-id", result)

        assert returned_id == real_session_id
        mock_register.assert_awaited_once_with(
            real_session_id,
            BackendName.CLAUDE,
        )


# --- Тесты _agent_silence_watchdog ---





# --- Тесты констант claude-interaction ---


class TestClaudeInteractionConstants:
    """Тесты значений констант, связанных с взаимодействием с Claude."""

    def test_agent_silence_timeout_positive(self) -> None:
        """Таймаут тишины агента — положительное число."""
        assert AGENT_SILENCE_TIMEOUT_SECONDS > 0

    def test_empty_response_text_not_empty(self) -> None:
        """Текст пустого ответа — непустая строка."""
        assert len(EMPTY_RESPONSE_TEXT) > 0

    def test_no_response_marker_is_specific_string(self) -> None:
        """NO_RESPONSE_MARKER — конкретная строка, которую Claude возвращает."""
        assert NO_RESPONSE_MARKER == "No response requested."

    def test_monitoring_mode_message_mentions_session(self) -> None:
        """Сообщение режима мониторинга упоминает сессию."""
        assert "сессии" in MONITORING_MODE_MESSAGE.lower() or "сессию" in MONITORING_MODE_MESSAGE.lower()
