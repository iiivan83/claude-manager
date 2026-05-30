"""Codex rollout-file discovery and session-list metadata helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from claude_manager import codex_session_index
from claude_manager.codex_session_file_reader import (
    LOOKBACK_DAYS_FOR_SESSION_LISTING,
    MAX_LINES_FOR_PREVIEW,
    MAX_RECENT_SESSIONS,
    RESPONSE_ITEM_ROLE_USER,
    RESPONSE_ITEM_TYPE_MESSAGE,
    ROLLOUT_TYPE_RESPONSE_ITEM,
    ROLLOUT_TYPE_SESSION_META,
    _clean_preview_text,
    _extract_text_from_content_blocks,
    _extract_uuid_from_rollout_filename,
    _parse_jsonl_string_lines,
    _read_file_lines_blocking,
)
from claude_manager.coding_agent_backend import SessionFileInfo

logger = logging.getLogger(__name__)

CODEX_BOOTSTRAP_AGENTS_PREFIX = "# AGENTS.md instructions for "
CODEX_BOOTSTRAP_INSTRUCTIONS_MARKER = "<INSTRUCTIONS>"
MAX_CONCURRENT_FILE_READS = 16
UUID_V7_TIMESTAMP_HEX_LENGTH = 12
MILLISECONDS_PER_SECOND = 1000


async def _gather_optional_results_with_concurrency_limit(
    awaitables: list,
) -> list:
    """Run awaitables with a concurrency cap and drop None results."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FILE_READS)

    async def with_semaphore(awaitable):
        should_close = True
        try:
            async with semaphore:
                should_close = False
                return await awaitable
        finally:
            if should_close:
                close = getattr(awaitable, "close", None)
                if close is not None:
                    close()

    tasks = [asyncio.create_task(with_semaphore(awaitable)) for awaitable in awaitables]
    try:
        results = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [result for result in results if result is not None]


def _iter_session_dirs_in_lookback_window(
    sessions_root: str,
    today: date,
    lookback_days: int,
) -> Iterator[str]:
    """Yield existing YYYY/MM/DD session directories from newest to oldest."""
    for days_ago in range(lookback_days):
        session_date = today - timedelta(days=days_ago)
        session_dir = os.path.join(
            sessions_root,
            f"{session_date:%Y}",
            f"{session_date:%m}",
            f"{session_date:%d}",
        )
        if os.path.isdir(session_dir):
            yield session_dir


def _read_session_meta_record_blocking(file_path: str) -> dict[str, object] | None:
    """Return the first session_meta record from a rollout file preview."""
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


def _is_codex_bootstrap_user_text(raw_text: str) -> bool:
    """Return whether text is Codex's injected AGENTS instructions block."""
    stripped_text = raw_text.lstrip()
    return (
        stripped_text.startswith(CODEX_BOOTSTRAP_AGENTS_PREFIX)
        and CODEX_BOOTSTRAP_INSTRUCTIONS_MARKER in stripped_text
    )


def _read_first_user_response_item_blocking(file_path: str) -> object | None:
    """Return content blocks for the first user response_item in a rollout file."""
    raw_lines = _read_file_lines_blocking(file_path, MAX_LINES_FOR_PREVIEW)
    for session_record in _parse_jsonl_string_lines(raw_lines, file_path):
        if session_record.get("type") != ROLLOUT_TYPE_RESPONSE_ITEM:
            continue
        payload = session_record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != RESPONSE_ITEM_TYPE_MESSAGE:
            continue
        if payload.get("role") != RESPONSE_ITEM_ROLE_USER:
            continue
        content_blocks = payload.get("content")
        if _is_codex_bootstrap_user_text(
            _extract_text_from_content_blocks(content_blocks)
        ):
            continue
        return content_blocks
    return None


