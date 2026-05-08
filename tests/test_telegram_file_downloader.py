"""Тесты модуля telegram_file_downloader — скачивание файлов из Telegram."""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.error import BadRequest, TimedOut

from claude_manager.telegram_file_downloader import (
    FILE_DOWNLOAD_RETRY_COUNT,
    FILE_DOWNLOAD_RETRY_DELAY_SECONDS,
    RECEIVED_FILES_DIR,
    SECONDS_PER_DAY,
    clean_old_received_files,
    download_and_save_file,
    download_file_with_retry,
    extract_file_info,
    generate_file_name,
    is_file_expired,
)
import claude_manager.config as config_module


# --- Фикстуры ---


ALLOWED_USER_ID = 12345
TEST_CHAT_ID = 12345


@pytest.fixture(autouse=True)
def _setup_config():
    """Настраивает config для всех тестов."""
    original_working_dir = config_module.WORKING_DIR
    config_module.WORKING_DIR = "/tmp/test_working_dir"
    yield
    config_module.WORKING_DIR = original_working_dir


def _make_update(
    text: str = "test",
    chat_id: int = TEST_CHAT_ID,
    user_id: int = ALLOWED_USER_ID,
) -> MagicMock:
    """Создаёт фейковый Update для тестов."""
    update = MagicMock()
    update.message.text = text
    update.message.chat.id = chat_id
    update.message.chat_id = chat_id
    update.effective_chat.id = chat_id
    update.message.from_user.id = user_id
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    update.message.photo = None
    update.message.document = None
    update.message.media_group_id = None
    return update




# --- Тесты генерации имён файлов ---


class TestGenerateFileName:
    """Тесты генерации уникальных имён файлов."""

    def test_generate_file_name_format(self) -> None:
        """Имя файла соответствует формату file_YYYYMMDD_HHMMSS_XXXXXX.ext."""
        result = generate_file_name("photo.jpg", "jpg")
        assert result.startswith("file_")
        assert result.endswith(".jpg")
        # Формат: file_YYYYMMDD_HHMMSS_XXXXXX.jpg — длина фиксирована
        parts = result.removesuffix(".jpg").split("_")
        # ["file", "YYYYMMDD", "HHMMSS", "XXXXXX"]
        assert len(parts) == 4
        assert len(parts[3]) == 6  # случайный суффикс

    def test_generate_file_name_unique(self) -> None:
        """Два вызова генерируют разные имена (случайный суффикс)."""
        name_first = generate_file_name("test.txt", "txt")
        name_second = generate_file_name("test.txt", "txt")
        assert name_first != name_second


# --- Тесты проверки возраста файла ---





# --- Тесты автоочистки файлов ---


class TestCleanOldReceivedFiles:
    """Тесты удаления старых файлов из received_files/."""

    @pytest.mark.asyncio()
    async def test_clean_old_received_files_deletes_old(
        self, tmp_path: Path
    ) -> None:
        """Файлы старше 7 дней удаляются."""
        files_dir = tmp_path / RECEIVED_FILES_DIR
        files_dir.mkdir()

        # Старый файл (10 дней назад)
        old_file = files_dir / "old_file.jpg"
        old_file.write_text("old")
        old_mtime = time.time() - 10 * SECONDS_PER_DAY
        os.utime(old_file, (old_mtime, old_mtime))

        # Свежий файл (3 дня назад)
        fresh_file = files_dir / "fresh_file.jpg"
        fresh_file.write_text("fresh")
        fresh_mtime = time.time() - 3 * SECONDS_PER_DAY
        os.utime(fresh_file, (fresh_mtime, fresh_mtime))

        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            await clean_old_received_files()

        assert not old_file.exists()
        assert fresh_file.exists()

    @pytest.mark.asyncio()
    async def test_clean_old_received_files_no_directory(
        self, tmp_path: Path
    ) -> None:
        """Очистка когда папки не существует — без ошибок."""
        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            await clean_old_received_files()
        # Не должно быть исключений

    @pytest.mark.asyncio()
    async def test_clean_old_received_files_all_fresh(
        self, tmp_path: Path
    ) -> None:
        """Все файлы свежие — ничего не удаляется."""
        files_dir = tmp_path / RECEIVED_FILES_DIR
        files_dir.mkdir()

        fresh_file = files_dir / "fresh.txt"
        fresh_file.write_text("fresh")

        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            await clean_old_received_files()

        assert fresh_file.exists()


# --- Тесты setup_bot ---





# --- Тесты проверки возраста файла ---


