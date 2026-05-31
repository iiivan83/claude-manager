"""Тесты модуля telegram_sender — низкоуровневая отправка в Telegram."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from claude_manager.telegram_sender import (
    SEND_RETRY_COUNT,
    SEND_RETRY_DELAY_SECONDS,
    fallback_to_plain_text,
    handle_network_error,
    handle_retry_after,
    send_raw,
    send_telegram_message,
)


# --- Фикст��ры ---


TEST_CHAT_ID = 12345


@pytest.fixture(autouse=True)
def _setup_mock_bot():
    """Создаёт фейковый Bot для всех тестов."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    return mock_bot


@pytest.fixture()
def mock_bot():
    """Возвращает фейковый Bot для явного использования в тестах."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot




# --- Тесты _send_telegram_message ---


class TestSendTelegramMessage:
    """Тесты низкоуровневой отправки в Telegram."""

    @pytest.mark.asyncio()
    async def test_send_telegram_message_html_fallback(
        self, mock_bot: MagicMock
    ) -> None:
        """При ошибке HTML переключается на plain text."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and kwargs.get("parse_mode") == ParseMode.HTML:
                raise BadRequest("Can't parse entities")
            return MagicMock()

        mock_bot.send_message = AsyncMock(side_effect=mock_send)

        await send_telegram_message(mock_bot, TEST_CHAT_ID, "<b>Тест</b>")

        assert call_count == 2
        # Второй вызов — без HTML
        second_call = mock_bot.send_message.call_args_list[1]
        assert second_call[1].get("parse_mode") is None

    @pytest.mark.asyncio()
    async def test_send_telegram_message_retry_after(
        self, mock_bot: MagicMock
    ) -> None:
        """Ожидание при RetryAfter, затем повторная отправка."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RetryAfter(retry_after=1)
            return MagicMock()

        mock_bot.send_message = AsyncMock(side_effect=mock_send)

        await send_telegram_message(mock_bot, TEST_CHAT_ID, "Тест")

        assert call_count == 2

    @pytest.mark.asyncio()
    async def test_send_telegram_message_network_retry(
        self, mock_bot: MagicMock
    ) -> None:
        """Повторные попытки при сетевой ошибке."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise TimedOut()
            return MagicMock()

        mock_bot.send_message = AsyncMock(side_effect=mock_send)

        await send_telegram_message(mock_bot, TEST_CHAT_ID, "Тест")

        assert call_count == 3

    @pytest.mark.asyncio()
    async def test_send_telegram_message_all_retries_failed(
        self, mock_bot: MagicMock
    ) -> None:
        """Все попытки отправки исчерпаны при сетевой ошибке."""
        mock_bot.send_message = AsyncMock(
            side_effect=NetworkError("Connection error")
        )

        # Функция не должна выбрасывать исключение — только логирует
        await send_telegram_message(mock_bot, TEST_CHAT_ID, "Тест")

        assert mock_bot.send_message.call_count == SEND_RETRY_COUNT


# --- Тесты констант отправки ---





# --- Дополнительные тесты _send_telegram_message ---


