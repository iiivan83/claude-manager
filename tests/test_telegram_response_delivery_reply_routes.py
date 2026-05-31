"""Tests for reply-route registration during Telegram response delivery."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import (
    config as config_module,
    reply_anchor_registry,
    reply_route_registry,
    telegram_response_delivery as delivery_module,
)
from claude_manager.coding_agent_backend import BackendName
from claude_manager.session_manager import ActiveSession


TEST_CHAT_ID = 12345
TEST_PROJECT_PATH = "/tmp/reply-route-project"
OTHER_PROJECT_PATH = "/tmp/reply-route-other-project"
EXPECTED_PROJECT_PATH = str(Path(TEST_PROJECT_PATH).resolve())
TEST_SESSION_ID = "session-route"


@pytest.fixture(autouse=True)
def _clear_routes() -> None:
    """Reset reply routes around every test."""
    reply_route_registry.clear_all()
    reply_anchor_registry.clear_all()
    yield
    reply_route_registry.clear_all()
    reply_anchor_registry.clear_all()


@pytest.fixture
def _send_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Install fake Telegram delivery dependencies."""
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


@pytest.fixture
def _working_dir() -> None:
    """Pin current project path."""
    original = config_module.WORKING_DIR
    config_module.WORKING_DIR = TEST_PROJECT_PATH
    yield
    config_module.WORKING_DIR = original


def _sent_message(message_id: int) -> MagicMock:
    """Build a Telegram Message double with message_id."""
    message = MagicMock()
    message.message_id = message_id
    return message


@pytest.mark.asyncio()
async def test_send_response_registers_route_for_sent_message(
    monkeypatch: pytest.MonkeyPatch,
    _working_dir: None,
    _send_mock: AsyncMock,
) -> None:
    """Direct session response becomes a route target."""
    _send_mock.return_value = _sent_message(6001)
    monkeypatch.setattr(
        delivery_module.session_manager,
        "get_active_session",
        lambda _chat_id: ActiveSession(TEST_SESSION_ID, BackendName.CODEX),
    )

    await delivery_module.send_response(
        TEST_CHAT_ID,
        "done",
        12,
        BackendName.CODEX,
        is_final=True,
        session_id=TEST_SESSION_ID,
    )

    route = reply_route_registry.get_route(TEST_CHAT_ID, 6001)
    assert route is not None
    assert route.project_path == EXPECTED_PROJECT_PATH
    assert route.session_id == TEST_SESSION_ID
    assert route.backend == BackendName.CODEX
    assert route.session_number == 12


@pytest.mark.asyncio()
async def test_long_response_registers_every_chunk(
    monkeypatch: pytest.MonkeyPatch,
    _working_dir: None,
    _send_mock: AsyncMock,
) -> None:
    """Reply to any chunk of a long answer resolves to the same route."""
    monkeypatch.setattr(
        delivery_module.message_splitter,
        "prepare_message",
        lambda _text: ["first", "second"],
    )
    _send_mock.side_effect = [_sent_message(6101), _sent_message(6102)]

    await delivery_module.send_response(
        TEST_CHAT_ID,
        "long",
        12,
        BackendName.CLAUDE,
        is_final=True,
        session_id=TEST_SESSION_ID,
    )

    first = reply_route_registry.get_route(TEST_CHAT_ID, 6101)
    second = reply_route_registry.get_route(TEST_CHAT_ID, 6102)
    assert first is not None
    assert second is not None
    assert first == second
    assert first.session_id == TEST_SESSION_ID
    assert first.backend == BackendName.CLAUDE
    assert first.session_number == 12
    assert first.project_path == EXPECTED_PROJECT_PATH


