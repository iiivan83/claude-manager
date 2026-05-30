"""In-memory Telegram reply anchor registry."""

from dataclasses import dataclass
from pathlib import Path

from claude_manager.coding_agent_backend import BackendName


@dataclass(frozen=True)
class ReplyAnchorKey:
    """Stable key for one project/backend/session reply anchor."""

    project_path: str
    backend: BackendName
    session_id: str


_anchors: dict[ReplyAnchorKey, int] = {}


def _normalize_project_path(project_path: str) -> str:
    """Return a stable absolute project path string."""
    return str(Path(project_path).expanduser().resolve())


def _key(
    project_path: str,
    backend: BackendName,
    session_id: str,
) -> ReplyAnchorKey:
    """Build a normalized registry key."""
    return ReplyAnchorKey(_normalize_project_path(project_path), backend, session_id)


def set_anchor(
    project_path: str,
    backend: BackendName,
    session_id: str,
    message_id: int,
) -> None:
    """Store the active Telegram reply anchor for a session."""
    _anchors[_key(project_path, backend, session_id)] = message_id


def get_anchor(
    project_path: str,
    backend: BackendName,
    session_id: str,
) -> int | None:
    """Return the active Telegram reply anchor for a session."""
    return _anchors.get(_key(project_path, backend, session_id))


def clear_anchor(
    project_path: str,
    backend: BackendName,
    session_id: str,
) -> None:
    """Remove the active Telegram reply anchor for a session."""
    _anchors.pop(_key(project_path, backend, session_id), None)


def move_anchor(
    project_path: str,
    backend: BackendName,
    old_session_id: str,
    new_session_id: str,
) -> None:
    """Move a reply anchor when a temporary session id becomes real."""
    old_key = _key(project_path, backend, old_session_id)
    anchor = _anchors.pop(old_key, None)
    if anchor is not None:
        _anchors[_key(project_path, backend, new_session_id)] = anchor


def clear_all() -> None:
    """Clear all reply anchors."""
    _anchors.clear()