class TestSendTelegramMessageExtended:
    """Дополнительные тесты _send_telegram_message для покрытия граничных случаев."""

    @pytest.mark.asyncio()
    async def test_success_on_first_try(
        self, mock_bot: MagicMock,
    ) -> None:
        """Успешная отправка с первой попытки — один вызов, без retry."""
        await send_telegram_message(mock_bot, TEST_CHAT_ID, "привет")

        mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio()
    async def test_returns_sent_message_on_success(
        self, mock_bot: MagicMock,
    ) -> None:
        """send_telegram_message returns Telegram's sent Message object."""
        sent_message = MagicMock()
        sent_message.message_id = 888
        mock_bot.send_message.return_value = sent_message

        result = await send_telegram_message(mock_bot, TEST_CHAT_ID, "текст")

        assert result is sent_message

    @pytest.mark.asyncio()
    async def test_returns_plain_text_message_after_html_fallback(
        self, mock_bot: MagicMock,
    ) -> None:
        """HTML fallback returns the message from the successful plain send."""
        fallback_message = MagicMock()
        fallback_message.message_id = 889
        mock_bot.send_message = AsyncMock(
            side_effect=[
                BadRequest("Can't parse entities"),
                fallback_message,
            ],
        )

        result = await send_telegram_message(
            mock_bot,
            TEST_CHAT_ID,
            "<b>broken",
        )

        assert result is fallback_message

    @pytest.mark.asyncio()
    async def test_bad_request_non_html_reraises(
        self, mock_bot: MagicMock,
    ) -> None:
        """BadRequest при parse_mode != HTML — пробрасывается наверх."""
        mock_bot.send_message = AsyncMock(
            side_effect=BadRequest("Some other error"),
        )

        with pytest.raises(BadRequest):
            await send_telegram_message(mock_bot,
                TEST_CHAT_ID, "plain text", parse_mode=None,
            )

    @pytest.mark.asyncio()
    async def test_network_error_retry_with_network_error_class(
        self, mock_bot: MagicMock,
    ) -> None:
        """NetworkError (не TimedOut) тоже вызывает retry."""
        call_count = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise NetworkError("Connection reset")
            return MagicMock()

        mock_bot.send_message = AsyncMock(side_effect=mock_send)

        await send_telegram_message(mock_bot, TEST_CHAT_ID, "Тест")

        assert call_count == 3

    @pytest.mark.asyncio()
    async def test_default_parse_mode_is_html(
        self, mock_bot: MagicMock,
    ) -> None:
        """По умолчанию parse_mode = HTML."""
        await send_telegram_message(mock_bot, TEST_CHAT_ID, "текст")

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["parse_mode"] == ParseMode.HTML

    @pytest.mark.asyncio()
    async def test_custom_parse_mode_passed_through(
        self, mock_bot: MagicMock,
    ) -> None:
        """Переданный parse_mode пробрасывается в send_message."""
        await send_telegram_message(mock_bot, TEST_CHAT_ID, "текст", parse_mode=None)

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["parse_mode"] is None

    @pytest.mark.asyncio()
    async def test_reply_markup_passed_through(
        self, mock_bot: MagicMock,
    ) -> None:
        """reply_markup пробрасывается в send_message."""
        mock_markup = MagicMock()
        await send_telegram_message(mock_bot,
            TEST_CHAT_ID, "текст", reply_markup=mock_markup,
        )

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["reply_markup"] is mock_markup

    @pytest.mark.asyncio()
    async def test_reply_to_message_id_passed_as_reply_parameters(
        self, mock_bot: MagicMock,
    ) -> None:
        """reply_to_message_id становится Telegram reply_parameters."""
        await send_telegram_message(
            mock_bot,
            TEST_CHAT_ID,
            "reply text",
            reply_to_message_id=777,
        )

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["reply_parameters"].message_id == 777
        assert call_kwargs["reply_parameters"].allow_sending_without_reply is True

    @pytest.mark.asyncio()
    async def test_reply_bad_request_retries_without_reply(
        self, mock_bot: MagicMock,
    ) -> None:
        """Если Telegram отклоняет reply metadata, текст уходит без reply."""
        mock_bot.send_message = AsyncMock(
            side_effect=[
                BadRequest("Message to be replied not found"),
                MagicMock(),
            ],
        )

        await send_telegram_message(
            mock_bot,
            TEST_CHAT_ID,
            "reply text",
            reply_to_message_id=777,
        )

        assert mock_bot.send_message.call_count == 2
        first_kwargs = mock_bot.send_message.call_args_list[0][1]
        second_kwargs = mock_bot.send_message.call_args_list[1][1]
        assert first_kwargs["reply_parameters"].message_id == 777
        assert "reply_parameters" not in second_kwargs


# --- Тесты автоочистки файлов ---





# --- Тесты _send_raw ---


class TestSendRaw:
    """Тесты прямого вызова Telegram API через _send_raw."""

    @pytest.mark.asyncio()
    async def test_send_raw_delegates_to_bot_send_message(
        self, mock_bot: MagicMock,
    ) -> None:
        """_send_raw передаёт все аргументы в bot.send_message."""
        await send_raw(mock_bot, TEST_CHAT_ID, "текст", ParseMode.HTML, None)

        mock_bot.send_message.assert_called_once_with(
            TEST_CHAT_ID, "текст",
            parse_mode=ParseMode.HTML, reply_markup=None,
        )

    @pytest.mark.asyncio()
    async def test_send_raw_passes_reply_markup(
        self, mock_bot: MagicMock,
    ) -> None:
        """_send_raw пробрасывает reply_markup в Telegram API."""
        mock_markup = MagicMock()
        await send_raw(mock_bot, TEST_CHAT_ID, "ok", None, mock_markup)

        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["reply_markup"] is mock_markup

    @pytest.mark.asyncio()
    async def test_send_raw_propagates_exception(
        self, mock_bot: MagicMock,
    ) -> None:
        """_send_raw не глотает исключения — пробрасывает наверх."""
        mock_bot.send_message = AsyncMock(
            side_effect=NetworkError("fail"),
        )
        with pytest.raises(NetworkError):
            await send_raw(mock_bot, TEST_CHAT_ID, "текст", None, None)


