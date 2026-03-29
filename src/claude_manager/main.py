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

from claude_manager import bot, config, session_manager
from claude_manager.config import ConfigError

logger = logging.getLogger(__name__)

# Имя файла-замка (содержит PID процесса и предотвращает двойной запуск)
LOCK_FILENAME = "bot.pid"

# Список команд для меню подсказок в Telegram (имя, описание)
TELEGRAM_COMMANDS = [
    ("new", "Создать новую сессию"),
    ("sessions", "Показать последние сессии"),
    ("all", "Режим мониторинга всех сессий"),
    ("stop", "Остановить Claude"),
]

# Формат логов: время, уровень важности, модуль, сообщение
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Формат даты в логах (без миллисекунд — для читаемости)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Библиотеки, чьи подробные логи нужно приглушить до WARNING
SILENCED_LOGGERS = ("httpx", "telegram")


def _setup_logging() -> None:
    """Настраивает систему логирования для всего приложения."""
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )

    # Приглушаем логи сторонних библиотек, чтобы не засоряли вывод
    for logger_name in SILENCED_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _acquire_lock() -> io.TextIOWrapper | None:
    """Захватывает файл-замок bot.pid для защиты от двойного запуска."""
    lock_path = os.path.join(config.WORKING_DIR, LOCK_FILENAME)

    try:
        lock_file = open(lock_path, "w")
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

    # Записываем PID (номер текущего процесса) для диагностики
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    return lock_file


async def _restore_state() -> None:
    """Восстанавливает состояние бота после перезапуска."""
    # load_bindings загружает привязки из sessions.json и дневной реестр
    try:
        await session_manager.load_bindings()
    except Exception:
        logger.warning(
            "Ошибка при восстановлении состояния — начинаю с чистого",
            exc_info=True,
        )

    # Если нет привязки к сессии — автоматически в режим мониторинга /all
    bindings = session_manager.get_all_bindings()
    if bindings:
        logger.info("Восстановлено %d привязок к сессиям", len(bindings))
    else:
        logger.info("Привязок нет — бот в режиме /all (мониторинг)")


async def _run_bot() -> None:
    """Создаёт Telegram-бота, восстанавливает состояние и запускает polling."""
    # Импорт здесь, чтобы избежать ошибки при отсутствии telegram
    from telegram.error import Conflict

    # setup_bot создаёт Application и регистрирует все обработчики
    application = await bot.setup_bot()

    # Восстанавливаем привязки сессий до начала обработки сообщений
    await _restore_state()

    try:
        # drop_pending_updates=True — игнорируем сообщения, пришедшие пока бот не работал
        application.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error(
            "Обнаружен конфликт: другой бот уже использует этот токен. "
            "Завершение."
        )


def main() -> None:
    """Главная функция — точка входа приложения."""
    _setup_logging()

    try:
        config.load_config()
    except ConfigError as error:
        logger.error("Ошибка конфигурации: %s", error)
        sys.exit(1)

    lock_file = _acquire_lock()
    if lock_file is None:
        logger.error(
            "Бот уже запущен (файл bot.pid заблокирован другим процессом)"
        )
        sys.exit(1)

    logger.info("Claude Manager запускается...")
    logger.info("Рабочая директория: %s", config.WORKING_DIR)

    try:
        asyncio.run(_run_bot())
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
