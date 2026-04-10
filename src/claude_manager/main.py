"""Точка входа приложения — запуск Telegram-бота Claude Manager.

Загружает настройки, защищает от двойного запуска через файл-замок,
восстанавливает сохранённое состояние сессий и запускает Telegram-опрос.
"""

import asyncio
import fcntl
import io
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from claude_manager import bot, config, project_manager
from claude_manager.config import ConfigError

logger = logging.getLogger(__name__)

# Глобальный путь к файлу-замку — в домашней папке, чтобы работал
# независимо от рабочей директории (LaunchAgent, watch_and_restart.sh, ручной запуск)
LOCK_PATH = os.path.join(os.path.expanduser("~"), ".claude-manager.lock")

# Формат логов: время, уровень важности, модуль, сообщение
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Формат даты в логах (без миллисекунд — для читаемости)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Библиотеки, чьи подробные логи нужно приглушить до WARNING
SILENCED_LOGGERS = ("httpx", "telegram")

# Путь к файлу с ошибками — туда пишутся строки уровня WARNING и выше.
# Совпадает с путём, куда LaunchAgent перенаправляет stderr, чтобы ротация
# применялась к тому же файлу, что и раньше.
ERROR_LOG_PATH = os.path.join(
    os.path.expanduser("~"), "Library", "Logs", "claude-manager.error.log"
)

# Максимальный размер одного файла error.log до ротации.
# 10 MB — компромисс: достаточно для разбора недавних ошибок без постоянной ротации,
# но не даёт одному файлу раздуться как раньше до сотен мегабайт (был прецедент: 314 MB).
ERROR_LOG_MAX_BYTES = 10 * 1024 * 1024

# Сколько архивных копий error.log хранить (error.log.1, error.log.2, ...).
# 5 копий × 10 MB = максимум 60 MB суммарного лога на диске (включая текущий).
ERROR_LOG_BACKUP_COUNT = 5


def _setup_logging() -> None:
    """Настраивает систему логирования для всего приложения."""
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )

    # Добавляем ротацию файла с ошибками: раньше error.log раздувался до 314 MB,
    # потому что зациклившийся процесс бесконечно спамил warning-строки.
    # RotatingFileHandler сам разбивает файл на куски при превышении размера
    # и хранит ограниченное число архивных копий.
    _attach_rotating_error_handler()

    # Приглушаем логи сторонних библиотек, чтобы не засоряли вывод
    for logger_name in SILENCED_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _ensure_error_log_directory() -> bool:
    """Создаёт папку для файла error.log, если её ещё нет. Возвращает успех."""
    # Без папки RotatingFileHandler упадёт при первом же вызове —
    # os.makedirs рекурсивно создаст всю цепочку вложенных папок.
    error_log_dir = os.path.dirname(ERROR_LOG_PATH)
    try:
        os.makedirs(error_log_dir, exist_ok=True)
    except OSError as error:
        # Если папку создать не удалось — пишем предупреждение и продолжаем
        # без файлового лога, чтобы не блокировать запуск бота.
        logger.warning(
            "Не удалось создать папку для лог-файла %s: %s",
            error_log_dir,
            error,
        )
        return False
    return True


