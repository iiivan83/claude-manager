"""Claude Code project-path encoding for session directory names."""

from __future__ import annotations

import os
import re

CLAUDE_PROJECTS_RELATIVE_DIR = ".claude/projects"
MAX_SANITIZED_PATH_LENGTH = 200
SANITIZE_PATH_PATTERN = re.compile(r"[^a-zA-Z0-9]")


def _to_base36(value: int) -> str:
    """Encode a positive integer as JavaScript Number.toString(36)."""
    if value == 0:
        return "0"
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    digits: list[str] = []
    remaining_value = value
    while remaining_value:
        remaining_value, remainder = divmod(remaining_value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))


def _djb2_hash(text: str) -> int:
    """Return the signed 32-bit djb2 hash used by Claude Code fallback code."""
    hash_value = 0
    utf16_bytes = text.encode("utf-16-le")
    for byte_index in range(0, len(utf16_bytes), 2):
        code_unit = int.from_bytes(utf16_bytes[byte_index:byte_index + 2], "little")
        hash_value = ((hash_value << 5) - hash_value + code_unit) & 0xFFFFFFFF
        if hash_value >= 0x80000000:
            hash_value -= 0x100000000
    return hash_value


def _sanitize_project_path(project_dir: str) -> str:
    """Replace non-ASCII-alphanumeric path characters with dashes."""
    return SANITIZE_PATH_PATTERN.sub("-", project_dir)


def _encode_project_path(project_dir: str) -> str:
    """Encode a project path into Claude Code's project folder name."""
    sanitized_path = _sanitize_project_path(project_dir)
    if len(sanitized_path) <= MAX_SANITIZED_PATH_LENGTH:
        return sanitized_path

    hash_suffix = _to_base36(abs(_djb2_hash(project_dir)))
    return f"{sanitized_path[:MAX_SANITIZED_PATH_LENGTH]}-{hash_suffix}"


def build_sessions_path(project_dir: str) -> str:
    """Build the Claude sessions directory for a project."""
    home_dir = os.path.expanduser("~")
    projects_root = os.path.join(home_dir, CLAUDE_PROJECTS_RELATIVE_DIR)
    encoded_name = _encode_project_path(project_dir)
    exact_path = os.path.join(projects_root, encoded_name)

    sanitized_path = _sanitize_project_path(project_dir)
    if len(sanitized_path) <= MAX_SANITIZED_PATH_LENGTH or os.path.isdir(exact_path):
        return exact_path

    prefix = sanitized_path[:MAX_SANITIZED_PATH_LENGTH] + "-"
    try:
        for entry_name in os.listdir(projects_root):
            candidate_path = os.path.join(projects_root, entry_name)
            if entry_name.startswith(prefix) and os.path.isdir(candidate_path):
                return candidate_path
    except OSError:
        return exact_path

    return exact_path
