"""Tests for Telegram input message_id reply-anchor candidates."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import bot as bot_module
from claude_manager import media_group_handler, session_manager
from claude_manager.bot import handle_document, handle_message, handle_photo


ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


def _make_update(
    text: str = "hello",
    *,
    message_id: int = 321,
) -> MagicMock:
    """Create a fake Telegram update with a stable message_id."""
    update = MagicMock()
    update.message.text = text
    update.message.message_id = message_id
    update.message.chat.id = TEST_CHAT_ID
    update.message.chat_id = TEST_CHAT_ID
    update.effective_chat.id = TEST_CHAT_ID
    update.message.from_user.id = ALLOWED_USER_ID
    update.effective_user.id = ALLOWED_USER_ID
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    update.message.photo = None
    update.message.document = None
    update.message.media_group_id = None
    return update


def _make_context() -> MagicMock:
    """Create a fake Telegram handler context."""
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


@pytest.fixture(autouse=True)
def _setup_bot(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install fake bot dependencies for transport handlers."""
    fake_application = MagicMock()
    fake_application.bot = MagicMock()
    original_application = bot_module._application
    original_allowed = bot_module.config.ALLOWED_USER_IDS
    bot_module._application = fake_application
    bot_module.config.ALLOWED_USER_IDS = {ALLOWED_USER_ID}
    monkeypatch.setattr(session_manager, "is_monitoring_mode", lambda _chat_id: False)
    monkeypatch.setattr(
        bot_module.claude_interaction,
        "build_busy_message_if_busy",
        lambda _chat_id: None,
    )
    yield fake_application
    bot_module._application = original_application
    bot_module.config.ALLOWED_USER_IDS = original_allowed


@pytest.mark.asyncio()
async def test_handle_message_passes_incoming_message_id_as_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text messages pass their Telegram message_id as anchor candidate."""
    send_to_claude = AsyncMock()
    monkeypatch.setattr(
        bot_module.claude_interaction,
        "send_to_claude_and_respond",
        send_to_claude,
    )

    await handle_message(_make_update("hello", message_id=321), _make_context())

    send_to_claude.assert_awaited_once_with(
        TEST_CHAT_ID,
        "hello",
        reply_to_message_id=321,
    )


@pytest.mark.asyncio()
async def test_handle_single_photo_passes_photo_message_id_as_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single photo input passes its Telegram message_id as anchor candidate."""
    send_to_claude = AsyncMock()
    monkeypatch.setattr(
        bot_module.claude_interaction,
        "send_to_claude_and_respond",
        send_to_claude,
    )
    monkeypatch.setattr(
        bot_module.telegram_file_downloader,
        "download_and_save_file",
        AsyncMock(return_value="/tmp/photo.jpg"),
    )
    update = _make_update(message_id=456)
    update.message.photo = [MagicMock()]
    update.message.caption = "describe"

    await handle_photo(update, _make_context())

    assert send_to_claude.await_args.kwargs["reply_to_message_id"] == 456


@pytest.mark.asyncio()
async def test_handle_document_passes_document_message_id_as_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document input passes its Telegram message_id as anchor candidate."""
    send_to_claude = AsyncMock()
    monkeypatch.setattr(
        bot_module.claude_interaction,
        "send_to_claude_and_respond",
        send_to_claude,
    )
    monkeypatch.setattr(
        bot_module.telegram_file_downloader,
        "download_and_save_file",
        AsyncMock(return_value="/tmp/report.pdf"),
    )
    update = _make_update(message_id=457)
    update.message.document = MagicMock()
    update.message.document.file_name = "report.pdf"

    await handle_document(update, _make_context())

    assert send_to_claude.await_args.kwargs["reply_to_message_id"] == 457


def test_select_album_anchor_prefers_caption_message() -> None:
    """Album anchor is the message that has a caption."""
    first = _make_update(message_id=10)
    first.message.caption = None
    second = _make_update(message_id=11)
    second.message.caption = "caption"

    assert media_group_handler.select_album_anchor_message_id([first, second]) == 11


def test_select_album_anchor_falls_back_to_first_message() -> None:
    """Album anchor falls back to the first message when captions are absent."""
    first = _make_update(message_id=10)
    first.message.caption = None
    second = _make_update(message_id=11)
    second.message.caption = None

    assert media_group_handler.select_album_anchor_message_id([first, second]) == 10
