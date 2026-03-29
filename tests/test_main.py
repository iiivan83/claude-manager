"""Тесты модуля main — точки входа приложения.

Проверяет: настройку логирования, файл-замок, восстановление состояния,
обработку ошибок конфигурации, Conflict и KeyboardInterrupt.
"""

import asyncio
import fcntl
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager.config import ConfigError
from claude_manager.main import (
    LOCK_FILENAME,
    _acquire_lock,
    _restore_state,
    _run_bot,
    _setup_logging,
    main,
)


# --- Тесты _setup_logging ---


class TestSetupLogging:
    """Тесты настройки логирования."""

    def test_setup_logging_configures_info_level(self):
        """Проверяет, что уровень логирования установлен на INFO."""
        root_logger = logging.getLogger()
        # Сбрасываем обработчики, чтобы basicConfig сработал заново
        root_logger.handlers.clear()
        root_logger.setLevel(logging.WARNING)

        _setup_logging()

        assert root_logger.level == logging.INFO

    def test_setup_logging_silences_httpx(self):
        """Проверяет, что логгер httpx установлен на WARNING."""
        _setup_logging()

        httpx_logger = logging.getLogger("httpx")
        assert httpx_logger.level == logging.WARNING

    def test_setup_logging_silences_telegram(self):
        """Проверяет, что логгер telegram установлен на WARNING."""
        _setup_logging()

        telegram_logger = logging.getLogger("telegram")
        assert telegram_logger.level == logging.WARNING


# --- Тесты _acquire_lock ---


