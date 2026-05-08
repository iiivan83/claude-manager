"""Claude Code session-file path encoding, listing, and JSONL reading."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime

from claude_manager.claude_code_session_path import build_sessions_path
from claude_manager.coding_agent_backend import (
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
)

logger = logging.getLogger(__name__)

EVENT_TYPE_ASSISTANT = "assistant"
EVENT_TYPE_RESULT = "result"
EVENT_TYPE_USER = "user"
CONTENT_BLOCK_TEXT = "text"
EMPTY_RESPONSE_MARKER = "No response requested."
EMPTY_RESPONSE_MARKERS = frozenset({EMPTY_RESPONSE_MARKER})
BUSY_EVENT_TYPES = frozenset({"assistant", "progress", "queue-operation"})

MAX_RECENT_SESSIONS = 15
PREVIEW_MAX_LENGTH = 120
MAX_LINES_FOR_PREVIEW = 50
MIN_MESSAGE_LENGTH = 2
COMMAND_XML_TAGS = frozenset((
    "command-name", "command-message", "command-args",
    "local-command-stdout", "local-command-caveat",
))

XML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def extract_text_from_message_content(content: object) -> str:
    """Extract the first text block from a Claude message content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for content_block in content:
            if (
                isinstance(content_block, dict)
                and content_block.get("type") == CONTENT_BLOCK_TEXT
            ):
                text = content_block.get("text", "")
                return text if isinstance(text, str) else ""
    return ""


def parse_jsonl_string_lines(raw_lines: list[str], file_path: str) -> list[dict[str, object]]:
    """Parse JSONL lines, skipping malformed records."""
    parsed_records: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(raw_lines, start=1):
        stripped_line = raw_line.strip()
        if not stripped_line:
            continue
        try:
            parsed_value = json.loads(stripped_line)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid JSON in Claude session %s at line %d",
                file_path,
                line_number,
            )
            continue
        if isinstance(parsed_value, dict):
            parsed_records.append(parsed_value)
    return parsed_records


def _clean_preview_text(raw_text: str) -> str:
    """Remove Claude command XML and collapse whitespace for session previews."""
    without_tags = XML_TAG_PATTERN.sub("", raw_text)
    collapsed_text = WHITESPACE_PATTERN.sub(" ", without_tags).strip()
    if len(collapsed_text) > PREVIEW_MAX_LENGTH:
        return collapsed_text[:PREVIEW_MAX_LENGTH] + "..."
    return collapsed_text


def _is_command_xml_message(text: str) -> bool:
    """Return whether text contains Claude slash-command XML markers."""
    return any(f"<{tag_name}" in text for tag_name in COMMAND_XML_TAGS)


def _extract_first_user_message_text(parsed_records: list[dict[str, object]]) -> str:
    """Return the first non-meta, non-command user message text."""
    for session_record in parsed_records:
        if session_record.get("type") != EVENT_TYPE_USER:
            continue
        if session_record.get("isMeta"):
            continue
        message = session_record.get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        text = extract_text_from_message_content(content)
        stripped_text = text.strip()
        if not stripped_text or len(stripped_text) < MIN_MESSAGE_LENGTH:
            continue
        if _is_command_xml_message(text):
            continue
        return text
    return ""


def _read_file_lines_blocking(file_path: str, max_lines: int | None = None) -> list[str]:
    """Read UTF-8 file lines for execution through asyncio.to_thread."""
    with open(file_path, encoding="utf-8") as file_handle:
        if max_lines is None:
            return file_handle.readlines()
        return [line for _, line in zip(range(max_lines), file_handle, strict=False)]


def _list_jsonl_file_paths_blocking(directory: str) -> list[str]:
    """Return absolute JSONL file paths in one directory."""
    jsonl_file_paths: list[str] = []
    for entry_name in os.listdir(directory):
        file_path = os.path.join(directory, entry_name)
        if entry_name.endswith(".jsonl") and os.path.isfile(file_path):
            jsonl_file_paths.append(file_path)
    return jsonl_file_paths


def _sort_paths_by_mtime_descending(file_paths: list[str]) -> list[str]:
    """Sort file paths from newest to oldest by modification time."""
    return sorted(file_paths, key=os.path.getmtime, reverse=True)