def _build_rotating_error_handler() -> RotatingFileHandler:
    """Создаёт настроенный RotatingFileHandler для файла error.log."""
    rotating_handler = RotatingFileHandler(
        ERROR_LOG_PATH,
        maxBytes=ERROR_LOG_MAX_BYTES,
        backupCount=ERROR_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    # В error.log пишем только предупреждения и ошибки — иначе файл снова
    # начнёт разбухать от информационных строк штатной работы бота.
    rotating_handler.setLevel(logging.WARNING)
    rotating_handler.setFormatter(
        logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    )
    return rotating_handler


def _has_rotating_handler_for_file(
    root_logger: logging.Logger, target_file_path: str
) -> bool:
    """Проверяет, есть ли у логгера RotatingFileHandler с заданным файлом."""
    # Защита от повторного подключения: если _setup_logging вызывается
    # несколько раз подряд (например, из тестов), не хотим плодить дубли.
    for existing_handler in root_logger.handlers:
        if (
            isinstance(existing_handler, RotatingFileHandler)
            and getattr(existing_handler, "baseFilename", None) == target_file_path
        ):
            return True
    return False


def _attach_rotating_error_handler() -> None:
    """Подключает к корневому логгеру файловый обработчик с ротацией по размеру."""
    # Шаг 1: подготовить папку. Если не получилось — тихо выходим
    # (предупреждение уже залогировано внутри _ensure_error_log_directory).
    if not _ensure_error_log_directory():
        return

    # Шаг 2: создать и настроить сам обработчик с ротацией.
    rotating_handler = _build_rotating_error_handler()

    # Шаг 3: прицепить к корневому логгеру, если такого ещё нет.
    root_logger = logging.getLogger()
    if _has_rotating_handler_for_file(root_logger, rotating_handler.baseFilename):
        return
    root_logger.addHandler(rotating_handler)


def _acquire_lock() -> io.TextIOWrapper | None:
    """Захватывает глобальный файл-замок для защиты от двойного запуска."""
    lock_path = LOCK_PATH

    # Открываем файл в режиме "a+" — создаёт файл, если его нет,
    # и НЕ трункирует (не обнуляет) существующий. Это важно: если две копии
    # бота стартуют одновременно, вторая НЕ должна стирать PID первой до
    # вызова flock — иначе диагностика «кто держит замок» ломается.
    try:
        lock_file = open(lock_path, "a+")
    except OSError as error:
        logger.error("Не удалось открыть файл-замок %s: %s", lock_path, error)
        return None

    try:
        # LOCK_EX — эксклюзивный замок (только один процесс)
        # LOCK_NB — не ждать, если занят (сразу ошибка)
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None

    # Только после успешного взятия замка можно модифицировать файл:
    # теперь другие копии уже не смогут его подменить. Обнуляем старый PID
    # и пишем свой, чтобы в файле был актуальный номер текущего процесса.
    lock_file.seek(0)
    lock_file.truncate(0)
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    return lock_file


def _run_bot() -> None:
    """Создаёт Telegram-бота и запускает polling."""
    from telegram.error import Conflict

    # setup_bot создаёт Application и регистрирует все обработчики
    # Восстановление состояния и очистка файлов — в post_init (вызывается автоматически)
    application = bot.setup_bot()

    try:
        # drop_pending_updates=True — игнорируем сообщения, пришедшие пока бот не работал
        application.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error(
            "Обнаружен конфликт: другой бот уже использует этот токен. "
            "Завершение."
        )


def _restore_last_selected_project() -> None:
    """Пытается восстановить последний выбранный проект и обновить config.WORKING_DIR."""
    try:
        last_project = asyncio.run(project_manager.load_last_selected_project())
    except Exception:
        logger.warning(
            "Не удалось восстановить последний выбранный проект, "
            "используется значение из .env",
            exc_info=True,
        )
        return

    if last_project is not None:
        config.WORKING_DIR = last_project
        logger.info("Восстановлен последний проект: %s", last_project)


def main() -> None:
    """Главная функция — точка входа приложения."""
    _setup_logging()

    try:
        config.load_config()
    except ConfigError as error:
        logger.error("Ошибка конфигурации: %s", error)
        sys.exit(1)

    # После загрузки .env пытаемся восстановить последний выбранный проект.
    # Если файл есть и путь валиден — обновляем WORKING_DIR. Иначе остаёмся на .env.
    _restore_last_selected_project()

    lock_file = _acquire_lock()
    if lock_file is None:
        logger.error(
            "Бот уже запущен (файл bot.pid заблокирован другим процессом)"
        )
        sys.exit(1)

    logger.info("Claude Manager запускается...")
    logger.info("Рабочая директория: %s", config.WORKING_DIR)

    try:
        _run_bot()
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения")
        sys.exit(0)
    except Exception:
        logger.exception("Непредвиденная ошибка")
        sys.exit(1)
    finally:
        lock_file.close()
        logger.info("Claude Manager остановлен")


if __name__ == "__main__":
    main()
