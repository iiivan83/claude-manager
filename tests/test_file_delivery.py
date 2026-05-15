"""Тесты модуля file_delivery — обработка файловых маркеров и доставка файлов."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram import MessageEntity

from claude_manager import file_sender
from claude_manager.file_delivery import (
    FILE_CONTENT_HEADER_TEMPLATE,
    IMAGE_EXTENSIONS,
    process_file_markers,
    process_show_file_markers,
    send_as_document,
    send_text_file,
    shift_entity,
)


# --- Фикстуры ---


TEST_CHAT_ID = 12345


@pytest.fixture()
def mock_bot():
    """Создаёт фейковый Bot для тестов."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_document = AsyncMock()
    return bot




# --- Тесты _shift_entity (сдвиг offset в MessageEntity) ---


class TestShiftEntity:
    """Тесты функции _shift_entity — создание копии MessageEntity со сдвигом offset."""

    def test_shifts_offset_by_positive_delta(self) -> None:
        """Положительный сдвиг увеличивает offset, остальные поля сохраняются."""
        entity = MessageEntity(type=MessageEntity.BOLD, offset=10, length=5)
        shifted = shift_entity(entity, 20)
        assert shifted.offset == 30
        assert shifted.length == 5
        assert shifted.type == MessageEntity.BOLD

    def test_shifts_offset_by_zero(self) -> None:
        """Нулевой сдвиг — offset не меняется."""
        entity = MessageEntity(type=MessageEntity.ITALIC, offset=7, length=3)
        shifted = shift_entity(entity, 0)
        assert shifted.offset == 7
        assert shifted.length == 3

    def test_preserves_url_field(self) -> None:
        """Поле url сохраняется при сдвиге."""
        entity = MessageEntity(
            type=MessageEntity.TEXT_LINK, offset=0, length=10,
            url="https://example.com",
        )
        shifted = shift_entity(entity, 5)
        assert shifted.url == "https://example.com"
        assert shifted.offset == 5

    def test_preserves_language_field(self) -> None:
        """Поле language сохраняется при сдвиге."""
        entity = MessageEntity(
            type=MessageEntity.CODE, offset=2, length=8, language="python",
        )
        shifted = shift_entity(entity, 10)
        assert shifted.language == "python"
        assert shifted.offset == 12

    def test_preserves_length_unchanged(self) -> None:
        """Длина entity не меняется при сдвиге."""
        entity = MessageEntity(type=MessageEntity.BOLD, offset=5, length=100)
        shifted = shift_entity(entity, 50)
        assert shifted.length == 100

    def test_returns_new_instance(self) -> None:
        """Возвращает новый экземпляр, а не мутирует оригинал."""
        entity = MessageEntity(type=MessageEntity.BOLD, offset=10, length=5)
        shifted = shift_entity(entity, 20)
        # Оригинал не изменён
        assert entity.offset == 10
        assert shifted.offset == 30


# --- Тесты _send_as_document (отправка файла как document-вложения) ---





# --- Тесты _send_text_file (контракт передачи резерва в file_sender) ---