def _list_rollout_files_blocking(
    sessions_root: str,
    lookback_days: int,
    today: date,
) -> list[str]:
    """Return rollout JSONL files inside the lookback window."""
    rollout_file_paths: list[str] = []
    for session_dir in _iter_session_dirs_in_lookback_window(
        sessions_root,
        today,
        lookback_days,
    ):
        try:
            entry_names = os.listdir(session_dir)
        except OSError as error:
            logger.warning("Could not list Codex session dir %s: %s", session_dir, error)
            continue
        for entry_name in entry_names:
            file_path = os.path.join(session_dir, entry_name)
            if (
                entry_name.startswith("rollout-")
                and entry_name.endswith(".jsonl")
                and os.path.isfile(file_path)
            ):
                rollout_file_paths.append(file_path)
    return rollout_file_paths


def _list_all_rollout_files_blocking(sessions_root: str) -> list[str]:
    """Return all rollout JSONL files under the Codex sessions root."""
    rollout_file_paths: list[str] = []
    for root_dir, _dir_names, file_names in os.walk(sessions_root):
        for file_name in file_names:
            file_path = os.path.join(root_dir, file_name)
            if file_name.startswith("rollout-") and file_name.endswith(".jsonl"):
                rollout_file_paths.append(file_path)
    return rollout_file_paths


def _candidate_paths_from_uuid_v7(sessions_root: str, session_id: str) -> list[str]:
    """Return likely exact rollout paths encoded in a UUIDv7 session id."""
    normalized_session_id = session_id.replace("-", "")
    if len(normalized_session_id) < UUID_V7_TIMESTAMP_HEX_LENGTH:
        return []
    try:
        timestamp = (
            int(normalized_session_id[:UUID_V7_TIMESTAMP_HEX_LENGTH], 16)
            / MILLISECONDS_PER_SECOND
        )
    except ValueError:
        return []

    return [
        os.path.join(
            sessions_root,
            f"{candidate:%Y}",
            f"{candidate:%m}",
            f"{candidate:%d}",
            f"rollout-{candidate:%Y-%m-%dT%H-%M-%S}-{session_id}.jsonl",
        )
        for candidate in (
            datetime.fromtimestamp(timestamp),
            datetime.fromtimestamp(timestamp, timezone.utc),
        )
    ]


def _find_rollout_file_by_uuid_date_blocking(
    sessions_root: str,
    session_id: str,
) -> str | None:
    """Find a rollout file by checking likely UUIDv7 date directories first."""
    for file_path in _candidate_paths_from_uuid_v7(sessions_root, session_id):
        if os.path.isfile(file_path):
            return file_path
    return None


def _sort_paths_by_mtime_descending(file_paths: list[str]) -> list[str]:
    """Sort file paths from newest to oldest by modification time."""
    return sorted(file_paths, key=os.path.getmtime, reverse=True)


def _payload_from_meta_record(meta_record: dict[str, object]) -> dict[str, object]:
    """Return a session_meta payload dict or an empty dict."""
    payload = meta_record.get("payload")
    return payload if isinstance(payload, dict) else {}


async def _read_project_meta_pair(
    file_path: str,
    project_dir: str,
) -> tuple[str, dict[str, object]] | None:
    """Return a file/meta pair when a rollout belongs to project_dir."""
    try:
        meta_record = await asyncio.to_thread(_read_session_meta_record_blocking, file_path)
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return None
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return None
    if meta_record is None:
        logger.debug("Codex session_meta not found in %s", file_path)
        return None
    if _payload_from_meta_record(meta_record).get("cwd") != project_dir:
        return None
    return file_path, meta_record


async def _build_session_file_info(
    file_path: str,
    meta_record: dict[str, object],
) -> SessionFileInfo | None:
    """Build user-facing metadata for one Codex rollout file."""
    payload = _payload_from_meta_record(meta_record)
    raw_session_id = payload.get("id")
    session_id = raw_session_id if isinstance(raw_session_id, str) else None
    session_id = session_id or _extract_uuid_from_rollout_filename(file_path)
    if session_id is None:
        return None
    try:
        last_modified_at = await asyncio.to_thread(os.path.getmtime, file_path)
        content_blocks = await asyncio.to_thread(
            _read_first_user_response_item_blocking,
            file_path,
        )
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return None
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return None

    preview = _clean_preview_text(_extract_text_from_content_blocks(content_blocks))
    return SessionFileInfo(session_id, file_path, last_modified_at, preview)


