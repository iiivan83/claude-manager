"""Tests for Codex session metadata reading."""

import json
from pathlib import Path

import pytest

from claude_manager import codex_session_metadata


def write_jsonl_with_truncated_trailing_multibyte_char(
    file_path: Path, complete_records: list[dict[str, object]]
) -> None:
    """Пишет валидные JSONL-записи, затем строку, оборванную посреди 2-байтного UTF-8.

    Воспроизводит rollout, который CLI ещё дописывает: хвост оборван посреди 2-байтной
    UTF-8 последовательности (\\xd0 из 'П'), поэтому open(encoding='utf-8').readlines()
    бросает UnicodeDecodeError (это ValueError, НЕ OSError).
    """
    with file_path.open("wb") as file_handle:
        for record in complete_records:
            file_handle.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        file_handle.write(b'{"type":"session_meta","payload":{"cwd":"\xd0')


@pytest.mark.asyncio
async def test_is_subagent_session_file_survives_truncated_multibyte(
    tmp_path: Path,
) -> None:
    """Проверка субагентности не падает на дописываемом на лету rollout (P2-24)."""
    rollout = tmp_path / "rollout.jsonl"
    write_jsonl_with_truncated_trailing_multibyte_char(rollout, [])

    result = await codex_session_metadata.is_subagent_session_file(str(rollout))

    assert result is False
