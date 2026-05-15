"""Tests for the launchd startup wrapper."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT_PATH = PROJECT_ROOT / "start-claude-manager.sh"


def test_start_wrapper_forwards_shutdown_signal_to_bot_process_tree() -> None:
    """The launchd wrapper must stop the Python bot and its children on restart."""
    script_text = START_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "trap terminate_running_bot_process_tree TERM INT" in script_text
    assert 'terminate_process_tree "$bot_process_id"' in script_text
    assert 'pgrep -P "$parent_process_id"' in script_text
    assert 'kill -TERM "$parent_process_id"' in script_text
    assert 'wait "$bot_process_id"' in script_text
