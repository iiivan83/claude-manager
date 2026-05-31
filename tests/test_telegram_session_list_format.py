"""Tests for compact Telegram /sessions row formatting."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio()
async def test_handle_sessions_limits_each_title_to_two_line_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long session titles are compacted before the whole /sessions response is sent."""
    sent_messages: list[str] = []

    async def send_message(_chat_id: int, text: str, **_kwargs: object) -> object:
        sent_messages.append(text)
        return SimpleNamespace(message_id=len(sent_messages))

    app = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=send_message))
    )
    rows = [
        SimpleNamespace(
            session_id=f"session-{index}",
            backend=BackendName.CLAUDE,
            preview=f"Длинная задача {index} " + ("А" * 300),
        )
        for index in range(5)
    ]

    monkeypatch.setattr(session_handlers, "_application_getter", lambda: app)
    monkeypatch.setattr(session_handlers, "_access_checker", lambda _update: True)
    monkeypatch.setattr(
        session_handlers.recent_sessions_refresh,
        "get_project_recent_sessions",
        AsyncMock(return_value=SimpleNamespace(rows=rows, degraded_messages=[])),
    )
    monkeypatch.setattr(
        session_handlers.daily_session_registry,
        "register_session",
        AsyncMock(side_effect=range(1, 6)),
    )
    monkeypatch.setattr(
        session_handlers.daily_session_registry,
        "get_session_summary",
        AsyncMock(return_value=""),
    )

    update = MagicMock()
    update.effective_chat.id = 12345

    await session_handlers.handle_sessions(update, MagicMock())

    assert len(sent_messages) == 1
    assert "/1" in sent_messages[0]
    assert "/5" in sent_messages[0]
    assert "А" * 200 not in sent_messages[0]
    for line in sent_messages[0].splitlines():
        title = line.split(" ", maxsplit=2)[2]
        assert len(title) <= session_handlers.SESSION_LIST_TITLE_MAX_LENGTH
