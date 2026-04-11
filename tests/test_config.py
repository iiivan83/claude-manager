"""Тесты модуля config — загрузка и проверка настроек из .env."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_manager.config import (
    DEFAULT_PROJECTS_ROOT,
    ConfigError,
    _parse_allowed_user_ids,
    _resolve_projects_root,
    _resolve_working_dir,
    load_config,
)
from claude_manager import config


# --- Вспомогательные инструменты ---

# Токен, используемый во всех тестах (не настоящий, только для тестов)
FAKE_BOT_TOKEN = "7654321:AAH_test_token_value"

# ID пользователей для тестов
FAKE_USER_ID = "123456789"
FAKE_USER_ID_2 = "987654321"
FAKE_USER_ID_3 = "555000111"


def _make_env(
    token: str | None = FAKE_BOT_TOKEN,
    user_ids: str | None = FAKE_USER_ID,
    working_dir: str | None = None,
    projects_root: str | None = None,
) -> dict[str, str]:
    """Собирает словарь переменных окружения для теста."""
    env = {}
    if token is not None:
        env["TELEGRAM_BOT_TOKEN"] = token
    if user_ids is not None:
        env["ALLOWED_USER_IDS"] = user_ids
    if working_dir is not None:
        env["CLAUDE_WORKING_DIR"] = working_dir
    if projects_root is not None:
        env["PROJECTS_ROOT_DIR"] = projects_root
    return env


# --- Юнит-тесты load_config ---


class TestLoadConfigSuccess:
    """Тесты успешной загрузки конфигурации."""

    @patch("claude_manager.config.load_dotenv")
    def test_load_config_all_values_set(self, mock_dotenv: object) -> None:
        """При наличии всех переменных окружения — константы заполняются корректно."""
        env = _make_env(working_dir="/tmp")
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert config.BOT_TOKEN == FAKE_BOT_TOKEN
            assert config.ALLOWED_USER_IDS == {123456789}
            assert config.WORKING_DIR == "/tmp"

    @patch("claude_manager.config.load_dotenv")
    def test_load_config_multiple_user_ids(self, mock_dotenv: object) -> None:
        """Несколько ID через запятую — все попадают в множество."""
        ids_string = f"{FAKE_USER_ID},{FAKE_USER_ID_2},{FAKE_USER_ID_3}"
        env = _make_env(user_ids=ids_string)
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert config.ALLOWED_USER_IDS == {123456789, 987654321, 555000111}

    @patch("claude_manager.config.load_dotenv")
    def test_load_config_working_dir_defaults_to_cwd(
        self, mock_dotenv: object
    ) -> None:
        """Без CLAUDE_WORKING_DIR — используется текущая директория."""
        env = _make_env()
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert config.WORKING_DIR == os.getcwd()


# --- Юнит-тесты _parse_allowed_user_ids ---


class TestParseAllowedUserIds:
    """Тесты разбора строки с Telegram-ID пользователей."""

    def test_single_id(self) -> None:
        """Один ID — возвращается множество с одним элементом."""
        assert _parse_allowed_user_ids("123456789") == {123456789}

    def test_multiple_ids(self) -> None:
        """Несколько ID через запятую — все попадают в множество."""
        result = _parse_allowed_user_ids("123456789, 987654321, 555000111")
        assert result == {123456789, 987654321, 555000111}


# --- Юнит-тесты _resolve_working_dir ---


class TestResolveWorkingDir:
    """Тесты определения рабочей директории."""

    def test_absolute_path(self) -> None:
        """Абсолютный путь к существующей директории — принимается как есть."""
        assert _resolve_working_dir("/tmp") == "/tmp"

    def test_none_returns_cwd(self) -> None:
        """None — возвращается текущая директория."""
        assert _resolve_working_dir(None) == os.getcwd()


# --- Граничные случаи ---


class TestEdgeCases:
    """Граничные случаи: пробелы, запятые, дубликаты, относительные пути."""

    def test_user_ids_with_extra_spaces(self) -> None:
        """Пробелы вокруг ID корректно убираются."""
        result = _parse_allowed_user_ids("  123456789 , 987654321  ")
        assert result == {123456789, 987654321}

    def test_user_ids_with_trailing_comma(self) -> None:
        """Запятая в конце строки не вызывает ошибку."""
        result = _parse_allowed_user_ids("123456789,987654321,")
        assert result == {123456789, 987654321}

    def test_user_ids_with_consecutive_commas(self) -> None:
        """Несколько запятых подряд не вызывают ошибку."""
        result = _parse_allowed_user_ids("123456789,,987654321")
        assert result == {123456789, 987654321}

    def test_user_ids_duplicate_values(self) -> None:
        """Дубликаты ID отбрасываются (множество)."""
        result = _parse_allowed_user_ids("123456789,123456789,987654321")
        assert result == {123456789, 987654321}

    def test_working_dir_relative_path(self) -> None:
        """Относительный путь преобразуется в абсолютный."""
        result = _resolve_working_dir(".")
        assert result == os.path.abspath(".")

    def test_working_dir_empty_string(self) -> None:
        """Пустая строка обрабатывается как «не задано»."""
        assert _resolve_working_dir("") == os.getcwd()

    @patch("claude_manager.config.load_dotenv")
    def test_load_config_can_be_called_twice(
        self, mock_dotenv: object
    ) -> None:
        """Повторный вызов load_config() перезаписывает значения без ошибок."""
        first_env = _make_env(user_ids="111")
        with patch.dict(os.environ, first_env, clear=True):
            load_config()

        second_env = _make_env(user_ids="222")
        with patch.dict(os.environ, second_env, clear=True):
            load_config()
            assert config.ALLOWED_USER_IDS == {222}

    @patch("claude_manager.config.load_dotenv")
    def test_load_config_env_overrides_system(
        self, mock_dotenv: object
    ) -> None:
        """load_dotenv вызывается с override=True."""
        env = _make_env()
        with patch.dict(os.environ, env, clear=True):
            load_config()
            # Проверяем, что load_dotenv вызван с override=True
            mock_dotenv.assert_called_with(override=True)


# --- Тесты ошибок ---


class TestConfigErrors:
    """Тесты ошибок при некорректных или отсутствующих параметрах."""

    @patch("claude_manager.config.load_dotenv")
    def test_missing_bot_token(self, mock_dotenv: object) -> None:
        """Нет токена — ConfigError с понятным сообщением."""
        env = _make_env(token=None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN не задан"):
                load_config()

    @patch("claude_manager.config.load_dotenv")
    def test_empty_bot_token(self, mock_dotenv: object) -> None:
        """Пустой токен — ConfigError с понятным сообщением."""
        env = _make_env(token="")
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN не задан"):
                load_config()

    @patch("claude_manager.config.load_dotenv")
    def test_missing_allowed_user_ids(self, mock_dotenv: object) -> None:
        """Нет списка пользователей — ConfigError."""
        env = _make_env(user_ids=None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="ALLOWED_USER_IDS не задан"):
                load_config()

    @patch("claude_manager.config.load_dotenv")
    def test_empty_allowed_user_ids(self, mock_dotenv: object) -> None:
        """Пустой список пользователей — ConfigError."""
        env = _make_env(user_ids="")
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="ALLOWED_USER_IDS не задан"):
                load_config()

    def test_non_numeric_user_id(self) -> None:
        """Нечисловое значение в списке ID — ConfigError."""
        with pytest.raises(ConfigError, match="нечисловое значение.*not_a_number"):
            _parse_allowed_user_ids("123456789,not_a_number")

    def test_only_commas_in_user_ids(self) -> None:
        """Только запятые — ConfigError (нет ни одного ID)."""
        with pytest.raises(ConfigError, match="не содержит ни одного корректного ID"):
            _parse_allowed_user_ids(",,,")

    def test_only_spaces_in_user_ids(self) -> None:
        """Только пробелы — ConfigError (нет ни одного ID)."""
        with pytest.raises(ConfigError, match="не содержит ни одного корректного ID"):
            _parse_allowed_user_ids("   ")

    def test_nonexistent_working_dir(self) -> None:
        """Несуществующая директория — ConfigError."""
        nonexistent_path = "/path/that/definitely/does/not/exist"
        with pytest.raises(ConfigError, match="несуществующую директорию"):
            _resolve_working_dir(nonexistent_path)

    def test_working_dir_is_file_not_directory(self) -> None:
        """Путь к файлу вместо директории — ConfigError."""
        # Создаём временный файл, чтобы путь существовал, но не был директорией
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as temp_file:
            temp_path = temp_file.name
        try:
            with pytest.raises(ConfigError, match="несуществующую директорию"):
                _resolve_working_dir(temp_path)
        finally:
            os.unlink(temp_path)


# --- Тесты новой переменной PROJECTS_ROOT_DIR ---


class TestResolveProjectsRoot:
    """Тесты определения корневой папки проектов."""

    def test_absolute_path(self) -> None:
        """Абсолютный путь к существующей директории принимается как есть."""
        assert _resolve_projects_root("/tmp") == "/tmp"

    def test_none_returns_default(self) -> None:
        """None — возвращается значение по умолчанию (если оно существует на машине)."""
        # На тестовой машине DEFAULT_PROJECTS_ROOT может не существовать — проверяем через patch
        with patch("claude_manager.config.os.path.isdir", return_value=True):
            result = _resolve_projects_root(None)
            assert result == os.path.abspath(DEFAULT_PROJECTS_ROOT)

    def test_empty_returns_default(self) -> None:
        """Пустая строка — возвращается значение по умолчанию."""
        with patch("claude_manager.config.os.path.isdir", return_value=True):
            result = _resolve_projects_root("")
            assert result == os.path.abspath(DEFAULT_PROJECTS_ROOT)

    def test_nonexistent_raises_error(self) -> None:
        """Несуществующая директория — ConfigError с понятным сообщением."""
        nonexistent_path = "/path/that/definitely/does/not/exist"
        with pytest.raises(ConfigError, match="несуществующую директорию"):
            _resolve_projects_root(nonexistent_path)


class TestLoadConfigProjectsRoot:
    """Тесты интеграции PROJECTS_ROOT_DIR в load_config."""

    @patch("claude_manager.config.load_dotenv")
    def test_projects_root_from_env(self, mock_dotenv: object) -> None:
        """Значение PROJECTS_ROOT_DIR читается из переменной окружения."""
        env = _make_env(working_dir="/tmp", projects_root="/tmp")
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert config.PROJECTS_ROOT_DIR == "/tmp"

    @patch("claude_manager.config.load_dotenv")
    def test_projects_root_default_when_missing(self, mock_dotenv: object) -> None:
        """Без PROJECTS_ROOT_DIR используется значение по умолчанию."""
        env = _make_env(working_dir="/tmp")
        with patch.dict(os.environ, env, clear=True), \
             patch("claude_manager.config.os.path.isdir", return_value=True):
            load_config()
            # Путь совпадает с default после нормализации
            assert config.PROJECTS_ROOT_DIR == os.path.abspath(DEFAULT_PROJECTS_ROOT)

    @patch("claude_manager.config.load_dotenv")
    def test_projects_root_nonexistent_raises(self, mock_dotenv: object) -> None:
        """Явно указанный несуществующий путь PROJECTS_ROOT_DIR — ConfigError."""
        env = _make_env(
            working_dir="/tmp",
            projects_root="/path/that/definitely/does/not/exist",
        )
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="PROJECTS_ROOT_DIR"):
                load_config()

    @patch("claude_manager.config.load_dotenv")
    def test_projects_root_is_file_not_directory(self, mock_dotenv: object) -> None:
        """Путь к файлу вместо директории — ConfigError."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as temp_file:
            temp_path = temp_file.name
        try:
            env = _make_env(working_dir="/tmp", projects_root=temp_path)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(ConfigError, match="PROJECTS_ROOT_DIR"):
                    load_config()
        finally:
            os.unlink(temp_path)


