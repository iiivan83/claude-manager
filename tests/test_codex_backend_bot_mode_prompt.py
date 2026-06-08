"""Tests for Telegram bot-mode prompt rules in the Codex backend."""

import pytest

from claude_manager.codex_backend import CodexBackend


@pytest.fixture()
def backend() -> CodexBackend:
    """Return a fresh Codex backend adapter."""
    return CodexBackend()


def patch_codex_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make command composition independent from a local Codex install."""
    monkeypatch.setattr(
        "claude_manager.codex_backend._resolve_codex_binary_path",
        lambda: "/bin/codex",
    )


def test_new_session_prompt_includes_telegram_file_delivery_rules(
    backend: CodexBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New Codex sessions receive file-delivery marker instructions."""
    patch_codex_binary(monkeypatch)
    user_prompt = "Покажи содержимое файла /tmp/report.txt"

    command_args = backend.compose_subprocess_command_args(
        "_new_abc123def456",
        "/tmp/project",
        user_prompt,
        [],
    )

    prompt_arg = command_args[-1]
    assert "[SEND_FILE:/absolute/path]" in prompt_arg
    assert "[SHOW_FILE:/absolute/path]" in prompt_arg
    assert "absolute path" in prompt_arg
    assert prompt_arg.endswith(user_prompt)


def test_resume_session_prompt_includes_telegram_file_delivery_rules(
    backend: CodexBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resumed Codex sessions receive the same file-delivery marker instructions."""
    patch_codex_binary(monkeypatch)
    user_prompt = "Пришли файлом /tmp/result.md"

    command_args = backend.compose_subprocess_command_args(
        "019dfaeb-7c5b-7ba1-9e56-a33b5e0b512a",
        "/tmp/project",
        user_prompt,
        [],
    )

    prompt_arg = command_args[-1]
    assert "[SEND_FILE:/absolute/path]" in prompt_arg
    assert "[SHOW_FILE:/absolute/path]" in prompt_arg
    assert "absolute path" in prompt_arg
    assert prompt_arg.endswith(user_prompt)