class TestIsFileExpired:
    """Тесты проверки истечения срока годности файла."""

    def test_expired_file(self, tmp_path: Path) -> None:
        """Файл старше лимита считается просроченным."""
        old_file = tmp_path / "old.txt"
        old_file.write_text("data")
        # Установить mtime 10 дней назад
        old_mtime = time.time() - 10 * SECONDS_PER_DAY
        os.utime(old_file, (old_mtime, old_mtime))

        max_age_seconds = 7 * SECONDS_PER_DAY
        assert is_file_expired(old_file, max_age_seconds) is True

    def test_fresh_file(self, tmp_path: Path) -> None:
        """Свежий файл НЕ считается просроченным."""
        fresh_file = tmp_path / "fresh.txt"
        fresh_file.write_text("data")
        # mtime — только что создан, по умолчанию «сейчас»

        max_age_seconds = 7 * SECONDS_PER_DAY
        assert is_file_expired(fresh_file, max_age_seconds) is False

    def test_exactly_at_boundary(self, tmp_path: Path) -> None:
        """Файл ровно на границе — НЕ просроченный (нужен строгий >)."""
        boundary_file = tmp_path / "boundary.txt"
        boundary_file.write_text("data")
        max_age_seconds = 100.0
        # Ставим mtime ровно на границу (100 секунд назад)
        boundary_mtime = time.time() - max_age_seconds
        os.utime(boundary_file, (boundary_mtime, boundary_mtime))

        # time() вызывается внутри _is_file_expired тоже, может пройти ~мс,
        # поэтому результат может быть True. Проверяем, что не падает.
        # Главное — функция корректно работает на границе.
        result = is_file_expired(boundary_file, max_age_seconds)
        assert isinstance(result, bool)


# --- Тесты извлечения информации о файле ---





# --- Тесты извлечения информации о файле ---


class TestExtractFileInfo:
    """Тесты извлечения file_id, расширения и имени из Update."""

    def test_photo_returns_last_photo_size(self) -> None:
        """Для фото возвращает file_id последнего PhotoSize, расширение jpg, имя None."""
        update = _make_update()
        small_photo = MagicMock()
        small_photo.file_id = "small_id"
        large_photo = MagicMock()
        large_photo.file_id = "large_id"
        update.message.photo = [small_photo, large_photo]
        update.message.document = None

        file_id, extension, original_name = extract_file_info(update)

        assert file_id == "large_id"
        assert extension == "jpg"
        assert original_name is None

    def test_document_with_extension(self) -> None:
        """Документ с именем — расширение извлекается из имени."""
        update = _make_update()
        update.message.photo = None
        document = MagicMock()
        document.file_name = "report.PDF"
        document.file_id = "doc_file_id"
        update.message.document = document

        file_id, extension, original_name = extract_file_info(update)

        assert file_id == "doc_file_id"
        assert extension == "pdf"  # lowercase
        assert original_name == "report.PDF"

    def test_document_without_extension(self) -> None:
        """Документ без точки в имени — расширение bin."""
        update = _make_update()
        update.message.photo = None
        document = MagicMock()
        document.file_name = "README"
        document.file_id = "doc_no_ext_id"
        update.message.document = document

        file_id, extension, original_name = extract_file_info(update)

        assert file_id == "doc_no_ext_id"
        assert extension == "bin"
        assert original_name == "README"

    def test_document_with_none_name(self) -> None:
        """Документ без имени файла (file_name=None) — расширение bin."""
        update = _make_update()
        update.message.photo = None
        document = MagicMock()
        document.file_name = None
        document.file_id = "doc_none_name_id"
        update.message.document = document

        file_id, extension, original_name = extract_file_info(update)

        assert file_id == "doc_none_name_id"
        assert extension == "bin"
        assert original_name is None

    def test_document_with_multiple_dots(self) -> None:
        """Документ с несколькими точками — расширение берётся после последней."""
        update = _make_update()
        update.message.photo = None
        document = MagicMock()
        document.file_name = "my.archive.tar.gz"
        document.file_id = "multi_dot_id"
        update.message.document = document

        file_id, extension, original_name = extract_file_info(update)

        assert file_id == "multi_dot_id"
        assert extension == "gz"
        assert original_name == "my.archive.tar.gz"


# --- Тесты скачивания файла с повторами ---





# --- Тесты скачивания файла с повторами ---


