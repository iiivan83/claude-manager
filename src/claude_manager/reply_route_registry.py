"""Telegram reply route registry."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from claude_manager.coding_agent_backend import BackendName

logger = logging.getLogger(__name__)

ROUTES_FILENAME = "reply_routes.json"
ROUTES_TEMP_SUFFIX = ".tmp"
DEFAULT_ROUTES_PATH = (
    Path.home() / ".local" / "state" / "claude-manager" / ROUTES_FILENAME
)
MAX_PERSISTED_ROUTES = 200


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
_routes_path: Path | None = None
_routes_loaded_from_disk = False


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


def _serialize_target(target: ReplyRouteTarget) -> dict[str, object]:
    """Convert a route target to the persisted JSON shape."""
    return {
        "project_path": target.project_path,
        "session_id": target.session_id,
        "backend": target.backend.value,
        "session_number": target.session_number,
        "project_number": target.project_number,
        "project_name": target.project_name,
    }


def _serialize_routes_to_json_dict() -> dict[str, list[dict[str, object]]]:
    """Serialize in-memory routes to the reply_routes.json shape."""
    return {
        "routes": [
            {
                "chat_id": key.chat_id,
                "bot_message_id": key.bot_message_id,
                "target": _serialize_target(target),
            }
            for key, target in _routes.items()
        ]
    }


def _coerce_int(raw_value: object) -> int | None:
    """Convert JSON value to int without accepting bool."""
    if isinstance(raw_value, int) and not isinstance(raw_value, bool):
        return raw_value
    return None


def _coerce_target(raw_value: object) -> ReplyRouteTarget | None:
    """Convert a JSON value into ReplyRouteTarget."""
    if not isinstance(raw_value, dict):
        return None

    project_path = raw_value.get("project_path")
    session_id = raw_value.get("session_id")
    raw_backend = raw_value.get("backend")
    session_number = _coerce_int(raw_value.get("session_number"))
    project_number = raw_value.get("project_number")
    project_name = raw_value.get("project_name")

    if (
        not isinstance(project_path, str)
        or not isinstance(session_id, str)
        or not isinstance(raw_backend, str)
        or session_number is None
    ):
        return None
    if project_number is not None:
        project_number = _coerce_int(project_number)
        if project_number is None:
            return None
    if project_name is not None and not isinstance(project_name, str):
        return None

    try:
        backend = BackendName(raw_backend)
    except ValueError:
        return None

    return ReplyRouteTarget(
        project_path=project_path,
        session_id=session_id,
        backend=backend,
        session_number=session_number,
        project_number=project_number,
        project_name=project_name,
    )


def _deserialize_routes(raw_data: object) -> dict[ReplyRouteKey, ReplyRouteTarget]:
    """Convert persisted JSON data into route entries."""
    if not isinstance(raw_data, dict):
        return {}
    raw_routes = raw_data.get("routes")
    if not isinstance(raw_routes, list):
        return {}

    routes: dict[ReplyRouteKey, ReplyRouteTarget] = {}
    for raw_route in raw_routes[-MAX_PERSISTED_ROUTES:]:
        if not isinstance(raw_route, dict):
            continue
        chat_id = _coerce_int(raw_route.get("chat_id"))
        bot_message_id = _coerce_int(raw_route.get("bot_message_id"))
        target = _coerce_target(raw_route.get("target"))
        if chat_id is None or bot_message_id is None or target is None:
            continue
        routes[ReplyRouteKey(chat_id, bot_message_id)] = _normalize_target(target)
    return routes


def _prune_old_routes() -> None:
    """Keep the persisted route map bounded."""
    while len(_routes) > MAX_PERSISTED_ROUTES:
        oldest_key = next(iter(_routes))
        _routes.pop(oldest_key, None)


def _save_routes() -> None:
    """Persist routes atomically when registry was loaded from disk."""
    if not _routes_loaded_from_disk or _routes_path is None:
        return

    try:
        _routes_path.parent.mkdir(parents=True, exist_ok=True)
        json_content = json.dumps(
            _serialize_routes_to_json_dict(),
            indent=2,
            ensure_ascii=False,
        )
        temp_path = _routes_path.with_name(ROUTES_FILENAME + ROUTES_TEMP_SUFFIX)
        temp_path.write_text(json_content, "utf-8")
        os.replace(str(temp_path), str(_routes_path))
    except OSError:
        logger.warning("Не удалось сохранить reply-route registry", exc_info=True)


def load_routes(path: Path | None = None) -> None:
    """Load persisted reply routes from disk."""
    global _routes, _routes_path, _routes_loaded_from_disk

    _routes_path = path or DEFAULT_ROUTES_PATH
    _routes_loaded_from_disk = False

    try:
        raw_data = json.loads(_routes_path.read_text("utf-8"))
    except FileNotFoundError:
        _routes = {}
        _routes_loaded_from_disk = True
        logger.info("Файл reply-route registry не найден, начинаю с чистого состояния")
        return
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Порча контента (битый JSON или файл, оборванный CLI посреди многобайтного
        # UTF-8 на лету) лечится одинаково: чистое состояние + запись разрешена
        # (_routes_loaded_from_disk = True), чтобы битый файл перезаписался при следующей
        # регистрации маршрута. В отличие от OSError (транзиентный сбой I/O) держать
        # запись заблокированной здесь бессмысленно.
        _routes = {}
        _routes_loaded_from_disk = True
        logger.warning("Файл reply-route registry повреждён, начинаю с чистого состояния")
        return
    except OSError:
        _routes = {}
        logger.warning("Не удалось загрузить reply-route registry", exc_info=True)
        return

    _routes = _deserialize_routes(raw_data)
    _routes_loaded_from_disk = True
    logger.info("Загружено %d reply-route маршрутов", len(_routes))


def register_route(
    chat_id: int,
    bot_message_id: int,
    target: ReplyRouteTarget,
) -> None:
    """Store a route from an outgoing bot message to a source session."""
    _routes[ReplyRouteKey(chat_id, bot_message_id)] = _normalize_target(target)
    _prune_old_routes()
    _save_routes()


def get_route(chat_id: int, bot_message_id: int) -> ReplyRouteTarget | None:
    """Return the target session for a Telegram reply to a bot message."""
    return _routes.get(ReplyRouteKey(chat_id, bot_message_id))


def clear_all() -> None:
    """Clear all in-memory reply routes."""
    _routes.clear()
