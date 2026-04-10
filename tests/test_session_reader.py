"""Тесты модуля session_reader — чтение файлов сессий Claude Code с диска."""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from claude_manager.session_reader import (
    MAX_RECENT_SESSIONS,
    SessionInfo,
    _build_sessions_path,
    _clean_preview,
    _encode_project_path,
    _extract_first_user_message,
    _is_command_message,
    _read_session_file,
    get_recent_sessions,
    get_session_messages,
)


# --- Вспомогательные инструменты ---


def _write_jsonl_file(path: Path, lines: list[dict]) -> None:
    """Записывает список словарей в JSONL-файл (по строке на каждый словарь)."""
    with open(path, "w", encoding="utf-8") as file_handle:
        for line_dict in lines:
            file_handle.write(json.dumps(line_dict) + "\n")


def _make_session_line(
    session_id: str = "test-session-id",
    timestamp: str = "2026-03-29T17:23:21.594Z",
) -> dict:
    """Создаёт первую строку JSONL-файла сессии с метаданными."""
    return {"sessionId": session_id, "timestamp": timestamp, "type": "system"}


def _make_user_message(
    text: str = "Привет, расскажи про проект",
    is_meta: bool = False,
    content_as_list: bool = False,
) -> dict:
    """Создаёт строку JSONL-файла с сообщением пользователя."""
    if content_as_list:
        content = [{"type": "text", "text": text}]
    else:
        content = text
    result: dict = {
        "type": "user",
        "message": {"role": "user", "content": content},
    }
    if is_meta:
        result["isMeta"] = True
    return result


# --- Юнит-тесты _encode_project_path ---


class TestEncodeProjectPath:
    """Тесты кодирования пути проекта в формат Claude Code."""

    def test_standard_path(self) -> None:
        """Стандартный путь — слеши заменяются на дефисы."""
        result = _encode_project_path(
            "/Users/ivan/Desktop/claude-sandbox/claude-manager"
        )
        assert result == "-Users-ivan-Desktop-claude-sandbox-claude-manager"

    def test_path_with_spaces(self) -> None:
        """Путь с пробелами — пробелы тоже заменяются на дефисы."""
        result = _encode_project_path("/Users/ivan/Desktop/my project")
        assert result == "-Users-ivan-Desktop-my-project"

    def test_root_path(self) -> None:
        """Корневой путь — один дефис."""
        result = _encode_project_path("/")
        assert result == "-"

    def test_path_with_underscores(self) -> None:
        """Регрессионный тест: подчёркивание заменяется на дефис (наш упавший случай)."""
        result = _encode_project_path(
            "/Users/ivan/Desktop/claude-sandbox/claude_manager"
        )
        assert result == "-Users-ivan-Desktop-claude-sandbox-claude-manager"

    def test_path_with_dots(self) -> None:
        """Точки заменяются на дефис, два дефиса подряд допустимы."""
        result = _encode_project_path("/Users/ivan/Desktop/project/.claude/skills")
        assert result == "-Users-ivan-Desktop-project--claude-skills"

    def test_path_with_mixed_special_chars(self) -> None:
        """Пробел и подчёркивание одновременно заменяются на дефис."""
        result = _encode_project_path("/Users/ivan/My Project_v2/src")
        assert result == "-Users-ivan-My-Project-v2-src"

    def test_path_with_digits(self) -> None:
        """Цифры сохраняются, не заменяются."""
        result = _encode_project_path("/Users/user1/project2024")
        assert result == "-Users-user1-project2024"

    def test_path_with_cyrillic(self) -> None:
        """Фиксирует ASCII-поведение регулярки: кириллица превращается в дефисы. Unicode-логика Claude Code — тема для отдельного issue."""
        result = _encode_project_path("/Users/ivan/Проект")
        assert result == "-Users-ivan-------"


# --- Юнит-тесты _build_sessions_path ---


class TestBuildSessionsPath:
    """Тесты построения полного пути к папке сессий."""

    def test_builds_correct_path(self) -> None:
        """Путь содержит домашнюю директорию и закодированное имя проекта."""
        result = _build_sessions_path("/Users/ivan/Desktop/claude-manager")
        home = os.path.expanduser("~")
        expected_suffix = ".claude/projects/-Users-ivan-Desktop-claude-manager"
        assert result.startswith(home)
        assert result.endswith(expected_suffix)

    def test_builds_path_with_underscore(self) -> None:
        """Путь с подчёркиванием — итоговая папка содержит дефис вместо подчёркивания."""
        result = _build_sessions_path(
            "/Users/ivan/Desktop/claude-sandbox/claude_manager"
        )
        home = os.path.expanduser("~")
        expected_suffix = (
            ".claude/projects/-Users-ivan-Desktop-claude-sandbox-claude-manager"
        )
        assert result.startswith(home)
        assert result.endswith(expected_suffix)
        # Явная защита от регрессии: в итоговом пути не должно быть подчёркиваний
        # из исходной части пути проекта
        assert "claude_manager" not in result


