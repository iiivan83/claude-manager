"""Tests for the common coding-agent backend contract."""

import signal
from dataclasses import FrozenInstanceError

import pytest

from claude_manager.coding_agent_backend import (
    BackendBinaryNotFoundError,
    BackendError,
    BackendName,
    BackendProtocolError,
    CodingAgentBackend,
    SessionFileInfo,
    SessionFileSnapshot,
    SessionMessage,
    SessionUnreadState,
    StopSignalStep,
    StopStrategy,
    TerminalStatus,
    UnknownBackendError,
    _create_backend_instance,
    get_backend,
)


def test_backend_name_enum_values_are_stable():
    """Backend enum values are the persisted lowercase names."""
    assert BackendName.CLAUDE.value == "claude"
    assert BackendName.CODEX.value == "codex"
    assert isinstance(BackendName.CLAUDE, str)


def test_terminal_status_enum_values_are_stable():
    """Terminal status enum values are the persisted lowercase names."""
    assert TerminalStatus.SUCCESS.value == "success"
    assert TerminalStatus.FAILED.value == "failed"
    assert isinstance(TerminalStatus.SUCCESS, str)


def test_session_file_info_is_frozen():
    """Session-file metadata cannot be mutated after construction."""
    session_file_info = SessionFileInfo(
        session_id="session-1",
        file_path="/tmp/session-1.jsonl",
        last_modified_at=1.5,
        preview="first message",
    )

    with pytest.raises(FrozenInstanceError):
        session_file_info.preview = "changed"


def test_session_message_is_frozen_and_allows_missing_timestamp():
    """Session messages allow absent timestamps but stay immutable."""
    session_message = SessionMessage(
        role="assistant",
        text="done",
        timestamp=None,
        is_empty_response=False,
    )

    with pytest.raises(FrozenInstanceError):
        session_message.text = "changed"


def test_session_file_snapshot_is_frozen():
    """Session snapshots are immutable cursor inputs for watcher code."""
    session_file_snapshot = SessionFileSnapshot(
        messages=[
            SessionMessage(
                role="user",
                text="hello",
                timestamp=1.0,
                is_empty_response=False,
            )
        ],
        raw_record_count=3,
        last_record={"type": "result"},
        is_turn_active=False,
    )

    with pytest.raises(FrozenInstanceError):
        session_file_snapshot.raw_record_count = 4


def test_session_unread_state_is_frozen_and_allows_empty_cursor():
    """Unread state allows the initial no-message cursor and stays immutable."""
    session_unread_state = SessionUnreadState(
        raw_record_count=0,
        last_delivered_idx=-1,
    )

    with pytest.raises(FrozenInstanceError):
        session_unread_state.last_delivered_idx = 0


def test_stop_signal_step_and_strategy_are_frozen():
    """Stop strategy DTOs cannot be rewritten by process lifecycle code."""
    stop_signal_step = StopSignalStep(
        signal_to_send=signal.SIGTERM,
        wait_seconds_before_next=5.0,
    )
    stop_strategy = StopStrategy(steps=(stop_signal_step,))

    with pytest.raises(FrozenInstanceError):
        stop_signal_step.wait_seconds_before_next = 0.0
    with pytest.raises(FrozenInstanceError):
        stop_strategy.steps = ()


def test_backend_error_hierarchy():
    """Backend-specific errors share the module base class."""
    assert issubclass(BackendBinaryNotFoundError, BackendError)
    assert issubclass(BackendProtocolError, BackendError)
    assert issubclass(UnknownBackendError, BackendError)
    assert issubclass(BackendError, Exception)


def test_coding_agent_backend_cannot_be_instantiated_directly():
    """The common backend contract is abstract."""
    with pytest.raises(TypeError):
        CodingAgentBackend()


def test_subclass_without_contract_methods_cannot_be_instantiated():
    """A backend subclass must implement the complete abstract contract."""

    class IncompleteBackend(CodingAgentBackend):
        pass

    with pytest.raises(TypeError) as error_info:
        IncompleteBackend()

    error_message = str(error_info.value)
    required_method_names = (
        "list_all_session_files_for_project",
        "session_file_exists_for_project",
        "read_session_file_snapshot",
        "is_turn_terminal_session_record",
        "is_error_event",
        "read_error_text_from_event",
        "read_terminal_status_from_event",
        "get_stop_strategy",
    )
    for required_method_name in required_method_names:
        assert required_method_name in error_message


def test_get_backend_with_invalid_string_raises_unknown_backend_error():
    """Unknown string backend names produce a diagnostic error."""
    with pytest.raises(UnknownBackendError) as error_info:
        get_backend("not_a_backend")

    error_message = str(error_info.value)
    assert "not_a_backend" in error_message
    assert "claude" in error_message
    assert "codex" in error_message


def test_create_backend_instance_with_invalid_value_lists_available_backends():
    """The lazy factory error lists the requested and available backend names."""
    with pytest.raises(UnknownBackendError) as error_info:
        _create_backend_instance("not_a_backend")

    error_message = str(error_info.value)
    assert "not_a_backend" in error_message
    assert "claude" in error_message
    assert "codex" in error_message


def test_get_backend_returns_codex_singleton():
    """The common factory lazily returns the Codex adapter singleton."""
    from claude_manager.codex_backend import CodexBackend

    first_backend = get_backend(BackendName.CODEX)
    second_backend = get_backend("codex")

    assert isinstance(first_backend, CodexBackend)
    assert first_backend is second_backend
