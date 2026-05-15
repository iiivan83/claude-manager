"""Tests for the safe restart shell script."""

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESTART_SCRIPT_PATH = PROJECT_ROOT / "restart-claude-manager.sh"


def _run_restart_script_helper(helper_command: str) -> subprocess.CompletedProcess[str]:
    """Run a restart script helper in a sourced Bash shell."""
    command = (
        "CLAUDE_MANAGER_RESTART_SOURCE_ONLY=1 "
        f"source {RESTART_SCRIPT_PATH}; "
        f"{helper_command}"
    )
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        text=True,
        capture_output=True,
    )


def test_launchctl_status_with_running_pid_is_success_even_after_sigterm() -> None:
    """A live launchd PID means post-flight success even with stale -15 status."""
    completed_process = _run_restart_script_helper(
        "launchctl_service_has_running_pid "
        "'26402\t-15\tcom.ivan.claude-manager'"
    )

    assert completed_process.returncode == 0


def test_launchctl_status_without_pid_is_not_running() -> None:
    """A launchd row without a PID is not a running service."""
    completed_process = _run_restart_script_helper(
        "launchctl_service_has_running_pid "
        "'-\t126\tcom.ivan.claude-manager'"
    )

    assert completed_process.returncode == 1


def test_process_table_with_python_child_is_ready() -> None:
    """Post-flight is ready when the launchd wrapper has a bot Python child."""
    completed_process = _run_restart_script_helper(
        """
        process_table_has_claude_manager_python_child 32325 "$(cat <<'PROCESS_TABLE'
32325 /Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python -c import sys; sys.path.insert(0, "/Users/ivan/Desktop/claude-sandbox/claude_manager/src"); import runpy; runpy._run_module_as_main("claude_manager")
1 /bin/bash /Users/ivan/.local/bin/start-claude-manager.sh
PROCESS_TABLE
)"
        """
    )

    assert completed_process.returncode == 0


def test_process_table_without_python_child_is_not_ready() -> None:
    """A launchd wrapper alone is not enough to mark the bot as ready."""
    completed_process = _run_restart_script_helper(
        """
        process_table_has_claude_manager_python_child 32325 "$(cat <<'PROCESS_TABLE'
1 /bin/bash /Users/ivan/.local/bin/start-claude-manager.sh
32325 sleep 10
PROCESS_TABLE
)"
        """
    )

    assert completed_process.returncode == 1
