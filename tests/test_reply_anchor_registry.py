"""Tests for in-memory Telegram reply anchors."""

from claude_manager import reply_anchor_registry
from claude_manager.coding_agent_backend import BackendName


PROJECT_A = "/tmp/project-a"
PROJECT_B = "/tmp/project-b"
SESSION_ID = "session-1"


def setup_function() -> None:
    """Clear registry state between tests."""
    reply_anchor_registry.clear_all()


def test_set_and_get_anchor_for_project_backend_session() -> None:
    """Anchor can be read by the same project/backend/session key."""
    reply_anchor_registry.set_anchor(
        PROJECT_A,
        BackendName.CLAUDE,
        SESSION_ID,
        101,
    )

    assert (
        reply_anchor_registry.get_anchor(
            PROJECT_A,
            BackendName.CLAUDE,
            SESSION_ID,
        )
        == 101
    )


def test_anchor_keys_do_not_mix_projects_or_backends() -> None:
    """Anchors from other projects or backends are isolated."""
    reply_anchor_registry.set_anchor(
        PROJECT_A,
        BackendName.CLAUDE,
        SESSION_ID,
        101,
    )
    reply_anchor_registry.set_anchor(
        PROJECT_B,
        BackendName.CODEX,
        SESSION_ID,
        202,
    )

    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID) == 101
    assert reply_anchor_registry.get_anchor(PROJECT_B, BackendName.CODEX, SESSION_ID) == 202
    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CODEX, SESSION_ID) is None


def test_clear_anchor_removes_only_matching_key() -> None:
    """Clearing one key does not remove another key."""
    reply_anchor_registry.set_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID, 101)
    reply_anchor_registry.set_anchor(PROJECT_B, BackendName.CLAUDE, SESSION_ID, 202)

    reply_anchor_registry.clear_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID)

    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CLAUDE, SESSION_ID) is None
    assert reply_anchor_registry.get_anchor(PROJECT_B, BackendName.CLAUDE, SESSION_ID) == 202


def test_move_anchor_transfers_temp_session_to_real_session() -> None:
    """Anchor follows a temporary session id when it becomes real."""
    reply_anchor_registry.set_anchor(
        PROJECT_A,
        BackendName.CODEX,
        "_new_123",
        303,
    )

    reply_anchor_registry.move_anchor(
        PROJECT_A,
        BackendName.CODEX,
        "_new_123",
        "real-session",
    )

    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CODEX, "_new_123") is None
    assert reply_anchor_registry.get_anchor(PROJECT_A, BackendName.CODEX, "real-session") == 303