def _normalize_session_message_timestamp(raw_timestamp: object) -> float | None:
    """Normalize a Claude JSONL timestamp to a Unix timestamp."""
    if raw_timestamp is None:
        return None
    if isinstance(raw_timestamp, int | float):
        return float(raw_timestamp)
    if isinstance(raw_timestamp, str):
        try:
            return datetime.fromisoformat(
                raw_timestamp.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            logger.debug("Could not parse Claude timestamp: %r", raw_timestamp)
    return None


def messages_from_jsonl_records(
    parsed_records: list[dict[str, object]],
) -> list[SessionMessage]:
    """Convert parsed Claude JSONL records into backend-neutral messages."""
    messages: list[SessionMessage] = []
    for session_record in parsed_records:
        record_type = session_record.get("type")
        if record_type not in {EVENT_TYPE_USER, EVENT_TYPE_ASSISTANT}:
            continue
        if record_type == EVENT_TYPE_USER and session_record.get("isMeta"):
            continue

        message = session_record.get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        text = extract_text_from_message_content(content)
        messages.append(
            SessionMessage(
                role=str(record_type),
                text=text,
                timestamp=_normalize_session_message_timestamp(
                    session_record.get("timestamp")
                ),
                is_empty_response=text in EMPTY_RESPONSE_MARKERS,
            )
        )
    return messages


async def _read_session_file_metadata(file_path: str) -> SessionFileInfo | None:
    """Read session metadata used by session-list UI."""
    try:
        raw_lines = await asyncio.to_thread(
            _read_file_lines_blocking,
            file_path,
            MAX_LINES_FOR_PREVIEW,
        )
        last_modified_at = await asyncio.to_thread(os.path.getmtime, file_path)
    except PermissionError:
        logger.error("No permission to read Claude session file: %s", file_path)
        return None
    except OSError as error:
        logger.warning("Could not read Claude session file %s: %s", file_path, error)
        return None

    parsed_records = parse_jsonl_string_lines(raw_lines, file_path)
    if not parsed_records:
        return None

    file_name = os.path.basename(file_path).removesuffix(".jsonl")
    first_record_session_id = parsed_records[0].get("sessionId", file_name)
    session_id = (
        first_record_session_id
        if isinstance(first_record_session_id, str)
        else file_name
    )
    preview = _clean_preview_text(_extract_first_user_message_text(parsed_records))

    return SessionFileInfo(
        session_id=session_id,
        file_path=file_path,
        last_modified_at=last_modified_at,
        preview=preview,
    )


async def list_session_file_infos_for_project(
    project_dir: str,
) -> list[SessionFileInfo]:
    """Return all Claude session-file metadata for a project."""
    sessions_dir = build_sessions_path(project_dir)
    if not await asyncio.to_thread(os.path.exists, sessions_dir):
        logger.warning("Claude sessions directory not found: %s", sessions_dir)
        return []
    if not await asyncio.to_thread(os.path.isdir, sessions_dir):
        logger.warning("Claude sessions path is not a directory: %s", sessions_dir)
        return []

    try:
        file_paths = await asyncio.to_thread(
            _list_jsonl_file_paths_blocking,
            sessions_dir,
        )
        sorted_file_paths = await asyncio.to_thread(
            _sort_paths_by_mtime_descending,
            file_paths,
        )
    except OSError as error:
        logger.error("Could not list Claude session files in %s: %s", sessions_dir, error)
        return []

    session_file_infos: list[SessionFileInfo] = []
    for file_path in sorted_file_paths:
        session_file_info = await _read_session_file_metadata(file_path)
        if session_file_info is not None:
            session_file_infos.append(session_file_info)
    return session_file_infos


async def session_file_exists_for_project(
    session_id: str,
    project_dir: str,
) -> bool:
    """Return whether an exact Claude session file exists for a project."""
    sessions_dir = build_sessions_path(project_dir)
    file_path = os.path.join(sessions_dir, f"{session_id}.jsonl")
    try:
        return await asyncio.to_thread(os.path.isfile, file_path)
    except OSError as error:
        logger.warning("Could not check Claude session file %s: %s", file_path, error)
        return False


async def read_session_file_snapshot(file_path: str) -> SessionFileSnapshot:
    """Read messages and watcher cursor state from one Claude JSONL file."""
    try:
        file_exists = await asyncio.to_thread(os.path.exists, file_path)
        if not file_exists:
            logger.debug("Claude session file not found: %s", file_path)
            return empty_session_file_snapshot()
        raw_lines = await asyncio.to_thread(_read_file_lines_blocking, file_path)
    except PermissionError:
        logger.error("No permission to read Claude session file: %s", file_path)
        return empty_session_file_snapshot()
    except OSError as error:
        logger.warning("Could not read Claude session file %s: %s", file_path, error)
        return empty_session_file_snapshot()

    raw_record_count = sum(1 for raw_line in raw_lines if raw_line.strip())
    parsed_records = parse_jsonl_string_lines(raw_lines, file_path)
    messages = messages_from_jsonl_records(parsed_records)
    last_record = parsed_records[-1] if parsed_records else None
    is_turn_active = (
        last_record is not None
        and last_record.get("type") in BUSY_EVENT_TYPES
    )
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_record_count,
        last_record=last_record,
        is_turn_active=is_turn_active,
    )


def empty_session_file_snapshot() -> SessionFileSnapshot:
    """Return the canonical empty session-file snapshot."""
    return SessionFileSnapshot(
        messages=[],
        raw_record_count=0,
        last_record=None,
        is_turn_active=False,
    )
