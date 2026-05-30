"""Behavior tests for project-switch pending delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    all_projects_monitor,
    bot as bot_module,
    config as config_module,
    daily_session_registry,
    project_manager,
    project_pending_delivery,
    session_watcher,
    silence_mode_registry,
    telegram_response_delivery,
    unread_buffer,
)
from claude_manager.coding_agent_backend import BackendName

ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


@pytest.fixture(autouse=True)
def _pending_delivery_state(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fake_application = SimpleNamespace(bot=MagicMock(name="telegram_bot"))
    switch_message_sender = AsyncMock()

    monkeypatch.setattr(config_module, "ALLOWED_USER_IDS", {ALLOWED_USER_ID})
    monkeypatch.setattr(config_module, "WORKING_DIR", "/fake/alpha")
    monkeypatch.setattr(bot_module, "_application", fake_application)
    monkeypatch.setattr(
        bot_module.telegram_sender,
        "send_telegram_message",
        switch_message_sender,
    )
    monkeypatch.setattr(silence_mode_registry, "is_enabled", lambda: False)
    monkeypatch.setattr(all_projects_monitor, "disable_for_chat", lambda _chat: False)
    monkeypatch.setattr(all_projects_monitor, "has_enabled_chats", lambda: False)
    monkeypatch.setattr(session_watcher, "resume_all", MagicMock())
    unread_buffer._snapshots.clear()

    yield switch_message_sender

    unread_buffer._snapshots.clear()


def _make_update(text: str = "/p1") -> MagicMock:
    update = MagicMock()
    update.message.text = text
    update.effective_chat.id = TEST_CHAT_ID
    update.effective_user.id = ALLOWED_USER_ID
    return update


def _project_info(
    name: str = "beta",
    path: str = "/fake/beta",
    is_current: bool = False,
) -> project_manager.ProjectInfo:
    return project_manager.ProjectInfo(
        name=name,
        absolute_path=path,
        is_current=is_current,
    )


def _pending_item(
    session_id: str,
    text: str,
    *,
    backend: BackendName = BackendName.CLAUDE,
    is_final: bool = True,
) -> project_manager.PendingDeliveryItem:
    return project_manager.PendingDeliveryItem(
        session_id=session_id,
        backend=backend,
        text=text,
        is_final=is_final,
    )


def _switch_result(
    pending_messages: list[project_manager.PendingDeliveryItem],
    *,
    count: int | None = None,
    already_active: bool = False,
) -> project_manager.SwitchResult:
    return project_manager.SwitchResult(
        success=True,
        already_active=already_active,
        old_path="/fake/alpha",
        new_path="/fake/beta",
        pending_messages_count=len(pending_messages) if count is None else count,
        pending_messages=pending_messages,
        error_message="",
    )


async def _run_switch(
    monkeypatch: pytest.MonkeyPatch,
    switch_result: project_manager.SwitchResult,
    *,
    project: project_manager.ProjectInfo | None = None,
) -> None:
    target_project = project or _project_info()
    monkeypatch.setattr(
        project_manager,
        "scan_available_projects",
        AsyncMock(return_value=[target_project]),
    )
    monkeypatch.setattr(
        project_manager,
        "switch_project",
        AsyncMock(return_value=switch_result),
    )

    await bot_module.handle_switch_project(_make_update(), MagicMock())


def _switch_text(switch_message_sender: AsyncMock) -> str:
    return switch_message_sender.await_args_list[0].args[2]


async def test_hidden_intermediate_pending_is_not_counted_or_delivered(
    monkeypatch: pytest.MonkeyPatch,
    _pending_delivery_state: AsyncMock,
) -> None:
    pending_messages = [
        _pending_item("session-progress", "Думаю", is_final=False),
        _pending_item("session-final", "Готово", is_final=True),
    ]
    send_response = AsyncMock()
    clear_snapshot = MagicMock()
    monkeypatch.setattr(silence_mode_registry, "is_enabled", lambda: True)
    monkeypatch.setattr(daily_session_registry, "register_session", AsyncMock(return_value=7))
    monkeypatch.setattr(telegram_response_delivery, "send_response", send_response)
    monkeypatch.setattr(unread_buffer, "clear_snapshot_for_session_backend_pair", clear_snapshot)

    await _run_switch(monkeypatch, _switch_result(pending_messages))

    assert "Непрочитанных сообщений: 1" in _switch_text(_pending_delivery_state)
    send_response.assert_awaited_once_with(
        TEST_CHAT_ID,
        "Готово",
        7,
        BackendName.CLAUDE,
        is_final=True,
    )
    clear_snapshot.assert_called_once_with("session-final", BackendName.CLAUDE)


async def test_visible_pending_count_falls_back_to_result_count(
    monkeypatch: pytest.MonkeyPatch,
    _pending_delivery_state: AsyncMock,
) -> None:
    send_response = AsyncMock()
    monkeypatch.setattr(telegram_response_delivery, "send_response", send_response)

    await _run_switch(monkeypatch, _switch_result([], count=3))

    assert "Непрочитанных сообщений: 3" in _switch_text(_pending_delivery_state)
    send_response.assert_not_awaited()


async def test_pending_delivery_uses_telegram_response_delivery_send_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_messages = [
        _pending_item(
            "codex-session",
            "Промежуточный ответ",
            backend=BackendName.CODEX,
            is_final=False,
        )
    ]
    register_session = AsyncMock(return_value=12)
    send_response = AsyncMock()
    monkeypatch.setattr(daily_session_registry, "register_session", register_session)
    monkeypatch.setattr(telegram_response_delivery, "send_response", send_response)

    await _run_switch(monkeypatch, _switch_result(pending_messages))

    register_session.assert_awaited_once_with("codex-session", BackendName.CODEX)
    send_response.assert_awaited_once_with(
        TEST_CHAT_ID,
        "Промежуточный ответ",
        12,
        BackendName.CODEX,
        is_final=False,
    )


async def test_unread_snapshot_cleanup_waits_for_successful_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_messages = [
        _pending_item("session-final", "Готово", is_final=True),
    ]
    send_response = AsyncMock(side_effect=RuntimeError("telegram delivery failed"))
    clear_snapshot = MagicMock()
    monkeypatch.setattr(daily_session_registry, "register_session", AsyncMock(return_value=8))
    monkeypatch.setattr(telegram_response_delivery, "send_response", send_response)
    monkeypatch.setattr(unread_buffer, "clear_snapshot_for_session_backend_pair", clear_snapshot)

    with pytest.raises(RuntimeError, match="telegram delivery failed"):
        await _run_switch(monkeypatch, _switch_result(pending_messages))

    clear_snapshot.assert_not_called()


async def test_leaving_all_mode_into_active_project_collects_pending_messages(
    monkeypatch: pytest.MonkeyPatch,
    _pending_delivery_state: AsyncMock,
) -> None:
    active_project = _project_info("alpha", "/fake/alpha", is_current=True)
    collected_pending = [
        _pending_item("session-from-all", "Ответ из all", backend=BackendName.CODEX),
    ]
    collect_pending = AsyncMock(return_value=(1, collected_pending))
    send_response = AsyncMock()

    monkeypatch.setattr(all_projects_monitor, "disable_for_chat", lambda _chat: True)
    monkeypatch.setattr(
        project_pending_delivery,
        "collect_pending_messages",
        collect_pending,
    )
    monkeypatch.setattr(daily_session_registry, "register_session", AsyncMock(return_value=5))
    monkeypatch.setattr(telegram_response_delivery, "send_response", send_response)

    await _run_switch(
        monkeypatch,
        _switch_result([], count=0, already_active=True),
        project=active_project,
    )

    collect_pending.assert_awaited_once_with("/fake/alpha")
    assert "Переключено на проект: alpha" in _switch_text(_pending_delivery_state)
    assert "Непрочитанных сообщений: 1" in _switch_text(_pending_delivery_state)
    send_response.assert_awaited_once_with(
        TEST_CHAT_ID,
        "Ответ из all",
        5,
        BackendName.CODEX,
        is_final=True,
    )
