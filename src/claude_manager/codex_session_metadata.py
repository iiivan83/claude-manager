"""Shared helpers for Codex session metadata."""

from __future__ import annotations

import asyncio
import json
import logging

from claude_manager.codex_session_file_reader import (
    MAX_LINES_FOR_PREVIEW,
    ROLLOUT_TYPE_SESSION_META,
    _read_file_lines_blocking,
)

logger = logging.getLogger(__name__)

CODEX_THREAD_SOURCE_SUBAGENT = "subagent"


def payload_from_session_meta(meta_record: dict[str, object]) -> dict[str, object]:
    """Return a Codex session_meta payload dict or an empty dict."""
    payload = meta_record.get("payload")
    return payload if isinstance(payload, dict) else {}


def is_subagent_meta_record(meta_record: dict[str, object]) -> bool:
    """Return whether Codex metadata describes a spawned subagent session."""
    payload = payload_from_session_meta(meta_record)
    source = payload.get("source")
    return (
        payload.get("thread_source") == CODEX_THREAD_SOURCE_SUBAGENT
        or source == CODEX_THREAD_SOURCE_SUBAGENT
        or (isinstance(source, dict) and CODEX_THREAD_SOURCE_SUBAGENT in source)
    )


def read_session_meta_record_blocking(file_path: str) -> dict[str, object] | None:
    """Return the first Codex session_meta record from a rollout file preview."""
    raw_lines = _read_file_lines_blocking(file_path, MAX_LINES_FOR_PREVIEW)
    for raw_line in raw_lines:
        try:
            parsed_value = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(parsed_value, dict)
            and parsed_value.get("type") == ROLLOUT_TYPE_SESSION_META
        ):
            return parsed_value
    return None


async def is_subagent_session_file(file_path: str) -> bool:
    """Return whether a Codex rollout file belongs to a spawned subagent."""
    try:
        meta_record = await asyncio.to_thread(read_session_meta_record_blocking, file_path)
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return False
    except UnicodeDecodeError as error:
        # Файл дописывается CLI на лету: хвост оборван посреди многобайтного UTF-8.
        # UnicodeDecodeError — подкласс ValueError, не OSError, поэтому нужна отдельная
        # ветка. Возвращаем тот же fallback, что и OSError; watcher повторит на след. опросе.
        logger.debug(
            "Codex session file %s not fully readable yet "
            "(incomplete UTF-8, likely mid-write): %s",
            file_path,
            error,
        )
        return False
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return False
    return meta_record is not None and is_subagent_meta_record(meta_record)