class TestSendTextFile:
    """Тесты контракта между _send_text_file (bot.py) и render_file_for_telegram (file_sender)."""

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities", return_value=[])
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_passes_header_reserve(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """render_file_for_telegram вызывается с first_chunk_reserve, равным UTF-16 длине заголовка."""
        mock_read.return_value = ("file content", None)
        mock_render.return_value = [("rendered text", [])]

        filename = "report.md"
        header = FILE_CONTENT_HEADER_TEMPLATE.format(filename=filename)
        expected_reserve = len(header.encode("utf-16-le")) // 2

        await send_text_file(mock_bot, TEST_CHAT_ID, f"/path/to/{filename}")

        mock_render.assert_called_once_with(
            "file content", first_chunk_reserve=expected_reserve,
        )

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities", return_value=[])
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_long_filename_passes_correct_reserve(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Длинное имя файла (200 символов) — резерв корректно учитывает все части заголовка."""
        mock_read.return_value = ("file content", None)
        mock_render.return_value = [("rendered text", [])]

        # Имя файла из 200 ASCII-символов
        long_filename = "a" * 200
        header = FILE_CONTENT_HEADER_TEMPLATE.format(filename=long_filename)
        # Заголовок: emoji U+1F4CE (2 UTF-16 units) + пробел (1) + filename (200) + \n\n (2) = 205
        expected_reserve = len(header.encode("utf-16-le")) // 2

        await send_text_file(mock_bot, TEST_CHAT_ID, f"/path/to/{long_filename}")

        actual_reserve = mock_render.call_args.kwargs["first_chunk_reserve"]
        assert actual_reserve == expected_reserve
        assert actual_reserve == 205


# --- Тесты _shift_entity (сдвиг offset в MessageEntity) ---





# --- Дополнительные тесты _send_text_file ---


class TestSendTextFileExtended:
    """Дополнительные тесты _send_text_file: ошибки чтения, заголовок, entity-сдвиг, мульти-чанки."""

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.telegram_sender.send_telegram_message", new_callable=AsyncMock)
    @patch.object(
        file_sender, "read_file_content",
        return_value=("", "Файл не найден: /tmp/missing.md"),
    )
    async def test_read_error_sends_error_message(
        self,
        mock_read: MagicMock,
        mock_send_msg: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """Ошибка чтения файла — отправляет текст ошибки, не пытается рендерить."""
        await send_text_file(mock_bot, TEST_CHAT_ID, "/tmp/missing.md")
        # telegram_sender.send_telegram_message receives (bot, chat_id, text, ...)
        mock_send_msg.assert_awaited_once()
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities", return_value=[])
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_first_chunk_gets_header_prepended(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Первый чанк получает заголовок с именем файла (скрепка + имя)."""
        mock_read.return_value = ("content", None)
        mock_render.return_value = [("rendered", [])]

        await send_text_file(mock_bot, TEST_CHAT_ID, "/path/to/notes.txt")

        sent_text = mock_bot.send_message.call_args.args[1]
        expected_header = FILE_CONTENT_HEADER_TEMPLATE.format(filename="notes.txt")
        assert sent_text.startswith(expected_header)
        assert sent_text == expected_header + "rendered"

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities")
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_first_chunk_entities_shifted_by_header_length(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Entities первого чанка сдвигаются на длину заголовка в UTF-16 code units."""
        mock_read.return_value = ("content", None)
        # Один entity с offset=0 (начало рендеренного текста)
        bold_entity = MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)
        mock_convert.return_value = [bold_entity]
        mock_render.return_value = [("hello world", [MagicMock()])]

        await send_text_file(mock_bot, TEST_CHAT_ID, "/path/to/file.md")

        header = FILE_CONTENT_HEADER_TEMPLATE.format(filename="file.md")
        header_utf16_length = len(header.encode("utf-16-le")) // 2

        sent_entities = mock_bot.send_message.call_args.kwargs["entities"]
        assert len(sent_entities) == 1
        assert sent_entities[0].offset == header_utf16_length
        assert sent_entities[0].length == 5

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities")
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_second_chunk_no_header_no_shift(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Второй чанк отправляется без заголовка и entities не сдвигаются."""
        mock_read.return_value = ("content", None)
        entity_chunk2 = MessageEntity(type=MessageEntity.ITALIC, offset=3, length=4)
        # convert_entities вызывается дважды — для каждого чанка
        mock_convert.side_effect = [[], [entity_chunk2]]
        mock_render.return_value = [
            ("first chunk", []),
            ("second chunk", [MagicMock()]),
        ]

        await send_text_file(mock_bot, TEST_CHAT_ID, "/path/to/long.md")

        # Два вызова send_message — по одному на каждый чанк
        assert mock_bot.send_message.call_count == 2

        # Второй вызов: текст без заголовка, entity без сдвига
        second_call = mock_bot.send_message.call_args_list[1]
        sent_text = second_call.args[1]
        assert not sent_text.startswith("\U0001F4CE")
        assert sent_text == "second chunk"

        sent_entities = second_call.kwargs["entities"]
        assert sent_entities == [entity_chunk2]
        assert sent_entities[0].offset == 3

    @pytest.mark.asyncio()
    @patch.object(file_sender, "convert_entities", return_value=[])
    @patch.object(file_sender, "render_file_for_telegram")
    @patch.object(file_sender, "read_file_content")
    async def test_multiple_chunks_all_sent(
        self,
        mock_read: MagicMock,
        mock_render: MagicMock,
        mock_convert: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Три чанка — все три отправлены через send_message."""
        mock_read.return_value = ("content", None)
        mock_render.return_value = [
            ("chunk 1", []),
            ("chunk 2", []),
            ("chunk 3", []),
        ]

        await send_text_file(mock_bot, TEST_CHAT_ID, "/path/to/huge.md")

        assert mock_bot.send_message.call_count == 3


# --- Дополнительные тесты _process_file_markers ---





# --- Тесты _send_as_document (отправка файла как document-вложения) ---


class TestSendAsDocument:
    """Тесты функции _send_as_document — отправка файла через Telegram send_document."""

    @pytest.mark.asyncio()
    @patch.object(file_sender, "check_binary_file", return_value=None)
    async def test_sends_file_via_send_document(
        self,
        mock_check: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Валидный файл — отправляется через bot.send_document с правильным путём."""
        mock_bot.send_document = AsyncMock()
        await send_as_document(mock_bot, TEST_CHAT_ID, "/tmp/test.pdf")
        mock_bot.send_document.assert_awaited_once_with(
            TEST_CHAT_ID, document="/tmp/test.pdf",
        )

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.telegram_sender.send_telegram_message", new_callable=AsyncMock)
    @patch.object(
        file_sender, "check_binary_file",
        return_value="Файл не найден: /tmp/missing.pdf",
    )
    async def test_error_sends_message_instead_of_document(
        self,
        mock_check: MagicMock,
        mock_send_msg: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """Ошибка проверки файла — отправляет текст ошибки, не вызывает send_document."""
        await send_as_document(mock_bot, TEST_CHAT_ID, "/tmp/missing.pdf")
        mock_send_msg.assert_awaited_once()
        mock_bot.send_document.assert_not_called()


# --- Дополнительные тесты _send_text_file ---





# --- Тесты _process_file_markers напрямую ---


class TestProcessFileMarkers:
    """Тесты функции _process_file_markers."""

    @pytest.mark.asyncio()
    async def test_process_file_markers_no_markers(
        self, mock_bot: MagicMock,
    ) -> None:
        """Текст без маркеров — возвращается без изменений, файлы не отправляются."""
        result = await process_file_markers(mock_bot, TEST_CHAT_ID, "обычный текст")
        assert result == "обычный текст"
        mock_bot.send_document.assert_not_called()

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch.object(file_sender, "strip_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/test.md"],
    )
    async def test_send_file_always_sends_as_document(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_send_document: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """[SEND_FILE] для текстового файла — всегда отправляет как document, не рендерит в чат."""
        result = await process_file_markers(mock_bot,
            TEST_CHAT_ID, "answer [SEND_FILE:/tmp/test.md]",
        )
        mock_send_document.assert_awaited_once()
        assert result == "answer"


# --- Тесты _process_show_file_markers ---





# --- Дополнительные тесты _process_file_markers ---


class TestProcessFileMarkersExtended:
    """Дополнительные тесты _process_file_markers: множественные маркеры, очистка текста."""

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch.object(file_sender, "strip_file_markers", return_value="ответ")
    @patch.object(
        file_sender, "extract_file_markers",
        return_value=["/tmp/a.pdf", "/tmp/b.pdf", "/tmp/c.pdf"],
    )
    async def test_multiple_markers_all_sent_as_documents(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_send_document: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """Три маркера [SEND_FILE] — все три файла отправлены как documents."""
        result = await process_file_markers(mock_bot,
            TEST_CHAT_ID,
            "ответ [SEND_FILE:/tmp/a.pdf] [SEND_FILE:/tmp/b.pdf] [SEND_FILE:/tmp/c.pdf]",
        )
        assert mock_send_document.await_count == 3
        assert mock_send_document.await_count >= 1


        assert result == "ответ"

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch.object(file_sender, "strip_file_markers", return_value="")
    @patch.object(
        file_sender, "extract_file_markers", return_value=["/tmp/only.txt"],
    )
    async def test_text_only_markers_returns_empty_string(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_send_document: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """Текст состоит только из маркера — после очистки возвращается пустая строка."""
        result = await process_file_markers(mock_bot,
            TEST_CHAT_ID, "[SEND_FILE:/tmp/only.txt]",
        )
        assert result == ""


# --- Дополнительные тесты _process_show_file_markers ---





# --- Тесты _process_show_file_markers ---


class TestProcessShowFileMarkers:
    """Тесты функции _process_show_file_markers."""

    @pytest.mark.asyncio()
    async def test_no_markers_returns_text_unchanged(
        self, mock_bot: MagicMock,
    ) -> None:
        """Текст без маркеров — возвращается без изменений."""
        result = await process_show_file_markers(mock_bot, TEST_CHAT_ID, "обычный текст")
        assert result == "обычный текст"

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_text_file", new_callable=AsyncMock)
    @patch.object(file_sender, "is_too_large_for_inline", return_value=False)
    @patch.object(file_sender, "is_text_file", return_value=True)
    @patch.object(file_sender, "strip_show_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_show_file_markers", return_value=["/tmp/file.md"],
    )
    async def test_show_file_renders_small_text_inline(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_is_large: MagicMock,
        mock_send_text: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """[SHOW_FILE] для маленького текстового файла — рендерит в чат."""
        result = await process_show_file_markers(mock_bot,
            TEST_CHAT_ID, "answer [SHOW_FILE:/tmp/file.md]",
        )
        mock_send_text.assert_awaited_once()
        assert result == "answer"

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch("claude_manager.file_delivery.telegram_sender.send_telegram_message", new_callable=AsyncMock)
    @patch.object(file_sender, "is_too_large_for_inline", return_value=True)
    @patch.object(file_sender, "is_text_file", return_value=True)
    @patch.object(file_sender, "strip_show_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_show_file_markers", return_value=["/tmp/huge.html"],
    )
    async def test_show_file_falls_back_to_document_for_large_text(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_is_large: MagicMock,
        mock_send_msg: AsyncMock,
        mock_send_document: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """[SHOW_FILE] для большого текстового файла — fallback на document + пояснение."""
        await process_show_file_markers(mock_bot,
            TEST_CHAT_ID, "answer [SHOW_FILE:/tmp/huge.html]",
        )
        mock_send_document.assert_awaited_once()
        mock_send_msg.assert_awaited_once()

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch.object(file_sender, "is_text_file", return_value=False)
    @patch.object(file_sender, "strip_show_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_show_file_markers", return_value=["/tmp/image.png"],
    )
    async def test_show_file_sends_binary_as_document(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_send_document: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """[SHOW_FILE] для бинарного файла — отправляет как document."""
        await process_show_file_markers(mock_bot,
            TEST_CHAT_ID, "answer [SHOW_FILE:/tmp/image.png]",
        )
        mock_send_document.assert_awaited_once()


# --- Тесты _send_text_file (контракт передачи резерва в file_sender) ---





# --- Дополнительные тесты _process_show_file_markers ---


class TestProcessShowFileMarkersExtended:
    """Дополнительные тесты _process_show_file_markers: множественные маркеры, смешанные типы."""

    @pytest.mark.asyncio()
    @patch("claude_manager.file_delivery.send_as_document", new_callable=AsyncMock)
    @patch("claude_manager.file_delivery.send_text_file", new_callable=AsyncMock)
    @patch.object(file_sender, "is_too_large_for_inline", return_value=False)
    @patch.object(file_sender, "is_text_file")
    @patch.object(file_sender, "strip_show_file_markers", return_value="answer")
    @patch.object(
        file_sender, "extract_show_file_markers",
        return_value=["/tmp/readme.md", "/tmp/photo.png"],
    )
    async def test_mixed_text_and_binary_files(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_is_text: MagicMock,
        mock_is_large: MagicMock,
        mock_send_text: AsyncMock,
        mock_send_document: AsyncMock,
        mock_bot: MagicMock,
    ) -> None:
        """Два маркера: текстовый рендерится inline, бинарный — как document."""
        # is_text_file вызывается 3 раза:
        # 1) line 344 для readme.md → True (идёт в if-ветку, вызывает is_too_large)
        # 2) line 344 для photo.png → False (short-circuit, is_too_large не вызывается)
        # 3) line 346 (elif) для photo.png → False (идёт в else-ветку)
        mock_is_text.side_effect = [True, False, False]

        await process_show_file_markers(mock_bot,
            TEST_CHAT_ID,
            "answer [SHOW_FILE:/tmp/readme.md] [SHOW_FILE:/tmp/photo.png]",
        )
        mock_send_text.assert_awaited_once()
        mock_send_document.assert_awaited_once()

    @pytest.mark.asyncio()
    @patch.object(file_sender, "strip_show_file_markers", return_value="")
    @patch.object(
        file_sender, "extract_show_file_markers", return_value=[],
    )
    async def test_empty_extract_returns_original_text(
        self,
        mock_extract: MagicMock,
        mock_strip: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """extract_show_file_markers возвращает пустой список — текст не изменён."""
        result = await process_show_file_markers(mock_bot, TEST_CHAT_ID, "plain text")
        assert result == "plain text"


# --- Тесты констант FILE_CONTENT_HEADER_TEMPLATE и IMAGE_EXTENSIONS ---





# --- Тесты констант FILE_CONTENT_HEADER_TEMPLATE и IMAGE_EXTENSIONS ---


class TestFileMarkerConstants:
    """Тесты констант, используемых при обработке файловых маркеров."""

    def test_file_content_header_contains_paperclip_emoji(self) -> None:
        """Шаблон заголовка содержит эмодзи скрепки (U+1F4CE)."""
        assert "\U0001F4CE" in FILE_CONTENT_HEADER_TEMPLATE

    def test_file_content_header_has_filename_placeholder(self) -> None:
        """Шаблон заголовка содержит placeholder {filename}."""
        assert "{filename}" in FILE_CONTENT_HEADER_TEMPLATE

    def test_file_content_header_format_produces_expected_result(self) -> None:
        """format() подставляет имя файла корректно."""
        result = FILE_CONTENT_HEADER_TEMPLATE.format(filename="test.md")
        assert "test.md" in result
        assert result.endswith("\n\n")

    def test_image_extensions_contains_common_formats(self) -> None:
        """IMAGE_EXTENSIONS содержит все основные форматы изображений."""
        expected_formats = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
        assert expected_formats.issubset(IMAGE_EXTENSIONS)

    def test_image_extensions_is_a_set(self) -> None:
        """IMAGE_EXTENSIONS — множество (не список), для O(1) lookup."""
        assert isinstance(IMAGE_EXTENSIONS, set)


# --- Тесты session_id_callback (раннее обновление привязок) ---