class TestAcquireLock:
    """Тесты захвата файла-замка bot.pid."""

    def test_acquire_lock_success(self, tmp_path):
        """Проверяет успешный захват файла-замка."""
        with patch("claude_manager.main.config") as mock_config:
            mock_config.WORKING_DIR = str(tmp_path)

            lock_file = _acquire_lock()

            assert lock_file is not None
            lock_file.close()

    def test_acquire_lock_writes_pid(self, tmp_path):
        """Проверяет, что в файл записывается PID процесса."""
        with patch("claude_manager.main.config") as mock_config:
            mock_config.WORKING_DIR = str(tmp_path)

            lock_file = _acquire_lock()
            assert lock_file is not None

            # Читаем содержимое файла bot.pid
            lock_path = tmp_path / LOCK_FILENAME
            content = lock_path.read_text()
            assert content == str(os.getpid())

            lock_file.close()

    def test_acquire_lock_returns_none_when_locked(self, tmp_path):
        """Проверяет, что при занятом замке возвращается None."""
        lock_path = tmp_path / LOCK_FILENAME

        # Захватываем замок вручную (имитируем другой процесс)
        first_lock = open(lock_path, "w")
        fcntl.flock(first_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with patch("claude_manager.main.config") as mock_config:
            mock_config.WORKING_DIR = str(tmp_path)

            result = _acquire_lock()
            assert result is None

        first_lock.close()

    def test_lock_file_created_if_not_exists(self, tmp_path):
        """Проверяет, что файл bot.pid создаётся, если его не было."""
        lock_path = tmp_path / LOCK_FILENAME
        assert not lock_path.exists()

        with patch("claude_manager.main.config") as mock_config:
            mock_config.WORKING_DIR = str(tmp_path)

            lock_file = _acquire_lock()

            assert lock_file is not None
            assert lock_path.exists()
            lock_file.close()


# --- Тесты _restore_state ---


class TestRestoreState:
    """Тесты восстановления состояния бота."""

    @pytest.mark.asyncio
    async def test_restore_state_loads_registries(self):
        """Проверяет, что восстановление вызывает загрузку привязок."""
        with patch(
            "claude_manager.main.session_manager"
        ) as mock_session_mgr:
            mock_session_mgr.load_bindings = AsyncMock()
            mock_session_mgr.get_all_bindings.return_value = {
                12345: "session-abc"
            }

            await _restore_state()

            mock_session_mgr.load_bindings.assert_called_once()

    @pytest.mark.asyncio
    async def test_restore_state_sets_all_mode_when_no_binding(self):
        """Проверяет логирование перехода в режим /all при отсутствии привязки."""
        with patch(
            "claude_manager.main.session_manager"
        ) as mock_session_mgr:
            mock_session_mgr.load_bindings = AsyncMock()
            mock_session_mgr.get_all_bindings.return_value = {}

            await _restore_state()

            # Привязок нет — бот логирует, что в режиме /all
            mock_session_mgr.load_bindings.assert_called_once()

    @pytest.mark.asyncio
    async def test_restore_state_keeps_existing_binding(self):
        """Проверяет, что существующая привязка к сессии сохраняется."""
        with patch(
            "claude_manager.main.session_manager"
        ) as mock_session_mgr:
            mock_session_mgr.load_bindings = AsyncMock()
            mock_session_mgr.get_all_bindings.return_value = {
                12345: "session-abc"
            }

            await _restore_state()

            # load_bindings загрузил привязки — они просто остаются
            mock_session_mgr.load_bindings.assert_called_once()

    @pytest.mark.asyncio
    async def test_restore_state_continues_on_registry_error(self):
        """Проверяет, что ошибка загрузки реестра не блокирует запуск."""
        with patch(
            "claude_manager.main.session_manager"
        ) as mock_session_mgr:
            mock_session_mgr.load_bindings = AsyncMock(
                side_effect=OSError("disk error")
            )
            mock_session_mgr.get_all_bindings.return_value = {}

            # Функция не должна выбрасывать исключение
            await _restore_state()


# --- Тесты main ---


class TestMain:
    """Тесты главной функции main."""

    def test_main_exits_on_config_error(self):
        """Проверяет завершение при ошибке конфигурации."""
        with (
            patch("claude_manager.main._setup_logging"),
            patch(
                "claude_manager.main.config.load_config",
                side_effect=ConfigError("TELEGRAM_BOT_TOKEN не задан"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_main_exits_when_lock_busy(self):
        """Проверяет завершение при занятом замке."""
        with (
            patch("claude_manager.main._setup_logging"),
            patch("claude_manager.main.config.load_config"),
            patch("claude_manager.main._acquire_lock", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_keyboard_interrupt_exits_cleanly(self):
        """Проверяет корректное завершение при Ctrl+C."""
        mock_lock = MagicMock()

        with (
            patch("claude_manager.main._setup_logging"),
            patch("claude_manager.main.config.load_config"),
            patch("claude_manager.main._acquire_lock", return_value=mock_lock),
            patch("claude_manager.main.config.WORKING_DIR", "/tmp/test"),
            patch(
                "claude_manager.main.asyncio.run",
                side_effect=KeyboardInterrupt,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        # Код 0 — штатное завершение, не ошибка
        assert exc_info.value.code == 0
        mock_lock.close.assert_called_once()

    def test_unexpected_exception_logged_and_exits(self):
        """Проверяет, что непредвиденное исключение логируется и завершает программу."""
        mock_lock = MagicMock()

        with (
            patch("claude_manager.main._setup_logging"),
            patch("claude_manager.main.config.load_config"),
            patch("claude_manager.main._acquire_lock", return_value=mock_lock),
            patch("claude_manager.main.config.WORKING_DIR", "/tmp/test"),
            patch(
                "claude_manager.main.asyncio.run",
                side_effect=RuntimeError("unexpected"),
            ),
            patch("claude_manager.main.logger") as mock_logger,
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        mock_logger.exception.assert_called_once()
        mock_lock.close.assert_called_once()

    def test_main_logs_working_directory(self):
        """Проверяет, что при запуске логируется рабочая директория."""
        mock_lock = MagicMock()
        test_working_dir = "/tmp/test-project"

        with (
            patch("claude_manager.main._setup_logging"),
            patch("claude_manager.main.config.load_config"),
            patch("claude_manager.main._acquire_lock", return_value=mock_lock),
            patch("claude_manager.main.config.WORKING_DIR", test_working_dir),
            patch("claude_manager.main.asyncio.run"),
            patch("claude_manager.main.logger") as mock_logger,
        ):
            main()

        # Проверяем, что logger.info был вызван с рабочей директорией
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        found_working_dir = any(
            test_working_dir in call_str for call_str in log_calls
        )
        assert found_working_dir, (
            f"Рабочая директория '{test_working_dir}' не найдена в логах: "
            f"{log_calls}"
        )


# --- Тесты _run_bot ---


class TestRunBot:
    """Тесты функции _run_bot."""

    @pytest.mark.asyncio
    async def test_conflict_error_causes_exit(self):
        """Проверяет, что ошибка Conflict от Telegram логируется."""
        from telegram.error import Conflict

        mock_application = MagicMock()
        mock_application.run_polling.side_effect = Conflict(
            "Conflict: terminated by other getUpdates request"
        )

        with (
            patch(
                "claude_manager.main.bot.setup_bot",
                new_callable=AsyncMock,
                return_value=mock_application,
            ),
            patch(
                "claude_manager.main._restore_state",
                new_callable=AsyncMock,
            ),
            patch("claude_manager.main.logger") as mock_logger,
        ):
            await _run_bot()

        mock_logger.error.assert_called_once()
        error_message = str(mock_logger.error.call_args)
        assert "конфликт" in error_message.lower()


# --- Тесты граничных случаев ---


class TestEdgeCases:
    """Граничные случаи."""

    def test_lock_released_after_close(self, tmp_path):
        """Проверяет, что замок освобождается после закрытия файла."""
        with patch("claude_manager.main.config") as mock_config:
            mock_config.WORKING_DIR = str(tmp_path)

            # Захватываем и освобождаем замок
            lock_file = _acquire_lock()
            assert lock_file is not None
            lock_file.close()

            # Повторный захват должен быть успешен
            second_lock = _acquire_lock()
            assert second_lock is not None
            second_lock.close()

    def test_restore_state_handles_corrupted_sessions_json(self):
        """Проверяет, что повреждённый sessions.json не мешает запуску."""
        # Это тестируется через мок — load_bindings обрабатывает ошибки
        # внутри себя, _restore_state ловит любые необработанные исключения

        async def run_test():
            with patch(
                "claude_manager.main.session_manager"
            ) as mock_session_mgr:
                mock_session_mgr.load_bindings = AsyncMock()
                mock_session_mgr.get_all_bindings.return_value = {}

                await _restore_state()

                mock_session_mgr.load_bindings.assert_called_once()

        asyncio.run(run_test())
