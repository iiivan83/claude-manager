"""Tests for Telegram audio and voice file download metadata."""

from unittest.mock import MagicMock

from claude_manager.telegram_file_downloader import extract_file_info


def _make_update() -> MagicMock:
    update = MagicMock()
    update.message.photo = None
    update.message.document = None
    update.message.voice = None
    update.message.audio = None
    return update


def test_extract_file_info_voice_uses_mime_extension() -> None:
    """Voice notes are saved with an extension derived from mime_type."""
    update = _make_update()
    update.message.voice = MagicMock(
        file_id="voice-file-id",
        mime_type="audio/ogg",
    )

    assert extract_file_info(update) == ("voice-file-id", "ogg", None)


def test_extract_file_info_audio_prefers_original_filename() -> None:
    """Audio attachments keep their Telegram filename extension."""
    update = _make_update()
    update.message.audio = MagicMock(
        file_id="audio-file-id",
        file_name="note.m4a",
        mime_type="audio/mp4",
    )

    assert extract_file_info(update) == ("audio-file-id", "m4a", "note.m4a")
