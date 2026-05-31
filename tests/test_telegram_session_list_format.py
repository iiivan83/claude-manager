"""Tests for compact Telegram /sessions row formatting."""

from types import SimpleNamespace

import pytest

from claude_manager import coding_agent_backend
from claude_manager.coding_agent_backend import BackendName, SessionMessage
from claude_manager import telegram_session_handlers as session_handlers


class FakePreviewBackend:
    """Backend stub that returns full session messages for one file."""

    async def read_messages_from_session_file(
        self,
        _file_path: str,
    ) -> list[SessionMessage]:
        """Return one long user-authored request."""
        return [
            SessionMessage(
                role="user",
                text="# AGENTS.md instructions for /tmp\n<INSTRUCTIONS>service</INSTRUCTIONS>",
                timestamp=None,
                is_empty_response=False,
            ),
            SessionMessage(
                role="user",
                text="Полный текст запроса без обрезания и без многоточия в конце",
                timestamp=None,
                is_empty_response=False,
            )
        ]


@pytest.mark.asyncio()
async def test_resolve_session_list_label_hydrates_truncated_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale truncated preview is replaced with full text from the session file."""
    row = SimpleNamespace(
        backend=BackendName.CODEX,
        file_path="/tmp/session.jsonl",
        preview="Полный текст запроса без обрезания...",
    )
    monkeypatch.setattr(
        coding_agent_backend,
        "get_backend",
        lambda _backend: FakePreviewBackend(),
    )

    label = await session_handlers._resolve_session_list_label(row, "")

    assert label == "Полный текст запроса без обрезания и без многоточия в конце"
