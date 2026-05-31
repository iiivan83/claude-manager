"""Behavior tests for Telegram response delivery before extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import telegram_response_delivery as delivery_module
from claude_manager import claude_interaction as ci_module
from claude_manager.coding_agent_backend import BackendName
from claude_manager.process_manager import SendResult
from claude_manager.session_manager import ActiveSession

TEST_CHAT_ID = 12345
TEST_SESSION_ID = "session-current"
OTHER_SESSION_ID = "session-other"
SEND_MARKER = "[SEND_FILE:/tmp/a.md]"
SHOW_MARKER = "[SHOW_FILE:/tmp/a.md]"


@pytest.fixture
def _send_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Install a fake Telegram application and capture outgoing messages."""
    fake_application = SimpleNamespace(bot=MagicMock(name="telegram_bot"))
    send_mock = AsyncMock()
    monkeypatch.setattr(delivery_module, "_application", fake_application)
    monkeypatch.setattr(
        delivery_module.telegram_sender,
        "send_telegram_message",
        send_mock,
    )
    monkeypatch.setattr(
        delivery_module.silence_mode_registry,
        "is_enabled",
        lambda: False,
    )
    return send_mock


@pytest.fixture(autouse=True)
def _restore_claude_interaction_callbacks():
    """Restore callback refs mutated by callback-resolution tests."""
    original_send_response_ref = ci_module._send_response_ref
    original_send_telegram_message_ref = ci_module._send_telegram_message_ref
    yield
    ci_module._send_response_ref = original_send_response_ref
    ci_module._send_telegram_message_ref = original_send_telegram_message_ref


def _latest_sent_text(send_mock: AsyncMock) -> str:
    """Return the text passed to telegram_sender.send_telegram_message."""
    return send_mock.await_args.args[2]


async def _call_delivery(delivery_name: str, is_final: bool, text: str) -> None:
    """Call one of the three delivery entry points with stable arguments."""
    if delivery_name == "response":
        await delivery_module.send_response(TEST_CHAT_ID, text, 4, is_final=is_final)
    elif delivery_name == "watcher":
        await delivery_module.send_watcher_message(
            TEST_CHAT_ID,
            text,
            TEST_SESSION_ID,
            BackendName.CLAUDE,
            session_number=4,
            is_final=is_final,
        )
    else:
        await delivery_module.send_all_projects_watcher_message(
            TEST_CHAT_ID,
            project_number=3,
            session_number=12,
            project_name="budget",
            session_id=TEST_SESSION_ID,
            backend=BackendName.CLAUDE,
            text=text,
            is_final=is_final,
        )


@pytest.mark.parametrize(
    ("text", "backend", "is_final", "expected_text"),
    [
        ("Готово", BackendName.CODEX, True, "#7 ⚡ Codex ✅ Готово"),
        ("Думаю", BackendName.CLAUDE, False, "#7 🤖 Claude ⏳ <i>Думаю</i>"),
    ],
)
async def test_send_response_formats_final_and_intermediate_messages(
    text: str,
    backend: BackendName,
    is_final: bool,
    expected_text: str,
    _send_mock: AsyncMock,
) -> None:
    """Final and intermediate responses keep their current Telegram headers."""
    await delivery_module.send_response(
        TEST_CHAT_ID, text, 7, backend, is_final=is_final,
    )

    assert _latest_sent_text(_send_mock) == expected_text


@pytest.mark.parametrize("raw_text", ["", ci_module.NO_RESPONSE_MARKER])
async def test_send_response_replaces_empty_payloads(
    raw_text: str,
    _send_mock: AsyncMock,
) -> None:
    """Empty result payloads are replaced before Telegram delivery."""
    await delivery_module.send_response(TEST_CHAT_ID, raw_text, 5, is_final=True)

    expected_text = f"#5 🤖 Claude ✅ {ci_module.EMPTY_RESPONSE_TEXT}"
    assert _latest_sent_text(_send_mock) == expected_text


@pytest.mark.parametrize(
    ("delivery_name", "is_final"),
    [
        ("response", True),
        ("response", False),
        ("watcher", True),
        ("watcher", False),
        ("all_projects", True),
        ("all_projects", False),
    ],
)
async def test_file_markers_are_processed_only_for_final_messages(
    delivery_name: str,
    is_final: bool,
    monkeypatch: pytest.MonkeyPatch,
    _send_mock: AsyncMock,
) -> None:
    """File markers are resolved only when the delivered message is final."""
    text = f"payload {SEND_MARKER} {SHOW_MARKER}"
    process_send = AsyncMock(side_effect=lambda _bot, _chat, value: value)
    process_show = AsyncMock(side_effect=lambda _bot, _chat, value: value)
    monkeypatch.setattr(delivery_module.file_delivery, "process_file_markers", process_send)
    monkeypatch.setattr(delivery_module.file_delivery, "process_show_file_markers", process_show)

    await _call_delivery(delivery_name, is_final, text)

    if is_final:
        process_send.assert_awaited_once()
        process_show.assert_awaited_once()
    else:
        process_send.assert_not_awaited()
        process_show.assert_not_awaited()


