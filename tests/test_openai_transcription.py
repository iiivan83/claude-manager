"""Tests for OpenAI audio transcription REST client."""

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_manager import openai_transcription


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


@pytest.mark.asyncio()
async def test_transcribe_audio_file_posts_multipart_and_returns_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audio transcription sends file and model, then returns response text."""
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"ogg bytes")
    requests = []

    def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
        requests.append((request, timeout))
        return _FakeResponse({"text": "Распознанный текст"})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
    monkeypatch.setattr(openai_transcription.urllib.request, "urlopen", fake_urlopen)

    text = await openai_transcription.transcribe_audio_file(str(audio_path))

    assert text == "Распознанный текст"
    request, timeout = requests[0]
    assert timeout == openai_transcription.OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS
    assert request.full_url == openai_transcription.OPENAI_TRANSCRIPTIONS_URL
    assert request.get_method() == "POST"
    headers = dict(request.header_items())
    assert headers["Authorization"] == "Bearer test-key"
    assert "multipart/form-data" in headers["Content-type"]
    body = request.data
    assert b"name=\"model\"" in body
    assert b"whisper-1" in body
    assert b"name=\"file\"; filename=\"voice.ogg\"" in body
    assert b"ogg bytes" in body
    assert b"test-key" not in body


@pytest.mark.asyncio()
async def test_transcribe_audio_file_requires_openai_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing OPENAI_API_KEY is a configuration error."""
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"ogg bytes")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(openai_transcription.OpenAITranscriptionConfigError):
        await openai_transcription.transcribe_audio_file(str(audio_path))


@pytest.mark.asyncio()
async def test_transcribe_audio_file_rejects_missing_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON response without text is treated as an API contract error."""
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"ogg bytes")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        openai_transcription.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse({}),
    )

    with pytest.raises(openai_transcription.OpenAITranscriptionError):
        await openai_transcription.transcribe_audio_file(str(audio_path))


@pytest.mark.asyncio()
async def test_transcribe_audio_file_wraps_http_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI HTTP errors are wrapped without exposing the API key."""
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"ogg bytes")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    error = urllib.error.HTTPError(
        url=openai_transcription.OPENAI_TRANSCRIPTIONS_URL,
        code=400,
        msg="Bad Request",
        hdrs=MagicMock(),
        fp=None,
    )
    monkeypatch.setattr(
        openai_transcription.urllib.request,
        "urlopen",
        MagicMock(side_effect=error),
    )

    with pytest.raises(openai_transcription.OpenAITranscriptionError) as exc_info:
        await openai_transcription.transcribe_audio_file(str(audio_path))

    assert "400" in str(exc_info.value)
    assert "test-key" not in str(exc_info.value)
