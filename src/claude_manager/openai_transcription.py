"""OpenAI audio transcription client."""

import asyncio
import json
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path


OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_TRANSCRIPTION_MODEL_ENV = "OPENAI_TRANSCRIPTION_MODEL"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS = 60.0
OPENAI_AUDIO_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_OPENAI_TRANSCRIPTION_MODEL = "whisper-1"


class OpenAITranscriptionError(Exception):
    """OpenAI audio transcription failed."""


class OpenAITranscriptionConfigError(OpenAITranscriptionError):
    """OpenAI transcription is not configured."""


async def transcribe_audio_file(file_path: str) -> str:
    """Transcribe an audio file with OpenAI."""
    audio_path = Path(file_path)
    api_key = _get_api_key()
    model = _get_model()
    return await asyncio.to_thread(_transcribe_audio_file_sync, audio_path, api_key, model)


def _get_api_key() -> str:
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        raise OpenAITranscriptionConfigError(f"{OPENAI_API_KEY_ENV} is not configured")
    return api_key


def _get_model() -> str:
    model = os.environ.get(OPENAI_TRANSCRIPTION_MODEL_ENV, "").strip()
    return model or DEFAULT_OPENAI_TRANSCRIPTION_MODEL


def _transcribe_audio_file_sync(audio_path: Path, api_key: str, model: str) -> str:
    if not audio_path.is_file():
        raise OpenAITranscriptionError(f"Audio file does not exist: {audio_path}")
    if audio_path.stat().st_size > OPENAI_AUDIO_MAX_FILE_SIZE_BYTES:
        raise OpenAITranscriptionError("Audio file is larger than OpenAI's 25 MB limit")

    body, boundary = _build_multipart_body(audio_path, model)
    request = urllib.request.Request(
        OPENAI_TRANSCRIPTIONS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS,
        ) as response:
            return _extract_text(response.read())
    except urllib.error.HTTPError as error:
        raise OpenAITranscriptionError(_format_http_error(error)) from error
    except urllib.error.URLError as error:
        raise OpenAITranscriptionError(f"OpenAI transcription request failed: {error}") from error


def _build_multipart_body(audio_path: Path, model: str) -> tuple[bytes, str]:
    boundary = f"claude-manager-{uuid.uuid4().hex}"
    filename = _safe_multipart_filename(audio_path.name)
    chunks = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="model"\r\n\r\n',
        model.encode(),
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        (
            'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'
        ).encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        audio_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def _safe_multipart_filename(filename: str) -> str:
    return (
        filename.replace("\\", "_")
        .replace('"', "_")
        .replace("\r", "_")
        .replace("\n", "_")
    )


def _extract_text(response_body: bytes) -> str:
    try:
        payload = json.loads(response_body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenAITranscriptionError("OpenAI transcription returned invalid JSON") from error

    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise OpenAITranscriptionError("OpenAI transcription response does not contain text")
    return text.strip()


def _format_http_error(error: urllib.error.HTTPError) -> str:
    try:
        response_body = error.read().decode(errors="replace").strip()
    except Exception:
        response_body = ""
    if response_body:
        response_body = response_body[:500]
        return f"OpenAI transcription HTTP {error.code}: {response_body}"
    return f"OpenAI transcription HTTP {error.code}: {error.reason}"