# --- Тесты предупреждения о нескольких user ID ---


class TestMultipleUserIdsWarning:
    """Тесты предупреждения в лог при нескольких ID в белом списке."""

    @patch("claude_manager.config.load_dotenv")
    def test_single_user_id_no_warning(
        self, mock_dotenv: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """При одном ID — warning НЕ выводится."""
        env = _make_env(user_ids=FAKE_USER_ID, working_dir="/tmp")
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("WARNING", logger="claude_manager.config"):
                load_config()
            assert not any(
                "В ALLOWED_USER_IDS указано" in record.message
                for record in caplog.records
            )

    @patch("claude_manager.config.load_dotenv")
    def test_multiple_user_ids_triggers_warning(
        self, mock_dotenv: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """При двух+ ID — warning выводится с количеством и текстом о дублировании."""
        ids_string = f"{FAKE_USER_ID},{FAKE_USER_ID_2}"
        env = _make_env(user_ids=ids_string, working_dir="/tmp")
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("WARNING", logger="claude_manager.config"):
                load_config()
            warning_records = [
                record
                for record in caplog.records
                if "В ALLOWED_USER_IDS указано" in record.message
            ]
            assert len(warning_records) == 1
            warning_message = warning_records[0].message
            assert "2 ID" in warning_message
            assert "дублирование сообщений" in warning_message
            assert "конфликты состояния" in warning_message

    @patch("claude_manager.config.load_dotenv")
    def test_three_user_ids_shows_correct_count(
        self, mock_dotenv: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """При трёх ID — warning содержит число 3."""
        ids_string = f"{FAKE_USER_ID},{FAKE_USER_ID_2},{FAKE_USER_ID_3}"
        env = _make_env(user_ids=ids_string, working_dir="/tmp")
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("WARNING", logger="claude_manager.config"):
                load_config()
            warning_records = [
                record
                for record in caplog.records
                if "В ALLOWED_USER_IDS указано" in record.message
            ]
            assert len(warning_records) == 1
            assert "3 ID" in warning_records[0].message


# --- Тесты E2E_TEST_USER_ID ---


class TestE2eTestUserId:
    """Тесты загрузки E2E_TEST_USER_ID из переменных окружения."""

    @patch("claude_manager.config.load_dotenv")
    def test_e2e_user_id_loaded_from_env(self, mock_dotenv: object) -> None:
        """E2E_TEST_USER_ID загружается как int из переменной окружения."""
        env = _make_env(working_dir="/tmp")
        env["E2E_TEST_USER_ID"] = "99999"
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert config.E2E_TEST_USER_ID == 99999

    @patch("claude_manager.config.load_dotenv")
    def test_e2e_user_id_none_when_missing(self, mock_dotenv: object) -> None:
        """Без переменной E2E_TEST_USER_ID — значение None, без ошибки."""
        env = _make_env(working_dir="/tmp")
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert config.E2E_TEST_USER_ID is None

    @patch("claude_manager.config.load_dotenv")
    def test_e2e_user_id_not_in_allowed_ids(self, mock_dotenv: object) -> None:
        """E2E_TEST_USER_ID НЕ добавляется в ALLOWED_USER_IDS."""
        env = _make_env(working_dir="/tmp")
        env["E2E_TEST_USER_ID"] = "99999"
        with patch.dict(os.environ, env, clear=True):
            load_config()
            assert 99999 not in config.ALLOWED_USER_IDS
            assert config.E2E_TEST_USER_ID == 99999

    @patch("claude_manager.config.load_dotenv")
    def test_e2e_user_id_invalid_raises_config_error(
        self, mock_dotenv: object
    ) -> None:
        """Нечисловое значение E2E_TEST_USER_ID вызывает ConfigError."""
        env = _make_env(working_dir="/tmp")
        env["E2E_TEST_USER_ID"] = "not-a-number"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigError, match="нечисловое значение"):
                load_config()
