"""Codex session-file JSONL reading helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime

from claude_manager.coding_agent_backend import (
    SessionFileSnapshot,
    SessionMessage,
)
from claude_manager.session_request_preview import clean_session_request_preview

logger = logging.getLogger(__name__)

ROLLOUT_TYPE_SESSION_META = "session_meta"
ROLLOUT_TYPE_RESPONSE_ITEM = "response_item"
ROLLOUT_TYPE_TURN_CONTEXT = "turn_context"
ROLLOUT_TYPE_COMPACTED = "compacted"
ROLLOUT_TYPE_EVENT_MSG = "event_msg"
BUSY_ROLLOUT_TYPES = frozenset({
    ROLLOUT_TYPE_EVENT_MSG,
    ROLLOUT_TYPE_RESPONSE_ITEM,
    ROLLOUT_TYPE_TURN_CONTEXT,
    ROLLOUT_TYPE_COMPACTED,
})
RAW_RECORD_INDEX_KEY = "_raw_record_index"

EVENT_MSG_SUBTYPE_TASK_COMPLETE = "task_complete"
EVENT_MSG_SUBTYPE_TASK_STARTED = "task_started"
EVENT_MSG_SUBTYPE_TURN_ABORTED = "turn_aborted"
EVENT_MSG_SUBTYPE_ERROR = "error"
EVENT_MSG_TERMINAL_FAILURE_SUBTYPES = frozenset({
    EVENT_MSG_SUBTYPE_ERROR,
    EVENT_MSG_SUBTYPE_TURN_ABORTED,
})

RESPONSE_ITEM_TYPE_MESSAGE = "message"
RESPONSE_ITEM_ROLE_USER = "user"
RESPONSE_ITEM_ROLE_ASSISTANT = "assistant"
CONTENT_BLOCK_TYPE_INPUT_TEXT = "input_text"
CONTENT_BLOCK_TYPE_OUTPUT_TEXT = "output_text"

MAX_RECENT_SESSIONS = 15
PREVIEW_MAX_LENGTH = 120
MAX_LINES_FOR_PREVIEW = 50
LOOKBACK_DAYS_FOR_SESSION_LISTING = 2
MAX_CONCURRENT_FILE_READS = 8

ROLLOUT_FILENAME_PATTERN = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.jsonl$"
)


def _extract_text_from_content_blocks(content_blocks: object) -> str:
    """Extract text from Codex response_item content blocks."""
    if not isinstance(content_blocks, list):
        return ""
    text_parts: list[str] = []
    for content_block in content_blocks:
        if not isinstance(content_block, dict):
            continue
        if content_block.get("type") not in {
            CONTENT_BLOCK_TYPE_INPUT_TEXT,
            CONTENT_BLOCK_TYPE_OUTPUT_TEXT,
        }:
            continue
        text = content_block.get("text")
        if isinstance(text, str):
            text_parts.append(text)
    return "\n".join(text_parts)


def _clean_preview_text(raw_text: str) -> str:
    """Collapse whitespace and trim a Codex session-list preview."""
    return clean_session_request_preview(raw_text, PREVIEW_MAX_LENGTH)


def _read_file_lines_blocking(
    file_path: str,
    max_lines: int | None = None,
) -> list[str]:
    """Read UTF-8 file lines for execution through asyncio.to_thread."""
    with open(file_path, encoding="utf-8") as file_handle:
        if max_lines is None:
            return file_handle.readlines()
        return [line for _, line in zip(range(max_lines), file_handle, strict=False)]


def _parse_jsonl_string_lines(
    raw_lines: list[str],
    file_path: str,
) -> list[dict[str, object]]:
    """Parse JSONL lines, skipping malformed Codex records."""
    parsed_records: list[dict[str, object]] = []
    raw_record_index = 0
    for line_number, raw_line in enumerate(raw_lines, start=1):
        stripped_line = raw_line.strip()
        if not stripped_line:
            continue
        raw_record_index += 1
        try:
            parsed_value = json.loads(stripped_line)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid JSON in Codex session %s at line %d",
                file_path,
                line_number,
            )
            continue
        if isinstance(parsed_value, dict):
            parsed_value[RAW_RECORD_INDEX_KEY] = raw_record_index
            parsed_records.append(parsed_value)
    return parsed_records


def _extract_uuid_from_rollout_filename(file_path: str) -> str | None:
    """Extract the Codex thread UUID from a rollout filename."""
    match = ROLLOUT_FILENAME_PATTERN.match(os.path.basename(file_path))
    return match.group(1) if match else None


def _parse_iso_timestamp_to_unix(raw_timestamp: object) -> float | None:
    """Normalize a Codex ISO timestamp to a Unix timestamp."""
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
            logger.debug("Could not parse Codex timestamp: %r", raw_timestamp)
    return None


def messages_from_jsonl_records(
    parsed_records: list[dict[str, object]],
) -> list[SessionMessage]:
    """Convert parsed Codex JSONL records into backend-neutral messages."""
    messages: list[SessionMessage] = []
    for session_record in parsed_records:
        if session_record.get("type") != ROLLOUT_TYPE_RESPONSE_ITEM:
            continue
        payload = session_record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != RESPONSE_ITEM_TYPE_MESSAGE:
            continue
        role = payload.get("role")
        if role not in {RESPONSE_ITEM_ROLE_USER, RESPONSE_ITEM_ROLE_ASSISTANT}:
            continue
        messages.append(
            SessionMessage(
                role=str(role),
                text=_extract_text_from_content_blocks(payload.get("content")),
                timestamp=_parse_iso_timestamp_to_unix(session_record.get("timestamp")),
                is_empty_response=False,
                raw_record_index=_read_raw_record_index(session_record),
            )
        )
    return messages


def _read_raw_record_index(session_record: dict[str, object]) -> int | None:
    """Return the non-empty JSONL record index for a parsed record."""
    raw_record_index = session_record.get(RAW_RECORD_INDEX_KEY)
    return raw_record_index if isinstance(raw_record_index, int) else None


def _public_record_without_raw_index(
    session_record: dict[str, object] | None,
) -> dict[str, object] | None:
    """Return a parsed record without internal cursor metadata."""
    if session_record is None:
        return None
    public_record = dict(session_record)
    public_record.pop(RAW_RECORD_INDEX_KEY, None)
    return public_record


def is_turn_terminal_session_record(record: dict[str, object]) -> bool:
    """Return whether a Codex session-file record marks turn completion."""
    if record.get("type") != ROLLOUT_TYPE_EVENT_MSG:
        return False
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return False
    payload_type = payload.get("type")
    return (
        payload_type == EVENT_MSG_SUBTYPE_TASK_COMPLETE
        or payload_type in EVENT_MSG_TERMINAL_FAILURE_SUBTYPES
    )


def _compute_is_turn_active_for_codex(last_record: dict[str, object] | None) -> bool:
    """Compute Codex turn activity from the final valid rollout record."""
    if last_record is None:
        return False
    return not is_turn_terminal_session_record(last_record)


async def read_messages_from_session_file(file_path: str) -> list[SessionMessage]:
    """Read backend-neutral messages from one Codex rollout JSONL file."""
    try:
        file_exists = await asyncio.to_thread(os.path.exists, file_path)
        if not file_exists:
            logger.debug("Codex session file not found: %s", file_path)
            return []
        raw_lines = await asyncio.to_thread(_read_file_lines_blocking, file_path)
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return []
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return []

    parsed_records = _parse_jsonl_string_lines(raw_lines, file_path)
    return messages_from_jsonl_records(parsed_records)


async def read_session_file_snapshot(file_path: str) -> SessionFileSnapshot:
    """Read messages and watcher cursor state from one Codex rollout file."""
    return await asyncio.to_thread(_read_session_file_snapshot_blocking, file_path)


async def read_session_file_cursor(file_path: str) -> SessionFileSnapshot:
    """Read lightweight cursor state from one Codex rollout file."""
    return await asyncio.to_thread(_read_session_file_cursor_blocking, file_path)


def _read_session_file_snapshot_blocking(file_path: str) -> SessionFileSnapshot:
    """Read and parse one Codex rollout file in a worker thread."""
    try:
        if not os.path.exists(file_path):
            logger.debug("Codex session file not found: %s", file_path)
            return empty_session_file_snapshot()
        raw_lines = _read_file_lines_blocking(file_path)
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return empty_session_file_snapshot()
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return empty_session_file_snapshot()

    raw_record_count = sum(1 for raw_line in raw_lines if raw_line.strip())
    parsed_records = _parse_jsonl_string_lines(raw_lines, file_path)
    messages = messages_from_jsonl_records(parsed_records)
    last_record = _public_record_without_raw_index(
        parsed_records[-1] if parsed_records else None
    )
    return SessionFileSnapshot(
        messages=messages,
        raw_record_count=raw_record_count,
        last_record=last_record,
        is_turn_active=_compute_is_turn_active_for_codex(last_record),
    )


def _read_session_file_cursor_blocking(file_path: str) -> SessionFileSnapshot:
    """Read raw count and active state without parsing historical messages."""
    try:
        if not os.path.exists(file_path):
            logger.debug("Codex session file not found: %s", file_path)
            return empty_session_file_snapshot()
        raw_record_count, last_record = _read_cursor_record_count_and_last_record(
            file_path
        )
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return empty_session_file_snapshot()
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return empty_session_file_snapshot()

    return SessionFileSnapshot(
        messages=[],
        raw_record_count=raw_record_count,
        last_record=last_record,
        is_turn_active=_compute_is_turn_active_for_codex(last_record),
    )


def _read_cursor_record_count_and_last_record(
    file_path: str,
) -> tuple[int, dict[str, object] | None]:
    """Count non-empty records and parse only the last non-empty Codex record."""
    raw_record_count = 0
    last_raw_record = ""
    with open(file_path, encoding="utf-8") as file_handle:
        for raw_line in file_handle:
            stripped_line = raw_line.strip()
            if not stripped_line:
                continue
            raw_record_count += 1
            last_raw_record = stripped_line

    if not last_raw_record:
        return 0, None
    try:
        parsed_record = json.loads(last_raw_record)
    except json.JSONDecodeError:
        logger.warning("Invalid final JSON record in Codex session %s", file_path)
        return raw_record_count, None
    return (
        raw_record_count,
        parsed_record if isinstance(parsed_record, dict) else None,
    )


def empty_session_file_snapshot() -> SessionFileSnapshot:
    """Return the canonical empty Codex session-file snapshot."""
    return SessionFileSnapshot(
        messages=[],
        raw_record_count=0,
        last_record=None,
        is_turn_active=False,
    )