async def _build_operational_session_file_info(
    file_path: str,
    meta_record: dict[str, object],
) -> SessionFileInfo | None:
    """Build lightweight metadata for watcher and all-project scans."""
    payload = _payload_from_meta_record(meta_record)
    raw_session_id = payload.get("id")
    session_id = raw_session_id if isinstance(raw_session_id, str) else None
    session_id = session_id or _extract_uuid_from_rollout_filename(file_path)
    if session_id is None:
        return None
    try:
        last_modified_at = await asyncio.to_thread(os.path.getmtime, file_path)
    except OSError as error:
        logger.warning("Could not stat Codex session file %s: %s", file_path, error)
        return None
    return SessionFileInfo(session_id, file_path, last_modified_at, preview="")


async def _list_session_file_infos_from_paths(
    file_paths: list[str],
    project_dir: str,
    max_results: int | None,
) -> list[SessionFileInfo]:
    """Filter rollout files by project and convert them to metadata."""
    meta_pairs = await _gather_optional_results_with_concurrency_limit(
        [_read_project_meta_pair(file_path, project_dir) for file_path in file_paths]
    )

    sorted_paths = await asyncio.to_thread(
        _sort_paths_by_mtime_descending,
        [file_path for file_path, _meta_record in meta_pairs],
    )
    meta_by_path = {file_path: meta_record for file_path, meta_record in meta_pairs}
    if max_results is not None:
        sorted_paths = sorted_paths[:max_results]

    return await _gather_optional_results_with_concurrency_limit(
        [
            _build_session_file_info(file_path, meta_by_path[file_path])
            for file_path in sorted_paths
        ]
    )


async def _list_operational_session_file_infos_from_paths(
    file_paths: list[str],
    project_dir: str,
) -> list[SessionFileInfo]:
    """Filter rollout files by project and return lightweight metadata."""
    meta_pairs = await _gather_optional_results_with_concurrency_limit(
        [_read_project_meta_pair(file_path, project_dir) for file_path in file_paths]
    )

    sorted_paths = await asyncio.to_thread(
        _sort_paths_by_mtime_descending,
        [file_path for file_path, _meta_record in meta_pairs],
    )
    meta_by_path = {file_path: meta_record for file_path, meta_record in meta_pairs}
    return await _gather_optional_results_with_concurrency_limit(
        [
            _build_operational_session_file_info(file_path, meta_by_path[file_path])
            for file_path in sorted_paths
        ]
    )


async def _read_project_meta_pair_for_known_projects(
    file_path: str,
    project_dirs: set[str],
) -> tuple[str, str, dict[str, object]] | None:
    """Return project/file/meta when a rollout belongs to one known project."""
    try:
        meta_record = await asyncio.to_thread(_read_session_meta_record_blocking, file_path)
    except PermissionError:
        logger.error("No permission to read Codex session file: %s", file_path)
        return None
    except OSError as error:
        logger.warning("Could not read Codex session file %s: %s", file_path, error)
        return None
    if meta_record is None:
        logger.debug("Codex session_meta not found in %s", file_path)
        return None

    raw_project_dir = _payload_from_meta_record(meta_record).get("cwd")
    if not isinstance(raw_project_dir, str) or raw_project_dir not in project_dirs:
        return None
    return raw_project_dir, file_path, meta_record


async def _build_operational_infos_by_project(
    meta_pairs_by_project: dict[str, list[tuple[str, dict[str, object]]]],
) -> dict[str, list[SessionFileInfo]]:
    """Convert grouped Codex metadata records into sorted lightweight infos."""
    result: dict[str, list[SessionFileInfo]] = {}
    for project_dir, meta_pairs in meta_pairs_by_project.items():
        sorted_paths = await asyncio.to_thread(
            _sort_paths_by_mtime_descending,
            [file_path for file_path, _meta_record in meta_pairs],
        )
        meta_by_path = {
            file_path: meta_record for file_path, meta_record in meta_pairs
        }
        result[project_dir] = await _gather_optional_results_with_concurrency_limit(
            [
                _build_operational_session_file_info(
                    file_path, meta_by_path[file_path]
                )
                for file_path in sorted_paths
            ]
        )
    return result


