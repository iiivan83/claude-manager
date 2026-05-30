"""Telegram handlers for project listing and project switching."""

import re
from collections.abc import Callable

from telegram import Update
from telegram.ext import Application, ContextTypes

from claude_manager import (
    all_projects_monitor,
    coding_agent_backend,
    config,
    project_manager,
    project_pending_delivery,
    session_manager,
    session_watcher,
    telegram_sender,
)
from claude_manager.coding_agent_backend import BackendName

PROJECT_CURRENT_MARKER = "\u25cf"
EMPTY_PROJECTS_TEMPLATE = "Проекты не найдены в папке {root}"
INVALID_PROJECT_NUMBER_TEMPLATE = "Проект #{number} не найден"
PROJECT_SWITCH_SUCCESS_TEMPLATE = "Переключено на проект: {name}"
PROJECT_SWITCH_PENDING_TEMPLATE = "Непрочитанных сообщений: {count}"
PROJECT_SWITCH_ERROR_TEMPLATE = "Ошибка переключения: {error}"
PROJECT_ALREADY_ACTIVE_TEMPLATE = "Уже работаю в проекте: {name}"
ALL_PROJECTS_MODE_LINE = "/all all"
PROJECT_SESSION_COMMAND_PATTERN = re.compile(r"^/(?P<project>\d+)s(?P<session>\d+)$")

_ApplicationGetter = Callable[[], Application | None]
_AccessChecker = Callable[[Update], bool]
_application_getter: _ApplicationGetter | None = None
_access_checker: _AccessChecker | None = None


def init_callbacks(application_getter: _ApplicationGetter, access_checker: _AccessChecker) -> None:
    """Inject bot-owned callbacks needed by project command handlers."""
    global _application_getter, _access_checker
    _application_getter = application_getter
    _access_checker = access_checker


def _get_application() -> Application:
    if _application_getter is None:
        raise RuntimeError("telegram project handlers are not initialized")
    application = _application_getter()
    if application is None:
        raise RuntimeError("telegram application is not initialized")
    return application


def _has_access(update: Update) -> bool:
    if _access_checker is None:
        raise RuntimeError("telegram project access checker is not initialized")
    return _access_checker(update)


async def _send_plain_message(chat_id: int, text: str) -> None:
    await telegram_sender.send_telegram_message(
        _get_application().bot, chat_id, text, parse_mode=None,
    )


def _format_project_line(project: project_manager.ProjectInfo, number: int, *, suppress_current_marker: bool = False) -> str:
    marker = (
        PROJECT_CURRENT_MARKER + " "
        if project.is_current and not suppress_current_marker
        else ""
    )
    return f"{marker}/p{number} {project.name}"


def _format_projects_message(projects: list[project_manager.ProjectInfo], all_mode_enabled: bool) -> str:
    all_marker = PROJECT_CURRENT_MARKER + " " if all_mode_enabled else ""
    lines = [f"{all_marker}{ALL_PROJECTS_MODE_LINE}"]
    lines.extend(
        _format_project_line(project, number, suppress_current_marker=all_mode_enabled)
        for number, project in enumerate(projects, start=1)
    )
    return "\n".join(lines)


async def handle_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /projects by listing available project switch commands."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    projects = await project_manager.scan_available_projects()
    if not projects:
        message = EMPTY_PROJECTS_TEMPLATE.format(root=config.PROJECTS_ROOT_DIR)
        await _send_plain_message(chat_id, message)
        return

    all_mode = all_projects_monitor.is_enabled_for_chat(chat_id)
    await _send_plain_message(chat_id, _format_projects_message(projects, all_mode))


async def _resolve_project_by_number(project_number: int) -> project_manager.ProjectInfo | None:
    projects = await project_manager.scan_available_projects()
    if project_number < 1 or project_number > len(projects):
        return None
    return projects[project_number - 1]


def _format_switch_result(result: project_manager.SwitchResult, project_name: str) -> str:
    if result.already_active:
        return PROJECT_ALREADY_ACTIVE_TEMPLATE.format(name=project_name)
    if not result.success:
        return PROJECT_SWITCH_ERROR_TEMPLATE.format(error=result.error_message)

    text = PROJECT_SWITCH_SUCCESS_TEMPLATE.format(name=project_name)
    visible_count = project_pending_delivery.count_visible_pending_messages(result)
    if visible_count > 0:
        text += "\n" + PROJECT_SWITCH_PENDING_TEMPLATE.format(count=visible_count)
    return text


async def _switch_to_project(
    chat_id: int,
    target_project: project_manager.ProjectInfo,
    was_all_projects_mode: bool,
) -> tuple[project_manager.SwitchResult, bool]:
    result = await project_manager.switch_project(target_project.absolute_path)
    all_mode_restored = False
    if not result.success and was_all_projects_mode:
        await all_projects_monitor.enable_for_chat(chat_id)
        all_mode_restored = True
    result = await project_pending_delivery.include_pending_for_all_mode_same_project(
        result, target_project, was_all_projects_mode,
    )
    return result, all_mode_restored


