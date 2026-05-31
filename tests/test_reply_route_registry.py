"""Tests for in-memory Telegram reply routes."""

from claude_manager import reply_route_registry
from claude_manager.coding_agent_backend import BackendName


TEST_CHAT_ID = 12345
PROJECT_A = "/tmp/reply-route-a"
PROJECT_B = "/tmp/reply-route-b"


def setup_function() -> None:
    """Clear route state between tests."""
    reply_route_registry.clear_all()


def test_register_and_get_route_by_chat_and_bot_message_id() -> None:
    """A registered bot message resolves to its exact target."""
    target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_A,
        session_id="session-1",
        backend=BackendName.CODEX,
        session_number=12,
        project_number=3,
        project_name="budget",
    )

    reply_route_registry.register_route(TEST_CHAT_ID, 5001, target)
    resolved = reply_route_registry.get_route(TEST_CHAT_ID, 5001)

    assert resolved is not None
    assert resolved.project_path.endswith("reply-route-a")
    assert resolved.session_id == "session-1"
    assert resolved.backend == BackendName.CODEX
    assert resolved.session_number == 12
    assert resolved.project_number == 3
    assert resolved.project_name == "budget"


def test_same_message_id_in_different_chats_does_not_mix() -> None:
    """Telegram message_id is chat-local, so chat_id is part of the key."""
    first_target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_A,
        session_id="session-a",
        backend=BackendName.CLAUDE,
        session_number=1,
        project_number=1,
        project_name="alpha",
    )
    second_target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_B,
        session_id="session-b",
        backend=BackendName.CODEX,
        session_number=2,
        project_number=2,
        project_name="beta",
    )

    reply_route_registry.register_route(111, 700, first_target)
    reply_route_registry.register_route(222, 700, second_target)

    assert reply_route_registry.get_route(111, 700).session_id == "session-a"
    assert reply_route_registry.get_route(222, 700).session_id == "session-b"


def test_clear_all_removes_routes() -> None:
    """Routes are memory-only and can be cleared to model restart behavior."""
    target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_A,
        session_id="session-1",
        backend=BackendName.CLAUDE,
        session_number=4,
    )
    reply_route_registry.register_route(TEST_CHAT_ID, 900, target)

    reply_route_registry.clear_all()

    assert reply_route_registry.get_route(TEST_CHAT_ID, 900) is None
