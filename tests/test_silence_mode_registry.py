"""Тесты модуля silence_mode_registry — режим тишины (подавление промежуточных)."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_manager import silence_mode_registry


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Сбрасывает глобальное состояние модуля перед каждым тестом."""
    silence_mode_registry._silence_enabled = False
    silence_mode_registry._loaded_from_disk = False
    yield
    silence_mode_registry._silence_enabled = False
    silence_mode_registry._loaded_from_disk = False


@pytest.fixture()
def silence_file(tmp_path: Path) -> Path:
    """Возвращает путь к временному файлу и мокает config.SILENCE_MODE_FILE."""
    file_path = tmp_path / "silence-mode"
    with patch.object(silence_mode_registry.config, "SILENCE_MODE_FILE", file_path):
        yield file_path


# --- Дефолтное состояние ---


class TestSilenceModeDefaultState:
    """Тесты состояния модуля до первого вызова load_state."""

    def test_default_state_is_disabled(self) -> None:
        """До load_state silence mode выключен."""
        assert silence_mode_registry.is_enabled() is False


# --- Enable / Disable ---


class TestSilenceModeEnableDisable:
    """Тесты включения и выключения silence mode."""

    def test_enable_after_load_sets_enabled_true(self, silence_file: Path) -> None:
        """enable() после load_state() устанавливает is_enabled()=True."""
        silence_mode_registry.load_state()
        silence_mode_registry.enable()
        assert silence_mode_registry.is_enabled() is True

    def test_disable_after_enable_sets_enabled_false(self, silence_file: Path) -> None:
        """disable() после enable() возвращает is_enabled()=False."""
        silence_mode_registry.load_state()
        silence_mode_registry.enable()
        silence_mode_registry.disable()
        assert silence_mode_registry.is_enabled() is False

    def test_enable_saves_to_disk(self, silence_file: Path) -> None:
        """enable() записывает {\"enabled\": true} в файл."""
        silence_mode_registry.load_state()
        silence_mode_registry.enable()

        content = json.loads(silence_file.read_text("utf-8"))
        assert content == {"enabled": True}


# --- Load state ---


class TestSilenceModeLoadState:
    """Тесты загрузки состояния с диска."""

    def test_load_state_reads_enabled_true(self, silence_file: Path) -> None:
        """load_state() читает {\"enabled\": true} и устанавливает is_enabled()=True."""
        silence_file.write_text(json.dumps({"enabled": True}), "utf-8")
        silence_mode_registry.load_state()
        assert silence_mode_registry.is_enabled() is True

    def test_load_state_missing_file_fallback_disabled(self, silence_file: Path) -> None:
        """Файл не существует — fallback на False, запись разрешена."""
        # silence_file не создан — FileNotFoundError
        silence_mode_registry.load_state()
        assert silence_mode_registry.is_enabled() is False
        assert silence_mode_registry._loaded_from_disk is True

    def test_load_state_corrupted_json_fallback_disabled(self, silence_file: Path) -> None:
        """Битый JSON — fallback на False, файл НЕ перезаписывается."""
        original_content = "not-valid-json{{"
        silence_file.write_text(original_content, "utf-8")

        silence_mode_registry.load_state()

        assert silence_mode_registry.is_enabled() is False
        assert silence_mode_registry._loaded_from_disk is True
        # Файл не должен быть перезаписан
        assert silence_file.read_text("utf-8") == original_content

    def test_load_state_missing_key_fallback_disabled(self, silence_file: Path) -> None:
        """JSON без ключа 'enabled' — fallback на False, запись разрешена."""
        silence_file.write_text(json.dumps({"other_key": 42}), "utf-8")

        silence_mode_registry.load_state()

        assert silence_mode_registry.is_enabled() is False
        assert silence_mode_registry._loaded_from_disk is True

    def test_load_state_os_error_no_write_permission(self, silence_file: Path) -> None:
        """OSError при чтении — запись заблокирована (_loaded_from_disk=False)."""
        with patch.object(Path, "read_text", side_effect=OSError("disk error")):
            silence_mode_registry.load_state()

        assert silence_mode_registry.is_enabled() is False
        assert silence_mode_registry._loaded_from_disk is False


# --- Guard: _loaded_from_disk ---


class TestSilenceModeLoadedFromDiskGuard:
    """Тесты защиты от записи до load_state."""

    def test_enable_before_load_does_not_write(self, silence_file: Path) -> None:
        """enable() до load_state() НЕ создаёт файл — запись заблокирована."""
        silence_mode_registry.enable()
        assert not silence_file.exists()


# --- Атомарная запись ---


class TestSilenceModeAtomicWrite:
    """Тесты атомарности записи (tmp + rename)."""

    def test_atomic_write_uses_tmp_and_rename(self, silence_file: Path) -> None:
        """_save_state вызывает os.replace с tmp-файлом и целевым файлом."""
        silence_mode_registry.load_state()

        with patch("claude_manager.silence_mode_registry.os.replace") as mock_replace:
            # Нужно, чтобы write_text не упала — создаём parent
            silence_file.parent.mkdir(parents=True, exist_ok=True)
            silence_mode_registry.enable()

            mock_replace.assert_called_once()
            call_args = mock_replace.call_args[0]
            # Первый аргумент — tmp-файл (с суффиксом .tmp)
            assert call_args[0].endswith(".tmp")
            # Второй аргумент — целевой файл
            assert call_args[1] == str(silence_file)
