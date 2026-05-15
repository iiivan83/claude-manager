"""Tests for the current CLI backend registry."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_manager.coding_agent_backend import BackendName
from claude_manager import current_backend_registry


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset module-level registry state before each test."""
    current_backend_registry._current_backend = current_backend_registry.DEFAULT_BACKEND
    current_backend_registry._loaded_from_disk = False
    yield
    current_backend_registry._current_backend = current_backend_registry.DEFAULT_BACKEND
    current_backend_registry._loaded_from_disk = False


@pytest.fixture()
def backend_file(tmp_path: Path) -> Path:
    """Return an isolated state file path."""
    file_path = tmp_path / ".claude-manager-current-backend"
    with patch.object(current_backend_registry.config, "CURRENT_BACKEND_FILE", file_path):
        yield file_path


def test_get_current_returns_default_before_load() -> None:
    """Before disk load, the registry defaults to Claude."""
    assert current_backend_registry.get_current() == BackendName.CLAUDE
    assert current_backend_registry.DEFAULT_BACKEND == BackendName.CLAUDE


def test_load_state_missing_file_defaults_to_claude_and_allows_write(
    backend_file: Path,
) -> None:
    """A missing state file is a normal first-run case."""
    current_backend_registry.load_state()

    assert current_backend_registry.get_current() == BackendName.CLAUDE
    assert current_backend_registry._loaded_from_disk is True


def test_set_current_persists_json_and_updates_memory_after_load(
    backend_file: Path,
) -> None:
    """set_current writes JSON and updates memory after a successful save."""
    current_backend_registry.load_state()

    current_backend_registry.set_current(BackendName.CODEX)

    assert current_backend_registry.get_current() == BackendName.CODEX
    assert json.loads(backend_file.read_text("utf-8")) == {"backend": "codex"}
    assert "BackendName" not in backend_file.read_text("utf-8")


def test_load_state_reads_json_backend_value(backend_file: Path) -> None:
    """load_state reads the persisted backend value from JSON."""
    backend_file.write_text(json.dumps({"backend": "codex", "extra": 1}), "utf-8")

    current_backend_registry.load_state()

    assert current_backend_registry.get_current() == BackendName.CODEX
    assert current_backend_registry._loaded_from_disk is True


@pytest.mark.parametrize("legacy_text", ["claude", "codex"])
def test_load_state_migrates_legacy_plain_text(
    backend_file: Path,
    legacy_text: str,
) -> None:
    """Legacy plain-text state is accepted and rewritten as JSON."""
    backend_file.write_text(legacy_text, "utf-8")

    current_backend_registry.load_state()

    assert current_backend_registry.get_current() == BackendName(legacy_text)
    assert json.loads(backend_file.read_text("utf-8")) == {"backend": legacy_text}
    assert current_backend_registry._loaded_from_disk is True


@pytest.mark.parametrize(
    "raw_content",
    [
        "not-json{{",
        json.dumps({"other_key": "claude"}),
        json.dumps({"backend": "gemini"}),
    ],
)
def test_load_state_bad_content_defaults_to_claude_and_allows_rewrite(
    backend_file: Path,
    raw_content: str,
) -> None:
    """Corrupt or unknown content falls back to Claude and remains writable."""
    backend_file.write_text(raw_content, "utf-8")

    current_backend_registry.load_state()

    assert current_backend_registry.get_current() == BackendName.CLAUDE
    assert current_backend_registry._loaded_from_disk is True
    current_backend_registry.set_current(BackendName.CODEX)
    assert json.loads(backend_file.read_text("utf-8")) == {"backend": "codex"}


def test_load_state_permission_error_blocks_later_set_current(
    backend_file: Path,
) -> None:
    """Unexpected read failures block writes so valid disk data is not overwritten."""
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        current_backend_registry.load_state()

    assert current_backend_registry.get_current() == BackendName.CLAUDE
    assert current_backend_registry._loaded_from_disk is False
    with patch(
        "claude_manager.current_backend_registry._save_state"
    ) as mock_save_state:
        with pytest.raises(RuntimeError, match="не загружен с диска"):
            current_backend_registry.set_current(BackendName.CODEX)
        mock_save_state.assert_not_called()


def test_set_current_before_load_raises_and_does_not_write(backend_file: Path) -> None:
    """set_current refuses to write before load_state has allowed disk writes."""
    with pytest.raises(RuntimeError, match="не загружен с диска"):
        current_backend_registry.set_current(BackendName.CODEX)

    assert current_backend_registry.get_current() == BackendName.CLAUDE
    assert not backend_file.exists()


def test_set_current_does_not_change_memory_when_save_fails(
    backend_file: Path,
) -> None:
    """The registry changes memory only after atomic disk write succeeds."""
    current_backend_registry.load_state()

    with patch(
        "claude_manager.current_backend_registry.os.replace",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError, match="disk full"):
            current_backend_registry.set_current(BackendName.CODEX)

    assert current_backend_registry.get_current() == BackendName.CLAUDE


def test_save_state_uses_tmp_file_and_atomic_replace(backend_file: Path) -> None:
    """State persistence uses a temp file followed by os.replace."""
    current_backend_registry.load_state()

    with patch("claude_manager.current_backend_registry.os.replace") as mock_replace:
        current_backend_registry.set_current(BackendName.CODEX)

    mock_replace.assert_called_once()
    source_path, target_path = mock_replace.call_args[0]
    assert source_path.endswith(".tmp")
    assert target_path == str(backend_file)
