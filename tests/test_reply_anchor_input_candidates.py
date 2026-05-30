"""Tests for Telegram input reply-anchor candidate selection."""

from unittest.mock import AsyncMock, MagicMock

from claude_manager import media_group_handler


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
