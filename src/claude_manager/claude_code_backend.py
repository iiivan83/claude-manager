"""Claude Code CLI implementation of the coding-agent backend contract."""

from __future__ import annotations

import json
import os
import shutil
import signal

from claude_manager.claude_code_session_file_reader import (
    BUSY_EVENT_TYPES,
    CONTENT_BLOCK_TEXT,
    EMPTY_RESPONSE_MARKER,
    EMPTY_RESPONSE_MARKERS,
    EVENT_TYPE_ASSISTANT,
    EVENT_TYPE_RESULT,
    MAX_RECENT_SESSIONS,
    list_all_session_file_infos_for_project,
    list_session_file_infos_for_project,
    read_session_file_cursor as read_claude_session_file_cursor,
    read_session_file_snapshot as read_claude_session_file_snapshot,
    session_file_exists_for_project as claude_session_file_exists_for_project,
)
from claude_manager.claude_code_session_path import _encode_project_path, build_sessions_path
from claude_manager.coding_agent_backend import (
    BackendBinaryNotFoundError,
    BackendName,
    BackendProtocolError,
    CodingAgentBackend,
    PermanentErrorKind,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    StopSignalStep,
    StopStrategy,
    TerminalStatus,
    UnifiedEvent,
)

BACKEND_DISPLAY_NAME_CLAUDE = "🤖 Claude"
CLAUDE_CLI_DEFAULT_PATH = "/usr/local/bin/claude"

# Точное имя API-модели Claude Opus 4.8 (вышла 28.05.2026). Фиксируем версию,
# а не алиас "opus" — чтобы при выходе следующей модели бот не уехал на неё
# автоматически и поведение оставалось воспроизводимым.
CLAUDE_OPUS_MODEL_ID = "claude-opus-4-8"

STREAM_JSON_INPUT_FORMAT = "stream-json"
STREAM_JSON_OUTPUT_FORMAT = "stream-json"
STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024
READ_LINE_TIMEOUT_SECONDS = 1800
TERMINATE_TIMEOUT_SECONDS = 5

CONTENT_BLOCK_THINKING = "thinking"

# В bot-режиме Claude общается с пользователем через Telegram, а не через TUI Claude Code.
# Инструмент AskUserQuestion в Telegram не работает: его «плашка» с вариантами никуда
# не доставляется, и Claude получает пустой ответ → уходит в «разумный дефолт».
# Отключаем инструмент и просим Claude задавать вопросы обычным текстом.
DISALLOWED_TOOLS_IN_BOT_MODE = "AskUserQuestion"
BOT_MODE_SYSTEM_PROMPT_APPENDIX = (
    "You are running inside a Telegram bot, not the interactive Claude Code TUI. "
    "The user cannot see interactive pickers or option menus. "
    "If you need to ask the user a question, write the question as plain text "
    "in your response (with numbered options when appropriate) and wait for the "
    "user's next text message. Never rely on UI-based question tools."
)

CLAUDE_CODE_STOP_STRATEGY = StopStrategy(
    steps=(
        StopSignalStep(signal.SIGTERM, float(TERMINATE_TIMEOUT_SECONDS)),
        StopSignalStep(signal.SIGKILL, 0.0),
    )
)

# Подстроки финального result-события Claude (is_error=true), при которых
# повтор бессмыслен: --resume каждый раз грузит то же самое состояние,
# поэтому каждая из 10 попыток обречена падать снова. Источник строк —
# реальные инциденты в логах бота (dev/docs/logs/root-cause-reports):
#   29-05 "Prompt is too long"   → история сессии не помещается в модель
#   13-04 "You've hit your limit" → исчерпан лимит запросов к Claude
# Сравнение регистронезависимое — Claude присылает текст без гарантий капитализации.
CLAUDE_CONTEXT_OVERFLOW_ERROR_MARKERS = ("prompt is too long",)
CLAUDE_USAGE_LIMIT_ERROR_MARKERS = ("hit your limit",)


