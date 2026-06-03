"""Tests for Telegram audio input transcription handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import bot as bot_module
from claude_manager import openai_transcription
from claude_manager import telegram_input_handlers as input_handlers


TEST_CHAT_ID = 12345


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install a minimal fake application for input handlers."""
    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.bot.send_message = AsyncMock()
    monkeypatch.setattr(
        input_handlers.session_manager,
        "is_monitoring_mode",
        lambda _chat_id: False,
    )
    monkeypatch.setattr(
        input_handlers.claude_interaction,
        "build_busy_message_if_busy",
        lambda _chat_id: None,
    )
    monkeypatch.setattr(
        input_handlers.reply_route_handler,
        "try_handle_unsupported_attachment_reply",
        AsyncMock(return_value=False),
    )
    input_handlers.init_callbacks(lambda: mock_app, lambda _update: True)
    yield mock_app
    bot_module._init_handler_callbacks()


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


def _make_voice_update() -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = TEST_CHAT_ID
    update.effective_user.id = TEST_CHAT_ID
    update.message.message_id = 77
    update.message.voice = MagicMock(
        file_id="voice-file-id",
        file_unique_id="voice-unique-id",
        mime_type="audio/ogg",
        duration=12,
        file_size=34567,
    )
    update.message.audio = None
    update.message.reply_to_message = None
    return update


@pytest.mark.asyncio()
async def test_handle_voice_transcribes_file_and_sends_text_to_agent(
    app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice input is downloaded, transcribed, and sent as user text."""
    download = AsyncMock(return_value="/tmp/received_files/voice.ogg")
    transcribe = AsyncMock(return_value="Проверь, пожалуйста, задачу")
    send_to_agent = AsyncMock()
    monkeypatch.setattr(
        input_handlers.telegram_file_downloader,
        "download_and_save_file",
        download,
    )
    monkeypatch.setattr(
        input_handlers.openai_transcription,
        "transcribe_audio_file",
        transcribe,
    )
    monkeypatch.setattr(
        input_handlers.claude_interaction,
        "send_to_claude_and_respond",
        send_to_agent,
    )

    update = _make_voice_update()
    await input_handlers.handle_voice(update, _make_context())

    download.assert_awaited_once()
    transcribe.assert_awaited_once_with("/tmp/received_files/voice.ogg")
    send_to_agent.assert_awaited_once_with(
        TEST_CHAT_ID,
        "Проверь, пожалуйста, задачу",
        reply_to_message_id=77,
    )
    app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio()
async def test_handle_voice_download_error_is_reported(
    app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download errors are reported and no agent message is sent."""
    monkeypatch.setattr(
        input_handlers.telegram_file_downloader,
        "download_and_save_file",
        AsyncMock(side_effect=RuntimeError("download failed")),
    )
    send_to_agent = AsyncMock()
    monkeypatch.setattr(
        input_handlers.claude_interaction,
        "send_to_claude_and_respond",
        send_to_agent,
    )

    await input_handlers.handle_voice(_make_voice_update(), _make_context())

    send_to_agent.assert_not_awaited()
    sent_text = app.bot.send_message.call_args.args[1]
    assert "не удалось скачать аудиофайл" in sent_text.lower()


@pytest.mark.asyncio()
async def test_handle_voice_transcription_error_is_reported(
    app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI transcription errors are reported without sending to the agent."""
    monkeypatch.setattr(
        input_handlers.telegram_file_downloader,
        "download_and_save_file",
        AsyncMock(return_value="/tmp/received_files/voice.ogg"),
    )
    monkeypatch.setattr(
        input_handlers.openai_transcription,
        "transcribe_audio_file",
        AsyncMock(side_effect=openai_transcription.OpenAITranscriptionError("api")),
    )
    send_to_agent = AsyncMock()
    monkeypatch.setattr(
        input_handlers.claude_interaction,
        "send_to_claude_and_respond",
        send_to_agent,
    )

    await input_handlers.handle_voice(_make_voice_update(), _make_context())

    send_to_agent.assert_not_awaited()
    sent_text = app.bot.send_message.call_args.args[1]
    assert "не удалось распознать аудиофайл" in sent_text.lower()


@pytest.mark.asyncio()
async def test_handle_voice_missing_openai_key_is_reported(
    app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing OpenAI key is explained without leaking any secret."""
    monkeypatch.setattr(
        input_handlers.telegram_file_downloader,
        "download_and_save_file",
        AsyncMock(return_value="/tmp/received_files/voice.ogg"),
    )
    monkeypatch.setattr(
        input_handlers.openai_transcription,
        "transcribe_audio_file",
        AsyncMock(
            side_effect=openai_transcription.OpenAITranscriptionConfigError(
                "OPENAI_API_KEY is missing"
            )
        ),
    )
    send_to_agent = AsyncMock()
    monkeypatch.setattr(
        input_handlers.claude_interaction,
        "send_to_claude_and_respond",
        send_to_agent,
    )

    await input_handlers.handle_voice(_make_voice_update(), _make_context())

    send_to_agent.assert_not_awaited()
    sent_text = app.bot.send_message.call_args.args[1]
    assert "openai_api_key" in sent_text.lower()
