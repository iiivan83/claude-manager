"""In-memory cursor and backoff state that the session file watcher keeps per session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


MessageCallback = Callable[..., Awaitable[None]]
CurrentSessionGetter = Callable[[int], Awaitable[object]]


@dataclass
class SessionWatcherState:
    """Cursor state for one session file inside one backend watcher."""

    raw_count: int = 0
    parsed_message_count: int = 0
    cli_process_is_currently_writing_session_file: bool = False
    last_delivered_idx: int = -1
    paused_at: float | None = None
    # True, пока запрос обрабатывает send_to_claude_and_respond: обработчик сам
    # доставит финальный ответ этой сессии. Watcher в это время не доставляет финал,
    # даже если agent-silence watchdog снял паузу для показа промежуточного прогресса —
    # иначе финал придёт дважды (от обработчика и от watcher).
    handler_owns_final_delivery: bool = False


@dataclass
class MissingFileRetryState:
    """Retry/backoff state for temporarily missing session files."""

    first_seen_missing_at_monotonic: float
    retry_after_monotonic: float
    attempt_count: int = 1
