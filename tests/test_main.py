"""Тесты модуля main — точки входа приложения.

Проверяет: настройку логирования, файл-замок,
обработку ошибок конфигурации, Conflict и KeyboardInterrupt.
"""

import fcntl
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_manager import config
from claude_manager.config import ConfigError
from claude_manager.main import (
    LOCK_PATH,
    _acquire_lock,
    _restore_last_selected_project,
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
        test_lock = str(tmp_path / "test.lock")
        with patch("claude_manager.main.LOCK_PATH", test_lock):
            lock_file = _acquire_lock()

            assert lock_file is not None
            lock_file.close()

    def test_acquire_lock_writes_pid(self, tmp_path):
        """Проверяет, что в файл записывается PID процесса."""
        test_lock = str(tmp_path / "test.lock")
        with patch("claude_manager.main.LOCK_PATH", test_lock):
            lock_file = _acquire_lock()
            assert lock_file is not None

            content = open(test_lock).read()
            assert content == str(os.getpid())

            lock_file.close()

    def test_acquire_lock_returns_none_when_locked(self, tmp_path):
        """Проверяет, что при занятом замке возвращается None."""
        test_lock = str(tmp_path / "test.lock")

        # Захватываем замок вручную (имитируем другой процесс)
        first_lock = open(test_lock, "w")
        fcntl.flock(first_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with patch("claude_manager.main.LOCK_PATH", test_lock):
            result = _acquire_lock()
            assert result is None

        first_lock.close()

    def test_lock_file_created_if_not_exists(self, tmp_path):
        """Проверяет, что файл-замок создаётся, если его не было."""
        test_lock = str(tmp_path / "test.lock")
        assert not os.path.exists(test_lock)

        with patch("claude_manager.main.LOCK_PATH", test_lock):
            lock_file = _acquire_lock()

            assert lock_file is not None
            assert os.path.exists(test_lock)
            lock_file.close()


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
                "claude_manager.main._run_bot",
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
                "claude_manager.main._run_bot",
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
            patch("claude_manager.main._restore_last_selected_project"),
            patch("claude_manager.main._acquire_lock", return_value=mock_lock),
            patch("claude_manager.main.config.WORKING_DIR", test_working_dir),
            patch("claude_manager.main._run_bot"),
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

    def test_conflict_error_causes_exit(self):
        """Проверяет, что ошибка Conflict от Telegram логируется."""
        from telegram.error import Conflict

        mock_application = MagicMock()
        mock_application.run_polling.side_effect = Conflict(
            "Conflict: terminated by other getUpdates request"
        )

        with (
            patch(
                "claude_manager.main.bot.setup_bot",
                return_value=mock_application,
            ),
            patch("claude_manager.main.logger") as mock_logger,
        ):
            _run_bot()

        mock_logger.error.assert_called_once()
        error_message = str(mock_logger.error.call_args)
        assert "конфликт" in error_message.lower()


# --- Тесты граничных случаев ---


class TestEdgeCases:
    """Граничные случаи."""

    def test_lock_released_after_close(self, tmp_path):
        """Проверяет, что замок освобождается после закрытия файла."""
        test_lock = str(tmp_path / "test.lock")
        with patch("claude_manager.main.LOCK_PATH", test_lock):
            # Захватываем и освобождаем замок
            lock_file = _acquire_lock()
            assert lock_file is not None
            lock_file.close()

            # Повторный захват должен быть успешен
            second_lock = _acquire_lock()
            assert second_lock is not None
            second_lock.close()

    def test_lock_uses_global_path_not_working_dir(self, tmp_path):
        """Проверяет, что замок в фиксированном глобальном пути, а не в WORKING_DIR."""
        # Два разных WORKING_DIR — но lock один и тот же
        test_lock = str(tmp_path / "global.lock")
        with patch("claude_manager.main.LOCK_PATH", test_lock):
            first_lock = _acquire_lock()
            assert first_lock is not None

            # Вторая попытка — тот же lock, должна вернуть None
            second_lock = _acquire_lock()
            assert second_lock is None

            first_lock.close()


# --- Тесты восстановления последнего выбранного проекта ---


class TestRestoreLastSelectedProject:
    """Тесты функции _restore_last_selected_project."""

    def test_updates_working_dir_on_valid_last_project(self, tmp_path) -> None:
        """Если load_last_selected_project вернул путь — config.WORKING_DIR обновляется."""
        target_path = str(tmp_path / "some_project")

        original_working_dir = config.WORKING_DIR
        try:
            with patch(
                "claude_manager.main.project_manager.load_last_selected_project",
                new=AsyncMock(return_value=target_path),
            ):
                _restore_last_selected_project()

            assert config.WORKING_DIR == target_path
        finally:
            config.WORKING_DIR = original_working_dir

    def test_keeps_working_dir_when_no_last_project(self, tmp_path) -> None:
        """Если последний проект не сохранён (None) — WORKING_DIR остаётся как был."""
        original_working_dir = config.WORKING_DIR
        config.WORKING_DIR = str(tmp_path)
        try:
            with patch(
                "claude_manager.main.project_manager.load_last_selected_project",
                new=AsyncMock(return_value=None),
            ):
                _restore_last_selected_project()

            assert config.WORKING_DIR == str(tmp_path)
        finally:
            config.WORKING_DIR = original_working_dir

    def test_continues_on_load_exception(self, tmp_path) -> None:
        """При ошибке в load_last_selected_project main не падает, WORKING_DIR не меняется."""
        original_working_dir = config.WORKING_DIR
        config.WORKING_DIR = str(tmp_path)
        try:
            with patch(
                "claude_manager.main.project_manager.load_last_selected_project",
                new=AsyncMock(side_effect=RuntimeError("bang")),
            ):
                # Не должно упасть
                _restore_last_selected_project()

            assert config.WORKING_DIR == str(tmp_path)
        finally:
            config.WORKING_DIR = original_working_dir