def _resolve_claude_binary_path() -> str:
    """Resolve the Claude CLI binary lazily."""
    binary_path = shutil.which("claude")
    if binary_path:
        return binary_path
    if os.path.exists(CLAUDE_CLI_DEFAULT_PATH):
        return CLAUDE_CLI_DEFAULT_PATH
    raise BackendBinaryNotFoundError(
        "Claude Code CLI not found. Ensure 'claude' is in PATH or installed at "
        f"{CLAUDE_CLI_DEFAULT_PATH}."
    )


class ClaudeCodeBackend(CodingAgentBackend):
    """Adapter for Claude Code CLI protocol and session files."""

    @property
    def name(self) -> BackendName:
        """Return the persisted backend name."""
        return BackendName.CLAUDE

    @property
    def display_name(self) -> str:
        """Return the Telegram-facing backend label."""
        return BACKEND_DISPLAY_NAME_CLAUDE

    def compose_subprocess_command_args(
        self,
        session_id: str,
        cwd: str,
        prompt_text: str,
        image_paths: list[str],
    ) -> list[str]:
        """Build Claude CLI argv for one turn."""
        del cwd, prompt_text, image_paths
        command_args = [
            _resolve_claude_binary_path(),
            "-p",
            "--output-format",
            STREAM_JSON_OUTPUT_FORMAT,
            "--verbose",
            "--input-format",
            STREAM_JSON_INPUT_FORMAT,
            "--dangerously-skip-permissions",
            "--model",
            CLAUDE_OPUS_MODEL_ID,
            "--effort",
            "max",
            "--disallowedTools",
            DISALLOWED_TOOLS_IN_BOT_MODE,
            "--append-system-prompt",
            BOT_MODE_SYSTEM_PROMPT_APPENDIX,
        ]
        if not session_id.startswith("_new_"):
            command_args.extend(["--resume", session_id])
        return command_args

    def encode_user_message_for_cli_stdin(
        self,
        prompt_text: str,
        image_paths: list[str],
    ) -> bytes:
        """Encode one user message as Claude stream-json stdin."""
        del image_paths
        message = {
            "type": "user",
            "message": {"role": "user", "content": prompt_text},
        }
        json_line = json.dumps(message, ensure_ascii=False) + "\n"
        return json_line.encode("utf-8")

    def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None:
        """Parse one Claude stream-json stdout line."""
        if not raw_line.strip():
            return None
        try:
            parsed_value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise BackendProtocolError(
                f"Invalid JSON from Claude: {raw_line[:200]!r}"
            ) from error
        return parsed_value if isinstance(parsed_value, dict) else {}

    def is_turn_complete_event(self, event: UnifiedEvent) -> bool:
        """Return whether a stdout event completes the turn."""
        return event.get("type") == EVENT_TYPE_RESULT

    def read_session_id_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract Claude session_id when present."""
        session_id = event.get("session_id")
        return session_id if isinstance(session_id, str) else None

    def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract final assistant text from a Claude result event."""
        if event.get("type") != EVENT_TYPE_RESULT:
            return None
        text = event.get("result")
        if text is None or text == EMPTY_RESPONSE_MARKER:
            return ""
        return text if isinstance(text, str) else str(text)

    def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract progress text from assistant content blocks."""
        if event.get("type") != EVENT_TYPE_ASSISTANT:
            return None

        message = event.get("message", {})
        content_blocks = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(content_blocks, list):
            return None

        text_content = None
        thinking_content = None
        for content_block in content_blocks:
            if not isinstance(content_block, dict):
                continue
            if content_block.get("type") == CONTENT_BLOCK_TEXT and not text_content:
                text_content = content_block.get("text")
            elif (
                content_block.get("type") == CONTENT_BLOCK_THINKING
                and not thinking_content
            ):
                thinking_content = content_block.get("thinking")

        if isinstance(text_content, str) and text_content:
            return text_content
        return thinking_content if isinstance(thinking_content, str) else None

    def locate_session_files_directory_for_project(self, project_dir: str) -> str:
        """Return the Claude session directory for a project."""
        return build_sessions_path(project_dir)

    async def list_session_files_for_project(
        self,
        project_dir: str,
    ) -> list[SessionFileInfo]:
        """Return recent Claude session files for a project."""
        session_file_infos = await list_session_file_infos_for_project(project_dir)
        return session_file_infos[:MAX_RECENT_SESSIONS]

    async def list_all_session_files_for_project(
        self,
        project_dir: str,
        lookback_days: int | None = None,
    ) -> list[SessionFileInfo]:
        """Return Claude session files for operational flows.

        Claude sessions live in a per-project directory, so listing is already
        cheap. lookback_days, when set, filters the result by file mtime so
        callers get the same recency semantics across backends.
        """
        return await list_all_session_file_infos_for_project(
            project_dir,
            lookback_days=lookback_days,
        )

    async def session_file_exists_for_project(
        self,
        session_id: str,
        project_dir: str,
    ) -> bool:
        """Return whether an exact Claude session file exists for a project."""
        return await claude_session_file_exists_for_project(session_id, project_dir)

    async def read_messages_from_session_file(
        self,
        file_path: str,
    ) -> list[SessionMessage]:
        """Read backend-neutral messages from a Claude JSONL session file."""
        return (await self.read_session_file_snapshot(file_path)).messages

    def text_markers_indicating_empty_response(self) -> frozenset[str]:
        """Return Claude markers that mean an empty assistant response."""
        return EMPTY_RESPONSE_MARKERS

    def event_types_meaning_cli_is_busy(self) -> frozenset[str]:
        """Return Claude session-file event types that indicate activity."""
        return BUSY_EVENT_TYPES

    def is_turn_terminal_session_record(self, record: dict[str, object]) -> bool:
        """Return whether a Claude session-file record marks turn completion."""
        return record.get("type") == EVENT_TYPE_RESULT

    async def read_session_file_snapshot(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        """Read messages and watcher cursor state from one Claude JSONL file."""
        return await read_claude_session_file_snapshot(file_path)

    async def read_session_file_cursor(
        self,
        file_path: str,
    ) -> SessionFileSnapshot:
        """Read lightweight cursor state from one Claude JSONL file."""
        return await read_claude_session_file_cursor(file_path)

    def is_error_event(self, event: UnifiedEvent) -> bool:
        """Return whether a Claude stdout event is an error result."""
        return event.get("type") == EVENT_TYPE_RESULT and bool(event.get("is_error"))

    def read_error_text_from_event(self, event: UnifiedEvent) -> str | None:
        """Extract error text from a Claude error result event."""
        if not self.is_error_event(event):
            return None
        error_text = event.get("result")
        return error_text if isinstance(error_text, str) and error_text else None

    def classify_permanent_error(
        self,
        error_text: str | None,
    ) -> PermanentErrorKind | None:
        """Recognize Claude error texts that must not be retried."""
        if not error_text:
            return None
        normalized_error_text = error_text.lower()
        if any(
            marker in normalized_error_text
            for marker in CLAUDE_CONTEXT_OVERFLOW_ERROR_MARKERS
        ):
            return PermanentErrorKind.CONTEXT_OVERFLOW
        if any(
            marker in normalized_error_text
            for marker in CLAUDE_USAGE_LIMIT_ERROR_MARKERS
        ):
            return PermanentErrorKind.USAGE_LIMIT
        return None

    def read_terminal_status_from_event(
        self,
        event: UnifiedEvent,
    ) -> TerminalStatus | None:
        """Return SUCCESS or FAILED for terminal Claude stdout events."""
        if not self.is_turn_complete_event(event):
            return None
        return TerminalStatus.FAILED if self.is_error_event(event) else TerminalStatus.SUCCESS

    def get_stop_strategy(self) -> StopStrategy:
        """Return Claude SIGTERM-to-SIGKILL stop strategy."""
        return CLAUDE_CODE_STOP_STRATEGY
