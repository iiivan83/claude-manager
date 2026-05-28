"""Common contract for Claude Code and Codex CLI backend adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias


class BackendName(str, Enum):
    """Persisted names of supported coding-agent backends."""

    CLAUDE = "claude"
    CODEX = "codex"


UnifiedEvent: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class SessionFileInfo:
    """Metadata for one CLI session file."""

    session_id: str
    file_path: str
    last_modified_at: float
    preview: str


@dataclass(frozen=True)
class SessionMessage:
    """One user or assistant message read from a session file."""

    role: str
    text: str
    timestamp: float | None
    is_empty_response: bool
    raw_record_index: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SessionFileSnapshot:
    """Backend-neutral snapshot of a session file for watcher cursors."""

    messages: list[SessionMessage]
    raw_record_count: int
    last_record: UnifiedEvent | None
    is_turn_active: bool


@dataclass(frozen=True)
class SessionUnreadState:
    """Backend-neutral unread-delivery cursor for one session file."""

    raw_record_count: int
    last_delivered_idx: int


class TerminalStatus(str, Enum):
    """Backend-neutral terminal status for one CLI turn."""

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class StopSignalStep:
    """One signal and wait interval in a backend stop strategy."""

    signal_to_send: int
    wait_seconds_before_next: float


@dataclass(frozen=True)
class StopStrategy:
    """Ordered signal sequence for stopping one backend subprocess."""

    steps: tuple[StopSignalStep, ...]


class BackendError(Exception):
    """Base exception for coding-agent backend failures."""


class BackendBinaryNotFoundError(BackendError):
    """Raised when a backend CLI binary cannot be found."""


class BackendProtocolError(BackendError):
    """Raised when a backend CLI returns malformed protocol data."""


class UnknownBackendError(BackendError):
    """Raised when code asks for a backend name this process does not support."""

    def __init__(self, requested_backend: object) -> None:
        available_backend_names = ", ".join(backend.value for backend in BackendName)
        super().__init__(
            "Unknown backend "
            f"{requested_backend!r}. Available backends: {available_backend_names}."
        )


class CodingAgentBackend(ABC):
    """Abstract adapter interface implemented by concrete CLI backends."""

    @property
    @abstractmethod
    def name(self) -> BackendName:
        """Return the persisted backend name."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Return the human-facing backend label."""

    @abstractmethod
    def compose_subprocess_command_args(
        self,
        session_id: str,
        cwd: str,
        prompt_text: str,
        image_paths: list[str],
    ) -> list[str]:
        """Build full subprocess argv for one CLI turn."""

    @abstractmethod
    def encode_user_message_for_cli_stdin(
        self,
        prompt_text: str,
        image_paths: list[str],
    ) -> bytes:
        """Encode the user prompt for subprocess stdin."""

    @abstractmethod
    def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None:
        """Parse one stdout JSONL line into a backend event."""

    @abstractmethod
    def is_turn_complete_event(self, event: UnifiedEvent) -> bool:
        """Return whether a stdout event ends the current turn."""

    @abstractmethod
    def read_session_id_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract a session identifier from a stdout event when present."""

    @abstractmethod
    def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract assistant text from a stdout event when present."""

    @abstractmethod
    def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract progress text from a stdout event when present."""

    @abstractmethod
    def locate_session_files_directory_for_project(self, project_dir: str) -> str:
        """Return the backend session-file directory for a project."""

    @abstractmethod
    async def list_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        """Return recent session files for user-facing session lists."""

    @abstractmethod
    async def list_all_session_files_for_project(
        self,
        project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        """Return known project session files for operational flows.

        lookback_days=None preserves the full scan (compat default).
        A positive value restricts the scan to that many recent days, used by
        latency-sensitive callers (watcher reset, pending collection).
        """

    async def list_all_session_files_for_projects(
        self,
        project_dirs: list[str],
    ) -> dict[str, list[SessionFileInfo]]:
        """Return operational session files grouped by project path."""
        return {
            project_dir: await self.list_all_session_files_for_project(project_dir)
            for project_dir in project_dirs
        }

    @abstractmethod
    async def session_file_exists_for_project(
        self,
        session_id: str,
        project_dir: str,
    ) -> bool:
        """Return whether a specific backend session file belongs to a project."""

    @abstractmethod
    async def read_messages_from_session_file(
        self,
        file_path: str,
    ) -> list[SessionMessage]:
        """Read user and assistant messages from a backend session file."""

    @abstractmethod
    def text_markers_indicating_empty_response(self) -> frozenset[str]:
        """Return backend text markers that mean an empty assistant response."""

    @abstractmethod
    def event_types_meaning_cli_is_busy(self) -> frozenset[str]:
        """Return session-file record types that indicate a turn may be active."""

    @abstractmethod
    def is_turn_terminal_session_record(self, record: dict[str, object]) -> bool:
        """Return whether a session-file record marks terminal turn state."""

    @abstractmethod
    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        """Read a complete backend-neutral snapshot of a session file."""

    async def read_session_file_cursor(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        """Read lightweight cursor state for one backend session file."""
        return await self.read_session_file_snapshot(file_path)

    @abstractmethod
    def is_error_event(self, event: UnifiedEvent) -> bool:
        """Return whether a stdout event represents a failed turn."""

    @abstractmethod
    def read_error_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract backend error text from a stdout event when present."""

    @abstractmethod
    def read_terminal_status_from_event(
        self,
        event: UnifiedEvent,
    ) -> TerminalStatus | None:
        """Extract terminal turn status from a stdout event when present."""

    @abstractmethod
    def get_stop_strategy(self) -> StopStrategy:
        """Return the backend-specific subprocess stop strategy."""


_INSTANCES_CACHE: dict[BackendName, CodingAgentBackend] = {}


def _coerce_backend_name(name: BackendName | str) -> BackendName:
    """Convert a persisted backend value into a BackendName."""
    if isinstance(name, BackendName):
        return name
    try:
        return BackendName(name)
    except ValueError as error:
        raise UnknownBackendError(name) from error


def _create_backend_instance(name: BackendName | str) -> CodingAgentBackend:
    """Create a backend adapter instance through lazy concrete imports."""
    backend_name = _coerce_backend_name(name)
    if backend_name == BackendName.CLAUDE:
        from claude_manager.claude_code_backend import ClaudeCodeBackend

        return ClaudeCodeBackend()
    if backend_name == BackendName.CODEX:
        from claude_manager.codex_backend import CodexBackend

        return CodexBackend()
    raise UnknownBackendError(name)


def get_backend(name: BackendName | str) -> CodingAgentBackend:
    """Return the singleton backend adapter for a backend name."""
    backend_name = _coerce_backend_name(name)
    if backend_name not in _INSTANCES_CACHE:
        _INSTANCES_CACHE[backend_name] = _create_backend_instance(backend_name)
    return _INSTANCES_CACHE[backend_name]


def get_all_backends() -> list[CodingAgentBackend]:
    """Return all backend adapters in stable UI order."""
    return [
        get_backend(BackendName.CLAUDE),
        get_backend(BackendName.CODEX),
    ]
