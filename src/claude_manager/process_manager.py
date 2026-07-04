"""Compatibility facade for Claude/Codex CLI process management."""

from claude_manager.claude_runner import (
    start_process,
    start_subprocess_for_backend,
)
from claude_manager.process_state import (
    ManagedProcess,
    ProcessKey,
    _busy_flags,
    _busy_lock,
    _make_backend_process_key,
    _make_process_key,
    _prefer_existing_process_key_unlocked,
    _processes,
    _remove_session_id_aliases_unlocked,
    _resolve_process_key_alias_unlocked,
    _resolve_session_id_alias_unlocked,
    _session_id_aliases,
    _split_process_key,
    _stop_events,
    has_process,
    is_busy,
    update_session_id,
)
from claude_manager.process_events import (
    _abort_if_send_superseded,
    _check_stop_requested,
    _extract_progress_text,
    _extract_result_text,
    _is_error_result,
    _process_events,
    _send_superseded_or_stopped,
    _should_send_progress,
)
from claude_manager.process_lifecycle import (
    _generate_temp_session_id,
    _restart_process,
    _start_subprocess_for_backend_turn,
    create_process,
)
from claude_manager.process_retry import (
    _build_exhausted_result,
    _classify_permanent_error_result,
    _execute_single_retry,
    _retry_loop,
    _wait_with_stop_check,
)
from claude_manager.process_send import (
    _BACKEND_NOT_PROVIDED,
    _execute_send,
    _prepare_for_send,
    _send_message_backend_aware,
    _send_message_legacy_claude,
    _validate_effective_backend,
    _validate_process_ready,
    send_message,
)
from claude_manager.process_types import (
    CONTENT_BLOCK_TEXT,
    CONTENT_BLOCK_THINKING,
    EMPTY_RESPONSE_MARKER,
    EVENT_TYPE_ASSISTANT,
    EVENT_TYPE_RESULT,
    MAX_RETRIES,
    PROGRESS_THROTTLE_SECONDS,
    RETRY_INTERVAL_SECONDS,
    STOP_CHECK_INTERVAL_SECONDS,
    TEMP_SESSION_PREFIX,
    CodingAgentStartError,
    ProcessManagerError,
    ProcessNotFoundError,
    ProcessStoppedError,
    ProgressCallback,
    RetryCallback,
    SendResult,
    SessionIdCallback,
    StopResult,
)
from claude_manager.process_stop import (
    _apply_backend_stop_strategy,
    _apply_stop_strategy,
    stop_all_processes,
    stop_process,
)
