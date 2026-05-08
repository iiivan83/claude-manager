"""Codex CLI implementation of the coding-agent backend contract."""

from __future__ import annotations

import json
import os
import shutil
import signal

from claude_manager.codex_session_file_reader import (
    BUSY_ROLLOUT_TYPES, CONTENT_BLOCK_TYPE_INPUT_TEXT,
    CONTENT_BLOCK_TYPE_OUTPUT_TEXT, EVENT_MSG_SUBTYPE_ERROR,
    EVENT_MSG_SUBTYPE_TASK_COMPLETE, EVENT_MSG_SUBTYPE_TASK_STARTED,
    EVENT_MSG_SUBTYPE_TURN_ABORTED, EVENT_MSG_TERMINAL_FAILURE_SUBTYPES,
    LOOKBACK_DAYS_FOR_SESSION_LISTING, MAX_CONCURRENT_FILE_READS,
    MAX_LINES_FOR_PREVIEW, MAX_RECENT_SESSIONS, PREVIEW_MAX_LENGTH,
    RESPONSE_ITEM_ROLE_ASSISTANT, RESPONSE_ITEM_ROLE_USER,
    RESPONSE_ITEM_TYPE_MESSAGE, ROLLOUT_FILENAME_PATTERN,
    ROLLOUT_TYPE_COMPACTED, ROLLOUT_TYPE_EVENT_MSG,
    ROLLOUT_TYPE_RESPONSE_ITEM, ROLLOUT_TYPE_SESSION_META,
    ROLLOUT_TYPE_TURN_CONTEXT, WHITESPACE_PATTERN, _clean_preview_text,
    _compute_is_turn_active_for_codex, _extract_text_from_content_blocks,
    _extract_uuid_from_rollout_filename, _parse_iso_timestamp_to_unix,
    _parse_jsonl_string_lines, _read_file_lines_blocking,
    is_turn_terminal_session_record as _is_codex_turn_terminal_session_record,
    read_messages_from_session_file as read_codex_messages_from_session_file,
    read_session_file_snapshot as read_codex_session_file_snapshot,
)
from claude_manager.codex_session_file_listing import (
    _iter_session_dirs_in_lookback_window, _list_all_rollout_files_blocking,
    _list_rollout_files_blocking, _read_first_user_response_item_blocking,
    _read_session_meta_record_blocking, _sort_paths_by_mtime_descending,
    list_all_session_file_infos_for_project, list_session_file_infos_for_project,
    session_file_exists_for_project as codex_session_file_exists_for_project,
    sessions_root_from_home,
)
from claude_manager.coding_agent_backend import (
    BackendBinaryNotFoundError,
    BackendName,
    BackendProtocolError,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    StopSignalStep,
    StopStrategy,
    TerminalStatus,
    UnifiedEvent,
)

BACKEND_DISPLAY_NAME_CODEX = "⚡ Codex"
CODEX_BINARY_NAME = "codex"
CODEX_CLI_DEFAULT_PATH = os.path.expanduser("~/.npm-global/bin/codex")
CODEX_SESSIONS_RELATIVE_DIR = ".codex/sessions"

STDOUT_EVENT_TYPE_THREAD_STARTED = "thread.started"
STDOUT_EVENT_TYPE_TURN_STARTED = "turn.started"
STDOUT_EVENT_TYPE_TURN_COMPLETED = "turn.completed"
STDOUT_EVENT_TYPE_TURN_FAILED = "turn.failed"
STDOUT_EVENT_TYPE_ITEM_COMPLETED = "item.completed"
STDOUT_TURN_TERMINAL_EVENT_TYPES = frozenset({
    STDOUT_EVENT_TYPE_TURN_COMPLETED,
    STDOUT_EVENT_TYPE_TURN_FAILED,
})

ITEM_TYPE_AGENT_MESSAGE = "agent_message"
ITEM_TYPE_REASONING = "reasoning"

CLI_FLAG_JSON = "--json"
CLI_FLAG_BYPASS_APPROVALS = "--dangerously-bypass-approvals-and-sandbox"
CLI_FLAG_SKIP_GIT_CHECK = "--skip-git-repo-check"
CLI_FLAG_CWD = "-C"
CLI_SUBCOMMAND_EXEC = "exec"
CLI_SUBCOMMAND_RESUME = "resume"

STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024
READ_LINE_TIMEOUT_SECONDS = 1800
TERMINATE_TIMEOUT_SECONDS = 5
STOP_SIGINT_TIMEOUT_SECONDS = 5
STOP_SIGTERM_TIMEOUT_SECONDS = 5

CODEX_STOP_STRATEGY = StopStrategy(
    steps=(
        StopSignalStep(signal.SIGINT, float(STOP_SIGINT_TIMEOUT_SECONDS)),
        StopSignalStep(signal.SIGTERM, float(STOP_SIGTERM_TIMEOUT_SECONDS)),
        StopSignalStep(signal.SIGKILL, 0.0),
    )
)


def _resolve_codex_binary_path() -> str:
    """Resolve the Codex CLI binary lazily."""
    binary_path = shutil.which(CODEX_BINARY_NAME)
    if binary_path:
        return binary_path
    if os.path.exists(CODEX_CLI_DEFAULT_PATH):
        return CODEX_CLI_DEFAULT_PATH
    raise BackendBinaryNotFoundError(
        "Codex CLI not found. Ensure 'codex' is in PATH or install it with "
        "npm install -g @openai/codex."
    )