# --- Юнит-тесты _clean_preview ---


class TestCleanPreview:
    """Тесты очистки текста превью от XML-тегов."""

    def test_removes_xml_tags(self) -> None:
        """XML-теги удаляются, текст между ними сохраняется."""
        result = _clean_preview(
            "<command-name>effort</command-name> Посмотри файл main.py"
        )
        assert result == "effort Посмотри файл main.py"

    def test_collapses_whitespace(self) -> None:
        """Множественные пробелы и переносы строк заменяются одним пробелом."""
        result = _clean_preview("Посмотри   файл\n\nmain.py   и скажи")
        assert result == "Посмотри файл main.py и скажи"

    def test_truncates_long_text(self) -> None:
        """Длинный текст обрезается до 120 символов с многоточием."""
        long_text = "А" * 200
        result = _clean_preview(long_text)
        # 120 символов содержимого + 3 символа "..."
        assert len(result) == 123
        assert result.endswith("...")

    def test_keeps_text_between_tags(self) -> None:
        """Текст между XML-тегами сохраняется после удаления тегов."""
        result = _clean_preview("<command-name>/effort</command-name>")
        assert result == "/effort"

    def test_returns_empty_for_only_tags(self) -> None:
        """Если весь текст — только XML-теги, возвращается пустая строка."""
        result = _clean_preview("<br/><hr/>")
        assert result == ""

    def test_exactly_120_chars_not_truncated(self) -> None:
        """Текст ровно в 120 символов не обрезается."""
        exact_text = "Б" * 120
        result = _clean_preview(exact_text)
        assert result == exact_text
        assert "..." not in result


# --- Юнит-тесты _is_command_message ---


class TestIsCommandMessage:
    """Тесты распознавания командных XML-тегов."""

    def test_detects_command_tags(self) -> None:
        """Командные XML-теги распознаются."""
        assert _is_command_message("<command-name>/effort</command-name>") is True

    def test_allows_normal_text(self) -> None:
        """Обычный текст не распознаётся как команда."""
        result = _is_command_message(
            "Посмотри файл main.py и скажи что он делает"
        )
        assert result is False


# --- Юнит-тесты _extract_first_user_message ---


class TestExtractFirstUserMessage:
    """Тесты поиска первого настоящего сообщения пользователя."""

    def test_finds_real_message(self) -> None:
        """Находит первое настоящее сообщение, пропуская мета и системные."""
        lines = [
            {"type": "system", "message": "init"},
            _make_user_message("meta info", is_meta=True),
            _make_user_message("<command-name>/effort</command-name>"),
            _make_user_message("Посмотри файл main.py"),
        ]
        result = _extract_first_user_message(lines)
        assert result == "Посмотри файл main.py"

    def test_skips_command_xml(self) -> None:
        """Сообщения с XML-тегами команд пропускаются."""
        lines = [
            _make_user_message("<command-name>/effort</command-name>"),
            _make_user_message("Добавь обработку ошибок"),
        ]
        result = _extract_first_user_message(lines)
        assert result == "Добавь обработку ошибок"

    def test_content_is_list(self) -> None:
        """Текст извлекается из content в формате списка."""
        lines = [
            _make_user_message(
                "Привет, расскажи про проект", content_as_list=True
            ),
        ]
        result = _extract_first_user_message(lines)
        assert result == "Привет, расскажи про проект"

    def test_empty_content(self) -> None:
        """Пустое сообщение пользователя — возвращается пустая строка."""
        lines = [_make_user_message("")]
        result = _extract_first_user_message(lines)
        assert result == ""

    def test_skips_very_short_messages(self) -> None:
        """Сообщения короче 2 символов пропускаются."""
        lines = [
            _make_user_message(""),
            _make_user_message("a"),
            _make_user_message("Привет мир"),
        ]
        result = _extract_first_user_message(lines)
        assert result == "Привет мир"


# --- Юнит-тесты SessionInfo ---


class TestSessionInfo:
    """Тесты структуры данных SessionInfo."""

    def test_fields(self) -> None:
        """SessionInfo содержит все нужные поля с правильными типами."""
        info = SessionInfo(
            session_id="abc-123",
            created_at="2026-03-29T17:23:21.594Z",
            preview="Привет",
        )
        assert info.session_id == "abc-123"
        assert info.created_at == "2026-03-29T17:23:21.594Z"
        assert info.preview == "Привет"


# --- Юнит-тесты get_recent_sessions ---


