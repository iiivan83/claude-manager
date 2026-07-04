"""Tests for dropping an incomplete trailing JSONL line (P2-26)."""

from claude_manager.jsonl_incomplete_trailing_line_dropper import (
    drop_incomplete_trailing_jsonl_line,
)


def test_torn_last_line_without_newline_is_dropped() -> None:
    raw_lines = ['{"type": "assistant"}\n', '{"type": "assi']

    assert drop_incomplete_trailing_jsonl_line(raw_lines) == [
        '{"type": "assistant"}\n',
    ]


def test_complete_json_last_line_without_newline_is_kept() -> None:
    """Валидный JSON без завершающего перевода строки — дописанная запись."""
    raw_lines = ['{"type": "assistant"}\n', '{"type": "result"}']

    assert drop_incomplete_trailing_jsonl_line(raw_lines) == raw_lines


def test_last_line_with_newline_is_kept_even_if_invalid_json() -> None:
    # Строка с '\n' дописана полностью: если это не JSON — это мусор, который
    # парсер пропустит штатно, но из raw-счётчика её убирать нельзя.
    raw_lines = ['{"type": "assistant"}\n', 'not json at all\n']

    assert drop_incomplete_trailing_jsonl_line(raw_lines) == raw_lines


def test_empty_lines_list_is_returned_as_is() -> None:
    assert drop_incomplete_trailing_jsonl_line([]) == []
