"""Управление проектами: сканирование папки, переключение, восстановление выбора.

Центральный модуль фичи «Переключение между проектами». Отвечает за три задачи:

1. Сканировать папку PROJECTS_ROOT_DIR и возвращать список доступных проектов.
2. Атомарно переключать бот на другой проект: останавливать процессы,
   сбрасывать состояние всех state-модулей, обновлять config.WORKING_DIR,
   перезагружать файлы состояния нового проекта.
3. Запоминать последний выбранный проект в отдельном файле и восстанавливать
   его при старте бота после перезапуска.

Не знает о Telegram API — только работа с файловой системой и координация
со state-модулями. Модуль верхнего уровня (bot.py) вызывает эти функции
в ответ на команды пользователя.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from claude_manager import (
    config,
    daily_session_registry,
    process_manager,
    session_manager,
    session_watcher,
)

logger = logging.getLogger(__name__)

# Суффикс временного файла при атомарной записи последнего выбранного проекта
_LAST_PROJECT_TEMP_SUFFIX = ".tmp"

# Внутренняя блокировка — гарантирует, что два параллельных вызова switch_project
# не перемешают состояние (например, один сбросит bindings, а другой уже начнёт загружать)
_switch_lock = asyncio.Lock()


@dataclass(frozen=True)
class ProjectInfo:
    """Информация об одном проекте в папке PROJECTS_ROOT_DIR."""

    name: str
    absolute_path: str
    is_current: bool


@dataclass(frozen=True)
class SwitchResult:
    """Результат попытки переключения на другой проект."""

    success: bool
    already_active: bool
    old_path: str
    new_path: str
    stopped_processes_count: int
    error_message: str


class ProjectSwitchError(Exception):
    """Ошибка переключения проекта: невалидный путь, отказ в доступе, выход за границы."""


# --- Внутренние утилиты ---


def _paths_point_to_same_dir(first_path: str, second_path: str) -> bool:
    """Сравнивает два пути по реальному расположению (после раскрытия симлинков)."""
    return os.path.realpath(first_path) == os.path.realpath(second_path)


def _is_path_inside_root(target_path: str, root_path: str) -> bool:
    """Проверяет, что target_path строго внутри root_path — защита от path traversal."""
    real_target = os.path.realpath(target_path)
    real_root = os.path.realpath(root_path)

    # Совпадение с корнем не считается «внутри» — запрещаем переключение на сам PROJECTS_ROOT_DIR
    if real_target == real_root:
        return False

    # startswith c разделителем в конце — чтобы "/root/foo-bar" не совпал с "/root/foo"
    return real_target.startswith(real_root + os.sep)


def _validate_target_path(target_path: str) -> None:
    """Проверяет, что путь существует, это папка, доступна на чтение и внутри PROJECTS_ROOT_DIR."""
    if not os.path.exists(target_path):
        raise ProjectSwitchError(f"Папка не существует: {target_path}")

    if not os.path.isdir(target_path):
        raise ProjectSwitchError(f"Это не папка: {target_path}")

    if not _is_path_inside_root(target_path, config.PROJECTS_ROOT_DIR):
        raise ProjectSwitchError(
            f"Путь вне корневой папки проектов: {target_path}"
        )

    if not os.access(target_path, os.R_OK):
        raise ProjectSwitchError(f"Нет прав на чтение папки: {target_path}")


def _should_include_project(entry_name: str, entry_full_path: str) -> bool:
    """Решает, нужно ли включать запись в список доступных проектов."""
    # Скрытые папки (начинаются с точки) — служебные, не показываем
    if entry_name.startswith("."):
        return False

    # Символические ссылки могут вести вовне PROJECTS_ROOT_DIR — для безопасности исключаем
    if os.path.islink(entry_full_path):
        return False

    # Только директории, не файлы
    if not os.path.isdir(entry_full_path):
        return False

    return True


def _list_project_entries() -> list[str]:
    """Читает содержимое PROJECTS_ROOT_DIR (блокирующая операция)."""
    return os.listdir(config.PROJECTS_ROOT_DIR)


def _build_project_info(entry_name: str) -> ProjectInfo:
    """Собирает ProjectInfo для одной записи в папке проектов."""
    full_path = os.path.join(config.PROJECTS_ROOT_DIR, entry_name)
    is_current = _paths_point_to_same_dir(full_path, config.WORKING_DIR)
    return ProjectInfo(
        name=entry_name,
        absolute_path=full_path,
        is_current=is_current,
    )


# --- Публичный API ---


async def scan_available_projects() -> list[ProjectInfo]:
    """Возвращает отсортированный по имени список доступных проектов из PROJECTS_ROOT_DIR."""
    if not os.path.isdir(config.PROJECTS_ROOT_DIR):
        logger.warning(
            "Папка проектов не существует: %s", config.PROJECTS_ROOT_DIR,
        )
        return []

    try:
        entries = await asyncio.to_thread(_list_project_entries)
    except OSError as error:
        logger.warning(
            "Не удалось прочитать папку проектов %s: %s",
            config.PROJECTS_ROOT_DIR,
            error,
        )
        return []

    projects: list[ProjectInfo] = []
    for entry_name in entries:
        full_path = os.path.join(config.PROJECTS_ROOT_DIR, entry_name)
        if _should_include_project(entry_name, full_path):
            projects.append(_build_project_info(entry_name))

    projects.sort(key=lambda project: project.name.lower())
    return projects


def get_current_project_path() -> str:
    """Возвращает абсолютный путь к текущему активному проекту."""
    return config.WORKING_DIR


async def _reset_all_state_modules() -> None:
    """Сбрасывает состояние всех state-модулей — для switch_project."""
    await session_manager.reset_state()
    await daily_session_registry.reset_state()
    session_watcher.reset_state()


async def _perform_switch(target_path: str) -> int:
    """Выполняет само переключение: останавливает процессы и сбрасывает state."""
    stopped_count = await process_manager.stop_all_processes()

    # Меняем WORKING_DIR ДО сброса state — модули при перезагрузке прочитают новый путь
    config.WORKING_DIR = target_path

    await _reset_all_state_modules()
    return stopped_count


async def _rollback_switch(old_path: str) -> None:
    """Пытается восстановить старое значение WORKING_DIR после неудачного переключения."""
    config.WORKING_DIR = old_path
    try:
        await _reset_all_state_modules()
    except Exception:
        # Второй сбой во время отката — записываем в лог, но не поднимаем выше
        logger.error(
            "Не удалось восстановить состояние после отката переключения",
            exc_info=True,
        )


def _make_error_result(
    old_path: str, target_path: str, error_message: str,
) -> SwitchResult:
    """Собирает SwitchResult для любой неудачи переключения — единый формат ошибки."""
    return SwitchResult(
        success=False,
        already_active=False,
        old_path=old_path,
        new_path=target_path,
        stopped_processes_count=0,
        error_message=error_message,
    )


def _make_success_result(
    old_path: str,
    target_path: str,
    stopped_count: int,
    already_active: bool,
) -> SwitchResult:
    """Собирает SwitchResult для успешного переключения или no-op (уже активен)."""
    return SwitchResult(
        success=True,
        already_active=already_active,
        old_path=old_path,
        new_path=target_path,
        stopped_processes_count=stopped_count,
        error_message="",
    )


def _precheck_switch(
    target_path: str, old_path: str,
) -> SwitchResult | None:
    """Проверяет путь и ловит случай «уже активен». Возвращает готовый результат или None, если надо реально переключаться."""
    try:
        _validate_target_path(target_path)
    except ProjectSwitchError as error:
        logger.warning("Отклонено переключение на %s: %s", target_path, error)
        return _make_error_result(old_path, target_path, str(error))

    # Уже в этом проекте — no-op, без остановки процессов и сброса состояния
    if _paths_point_to_same_dir(target_path, old_path):
        return _make_success_result(
            old_path, target_path, stopped_count=0, already_active=True,
        )
    return None


async def _try_switch_with_rollback(
    target_path: str, old_path: str,
) -> tuple[int | None, SwitchResult | None]:
    """Выполняет переключение с откатом при ошибке. Возвращает (stopped_count, None) или (None, error_result)."""
    try:
        stopped_count = await _perform_switch(target_path)
    except Exception as error:
        logger.error(
            "Ошибка при переключении проекта на %s: %s",
            target_path, error, exc_info=True,
        )
        await _rollback_switch(old_path)
        error_result = _make_error_result(
            old_path, target_path, f"Ошибка переключения: {error}",
        )
        return None, error_result
    return stopped_count, None


async def _finalize_successful_switch(
    old_path: str, target_path: str, stopped_count: int,
) -> SwitchResult:
    """Сохраняет путь в файл последнего проекта, пишет лог и собирает успешный результат."""
    # Ошибка save_selected_project не отменяет успешное переключение — она логируется внутри
    await save_selected_project(target_path)
    logger.info(
        "Переключение проекта выполнено: %s -> %s (остановлено процессов=%d)",
        old_path, target_path, stopped_count,
    )
    return _make_success_result(
        old_path, target_path, stopped_count, already_active=False,
    )


async def switch_project(target_path: str) -> SwitchResult:
    """Атомарно переключает бота на другой проект. Возвращает результат переключения."""
    async with _switch_lock:
        old_path = config.WORKING_DIR

        # Валидация и проверка «уже активен» — при ошибке или no-op выходим без сайд-эффектов
        early_result = _precheck_switch(target_path, old_path)
        if early_result is not None:
            return early_result

        # Основное переключение с транзакционной семантикой — при сбое делаем rollback
        stopped_count, error_result = await _try_switch_with_rollback(
            target_path, old_path,
        )
        if error_result is not None:
            return error_result

        return await _finalize_successful_switch(
            old_path, target_path, stopped_count or 0,
        )


async def save_selected_project(path: str) -> None:
    """Атомарно записывает путь к выбранному проекту в LAST_PROJECT_FILE."""
    last_file = config.LAST_PROJECT_FILE
    temp_file = last_file.with_name(last_file.name + _LAST_PROJECT_TEMP_SUFFIX)

    try:
        await asyncio.to_thread(temp_file.write_text, path, "utf-8")
        await asyncio.to_thread(os.replace, str(temp_file), str(last_file))
    except OSError:
        # Невозможность сохранить последний проект не должна отменять успешное переключение
        logger.error(
            "Не удалось сохранить последний проект в %s",
            last_file, exc_info=True,
        )


async def load_last_selected_project() -> str | None:
    """Читает LAST_PROJECT_FILE и возвращает валидный путь или None при любых ошибках."""
    last_file = config.LAST_PROJECT_FILE

    if not last_file.exists():
        return None

    try:
        content = await asyncio.to_thread(last_file.read_text, "utf-8")
    except OSError as error:
        logger.warning(
            "Не удалось прочитать файл последнего проекта %s: %s",
            last_file, error,
        )
        return None

    stored_path = content.strip()
    if not stored_path:
        return None

    # Валидируем путь — если проект удалён или вне PROJECTS_ROOT_DIR, возвращаем None
    try:
        _validate_target_path(stored_path)
    except ProjectSwitchError as error:
        logger.warning(
            "Сохранённый последний проект невалиден (%s): %s",
            stored_path, error,
        )
        return None

    return stored_path
