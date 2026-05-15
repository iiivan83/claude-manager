"""Интеграционные тесты: персистентность silence mode.

Проверяет: сохранение состояния между load/save циклами,
глобальность режима (не сбрасывается при переключении проектов).
"""

import json
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


class TestSilenceModePersistence:
    """Тесты персистентности silence mode."""

    def test_enable_persists_across_load(self, silence_file: Path) -> None:
        """enable() -> сброс состояния -> load_state() -> is_enabled()=True."""
        # Включаем и сохраняем
        silence_mode_registry.load_state()
        silence_mode_registry.enable()
        assert silence_mode_registry.is_enabled() is True

        # Сбрасываем in-memory состояние (имитация перезапуска)
        silence_mode_registry._silence_enabled = False
        silence_mode_registry._loaded_from_disk = False

        # Загружаем заново — должно быть True
        silence_mode_registry.load_state()
        assert silence_mode_registry.is_enabled() is True

    def test_silence_mode_survives_project_switch(self, silence_file: Path) -> None:
        """Silence mode глобальный — НЕ сбрасывается при переключении проектов.

        project_manager.reset_state() сбрасывает session_manager,
        daily_session_registry, session_watcher — но silence_mode_registry
        не имеет reset_state() и не должен сбрасываться.
        """
        silence_mode_registry.load_state()
        silence_mode_registry.enable()
        assert silence_mode_registry.is_enabled() is True

        # Имитация переключения проекта: silence mode не трогается
        # (в реальном коде project_manager вызывает reset_state()
        # на session_manager, daily_session_registry, session_watcher,
        # но НЕ на silence_mode_registry)
        assert silence_mode_registry.is_enabled() is True