@pytest.mark.asyncio()
async def test_all_projects_delivery_registers_full_link_metadata(
    _send_mock: AsyncMock,
) -> None:
    """All-mode message stores project and session numbers for /PsS links."""
    _send_mock.return_value = _sent_message(6201)

    await delivery_module.send_all_projects_watcher_message(
        TEST_CHAT_ID,
        project_number=3,
        session_number=12,
        project_name="budget",
        project_path=TEST_PROJECT_PATH,
        session_id=TEST_SESSION_ID,
        backend=BackendName.CODEX,
        text="all text",
        is_final=True,
    )

    route = reply_route_registry.get_route(TEST_CHAT_ID, 6201)
    assert route is not None
    assert route.project_number == 3
    assert route.session_number == 12
    assert route.project_name == "budget"
    assert route.project_path == TEST_PROJECT_PATH
    assert route.session_id == TEST_SESSION_ID
    assert route.backend == BackendName.CODEX


@pytest.mark.asyncio()
async def test_service_message_without_route_target_is_not_registered(
    _working_dir: None,
    _send_mock: AsyncMock,
) -> None:
    """Delivery without session_id remains non-routable."""
    _send_mock.return_value = _sent_message(6301)

    await delivery_module.send_response(
        TEST_CHAT_ID,
        "legacy",
        12,
        BackendName.CLAUDE,
        is_final=True,
        session_id=None,
    )

    assert reply_route_registry.get_route(TEST_CHAT_ID, 6301) is None


@pytest.mark.asyncio()
async def test_send_response_keeps_original_project_when_file_markers_switch_it(
    monkeypatch: pytest.MonkeyPatch,
    _working_dir: None,
    _send_mock: AsyncMock,
) -> None:
    """Route target keeps the project captured before final file processing."""
    _send_mock.return_value = _sent_message(6401)

    async def switch_project_during_file_markers(
        _bot: object,
        _chat_id: int,
        text: str,
    ) -> str:
        config_module.WORKING_DIR = OTHER_PROJECT_PATH
        return text

    monkeypatch.setattr(
        delivery_module.file_delivery,
        "process_file_markers",
        switch_project_during_file_markers,
    )
    monkeypatch.setattr(
        delivery_module.file_delivery,
        "process_show_file_markers",
        AsyncMock(side_effect=lambda _bot, _chat_id, text: text),
    )

    await delivery_module.send_response(
        TEST_CHAT_ID,
        "done",
        12,
        BackendName.CLAUDE,
        is_final=True,
        session_id=TEST_SESSION_ID,
    )

    route = reply_route_registry.get_route(TEST_CHAT_ID, 6401)
    assert route is not None
    assert route.project_path == EXPECTED_PROJECT_PATH


@pytest.mark.asyncio()
async def test_send_watcher_message_keeps_original_project_when_file_markers_switch_it(
    monkeypatch: pytest.MonkeyPatch,
    _working_dir: None,
    _send_mock: AsyncMock,
) -> None:
    """Watcher reply anchor and route target use the original project."""
    _send_mock.return_value = _sent_message(6501)
    reply_anchor_registry.set_anchor(
        TEST_PROJECT_PATH,
        BackendName.CLAUDE,
        TEST_SESSION_ID,
        909,
    )

    async def switch_project_during_file_markers(
        _bot: object,
        _chat_id: int,
        text: str,
    ) -> str:
        config_module.WORKING_DIR = OTHER_PROJECT_PATH
        return text

    monkeypatch.setattr(
        delivery_module.file_delivery,
        "process_file_markers",
        switch_project_during_file_markers,
    )
    monkeypatch.setattr(
        delivery_module.file_delivery,
        "process_show_file_markers",
        AsyncMock(side_effect=lambda _bot, _chat_id, text: text),
    )

    await delivery_module.send_watcher_message(
        TEST_CHAT_ID,
        "watcher text",
        TEST_SESSION_ID,
        BackendName.CLAUDE,
        session_number=12,
        is_final=True,
    )

    route = reply_route_registry.get_route(TEST_CHAT_ID, 6501)
    assert route is not None
    assert route.project_path == EXPECTED_PROJECT_PATH
    assert _send_mock.await_args.kwargs["reply_to_message_id"] == 909
