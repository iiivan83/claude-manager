"""Тесты модуля file_sender — парсинг маркеров, определение типа файла, рендеринг."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_manager.file_sender import (
    EMPTY_FILE_PLACEHOLDER,
    MAX_BINARY_FILE_SIZE_BYTES,
    MAX_TEXT_FILE_SIZE_BYTES,
    TELEGRAM_MESSAGE_LIMIT,
    check_binary_file,
    convert_entities,
    extract_file_markers,
    is_text_file,
    read_file_content,
    render_file_for_telegram,
    strip_file_markers,
)


# --- Тесты extract_file_markers ---


class TestExtractFileMarkers:
    """Тесты извлечения путей файлов из маркеров [SEND_FILE:path]."""

    def test_single_marker(self) -> None:
        """Один маркер в тексте — извлекается один путь."""
        text = "text [SEND_FILE:/path/to/file.md] more"
        assert extract_file_markers(text) == ["/path/to/file.md"]

    def test_multiple_markers(self) -> None:
        """Несколько маркеров — все пути в порядке появления."""
        text = (
            "[SEND_FILE:/a.md] text "
            "[SEND_FILE:/b.py] more "
            "[SEND_FILE:/c.json]"
        )
        assert extract_file_markers(text) == ["/a.md", "/b.py", "/c.json"]

    def test_no_markers(self) -> None:
        """Текст без маркеров — пустой список."""
        assert extract_file_markers("обычный текст без маркеров") == []

    def test_path_with_spaces(self) -> None:
        """Путь с пробелами корректно извлекается."""
        text = "[SEND_FILE:/path/to my file.md]"
        assert extract_file_markers(text) == ["/path/to my file.md"]

    def test_strips_whitespace(self) -> None:
        """Лишние пробелы вокруг пути убираются strip()."""
        text = "[SEND_FILE:  /path/file.md  ]"
        assert extract_file_markers(text) == ["/path/file.md"]


# --- Тесты strip_file_markers ---


class TestStripFileMarkers:
    """Тесты вырезки маркеров из текста."""

    def test_removes_single_marker(self) -> None:
        """Маркер вырезан, текст до и после на месте."""
        text = "before [SEND_FILE:/path/file.md] after"
        result = strip_file_markers(text)
        assert "[SEND_FILE" not in result
        assert "before" in result
        assert "after" in result

    def test_collapses_empty_lines(self) -> None:
        """После вырезки маркера лишние пустые строки схлопываются."""
        text = "line1\n\n[SEND_FILE:/path/file.md]\n\n\nline2"
        result = strip_file_markers(text)
        # Не больше двух переносов подряд (одна пустая строка)
        assert "\n\n\n" not in result

    def test_no_markers_unchanged(self) -> None:
        """Текст без маркеров возвращается без изменений."""
        text = "обычный текст"
        assert strip_file_markers(text) == text

    def test_removes_multiple_markers(self) -> None:
        """Несколько маркеров — все вырезаны."""
        text = "[SEND_FILE:/a.md] text [SEND_FILE:/b.py]"
        result = strip_file_markers(text)
        assert "[SEND_FILE" not in result
        assert "text" in result


# --- Тесты is_text_file ---


class TestIsTextFile:
    """Тесты определения типа файла по расширению или имени."""

    def test_markdown_is_text(self) -> None:
        """Файл .md — текстовый."""
        assert is_text_file("/path/file.md") is True

    def test_python_is_text(self) -> None:
        """Файл .py — текстовый."""
        assert is_text_file("/path/file.py") is True

    def test_json_is_text(self) -> None:
        """Файл .json — текстовый."""
        assert is_text_file("/path/file.json") is True

    @pytest.mark.parametrize("extension", [
        "sh", "js", "ts", "html", "css", "yml", "yaml", "toml",
    ])
    def test_various_text_extensions(self, extension: str) -> None:
        """Распространённые текстовые расширения определяются как текст."""
        assert is_text_file(f"/path/file.{extension}") is True

    def test_png_is_binary(self) -> None:
        """Файл .png — бинарный."""
        assert is_text_file("/path/image.png") is False

    def test_zip_is_binary(self) -> None:
        """Файл .zip — бинарный."""
        assert is_text_file("/path/archive.zip") is False

    def test_pdf_is_binary(self) -> None:
        """Файл .pdf — бинарный."""
        assert is_text_file("/path/doc.pdf") is False

    def test_no_extension_is_binary(self) -> None:
        """Файл без расширения и не в TEXT_FILE_NAMES — бинарный."""
        assert is_text_file("/path/randomfile") is False

    def test_known_filenames_are_text(self) -> None:
        """Makefile, Dockerfile, .gitignore, .env — текстовые."""
        assert is_text_file("/path/Makefile") is True
        assert is_text_file("/path/Dockerfile") is True
        assert is_text_file("/path/.gitignore") is True
        assert is_text_file("/path/.env") is True

    def test_case_insensitive(self) -> None:
        """Расширения регистронезависимы."""
        assert is_text_file("/path/FILE.MD") is True
        assert is_text_file("/path/script.PY") is True


# --- Тесты read_file_content ---


class TestReadFileContent:
    """Тесты чтения текстовых файлов с диска."""

    def test_reads_existing_file(self, tmp_path: Path) -> None:
        """Существующий файл — возвращает содержимое и None в качестве ошибки."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        content, error = read_file_content(str(test_file))
        assert content == "hello world"
        assert error is None

    def test_file_not_found(self) -> None:
        """Несуществующий путь — пустая строка и сообщение об ошибке."""
        content, error = read_file_content("/nonexistent/path/file.txt")
        assert content == ""
        assert "Файл не найден" in error

    @patch("claude_manager.file_sender.os.path.getsize")
    def test_permission_error(self, mock_getsize: MagicMock) -> None:
        """PermissionError — сообщение о нехватке прав."""
        mock_getsize.side_effect = PermissionError("denied")
        content, error = read_file_content("/path/secret.txt")
        assert content == ""
        assert "Нет доступа к файлу" in error

    @patch("claude_manager.file_sender.os.path.getsize")
    def test_file_too_large(self, mock_getsize: MagicMock) -> None:
        """Файл больше лимита — сообщение о превышении размера."""
        mock_getsize.return_value = MAX_TEXT_FILE_SIZE_BYTES + 1
        content, error = read_file_content("/path/huge.txt")
        assert content == ""
        assert "слишком большой" in error

    @patch("claude_manager.file_sender.Path.read_text")
    @patch("claude_manager.file_sender.os.path.getsize")
    def test_unicode_decode_error(
        self, mock_getsize: MagicMock, mock_read_text: MagicMock,
    ) -> None:
        """Бинарный файл, прочитанный как текст — сообщение об ошибке."""
        mock_getsize.return_value = 100
        mock_read_text.side_effect = UnicodeDecodeError(
            "utf-8", b"\xff", 0, 1, "invalid byte",
        )
        content, error = read_file_content("/path/binary.bin")
        assert content == ""
        assert "не является текстовым" in error

    def test_empty_file(self, tmp_path: Path) -> None:
        """Пустой файл — пустая строка, ошибки нет."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")
        content, error = read_file_content(str(test_file))
        assert content == ""
        assert error is None


# --- Тесты render_file_for_telegram ---


class TestRenderFileForTelegram:
    """Тесты рендеринга текстового содержимого через telegramify-markdown."""

    def test_simple_markdown(self) -> None:
        """Простой Markdown — возвращает список чанков."""
        chunks = render_file_for_telegram("**bold** text")
        assert len(chunks) >= 1
        # Первый чанк содержит текст
        text, entities = chunks[0]
        assert len(text) > 0

    def test_long_content_splits(self) -> None:
        """Текст длиннее 4096 символов — разбивается на несколько чанков."""
        # Создаём длинный текст из множества строк
        long_content = "\n".join(
            f"Line {i}: some text here" for i in range(500)
        )
        chunks = render_file_for_telegram(long_content)
        assert len(chunks) > 1
        # Каждый чанк не превышает лимит Telegram (в UTF-16 code units)
        for text, _ in chunks:
            utf16_len = len(text.encode("utf-16-le")) // 2
            assert utf16_len <= TELEGRAM_MESSAGE_LIMIT

    def test_empty_content(self) -> None:
        """Пустой контент — заглушка '(пустой файл)'."""
        chunks = render_file_for_telegram("")
        assert chunks == [(EMPTY_FILE_PLACEHOLDER, [])]

    def test_first_chunk_reserve_reduces_effective_limit(self) -> None:
        """Резерв уменьшает допустимый размер каждого чанка на указанное значение."""
        reserve = 200
        long_content = "\n".join(
            f"Line {i}: some text here" for i in range(500)
        )
        chunks = render_file_for_telegram(long_content, first_chunk_reserve=reserve)
        assert len(chunks) > 1
        reduced_limit = TELEGRAM_MESSAGE_LIMIT - reserve
        for text, _ in chunks:
            utf16_length = len(text.encode("utf-16-le")) // 2
            assert utf16_length <= reduced_limit

    def test_first_chunk_reserve_zero_is_backward_compatible(self) -> None:
        """Резерв 0 даёт тот же результат, что и вызов без параметра."""
        long_content = "\n".join(
            f"Line {i}: some text here" for i in range(500)
        )
        chunks_default = render_file_for_telegram(long_content)
        chunks_zero = render_file_for_telegram(long_content, first_chunk_reserve=0)
        assert len(chunks_default) == len(chunks_zero)
        for (text_default, _), (text_zero, _) in zip(chunks_default, chunks_zero):
            assert len(text_default) == len(text_zero)

    def test_first_chunk_reserve_absurd_value_uses_fallback(self) -> None:
        """Резерв >= лимита Telegram — fallback на половину лимита, без падения."""
        long_content = "\n".join(
            f"Line {i}: some text here" for i in range(500)
        )
        fallback_limit = TELEGRAM_MESSAGE_LIMIT // 2
        chunks = render_file_for_telegram(
            long_content, first_chunk_reserve=TELEGRAM_MESSAGE_LIMIT,
        )
        assert len(chunks) >= 1
        for text, _ in chunks:
            utf16_length = len(text.encode("utf-16-le")) // 2
            assert utf16_length <= fallback_limit


# --- Тесты convert_entities ---


class TestConvertEntities:
    """Тесты конвертации entities из telegramify-markdown в telegram.MessageEntity."""

    def test_basic_conversion(self) -> None:
        """Entity с type, offset, length — конвертируется с сохранением полей."""
        source_entity = MagicMock()
        source_entity.type = "bold"
        source_entity.offset = 0
        source_entity.length = 4
        source_entity.url = None
        source_entity.language = None

        result = convert_entities([source_entity])
        assert len(result) == 1
        assert result[0].type == "bold"
        assert result[0].offset == 0
        assert result[0].length == 4

    def test_entity_with_url(self) -> None:
        """Entity с url — url сохраняется при конвертации."""
        source_entity = MagicMock()
        source_entity.type = "text_link"
        source_entity.offset = 0
        source_entity.length = 10
        source_entity.url = "https://example.com"
        source_entity.language = None

        result = convert_entities([source_entity])
        assert result[0].url == "https://example.com"

    def test_empty_list(self) -> None:
        """Пустой список — пустой список."""
        assert convert_entities([]) == []


# --- Тесты check_binary_file ---


class TestCheckBinaryFile:
    """Тесты проверки бинарного файла перед отправкой."""

    def test_existing_file_ok(self, tmp_path: Path) -> None:
        """Существующий файл нормального размера — None (ошибки нет)."""
        test_file = tmp_path / "image.png"
        test_file.write_bytes(b"\x89PNG")
        result = check_binary_file(str(test_file))
        assert result is None

    def test_file_not_found(self) -> None:
        """Несуществующий файл — строка с ошибкой."""
        result = check_binary_file("/nonexistent/path/file.zip")
        assert result is not None
        assert "Файл не найден" in result

    @patch("claude_manager.file_sender.os.path.getsize")
    def test_file_too_large(self, mock_getsize: MagicMock) -> None:
        """Файл больше 50 МБ — строка с ошибкой."""
        mock_getsize.return_value = MAX_BINARY_FILE_SIZE_BYTES + 1
        result = check_binary_file("/path/huge.zip")
        assert result is not None
        assert "слишком большой" in result