class TestGetRecentSessions:
    """Тесты получения списка последних сессий."""

    @pytest.fixture()
    def sessions_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Временная папка, имитирующая директорию сессий Claude Code.

        Подменяет _build_sessions_path, чтобы функции модуля
        обращались к временной папке вместо реальной.
        """
        sessions_path = tmp_path / "sessions"
        sessions_path.mkdir()
        monkeypatch.setattr(
            "claude_manager.session_reader._build_sessions_path",
            lambda project_dir: str(sessions_path),
        )
        return sessions_path

    @pytest.mark.asyncio
    async def test_returns_sorted_list(self, sessions_dir: Path) -> None:
        """Сессии отсортированы по времени модификации (новые первые)."""
        base_time = time.time()
        # Создаём 3 файла с разными mtime
        for index in range(3):
            file_path = sessions_dir / f"session-{index}.jsonl"
            _write_jsonl_file(file_path, [
                _make_session_line(
                    session_id=f"session-{index}",
                    timestamp=f"2026-03-2{index}T10:00:00Z",
                ),
                _make_user_message(f"Сообщение {index}"),
            ])
            # Устанавливаем разное время модификации
            mtime = base_time + index * 100
            os.utime(file_path, (mtime, mtime))

        result = await get_recent_sessions("/fake/project")

        assert len(result) == 3
        # Первый элемент — самая новая сессия (session-2)
        assert result[0].session_id == "session-2"
        assert result[1].session_id == "session-1"
        assert result[2].session_id == "session-0"

    @pytest.mark.asyncio
    async def test_limits_to_15(self, sessions_dir: Path) -> None:
        """Возвращается не более 15 сессий."""
        for index in range(20):
            file_path = sessions_dir / f"session-{index:03d}.jsonl"
            _write_jsonl_file(file_path, [
                _make_session_line(session_id=f"session-{index:03d}"),
                _make_user_message(f"Сообщение {index}"),
            ])

        result = await get_recent_sessions("/fake/project")

        assert len(result) == MAX_RECENT_SESSIONS

    @pytest.mark.asyncio
    async def test_empty_directory(self, sessions_dir: Path) -> None:
        """Пустая папка (без JSONL-файлов) — пустой список."""
        # Создаём подкаталог memory, чтобы папка не была совсем пустой
        (sessions_dir / "memory").mkdir()

        result = await get_recent_sessions("/fake/project")

        assert result == []

    @pytest.mark.asyncio
    async def test_ignores_subdirectories(self, sessions_dir: Path) -> None:
        """Подкаталоги игнорируются, возвращается только JSONL-файл."""
        # Подкаталоги с UUID-именами
        (sessions_dir / "abc-def-123").mkdir()
        (sessions_dir / "xyz-789-ghi").mkdir()
        # Один настоящий JSONL-файл
        file_path = sessions_dir / "real-session.jsonl"
        _write_jsonl_file(file_path, [
            _make_session_line(session_id="real-session"),
            _make_user_message("Настоящее сообщение"),
        ])

        result = await get_recent_sessions("/fake/project")

        assert len(result) == 1
        assert result[0].session_id == "real-session"

    @pytest.mark.asyncio
    async def test_mixed_valid_and_invalid_files(
        self, sessions_dir: Path
    ) -> None:
        """Повреждённые файлы пропускаются, валидные возвращаются."""
        # 2 валидных файла
        for index in range(2):
            file_path = sessions_dir / f"valid-{index}.jsonl"
            _write_jsonl_file(file_path, [
                _make_session_line(session_id=f"valid-{index}"),
                _make_user_message(f"Сообщение {index}"),
            ])

        # 1 файл с невалидным JSON
        broken_path = sessions_dir / "broken.jsonl"
        broken_path.write_text("not a json {{{")

        result = await get_recent_sessions("/fake/project")

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_nonexistent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Папка сессий не существует — пустой список и предупреждение в логах."""
        nonexistent_path = str(tmp_path / "nonexistent")
        monkeypatch.setattr(
            "claude_manager.session_reader._build_sessions_path",
            lambda project_dir: nonexistent_path,
        )

        result = await get_recent_sessions("/path/that/does/not/exist")

        assert result == []

    @pytest.mark.asyncio
    async def test_permission_error(
        self, sessions_dir: Path
    ) -> None:
        """Ошибка доступа к папке — пустой список."""
        # Убираем права на чтение папки
        original_mode = sessions_dir.stat().st_mode
        sessions_dir.chmod(0o000)
        try:
            result = await get_recent_sessions("/fake/project")
            assert result == []
        finally:
            # Восстанавливаем права, чтобы pytest мог удалить tmp_path
            sessions_dir.chmod(original_mode)


# --- Юнит-тесты get_session_messages ---