@pytest.mark.parametrize(
    ("delivery_name", "is_final", "expected_send_count"),
    [
        ("response", False, 0),
        ("watcher", False, 0),
        ("all_projects", False, 0),
        ("response", True, 1),
    ],
)
async def test_silence_mode_suppresses_only_intermediate_delivery(
    delivery_name: str,
    is_final: bool,
    expected_send_count: int,
    monkeypatch: pytest.MonkeyPatch,
    _send_mock: AsyncMock,
) -> None:
    """Silence mode suppresses progress messages but keeps final responses."""
    monkeypatch.setattr(delivery_module.silence_mode_registry, "is_enabled", lambda: True)

    await _call_delivery(delivery_name, is_final, "payload")

    assert _send_mock.await_count == expected_send_count


@pytest.mark.parametrize(
    ("active_session", "expected_text"),
    [
        (
            ActiveSession(TEST_SESSION_ID, BackendName.CLAUDE),
            "#4 🤖 Claude ✅ Ответ",
        ),
        (
            ActiveSession(OTHER_SESSION_ID, BackendName.CLAUDE),
            "<b>/4</b> 🤖 Claude ✅ Ответ",
        ),
    ],
)
async def test_watcher_headers_distinguish_current_and_other_session(
    active_session: ActiveSession,
    expected_text: str,
    monkeypatch: pytest.MonkeyPatch,
    _send_mock: AsyncMock,
) -> None:
    """Watcher messages keep plain headers for current session and command headers for others."""
    monkeypatch.setattr(
        delivery_module.session_manager,
        "get_active_session",
        lambda _chat_id: active_session,
    )

    await delivery_module.send_watcher_message(
        TEST_CHAT_ID,
        "Ответ",
        TEST_SESSION_ID,
        BackendName.CLAUDE,
        session_number=4,
        is_final=True,
    )

    assert _latest_sent_text(_send_mock) == expected_text


@pytest.mark.parametrize(
    ("backend", "is_final", "text", "expected_text"),
    [
        (BackendName.CODEX, True, "Готово", "/3s12 budget ⚡ Codex ✅ Готово"),
        (
            BackendName.CLAUDE,
            False,
            "Думаю",
            "/3s12 budget 🤖 Claude ⏳ <i>Думаю</i>",
        ),
    ],
)
async def test_all_projects_watcher_header_includes_project_and_session_command(
    backend: BackendName,
    is_final: bool,
    text: str,
    expected_text: str,
    _send_mock: AsyncMock,
) -> None:
    """All-project watcher messages keep their /project-session command header."""
    await delivery_module.send_all_projects_watcher_message(
        TEST_CHAT_ID,
        project_number=3,
        session_number=12,
        project_name="budget",
        session_id=TEST_SESSION_ID,
        backend=backend,
        text=text,
        is_final=is_final,
    )

    assert _latest_sent_text(_send_mock) == expected_text


async def test_claude_interaction_resolves_send_response_callback_by_module_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """claude_interaction resolves delivery callbacks through the registered module."""
    old_send_response = AsyncMock()
    new_send_response = AsyncMock()
    callback_module = SimpleNamespace(
        send_response=old_send_response,
        send_telegram_message=AsyncMock(),
    )
    ci_module.init_callbacks(
        send_response_module=callback_module,
        send_response_attr="send_response",
        send_telegram_message_module=callback_module,
        send_telegram_message_attr="send_telegram_message",
    )
    callback_module.send_response = new_send_response
    register_session = AsyncMock(return_value=9)
    monkeypatch.setattr(ci_module.daily_session_registry, "register_session", register_session)

    result = SendResult("OK", TEST_SESSION_ID, False, 0, backend=BackendName.CODEX)
    returned_session_id = await ci_module.handle_claude_result(
        TEST_CHAT_ID, TEST_SESSION_ID, result,
    )

    assert returned_session_id == TEST_SESSION_ID
    old_send_response.assert_not_awaited()
    new_send_response.assert_awaited_once_with(
        TEST_CHAT_ID,
        "OK",
        9,
        BackendName.CODEX,
        is_final=True,
        session_id=TEST_SESSION_ID,
    )
