"""Tests for Telegram reply routes."""

from pathlib import Path

from claude_manager import reply_route_registry
from claude_manager.coding_agent_backend import BackendName


TEST_CHAT_ID = 12345
PROJECT_A = "/tmp/reply-route-a"
PROJECT_B = "/tmp/reply-route-b"


def setup_function() -> None:
    """Clear route state between tests."""
    reply_route_registry.clear_all()
    reply_route_registry._routes_path = None
    reply_route_registry._routes_loaded_from_disk = False


def teardown_function() -> None:
    """Reset route storage state after tests."""
    reply_route_registry.clear_all()
    reply_route_registry._routes_path = None
    reply_route_registry._routes_loaded_from_disk = False


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
    """Routes can be cleared from memory without deleting persisted storage."""
    target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_A,
        session_id="session-1",
        backend=BackendName.CLAUDE,
        session_number=4,
    )
    reply_route_registry.register_route(TEST_CHAT_ID, 900, target)

    reply_route_registry.clear_all()

    assert reply_route_registry.get_route(TEST_CHAT_ID, 900) is None


def test_registered_route_survives_registry_reload(tmp_path: Path) -> None:
    """A bot message route survives an in-process restart reload."""
    routes_path = tmp_path / "reply_routes.json"
    target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_A,
        session_id="session-1",
        backend=BackendName.CODEX,
        session_number=12,
        project_number=3,
        project_name="alpha",
    )

    reply_route_registry.load_routes(routes_path)
    reply_route_registry.register_route(TEST_CHAT_ID, 901, target)
    reply_route_registry.clear_all()

    reply_route_registry.load_routes(routes_path)
    resolved = reply_route_registry.get_route(TEST_CHAT_ID, 901)

    assert resolved == reply_route_registry.ReplyRouteTarget(
        project_path=str(Path(PROJECT_A).resolve()),
        session_id="session-1",
        backend=BackendName.CODEX,
        session_number=12,
        project_number=3,
        project_name="alpha",
    )


def test_register_route_keeps_only_last_200_routes(tmp_path: Path) -> None:
    """Adding route 201 removes the oldest persisted route."""
    routes_path = tmp_path / "reply_routes.json"
    target = reply_route_registry.ReplyRouteTarget(
        project_path=PROJECT_A,
        session_id="session-1",
        backend=BackendName.CODEX,
        session_number=12,
        project_number=3,
        project_name="alpha",
    )

    reply_route_registry.load_routes(routes_path)
    for message_id in range(1, 202):
        reply_route_registry.register_route(TEST_CHAT_ID, message_id, target)

    assert reply_route_registry.get_route(TEST_CHAT_ID, 1) is None
    assert reply_route_registry.get_route(TEST_CHAT_ID, 2) == reply_route_registry.ReplyRouteTarget(
        project_path=str(Path(PROJECT_A).resolve()),
        session_id="session-1",
        backend=BackendName.CODEX,
        session_number=12,
        project_number=3,
        project_name="alpha",
    )
    assert reply_route_registry.get_route(TEST_CHAT_ID, 201) is not None


def test_load_routes_recovers_from_truncated_multibyte_file(tmp_path: Path) -> None:
    """Битый на многобайте reply_routes.json → чистое состояние, запись НЕ блокируется (P3-50)."""
    routes_path = tmp_path / "reply_routes.json"
    # Файл, оборванный посреди 2-байтного UTF-8 символа (\xd0 без второго байта).
    routes_path.write_bytes(b'{"routes":[{"project_name":"\xd0')

    reply_route_registry.load_routes(routes_path)

    # Загрузка не упала, состояние чистое, персистентность НЕ отключена:
    assert reply_route_registry.get_route(1, 1) is None
    assert reply_route_registry._routes_loaded_from_disk is True

    # Регистрация нового маршрута перезаписывает битый файл (запись разрешена).
    target = reply_route_registry.ReplyRouteTarget(
        project_path="/tmp/x",
        session_id="s",
        backend=BackendName.CODEX,
        session_number=1,
    )
    reply_route_registry.register_route(1, 1, target)
    reply_route_registry.clear_all()
    reply_route_registry.load_routes(routes_path)
    assert reply_route_registry.get_route(1, 1) is not None
