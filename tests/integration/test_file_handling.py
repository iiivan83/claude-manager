"""Интеграционные тесты: работа с файлами (фото и документы).

Проверяет цепочку:
- Сохранение фото/документа в received_files/
- Формирование текстового задания для Claude через _build_file_task
- Правильность пути к файлу и текста задания
"""

from pathlib import Path

import pytest

from claude_manager.claude_interaction import build_file_task as _build_file_task
from claude_manager.file_delivery import IMAGE_EXTENSIONS
from claude_manager.telegram_file_downloader import (
    RECEIVED_FILES_DIR,
    generate_file_name as _generate_file_name,
)


# --- Тесты: генерация имён файлов ---


class TestGenerateFileName:
    """Генерация уникальных имён файлов для полученных из Telegram файлов."""

    def test_generated_name_has_correct_extension(self) -> None:
        """Имя файла содержит указанное расширение."""
        name = _generate_file_name("photo.jpg", "jpg")
        assert name.endswith(".jpg")

    def test_generated_name_starts_with_prefix(self) -> None:
        """Имя файла начинается с 'file_'."""
        name = _generate_file_name("doc.pdf", "pdf")
        assert name.startswith("file_")

    def test_two_names_are_unique(self) -> None:
        """Два вызова подряд генерируют разные имена (из-за random-суффикса)."""
        name_first = _generate_file_name("a.txt", "txt")
        name_second = _generate_file_name("a.txt", "txt")
        # Теоретически могут совпасть (шанс 1 из 2 млрд), но практически — нет
        assert name_first != name_second


# --- Тесты: формирование задания для Claude ---


class TestBuildFileTask:
    """Формирование текстового задания из файла и подписи."""

    def test_photo_with_caption_includes_caption_and_path(self) -> None:
        """Фото с подписью — задание включает подпись и путь к файлу."""
        task = _build_file_task(
            "/tmp/received_files/photo_001.jpg",
            caption="Объясни этот график",
            is_image=True,
        )
        assert "Объясни этот график" in task
        assert "/tmp/received_files/photo_001.jpg" in task

    def test_photo_without_caption_asks_to_describe(self) -> None:
        """Фото без подписи — задание просит описать, что на фотографии."""
        task = _build_file_task(
            "/tmp/received_files/photo_002.jpg",
            caption=None,
            is_image=True,
        )
        assert "фотограф" in task.lower() or "фото" in task.lower()
        assert "/tmp/received_files/photo_002.jpg" in task

    def test_document_with_caption_includes_task(self) -> None:
        """Документ с подписью — задание включает подпись."""
        task = _build_file_task(
            "/tmp/received_files/report.pdf",
            caption="Сделай резюме",
            is_image=False,
        )
        assert "Сделай резюме" in task
        assert "/tmp/received_files/report.pdf" in task

    def test_document_without_caption_asks_to_describe(self) -> None:
        """Документ без подписи — задание просит описать содержимое."""
        task = _build_file_task(
            "/tmp/received_files/data.csv",
            caption=None,
            is_image=False,
        )
        assert "содержимое" in task.lower() or "файл" in task.lower()
        assert "/tmp/received_files/data.csv" in task

    def test_task_always_mentions_read_instruction(self) -> None:
        """Задание всегда содержит указание прочитать файл."""
        task_with_caption = _build_file_task("/tmp/f.txt", "Анализируй", False)
        task_without_caption = _build_file_task("/tmp/f.txt", None, False)

        # Оба варианта указывают Claude прочитать файл
        assert "Прочитай" in task_with_caption or "прочитай" in task_with_caption
        assert "Прочитай" in task_without_caption or "прочитай" in task_without_caption


# --- Тесты: определение типа файла по расширению ---


class TestImageExtensions:
    """Проверка корректности набора расширений изображений."""

    def test_common_image_extensions_are_supported(self) -> None:
        """Популярные форматы изображений включены в список."""
        expected = {"jpg", "jpeg", "png", "gif", "webp"}
        for ext in expected:
            assert ext in IMAGE_EXTENSIONS

    def test_non_image_extensions_are_not_in_set(self) -> None:
        """Расширения не-изображений отсутствуют в списке."""
        non_images = {"pdf", "txt", "csv", "py", "json", "zip"}
        for ext in non_images:
            assert ext not in IMAGE_EXTENSIONS


# --- Тесты: интеграция file_name + task ---


class TestFileNameToTask:
    """Цепочка: генерация имени -> построение задания."""

    def test_generated_photo_name_produces_valid_task(self) -> None:
        """Сгенерированное имя фото используется в задании без ошибок."""
        file_name = _generate_file_name("sunset.jpg", "jpg")
        full_path = f"/project/received_files/{file_name}"

        task = _build_file_task(full_path, caption="Что на фото?", is_image=True)

        assert full_path in task
        assert "Что на фото?" in task

    def test_generated_doc_name_produces_valid_task(self) -> None:
        """Сгенерированное имя документа используется в задании без ошибок."""
        file_name = _generate_file_name("report.xlsx", "xlsx")
        full_path = f"/project/received_files/{file_name}"

        task = _build_file_task(full_path, caption=None, is_image=False)

        assert full_path in task
