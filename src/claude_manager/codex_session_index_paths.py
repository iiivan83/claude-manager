"""Path and directory-signature helpers for the Codex session index."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

UUID_V7_TIMESTAMP_HEX_LENGTH = 12
MILLISECONDS_PER_SECOND = 1000

DirectorySignature = tuple[tuple[str, int, int], ...]


def _normalize_sessions_root(sessions_root: str) -> str:
    expanded = os.path.expanduser(sessions_root) if sessions_root.startswith("~") else sessions_root
    return os.path.abspath(expanded)


def _directory_signature_blocking(
    sessions_root: str,
    today: date,
    lookback_days: int,
) -> DirectorySignature:
    signature: list[tuple[str, int, int]] = []
    for session_dir in _session_dirs_in_window(sessions_root, today, lookback_days):
        if not os.path.isdir(session_dir):
            continue
        try:
            stat_result = os.stat(session_dir)
            rollout_count = len(_rollout_entry_names(session_dir))
        except OSError as error:
            logger.warning("Could not stat Codex session dir %s: %s", session_dir, error)
            continue
        signature.append((session_dir, stat_result.st_mtime_ns, rollout_count))
    return tuple(signature)


def _list_rollout_files_blocking(
    sessions_root: str,
    today: date,
    lookback_days: int,
) -> list[str]:
    file_paths: list[str] = []
    for session_dir in _session_dirs_in_window(sessions_root, today, lookback_days):
        if not os.path.isdir(session_dir):
            continue
        try:
            entry_names = _rollout_entry_names(session_dir)
        except OSError as error:
            logger.warning("Could not list Codex session dir %s: %s", session_dir, error)
            continue
        for entry_name in entry_names:
            file_path = os.path.join(session_dir, entry_name)
            if os.path.isfile(file_path):
                file_paths.append(file_path)
    return file_paths


def _rollout_entry_names(session_dir: str) -> list[str]:
    return sorted(
        entry_name
        for entry_name in os.listdir(session_dir)
        if entry_name.startswith("rollout-") and entry_name.endswith(".jsonl")
    )


def _session_dirs_in_window(
    sessions_root: str,
    today: date,
    lookback_days: int,
) -> list[str]:
    return [
        os.path.join(
            sessions_root,
            f"{session_date:%Y}",
            f"{session_date:%m}",
            f"{session_date:%d}",
        )
        for session_date in (
            today - timedelta(days=days_ago)
            for days_ago in range(max(0, lookback_days))
        )
    ]


def _candidate_paths_from_uuid_v7(sessions_root: str, session_id: str) -> list[str]:
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

    paths = [
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
    return list(dict.fromkeys(paths))


def _file_belongs_to_window(file_path: str, sessions_root: str, key: object) -> bool:
    file_date = _file_date_from_path(file_path, sessions_root)
    if file_date is None:
        return False
    window_end_date = getattr(key, "window_end_date")
    lookback_days = getattr(key, "lookback_days")
    oldest_date = window_end_date - timedelta(days=lookback_days - 1)
    return oldest_date <= file_date <= window_end_date


def _file_date_in_default_window(
    file_path: str,
    sessions_root: str,
    today: date,
    lookback_days: int,
) -> bool:
    file_date = _file_date_from_path(file_path, sessions_root)
    if file_date is None:
        return False
    oldest_date = today - timedelta(days=lookback_days - 1)
    return oldest_date <= file_date <= today


def _file_date_from_path(file_path: str, sessions_root: str) -> date | None:
    try:
        relative_parts = os.path.relpath(file_path, sessions_root).split(os.sep)
        return date(
            int(relative_parts[0]),
            int(relative_parts[1]),
            int(relative_parts[2]),
        )
    except (IndexError, ValueError):
        return None