class TestGetSessionMessages:
    """Тесты чтения сообщений конкретной сессии."""

    @pytest.fixture()
    def sessions_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Временная папка для файлов сессий."""
        sessions_path = tmp_path / "sessions"
        sessions_path.mkdir()
        monkeypatch.setattr(
            "claude_manager.session_reader._build_sessions_path",
            lambda project_dir: str(sessions_path),
        )
        return sessions_path

    @pytest.mark.asyncio
    async def test_returns_all_lines(self, sessions_dir: Path) -> None:
        """Все строки из JSONL-файла возвращаются."""
        lines = [
            {"type": "system", "timestamp": "2026-03-29T10:00:00Z"},
            {"type": "user", "message": {"content": "Привет"}},
            {"type": "assistant", "message": {"content": "Ответ"}},
            {"type": "user", "message": {"content": "Ещё вопрос"}},
            {"type": "assistant", "message": {"content": "Ещё ответ"}},
        ]
        file_path = sessions_dir / "test-session.jsonl"
        _write_jsonl_file(file_path, lines)

        result = await get_session_messages("test-session", "/fake/project")

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_file_not_found(
        self, sessions_dir: Path
    ) -> None:
        """Файл сессии не существует — пустой список."""
        result = await get_session_messages(
            "nonexistent-session-id", "/fake/project"
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_partial_corruption(self, sessions_dir: Path) -> None:
        """Частичная порча файла — валидные строки возвращаются."""
        file_path = sessions_dir / "partial.jsonl"
        with open(file_path, "w", encoding="utf-8") as file_handle:
            # Валидная строка
            file_handle.write(json.dumps({"type": "system"}) + "\n")
            # Невалидная строка
            file_handle.write("broken json\n")
            # Валидная строка
            file_handle.write(json.dumps({"type": "user"}) + "\n")

        result = await get_session_messages("partial", "/fake/project")

        assert len(result) == 2


# --- Юнит-тесты _read_session_file ---


class TestReadSessionFile:
    """Тесты чтения одного JSONL-файла сессии."""

    @pytest.mark.asyncio
    async def test_corrupted_json(self, tmp_path: Path) -> None:
        """Файл с невалидным JSON — возвращается None."""
        file_path = tmp_path / "broken.jsonl"
        file_path.write_text("not a json {{{")

        result = await _read_session_file(str(file_path))

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_timestamp(self, tmp_path: Path) -> None:
        """Файл без поля timestamp — возвращается None."""
        file_path = tmp_path / "no-timestamp.jsonl"
        _write_jsonl_file(file_path, [
            {"sessionId": "abc-123", "type": "system"},
        ])

        result = await _read_session_file(str(file_path))

        assert result is None

    @pytest.mark.asyncio
    async def test_session_id_from_filename(self, tmp_path: Path) -> None:
        """Если sessionId нет в JSON, берётся из имени файла."""
        file_path = tmp_path / "abc-def-123.jsonl"
        _write_jsonl_file(file_path, [
            {"timestamp": "2026-03-29T10:00:00Z", "type": "system"},
            _make_user_message("Тестовое сообщение"),
        ])

        result = await _read_session_file(str(file_path))

        assert result is not None
        assert result.session_id == "abc-def-123"

    @pytest.mark.asyncio
    async def test_permission_mode_first_line_claude_cli_2_1_96(
        self, tmp_path: Path
    ) -> None:
        """Регрессия Claude CLI 2.1.96: первая строка — permission-mode без timestamp.

        Начиная с Claude CLI 2.1.96 файлы сессий начинаются со служебного
        события permission-mode, у которого нет поля timestamp. Старый код
        падал на parsed_lines[0].get("timestamp") и отбрасывал такие файлы,
        из-за чего session_watcher переставал видеть живые сессии и бот
        тихо переставал слать сообщения в Telegram.

        Фикстура повторяет реальный файл
        031bc262-3927-43c4-88e1-1c4ba6a82b61.jsonl из ~/.claude/projects/.
        """
        file_path = tmp_path / "031bc262-3927-43c4-88e1-1c4ba6a82b61.jsonl"
        _write_jsonl_file(file_path, [
            {
                "type": "permission-mode",
                "permissionMode": "default",
                "sessionId": "031bc262-3927-43c4-88e1-1c4ba6a82b61",
            },
            {
                "parentUuid": None,
                "isSidechain": False,
                "type": "system",
                "subtype": "bridge_status",
                "sessionId": "031bc262-3927-43c4-88e1-1c4ba6a82b61",
                "timestamp": "2026-04-10T13:12:04.310Z",
                "isMeta": False,
            },
            {
                "type": "file-history-snapshot",
                "messageId": "b2ac2be1-a344-4dbd-8733-b6c24695a27b",
            },
            _make_user_message("Давай починем бота"),
        ])

        result = await _read_session_file(str(file_path))

        assert result is not None
        assert result.session_id == "031bc262-3927-43c4-88e1-1c4ba6a82b61"
        assert result.created_at == "2026-04-10T13:12:04.310Z"
        assert result.preview == "Давай починем бота"