class CodexBackend(CodingAgentBackend):
    """Adapter for Codex CLI protocol and rollout session files."""

    @property
    def name(self) -> BackendName:
        """Return the persisted backend name."""
        return BackendName.CODEX

    @property
    def display_name(self) -> str:
        """Return the Telegram-facing backend label."""
        return BACKEND_DISPLAY_NAME_CODEX

    def compose_subprocess_command_args(
        self,
        session_id: str,
        cwd: str,
        prompt_text: str,
        image_paths: list[str],
    ) -> list[str]:
        """Build Codex CLI argv for one turn."""
        del image_paths
        common_flags = [
            CLI_FLAG_JSON,
            CLI_FLAG_BYPASS_APPROVALS,
            CLI_FLAG_SKIP_GIT_CHECK,
        ]
        if session_id.startswith("_new_"):
            return [
                _resolve_codex_binary_path(),
                CLI_SUBCOMMAND_EXEC,
                *common_flags,
                CLI_FLAG_CWD,
                cwd,
                prompt_text,
            ]
        return [
            _resolve_codex_binary_path(),
            CLI_SUBCOMMAND_EXEC,
            CLI_SUBCOMMAND_RESUME,
            session_id,
            *common_flags,
            prompt_text,
        ]

    def encode_user_message_for_cli_stdin(
        self,
        prompt_text: str,
        image_paths: list[str],
    ) -> bytes:
        """Return empty stdin because Codex receives prompts through argv."""
        del prompt_text, image_paths
        return b""

    def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None:
        """Parse one Codex --json stdout line."""
        if not raw_line.strip():
            return None
        try:
            parsed_value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise BackendProtocolError(
                f"Invalid JSON from Codex: {raw_line[:200]!r}"
            ) from error
        return parsed_value if isinstance(parsed_value, dict) else {}

    def is_turn_complete_event(self, event: UnifiedEvent) -> bool:
        """Return whether a stdout event completes the turn."""
        return event.get("type") in STDOUT_TURN_TERMINAL_EVENT_TYPES

    def read_session_id_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract Codex thread_id from the thread.started event."""
        if event.get("type") != STDOUT_EVENT_TYPE_THREAD_STARTED:
            return None
        thread_id = event.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None

    def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract assistant text from a Codex item.completed event."""
        if event.get("type") != STDOUT_EVENT_TYPE_ITEM_COMPLETED:
            return None
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != ITEM_TYPE_AGENT_MESSAGE:
            return None
        text = item.get("text")
        return text if isinstance(text, str) else None

    def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract reasoning progress text from a Codex item.completed event."""
        if event.get("type") != STDOUT_EVENT_TYPE_ITEM_COMPLETED:
            return None
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != ITEM_TYPE_REASONING:
            return None
        text = item.get("text")
        return text if isinstance(text, str) else None

    def locate_session_files_directory_for_project(self, project_dir: str) -> str:
        """Return the global Codex sessions root."""
        del project_dir
        return sessions_root_from_home(os.path.expanduser("~"))

    async def list_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        """Return recent Codex rollout files for a project."""
        return await list_session_file_infos_for_project(
            self.locate_session_files_directory_for_project(project_dir),
            project_dir,
        )

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        """Return all Codex rollout files for operational flows."""
        return await list_all_session_file_infos_for_project(
            self.locate_session_files_directory_for_project(project_dir),
            project_dir,
        )

    async def session_file_exists_for_project(
        self,
        session_id: str,
        project_dir: str,
    ) -> bool:
        """Return whether an exact Codex rollout file belongs to a project."""
        return await codex_session_file_exists_for_project(
            self.locate_session_files_directory_for_project(project_dir),
            session_id,
            project_dir,
        )

    async def read_messages_from_session_file(
        self,
        file_path: str,
    ) -> list[SessionMessage]:
        """Read backend-neutral messages from a Codex rollout file."""
        return await read_codex_messages_from_session_file(file_path)

    def text_markers_indicating_empty_response(self) -> frozenset[str]:
        """Return Codex markers that mean an empty assistant response."""
        return frozenset()

    def event_types_meaning_cli_is_busy(self) -> frozenset[str]:
        """Return Codex rollout record types that indicate activity."""
        return BUSY_ROLLOUT_TYPES

    def is_turn_terminal_session_record(self, record: dict[str, object]) -> bool:
        """Return whether a Codex rollout record marks turn completion."""
        return _is_codex_turn_terminal_session_record(record)

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        """Read messages and watcher cursor state from one Codex rollout file."""
        return await read_codex_session_file_snapshot(file_path)

    def is_error_event(self, event: UnifiedEvent) -> bool:
        """Return whether a Codex stdout event is a failed turn."""
        return event.get("type") == STDOUT_EVENT_TYPE_TURN_FAILED

    def read_error_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract error text from a Codex turn.failed event."""
        if not self.is_error_event(event):
            return None
        error_payload = event.get("error")
        if not isinstance(error_payload, dict):
            return None
        message = error_payload.get("message")
        return message if isinstance(message, str) and message else None

    def read_terminal_status_from_event(
        self,
        event: UnifiedEvent,
    ) -> TerminalStatus | None:
        """Return SUCCESS or FAILED for terminal Codex stdout events."""
        if not self.is_turn_complete_event(event):
            return None
        if self.is_error_event(event):
            return TerminalStatus.FAILED
        return TerminalStatus.SUCCESS

    def get_stop_strategy(self) -> StopStrategy:
        """Return Codex SIGINT-to-SIGTERM-to-SIGKILL stop strategy."""
        return CODEX_STOP_STRATEGY
