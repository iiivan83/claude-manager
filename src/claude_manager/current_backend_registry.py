"""Persistent global registry for the current CLI backend."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from claude_manager import config
from claude_manager.coding_agent_backend import BackendName

logger = logging.getLogger(__name__)

DEFAULT_BACKEND: BackendName = BackendName.CLAUDE
_current_backend: BackendName = DEFAULT_BACKEND
_loaded_from_disk: bool = False

_LOAD_BLOCKED_ERROR = (
    "Текущий бэкенд не загружен с диска — переключение невозможно до "
    "перезапуска бота. Подробности — в логах load_state."
)


def get_current() -> BackendName:
    """Return the backend used for new sessions."""
    return _current_backend


def set_current(name: BackendName) -> None:
    """Persist a new backend and update memory only after disk write succeeds."""
    global _current_backend
    if not _loaded_from_disk:
        raise RuntimeError(_LOAD_BLOCKED_ERROR)

    previous_backend = _current_backend
    _save_state(name)
    _current_backend = name
    logger.info(
        "Текущий бэкенд переключён: %s → %s",
        previous_backend.value,
        name.value,
    )


def load_state() -> None:
    """Load the persisted backend selection from disk."""
    global _current_backend, _loaded_from_disk
    file_path = config.CURRENT_BACKEND_FILE

    try:
        content = file_path.read_text("utf-8")
        stripped_content = content.strip()
        if stripped_content in {backend.value for backend in BackendName}:
            backend = BackendName(stripped_content)
            _current_backend = backend
            _loaded_from_disk = True
            _migrate_legacy_plain_text_best_effort(backend)
            logger.info("Текущий бэкенд загружен с legacy-файла: %s", backend.value)
            return

        data = json.loads(content)
        raw_name = data["backend"]
        backend = BackendName(raw_name)
        _current_backend = backend
        _loaded_from_disk = True
        logger.info("Текущий бэкенд загружен с диска: %s", backend.value)
    except FileNotFoundError:
        _current_backend = DEFAULT_BACKEND
        _loaded_from_disk = True
        logger.info("Файл текущего бэкенда не найден, используется дефолт: %s", DEFAULT_BACKEND.value)
    except (json.JSONDecodeError, KeyError, ValueError) as error:
        _current_backend = DEFAULT_BACKEND
        _loaded_from_disk = True
        logger.warning(
            "Файл текущего бэкенда повреждён, используется дефолт: %s",
            error,
        )
    except Exception as error:
        _current_backend = DEFAULT_BACKEND
        _loaded_from_disk = False
        logger.error(
            "Ошибка загрузки текущего бэкенда, запись заблокирована: %s",
            error,
        )


def _migrate_legacy_plain_text_best_effort(backend: BackendName) -> None:
    """Rewrite legacy plain-text backend state as JSON without blocking load."""
    try:
        _save_state(backend)
    except Exception as error:
        logger.warning(
            "Не удалось мигрировать legacy-файл текущего бэкенда: %s",
            error,
        )


def _save_state(backend_to_save: BackendName) -> None:
    """Atomically save the backend selection as JSON."""
    file_path = config.CURRENT_BACKEND_FILE
    temp_path = Path(str(file_path) + ".tmp")

    json_content = json.dumps({"backend": backend_to_save.value})
    temp_path.write_text(json_content, "utf-8")
    os.replace(str(temp_path), str(file_path))
