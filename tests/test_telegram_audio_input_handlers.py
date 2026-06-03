"""Tests for Telegram audio input transcription handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_manager import bot as bot_module
from claude_manager import openai_transcription
from claude_manager import process_manager
from claude_manager import reply_route_handler
from claude_manager import reply_route_registry
from claude_manager import telegram_input_handlers as input_handlers
from claude_manager.coding_agent_backend import BackendName
from claude_manager.process_manager import SendResult


TEST_CHAT_ID = 12345
BOT_MESSAGE_ID = 8001
CURRENT_PROJECT = "/tmp/current-project"


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
    input_handlers.init_callbacks(lambda: mock_app, lambda _update: True)
    yield mock_app
    bot_module._init_handler_callbacks()


@pytest.fixture(autouse=True)
def _reset_reply_routes() -> None:
    """Reset reply routes around audio input tests."""
    reply_route_registry.clear_all()
    reply_route_handler._inflight_route_sends.clear()
    yield
    for task in list(reply_route_handler._background_tasks):
        task.cancel()
    reply_route_registry.clear_all()
    reply_route_handler._inflight_route_sends.clear()


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
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


def _target() -> reply_route_registry.ReplyRouteTarget:
    """Build a route target for voice reply tests."""
    return reply_route_registry.ReplyRouteTarget(
        project_path=CURRENT_PROJECT,
        session_id="session-route",
        backend=BackendName.CODEX,
        session_number=15,
        project_number=2,
        project_name="claude-manager",
    )


async def _drain_background_tasks() -> None:
    """Wait for routed background sends started by the handler."""
    tasks = list(reply_route_handler._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


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
async def test_handle_voice_reply_routes_transcript_to_saved_session(
    app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice reply is transcribed and routed to the saved bot-message session."""
    reply_route_registry.register_route(TEST_CHAT_ID, BOT_MESSAGE_ID, _target())
    download = AsyncMock(return_value="/tmp/received_files/voice.ogg")
    transcribe = AsyncMock(return_value="Проверь и исправь это")
    send_message = AsyncMock(
        return_value=SendResult(
            text="accepted",
            session_id="session-route",
            is_error=False,
            retries_used=0,
            backend=BackendName.CODEX,
        )
    )
    send_to_active_session = AsyncMock()
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
        send_to_active_session,
    )
    monkeypatch.setattr(
        input_handlers.session_manager,
        "is_monitoring_mode",
        lambda _chat_id: True,
    )
    monkeypatch.setattr(
        input_handlers.all_projects_monitor,
        "is_enabled_for_chat",
        lambda _chat_id: True,
    )
    monkeypatch.setattr(process_manager, "send_message", send_message)
    monkeypatch.setattr(process_manager, "is_busy", lambda *_args: False)
    monkeypatch.setattr(
        reply_route_handler,
        "_target_project_is_available",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        reply_route_handler,
        "_target_session_is_available",
        AsyncMock(return_value=True),
    )

    update = _make_voice_update()
    update.message.reply_to_message = MagicMock()
    update.message.reply_to_message.message_id = BOT_MESSAGE_ID
    context = _make_context()
    await input_handlers.handle_voice(update, context)
    await _drain_background_tasks()

    send_message.assert_awaited_once_with(
        "session-route",
        "Проверь и исправь это",
        backend=BackendName.CODEX,
        cwd=CURRENT_PROJECT,
    )
    send_to_active_session.assert_not_awaited()
    context.bot.send_message.assert_awaited()


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
