"""Generate short human-facing summaries for session lists."""

from __future__ import annotations

import asyncio
import logging
import tempfile

from claude_manager.coding_agent_backend import (
    BackendError,
    BackendName,
    BackendProtocolError,
    CodingAgentBackend,
    get_backend,
)

logger = logging.getLogger(__name__)

SUMMARY_GENERATOR_SESSION_ID = "_new_session_summary"
SUMMARY_TEMP_DIR_PREFIX = "claude-manager-session-summary-"
SUMMARY_MAX_LENGTH = 160
SUBPROCESS_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024


def _build_summary_prompt(user_prompt: str) -> str:
    """Build the LLM prompt that asks for one short session title."""
    return (
        "Сформулируй краткую суть пользовательского запроса для списка сессий. "
        "Ответь только одной фразой на русском языке, без кавычек, без точки в конце. "
        f"Максимум {SUMMARY_MAX_LENGTH} символов.\n\n"
        "Запрос пользователя:\n"
        f"{user_prompt.strip()}"
    )


def _clean_generated_summary(raw_text: str) -> str:
    """Normalize one generated summary for storage in daily_sessions.json."""
    first_line = raw_text.strip().splitlines()[0] if raw_text.strip() else ""
    summary = first_line.strip().strip("\"'`«»“”")
    summary = summary.rstrip(".。")
    return summary


def _extract_summary_from_stdout(
    stdout_text: str,
    backend: CodingAgentBackend,
) -> str:
    """Read backend JSONL stdout and return the generated assistant text."""
    summary_parts: list[str] = []
    for raw_line in stdout_text.splitlines():
        if not raw_line.strip():
            continue
        event = backend.parse_stdout_line_into_event(raw_line)
        if event is None:
            continue
        text = backend.read_assistant_text_from_event(event)
        if text:
            summary_parts.append(text)
    return _clean_generated_summary(" ".join(summary_parts))


async def _run_summary_subprocess(
    backend: CodingAgentBackend,
    prompt_text: str,
    cwd: str,
) -> tuple[str, str, int]:
    """Run one isolated backend subprocess for summary generation."""
    command_args = backend.compose_subprocess_command_args(
        SUMMARY_GENERATOR_SESSION_ID,
        cwd,
        prompt_text,
        [],
    )
    stdin_payload = backend.encode_user_message_for_cli_stdin(prompt_text, [])
    process = await asyncio.create_subprocess_exec(
        *command_args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=SUBPROCESS_BUFFER_LIMIT_BYTES,
        cwd=cwd,
    )
    try:
        stdout_bytes, stderr_bytes = await process.communicate(stdin_payload)
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    return stdout_text, stderr_text, process.returncode or 0


async def generate_session_summary(
    user_prompt: str,
    backend_name: BackendName,
) -> str:
    """Generate a short session summary with an isolated LLM call."""
    if not user_prompt.strip():
        return ""

    backend = get_backend(backend_name)
    prompt_text = _build_summary_prompt(user_prompt)
    try:
        with tempfile.TemporaryDirectory(prefix=SUMMARY_TEMP_DIR_PREFIX) as temp_dir:
            stdout_text, stderr_text, return_code = await _run_summary_subprocess(
                backend,
                prompt_text,
                temp_dir,
            )
    except (OSError, BackendError) as error:
        logger.warning(
            "Не удалось сгенерировать summary сессии через %s: %s",
            backend_name.value,
            error,
        )
        return ""

    if return_code != 0:
        logger.warning(
            "Генератор summary завершился с кодом %s (%s): %s",
            return_code,
            backend_name.value,
            stderr_text[:300],
        )
        return ""

    try:
        return _extract_summary_from_stdout(stdout_text, backend)
    except BackendProtocolError as error:
        logger.warning(
            "Не удалось прочитать stdout генератора summary (%s): %s",
            backend_name.value,
            error,
        )
        return ""
