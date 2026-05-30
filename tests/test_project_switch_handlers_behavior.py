"""Behavior tests for Telegram project-switch handlers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    all_projects_monitor,
    bot as bot_module,
    config as config_module,
    daily_session_registry,
    project_manager,
    reply_anchor_registry,
    session_manager,
    session_watcher,
    silence_mode_registry,
    telegram_response_delivery,
    unread_buffer,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.project_pending_delivery import PendingDeliveryItem

ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


@pytest.fixture(autouse=True)
def _handler_state(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fake_application = SimpleNamespace(bot=MagicMock(name="telegram_bot"))
    message_sender = AsyncMock()
    monkeypatch.setattr(config_module, "ALLOWED_USER_IDS", {ALLOWED_USER_ID})
    monkeypatch.setattr(config_module, "PROJECTS_ROOT_DIR", "/fake/projects")
    monkeypatch.setattr(config_module, "WORKING_DIR", "/fake/projects/alpha")
    monkeypatch.setattr(bot_module, "_application", fake_application)
    monkeypatch.setattr(bot_module.telegram_sender, "send_telegram_message", message_sender)
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat: False)
    monkeypatch.setattr(all_projects_monitor, "disable_for_chat", lambda _chat: False)
    monkeypatch.setattr(all_projects_monitor, "has_enabled_chats", lambda: False)
    monkeypatch.setattr(all_projects_monitor, "resolve_link", lambda *_args: None)
    monkeypatch.setattr(all_projects_monitor, "enable_for_chat", AsyncMock())
    monkeypatch.setattr(session_watcher, "resume_all", MagicMock())
    monkeypatch.setattr(silence_mode_registry, "is_enabled", lambda: False)
    unread_buffer._snapshots.clear()
    yield message_sender
    unread_buffer._snapshots.clear()


def _make_update(text: str) -> MagicMock:
    update = MagicMock()
    update.message.text = text
    update.effective_chat.id = TEST_CHAT_ID
    update.effective_user.id = ALLOWED_USER_ID
    return update


def _project(name: str, path: str, *, current: bool = False) -> project_manager.ProjectInfo:
    return project_manager.ProjectInfo(name=name, absolute_path=path, is_current=current)


def _switch_result(
    *,
    success: bool = True,
    pending: list[PendingDeliveryItem] | None = None,
    error: str = "",
) -> project_manager.SwitchResult:
    pending_messages = pending or []
    return project_manager.SwitchResult(
        success=success,
        already_active=False,
        old_path="/fake/projects/alpha",
        new_path="/fake/projects/beta",
        pending_messages_count=len(pending_messages),
        pending_messages=pending_messages,
        error_message=error,
    )


def _sent_texts(message_sender: AsyncMock) -> list[str]:
    return [call.args[2] for call in message_sender.await_args_list]


async def _run_project_entry(text: str) -> None:
    update = _make_update(text)
    if text.startswith("/p"):
        await bot_module.handle_switch_project(update, MagicMock())
        return
    await bot_module.handle_switch_project_session(update, MagicMock())


@pytest.mark.parametrize(
    ("all_mode", "expected_lines"),
    [
        (False, ["/all all", "/p1 alpha", f"{bot_module.PROJECT_CURRENT_MARKER} /p2 beta"]),
        (True, [f"{bot_module.PROJECT_CURRENT_MARKER} /all all", "/p1 alpha", "/p2 beta"]),
    ],
)
async def test_projects_output_includes_all_line_and_current_marker(
    monkeypatch: pytest.MonkeyPatch,
    _handler_state: AsyncMock,
    all_mode: bool,
    expected_lines: list[str],
) -> None:
    projects = [_project("alpha", "/fake/projects/alpha"), _project("beta", "/fake/projects/beta", current=True)]
    monkeypatch.setattr(project_manager, "scan_available_projects", AsyncMock(return_value=projects))
    monkeypatch.setattr(all_projects_monitor, "is_enabled_for_chat", lambda _chat: all_mode)

    await bot_module.handle_projects(_make_update("/projects"), MagicMock())

    assert _sent_texts(_handler_state) == ["\n".join(expected_lines)]


@pytest.mark.parametrize("text", ["/p0", "/p3", "/3s1"])
async def test_invalid_project_numbers_do_not_switch(
    monkeypatch: pytest.MonkeyPatch,
    _handler_state: AsyncMock,
    text: str,
) -> None:
    projects = [_project("alpha", "/fake/projects/alpha"), _project("beta", "/fake/projects/beta")]
    switch_project = AsyncMock()
    monkeypatch.setattr(project_manager, "scan_available_projects", AsyncMock(return_value=projects))
    monkeypatch.setattr(project_manager, "switch_project", switch_project)

    await _run_project_entry(text)

    assert _sent_texts(_handler_state) == [f"Проект #{text.lstrip('/p').split('s')[0]} не найден"]
    switch_project.assert_not_awaited()


async def test_switch_project_success_sends_project_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    _handler_state: AsyncMock,
) -> None:
    projects = [_project("beta", "/fake/projects/beta")]
    switch_project = AsyncMock(return_value=_switch_result())
    monkeypatch.setattr(project_manager, "scan_available_projects", AsyncMock(return_value=projects))
    monkeypatch.setattr(project_manager, "switch_project", switch_project)

    await bot_module.handle_switch_project(_make_update("/p1"), MagicMock())

    switch_project.assert_awaited_once_with("/fake/projects/beta")
    assert _sent_texts(_handler_state) == ["Переключено на проект: beta"]


@pytest.mark.parametrize("text", ["/p1", "/1s7"])
async def test_failed_project_entry_restores_all_mode(
    monkeypatch: pytest.MonkeyPatch,
    _handler_state: AsyncMock,
    text: str,
) -> None:
    target = all_projects_monitor.AllProjectSessionLink(1, 7, "beta", "/exact/beta", "sess-7", BackendName.CODEX)
    projects = [_project("beta", "/fake/projects/beta")]
    monkeypatch.setattr(project_manager, "scan_available_projects", AsyncMock(return_value=projects))
    monkeypatch.setattr(project_manager, "switch_project", AsyncMock(return_value=_switch_result(success=False, error="switch failed")))
    monkeypatch.setattr(all_projects_monitor, "resolve_link", lambda *_args: target)
    monkeypatch.setattr(all_projects_monitor, "disable_for_chat", lambda _chat: True)
    enable_all = AsyncMock()
    monkeypatch.setattr(all_projects_monitor, "enable_for_chat", enable_all)

    await _run_project_entry(text)

    enable_all.assert_awaited_once_with(TEST_CHAT_ID)
    session_watcher.resume_all.assert_not_called()
    assert _sent_texts(_handler_state) == ["Ошибка переключения: switch failed"]


async def test_successful_switch_delivers_pending_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    _handler_state: AsyncMock,
) -> None:
    pending = [PendingDeliveryItem("codex-session", BackendName.CODEX, "Ответ из фона", True)]
    monkeypatch.setattr(project_manager, "scan_available_projects", AsyncMock(return_value=[_project("beta", "/fake/projects/beta")]))
    monkeypatch.setattr(project_manager, "switch_project", AsyncMock(return_value=_switch_result(pending=pending)))
    monkeypatch.setattr(daily_session_registry, "register_session", AsyncMock(return_value=17))
    monkeypatch.setattr(reply_anchor_registry, "get_anchor", lambda *_args: None)
    send_response = AsyncMock()
    monkeypatch.setattr(telegram_response_delivery, "send_response", send_response)

    await bot_module.handle_switch_project(_make_update("/p1"), MagicMock())

    assert _sent_texts(_handler_state) == ["Переключено на проект: beta\nНепрочитанных сообщений: 1"]
    send_response.assert_awaited_once_with(TEST_CHAT_ID, "Ответ из фона", 17, BackendName.CODEX, is_final=True)


async def test_project_session_link_uses_exact_target_and_binds_session(
    monkeypatch: pytest.MonkeyPatch,
    _handler_state: AsyncMock,
) -> None:
    target = all_projects_monitor.AllProjectSessionLink(1, 9, "beta-link", "/exact/beta", "exact-session", BackendName.CODEX)
    monkeypatch.setattr(project_manager, "scan_available_projects", AsyncMock(return_value=[_project("beta-scan", "/scan/beta")]))
    switch_project = AsyncMock(return_value=_switch_result())
    monkeypatch.setattr(project_manager, "switch_project", switch_project)
    monkeypatch.setattr(all_projects_monitor, "resolve_link", lambda *_args: target)
    monkeypatch.setattr(all_projects_monitor, "disable_for_chat", lambda _chat: True)
    set_active = AsyncMock(return_value=42)
    monkeypatch.setattr(session_manager, "set_active_session", set_active)
    monkeypatch.setattr(session_manager, "switch_to_session", AsyncMock())

    await bot_module.handle_switch_project_session(_make_update("/1s9"), MagicMock())

    switch_project.assert_awaited_once_with("/exact/beta")
    set_active.assert_awaited_once_with(TEST_CHAT_ID, "exact-session", BackendName.CODEX)
    sent_text = _sent_texts(_handler_state)[0]
    assert sent_text.startswith("Переключено на проект: beta-link\nПодключён к сессии #42 (")
    assert sent_text.endswith("Codex)")
    assert "beta-scan" not in sent_text
