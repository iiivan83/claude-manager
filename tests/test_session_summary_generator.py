"""Tests for LLM-generated session summaries."""

import json

from claude_manager.session_summary_generator import (
    SUMMARY_MAX_LENGTH,
    _clean_generated_summary,
    _extract_summary_from_stdout,
)


class FakeSummaryBackend:
    """Small backend stub that exposes assistant text from JSONL records."""

    def parse_stdout_line_into_event(self, raw_line: str) -> dict:
        """Parse one JSONL line."""
        return json.loads(raw_line)

    def read_assistant_text_from_event(self, event: dict) -> str | None:
        """Return text from fake assistant records only."""
        if event.get("type") != "assistant":
            return None
        text = event.get("text")
        return text if isinstance(text, str) else None


def test_clean_generated_summary_removes_wrapping_noise() -> None:
    """Generated summaries are stored without quotes or a trailing period."""
    result = _clean_generated_summary("  «Загрузка отзывов за период.»\nлишнее")

    assert result == "Загрузка отзывов за период"


def test_clean_generated_summary_limits_length() -> None:
    """Generated summaries are capped before storage in daily_sessions.json."""
    result = _clean_generated_summary("А" * (SUMMARY_MAX_LENGTH + 10))

    assert result == "А" * SUMMARY_MAX_LENGTH


def test_extract_summary_from_stdout_uses_backend_assistant_text() -> None:
    """JSONL stdout is parsed through the backend contract."""
    stdout_text = "\n".join([
        json.dumps({"type": "metadata", "text": "ignore"}, ensure_ascii=False),
        json.dumps({"type": "assistant", "text": "Краткая суть"}, ensure_ascii=False),
    ])

    result = _extract_summary_from_stdout(stdout_text, FakeSummaryBackend())

    assert result == "Краткая суть"
