"""In-memory Telegram reply route registry."""

from dataclasses import dataclass
from pathlib import Path

from claude_manager.coding_agent_backend import BackendName


@dataclass(frozen=True)
class ReplyRouteKey:
    """Stable key for one outgoing bot message in one Telegram chat."""

    chat_id: int
    bot_message_id: int


@dataclass(frozen=True)
class ReplyRouteTarget:
    """Target session for a future Telegram reply to a bot message."""

    project_path: str
    session_id: str
    backend: BackendName
    session_number: int
    project_number: int | None = None
    project_name: str | None = None


_routes: dict[ReplyRouteKey, ReplyRouteTarget] = {}


def _normalize_project_path(project_path: str) -> str:
    """Return a stable absolute project path string."""
    return str(Path(project_path).expanduser().resolve())


def _normalize_target(target: ReplyRouteTarget) -> ReplyRouteTarget:
    """Return target with normalized project path."""
    return ReplyRouteTarget(
        project_path=_normalize_project_path(target.project_path),
        session_id=target.session_id,
        backend=target.backend,
        session_number=target.session_number,
        project_number=target.project_number,
        project_name=target.project_name,
    )


def register_route(
    chat_id: int,
    bot_message_id: int,
    target: ReplyRouteTarget,
) -> None:
    """Store a route from an outgoing bot message to a source session."""
    _routes[ReplyRouteKey(chat_id, bot_message_id)] = _normalize_target(target)


def get_route(chat_id: int, bot_message_id: int) -> ReplyRouteTarget | None:
    """Return the target session for a Telegram reply to a bot message."""
    return _routes.get(ReplyRouteKey(chat_id, bot_message_id))


def clear_all() -> None:
    """Clear all in-memory reply routes."""
    _routes.clear()
