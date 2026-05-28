"""Tests for the Linux restart shell script."""

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESTART_SCRIPT_PATH = PROJECT_ROOT / "restart-claude-manager.sh"


def _run_helper(helper_invocation: str) -> subprocess.CompletedProcess[str]:
    """Source the restart script in source-only mode and run a helper invocation."""
    bash_command = (
        "CLAUDE_MANAGER_RESTART_SOURCE_ONLY=1 "
        f"source {RESTART_SCRIPT_PATH}; "
        f"{helper_invocation}"
    )
    return subprocess.run(
        ["bash", "-c", bash_command],
        check=False,
        text=True,
        capture_output=True,
    )


# --- check_editable_install ---


def test_check_editable_install_fails_when_binary_missing(tmp_path: Path) -> None:
    """Нет .venv/bin/claude-manager — return 1 с понятным сообщением."""
    fake_bin = tmp_path / "empty-bin"
    fake_bin.mkdir()

    result = _run_helper(f'check_editable_install "{fake_bin}"')

    assert result.returncode == 1
    assert "не найден" in result.stdout


def test_check_editable_install_fails_when_import_breaks(tmp_path: Path) -> None:
    """Бинарник есть, но import claude_manager падает — return 1."""
    fake_bin = tmp_path / "broken-bin"
    fake_bin.mkdir()
    # Подкладываем фейковый claude-manager (чтобы прошёл первый check)
    fake_claude_manager = fake_bin / "claude-manager"
    fake_claude_manager.write_text("#!/bin/bash\nexit 0\n")
    fake_claude_manager.chmod(0o755)
    # И фейковый python, который падает при -c "import claude_manager"
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/bash\nexit 1\n")
    fake_python.chmod(0o755)

    result = _run_helper(f'check_editable_install "{fake_bin}"')

    assert result.returncode == 1
    assert "не импортируется" in result.stdout


# --- service_is_running ---


def test_service_is_running_returns_zero_when_both_pass() -> None:
    """systemctl is-active 0 + pgrep 0 → return 0."""
    result = _run_helper(
        'systemctl() { return 0; }; '
        'pgrep() { return 0; }; '
        'service_is_running'
    )
    assert result.returncode == 0


def test_service_is_running_returns_nonzero_when_systemctl_inactive() -> None:
    """systemctl is-active не-0 → return 1, даже если pgrep успешен."""
    result = _run_helper(
        'systemctl() { return 3; }; '
        'pgrep() { return 0; }; '
        'service_is_running'
    )
    assert result.returncode != 0


def test_service_is_running_returns_nonzero_when_pgrep_misses() -> None:
    """systemctl is-active 0, но pgrep не нашёл процесс → return 1."""
    result = _run_helper(
        'systemctl() { return 0; }; '
        'pgrep() { return 1; }; '
        'service_is_running'
    )
    assert result.returncode != 0


# --- print_diagnostics_on_failure ---


def test_print_diagnostics_prints_log_and_journal_sections(tmp_path: Path) -> None:
    """Диагностика печатает обе секции, даже если файлов/journal нет."""
    nonexistent_log = tmp_path / "no-such-log.log"

    result = _run_helper(
        'journalctl() { echo "FAKE_JOURNAL_LINE"; }; '
        f'print_diagnostics_on_failure "fake.service" "{nonexistent_log}"'
    )

    assert result.returncode == 0
    assert "строк" in result.stdout  # заголовки секций
    assert "лог не найден" in result.stdout  # fallback при отсутствии файла
    assert "FAKE_JOURNAL_LINE" in result.stdout
