"""Режим тишины (silence mode) — подавление промежуточных сообщений.

Хранит глобальный флаг: включён ли silence mode. Когда включён,
бот доставляет только финальные ответы Claude (is_final=True),
подавляя промежуточные обновления (thinking, progress).
Состояние персистентно — сохраняется между перезапусками бота.
"""

import json
import logging
import os
from pathlib import Path

from claude_manager import config

logger = logging.getLogger(__name__)

# Внутреннее состояние модуля
_silence_enabled: bool = False

# Защита от затирания данных — запись заблокирована до первого load_state()
_loaded_from_disk: bool = False


def is_enabled() -> bool:
    """Возвращает True если silence mode включён."""
    return _silence_enabled


def enable() -> None:
    """Включает silence mode и сохраняет состояние на диск."""
    global _silence_enabled
    _silence_enabled = True
    _save_state()


def disable() -> None:
    """Выключает silence mode и сохраняет состояние на диск."""
    global _silence_enabled
    _silence_enabled = False
    _save_state()


def load_state() -> None:
    """Загружает состояние silence mode с диска при старте бота."""
    global _silence_enabled, _loaded_from_disk

    file_path = config.SILENCE_MODE_FILE

    try:
        content = file_path.read_text("utf-8")
        data = json.loads(content)
        _silence_enabled = data["enabled"]
        _loaded_from_disk = True
        logger.info("Silence mode загружен: enabled=%s", _silence_enabled)
    except FileNotFoundError:
        # Файл ещё не создавался — штатная ситуация, используем дефолт
        _silence_enabled = False
        _loaded_from_disk = True
    except (json.JSONDecodeError, KeyError) as error:
        # Битый файл или нет ключа — безопасный дефолт, разрешаем запись
        # (следующий enable/disable перезапишет битый файл)
        _silence_enabled = False
        _loaded_from_disk = True
        logger.warning("Silence mode: повреждённый файл, используем дефолт: %s", error)
    except Exception as error:
        # Непредвиденная ошибка — НЕ разрешаем запись (данные могут быть корректны)
        _silence_enabled = False
        _loaded_from_disk = False
        logger.error("Silence mode: ошибка загрузки, запись заблокирована: %s", error)


def _save_state() -> None:
    """Атомарно сохраняет состояние на диск (tmp + rename)."""
    if not _loaded_from_disk:
        logger.warning("Запись silence mode заблокирована — данные не были загружены с диска")
        return

    file_path = config.SILENCE_MODE_FILE
    temp_path = Path(str(file_path) + ".tmp")

    json_content = json.dumps({"enabled": _silence_enabled})
    temp_path.write_text(json_content, "utf-8")
    os.replace(str(temp_path), str(file_path))