# --- Тесты _fallback_to_plain_text ---





# --- Тесты _fallback_to_plain_text ---


class TestFallbackToPlainText:
    """Тесты переключения на plain text при ошибке HTML-парсинга."""

    @pytest.mark.asyncio()
    async def test_fallback_strips_html_and_resends(
        self, mock_bot: MagicMock,
    ) -> None:
        """При parse_mode=HTML снимает теги и отправляет plain text."""
        result = await fallback_to_plain_text(mock_bot,
            TEST_CHAT_ID, "<b>жирный</b> текст", ParseMode.HTML, None,
        )

        assert result is mock_bot.send_message.return_value
        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["parse_mode"] is None
        # Текст без HTML-тегов
        sent_text = mock_bot.send_message.call_args[0][1]
        assert "<b>" not in sent_text
        assert "жирный" in sent_text

    @pytest.mark.asyncio()
    async def test_fallback_returns_false_for_non_html(
        self, mock_bot: MagicMock,
    ) -> None:
        """Если parse_mode != HTML, возвращает None и ничего не отправляет."""
        result = await fallback_to_plain_text(mock_bot,
            TEST_CHAT_ID, "текст", None, None,
        )

        assert result is None
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio()
    async def test_fallback_returns_false_for_markdown(
        self, mock_bot: MagicMock,
    ) -> None:
        """Если parse_mode = Markdown (не HTML), возвращает None."""
        result = await fallback_to_plain_text(mock_bot,
            TEST_CHAT_ID, "*bold*", ParseMode.MARKDOWN, None,
        )

        assert result is None
        mock_bot.send_message.assert_not_called()


# --- Тесты _handle_retry_after ---





# --- Тесты _handle_retry_after ---


class TestHandleRetryAfter:
    """Тесты обработки RetryAfter от Telegram."""

    @pytest.mark.asyncio()
    async def test_handle_retry_after_waits_and_resends(
        self, mock_bot: MagicMock,
    ) -> None:
        """Ждёт указанное время и повторяет отправку."""
        with patch("claude_manager.telegram_sender.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await handle_retry_after(mock_bot,
                TEST_CHAT_ID, "текст", ParseMode.HTML, None, 5,
            )

            mock_sleep.assert_called_once_with(5)
            mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio()
    async def test_handle_retry_after_resend_failure_silenced(
        self, mock_bot: MagicMock,
    ) -> None:
        """Если повторная отправка после ожидания падает — исключение не пробрасывается."""
        mock_bot.send_message = AsyncMock(
            side_effect=NetworkError("fail after retry"),
        )

        with patch("claude_manager.telegram_sender.asyncio.sleep", new_callable=AsyncMock):
            # Не должна выбросить исключение
            await handle_retry_after(mock_bot,
                TEST_CHAT_ID, "текст", ParseMode.HTML, None, 1,
            )


# --- Тесты _handle_network_error ---





# --- Тесты _handle_network_error ---


class TestHandleNetworkError:
    """Тесты решения о retry при сетевой ошибке."""

    def test_returns_true_on_first_attempt(self) -> None:
        """Первая попытка (attempt=0) — повторить."""
        assert handle_network_error(0, TEST_CHAT_ID) is True

    def test_returns_true_on_middle_attempt(self) -> None:
        """Средняя попытка (attempt=1 из 3) — повторить."""
        assert handle_network_error(1, TEST_CHAT_ID) is True

    def test_returns_false_on_last_attempt(self) -> None:
        """Последняя попытка (attempt=2 из 3) — не повторять."""
        assert handle_network_error(SEND_RETRY_COUNT - 1, TEST_CHAT_ID) is False


# --- Дополнительные тесты _send_telegram_message ---





# --- Тесты констант отправки ---


class TestSendRetryConstants:
    """Проверка значений констант retry-логики отправки в Telegram."""

    def test_send_retry_count_value(self) -> None:
        """SEND_RETRY_COUNT = 3 — контракт для retry-цикла."""
        assert SEND_RETRY_COUNT == 3

    def test_send_retry_delay_value(self) -> None:
        """SEND_RETRY_DELAY_SECONDS = 2 — пауза между попытками."""
        assert SEND_RETRY_DELAY_SECONDS == 2


# --- Тесты _send_raw ---