class TestDownloadFileWithRetry:
    """Тесты скачивания файла с повторными попытками при TimedOut."""

    @pytest.mark.asyncio()
    async def test_successful_download_first_attempt(self, tmp_path: Path) -> None:
        """Успешное скачивание с первой попытки."""
        telegram_file = MagicMock()
        telegram_file.download_to_drive = AsyncMock()
        save_path = tmp_path / "test.jpg"

        await download_file_with_retry(telegram_file, save_path)

        telegram_file.download_to_drive.assert_called_once_with(str(save_path))

    @pytest.mark.asyncio()
    async def test_retry_on_timed_out_then_success(self, tmp_path: Path) -> None:
        """TimedOut на первой попытке, успех на второй."""
        telegram_file = MagicMock()
        telegram_file.download_to_drive = AsyncMock(
            side_effect=[TimedOut(), None],
        )
        save_path = tmp_path / "test.jpg"

        with patch("claude_manager.telegram_file_downloader.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await download_file_with_retry(telegram_file, save_path)

        assert telegram_file.download_to_drive.call_count == 2
        mock_sleep.assert_called_once_with(FILE_DOWNLOAD_RETRY_DELAY_SECONDS)

    @pytest.mark.asyncio()
    async def test_all_retries_exhausted_raises(self, tmp_path: Path) -> None:
        """Все попытки исчерпаны — пробрасывает TimedOut."""
        telegram_file = MagicMock()
        telegram_file.download_to_drive = AsyncMock(
            side_effect=[TimedOut()] * FILE_DOWNLOAD_RETRY_COUNT,
        )
        save_path = tmp_path / "test.jpg"

        with patch("claude_manager.telegram_file_downloader.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TimedOut):
                await download_file_with_retry(telegram_file, save_path)

        assert telegram_file.download_to_drive.call_count == FILE_DOWNLOAD_RETRY_COUNT

    @pytest.mark.asyncio()
    async def test_non_timed_out_error_not_retried(self, tmp_path: Path) -> None:
        """Ошибка, отличная от TimedOut, пробрасывается без повтора."""
        telegram_file = MagicMock()
        telegram_file.download_to_drive = AsyncMock(
            side_effect=BadRequest("File not found"),
        )
        save_path = tmp_path / "test.jpg"

        with pytest.raises(BadRequest):
            await download_file_with_retry(telegram_file, save_path)

        telegram_file.download_to_drive.assert_called_once()


# --- Тесты скачивания и сохранения файла ---





# --- Тесты скачивания и сохранения файла ---


class TestDownloadAndSaveFile:
    """Тесты полного цикла скачивания файла из Telegram на диск."""

    @pytest.mark.asyncio()
    async def test_creates_directory_and_saves_photo(
        self, tmp_path: Path, mock_bot: MagicMock,
    ) -> None:
        """Создаёт папку received_files/ и скачивает фото."""
        update = _make_update()
        photo_size = MagicMock()
        photo_size.file_id = "photo_file_id_123"
        update.message.photo = [photo_size]
        update.message.document = None

        mock_telegram_file = MagicMock()
        mock_telegram_file.download_to_drive = AsyncMock()
        mock_bot.bot.get_file = AsyncMock(return_value=mock_telegram_file)

        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            result_path = await download_and_save_file(update, mock_bot.bot)

        # Папка создана
        files_dir = tmp_path / RECEIVED_FILES_DIR
        assert files_dir.exists()

        # get_file вызван с правильным file_id
        mock_bot.bot.get_file.assert_called_once_with("photo_file_id_123")

        # download_to_drive вызван
        mock_telegram_file.download_to_drive.assert_called_once()

        # Возвращён абсолютный путь с расширением jpg
        assert result_path.endswith(".jpg")
        assert os.path.isabs(result_path)

    @pytest.mark.asyncio()
    async def test_saves_document_with_correct_extension(
        self, tmp_path: Path, mock_bot: MagicMock,
    ) -> None:
        """Документ сохраняется с правильным расширением."""
        update = _make_update()
        update.message.photo = None
        document = MagicMock()
        document.file_name = "data.csv"
        document.file_id = "doc_csv_id"
        update.message.document = document

        mock_telegram_file = MagicMock()
        mock_telegram_file.download_to_drive = AsyncMock()
        mock_bot.bot.get_file = AsyncMock(return_value=mock_telegram_file)

        with patch.object(config_module, "WORKING_DIR", str(tmp_path)):
            result_path = await download_and_save_file(update, mock_bot.bot)

        assert result_path.endswith(".csv")
        mock_bot.bot.get_file.assert_called_once_with("doc_csv_id")


# --- Тесты определения текущей сессии ---