def _resume_session_watcher_if_needed(was_all_projects_mode: bool, all_mode_restored: bool) -> None:
    if was_all_projects_mode and not all_mode_restored:
        if not all_projects_monitor.has_enabled_chats():
            session_watcher.resume_all()


async def _send_switch_result(chat_id: int, target_project: project_manager.ProjectInfo, result: project_manager.SwitchResult) -> None:
    await _send_plain_message(chat_id, _format_switch_result(result, target_project.name))
    if result.success and result.pending_messages_count > 0:
        await project_pending_delivery.deliver_pending_messages(
            chat_id, result.pending_messages,
        )


async def handle_switch_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pN by switching the bot to a numbered project."""
    if not _has_access(update):
        return

    chat_id = update.effective_chat.id
    project_number = int(update.message.text[2:])
    was_all_projects_mode = False
    all_mode_restored = False
    try:
        target_project = await _resolve_project_by_number(project_number)
        if target_project is None:
            message = INVALID_PROJECT_NUMBER_TEMPLATE.format(number=project_number)
            await _send_plain_message(chat_id, message)
            return
        was_all_projects_mode = all_projects_monitor.disable_for_chat(chat_id)
        result, all_mode_restored = await _switch_to_project(
            chat_id, target_project, was_all_projects_mode,
        )
        await _send_switch_result(chat_id, target_project, result)
    finally:
        _resume_session_watcher_if_needed(was_all_projects_mode, all_mode_restored)


def _parse_project_session_command(raw_text: str) -> tuple[int, int] | None:
    match = PROJECT_SESSION_COMMAND_PATTERN.match(raw_text)
    if match is None:
        return None
    return int(match.group("project")), int(match.group("session"))


async def _resolve_project_session_target(project_number: int, link_target: all_projects_monitor.AllProjectSessionLink | None) -> project_manager.ProjectInfo | None:
    scanned_project = await _resolve_project_by_number(project_number)
    if link_target is None:
        return scanned_project
    return project_manager.ProjectInfo(
        name=link_target.project_name,
        absolute_path=link_target.project_path,
        is_current=scanned_project.is_current if scanned_project else False,
    )


async def _bind_requested_session(
    chat_id: int,
    session_number: int,
    link_target: all_projects_monitor.AllProjectSessionLink | None,
) -> tuple[bool, int, BackendName]:
    if link_target is not None:
        day_number = await session_manager.set_active_session(
            chat_id, link_target.session_id, link_target.backend,
        )
        return True, day_number, link_target.backend
    result = await session_manager.switch_to_session(chat_id, session_number)
    return result.found, result.day_number, result.backend


def _backend_display_name(backend: BackendName) -> str:
    return coding_agent_backend.get_backend(backend).display_name


async def _send_project_session_success(
    chat_id: int,
    target_project: project_manager.ProjectInfo,
    result: project_manager.SwitchResult,
    day_number: int,
    backend: BackendName,
) -> None:
    text = (
        f"Переключено на проект: {target_project.name}\n"
        f"Подключён к сессии #{day_number} ({_backend_display_name(backend)})"
    )
    visible_count = project_pending_delivery.count_visible_pending_messages(result)
    if visible_count > 0:
        text += "\n" + PROJECT_SWITCH_PENDING_TEMPLATE.format(count=visible_count)
    await _send_plain_message(chat_id, text)
    if result.pending_messages_count > 0:
        await project_pending_delivery.deliver_pending_messages(
            chat_id, result.pending_messages,
        )


async def _finish_project_session_switch(
    chat_id: int,
    session_number: int,
    target_project: project_manager.ProjectInfo,
    result: project_manager.SwitchResult,
    link_target: all_projects_monitor.AllProjectSessionLink | None,
) -> None:
    if not result.success:
        await _send_plain_message(chat_id, _format_switch_result(result, target_project.name))
        return
    session_found, day_number, backend = await _bind_requested_session(
        chat_id, session_number, link_target,
    )
    if not session_found:
        message = f"Сессия #{session_number} не найдена в проекте {target_project.name}"
        await _send_plain_message(chat_id, message)
        return
    await _send_project_session_success(
        chat_id, target_project, result, day_number, backend,
    )


async def handle_switch_project_session(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /<project>s<session> links from all-project mode."""
    if not _has_access(update):
        return

    parsed_command = _parse_project_session_command(update.message.text)
    if parsed_command is None:
        return
    chat_id = update.effective_chat.id
    project_number, session_number = parsed_command
    link_target = all_projects_monitor.resolve_link(project_number, session_number)
    was_all_projects_mode = False
    all_mode_restored = False
    try:
        target_project = await _resolve_project_session_target(
            project_number, link_target,
        )
        if target_project is None:
            message = INVALID_PROJECT_NUMBER_TEMPLATE.format(number=project_number)
            await _send_plain_message(chat_id, message)
            return
        was_all_projects_mode = all_projects_monitor.disable_for_chat(chat_id)
        result, all_mode_restored = await _switch_to_project(
            chat_id, target_project, was_all_projects_mode,
        )
        await _finish_project_session_switch(
            chat_id, session_number, target_project, result, link_target,
        )
    finally:
        _resume_session_watcher_if_needed(was_all_projects_mode, all_mode_restored)