async def list_session_file_infos_for_project(
    sessions_root: str,
    project_dir: str,
) -> list[SessionFileInfo]:
    """Return recent Codex rollout metadata for one project."""
    if not await asyncio.to_thread(os.path.exists, sessions_root):
        logger.info("Codex sessions directory not found: %s", sessions_root)
        return []
    file_paths = await asyncio.to_thread(
        _list_rollout_files_blocking,
        sessions_root,
        LOOKBACK_DAYS_FOR_SESSION_LISTING,
        date.today(),
    )
    return await _list_session_file_infos_from_paths(
        file_paths,
        project_dir,
        MAX_RECENT_SESSIONS,
    )


async def list_all_session_file_infos_for_project(
    sessions_root: str,
    project_dir: str,
    lookback_days: int | None = None,
) -> list[SessionFileInfo]:
    """Return Codex rollout metadata for one project.

    With lookback_days set the scan touches only the recent N day directories,
    which avoids walking the global ~/.codex/sessions root that can hold tens
    of thousands of unrelated files. lookback_days=None keeps the legacy full
    scan for compat.
    """
    if not await asyncio.to_thread(os.path.exists, sessions_root):
        logger.info("Codex sessions directory not found: %s", sessions_root)
        return []
    if lookback_days is None:
        file_paths = await asyncio.to_thread(
            _list_all_rollout_files_blocking, sessions_root,
        )
        return await _list_operational_session_file_infos_from_paths(file_paths, project_dir)
    return await codex_session_index.list_project_session_file_infos(
        sessions_root, project_dir, lookback_days,
    )


async def list_all_session_file_infos_by_project(
    sessions_root: str,
    project_dirs: list[str],
) -> dict[str, list[SessionFileInfo]]:
    """Return all Codex rollout metadata grouped by exact project path."""
    result: dict[str, list[SessionFileInfo]] = {
        project_dir: [] for project_dir in project_dirs
    }
    if not await asyncio.to_thread(os.path.exists, sessions_root):
        logger.info("Codex sessions directory not found: %s", sessions_root)
        return result

    file_paths = await asyncio.to_thread(_list_all_rollout_files_blocking, sessions_root)
    project_dir_set = set(project_dirs)
    matched_triples = await _gather_optional_results_with_concurrency_limit(
        [
            _read_project_meta_pair_for_known_projects(file_path, project_dir_set)
            for file_path in file_paths
        ]
    )
    meta_pairs_by_project: dict[str, list[tuple[str, dict[str, object]]]] = {
        project_dir: [] for project_dir in project_dirs
    }
    for project_dir, matched_file_path, meta_record in matched_triples:
        meta_pairs_by_project[project_dir].append((matched_file_path, meta_record))

    result.update(await _build_operational_infos_by_project(meta_pairs_by_project))
    return result


async def session_file_exists_for_project(
    sessions_root: str,
    session_id: str,
    project_dir: str,
) -> bool:
    """Return whether an exact Codex rollout belongs to one project."""
    if not await asyncio.to_thread(os.path.exists, sessions_root):
        return False
    likely_file_path = await asyncio.to_thread(
        _find_rollout_file_by_uuid_date_blocking,
        sessions_root,
        session_id,
    )
    if likely_file_path is not None:
        meta_pair = await _read_project_meta_pair(likely_file_path, project_dir)
        if meta_pair is None:
            return False
        payload = _payload_from_meta_record(meta_pair[1])
        meta_session_id = payload.get("id")
        return not isinstance(meta_session_id, str) or meta_session_id == session_id

    file_paths = await asyncio.to_thread(_list_all_rollout_files_blocking, sessions_root)
    for file_path in file_paths:
        if _extract_uuid_from_rollout_filename(file_path) != session_id:
            continue
        meta_pair = await _read_project_meta_pair(file_path, project_dir)
        if meta_pair is None:
            continue
        payload = _payload_from_meta_record(meta_pair[1])
        meta_session_id = payload.get("id")
        if isinstance(meta_session_id, str) and meta_session_id != session_id:
            continue
        return True
    return False


def sessions_root_from_home(home_dir: str) -> str:
    """Return the Codex sessions root under a home directory."""
    return str(Path(home_dir) / ".codex" / "sessions")
