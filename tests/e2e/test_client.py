"""Клиент для E2E тестирования бота через Telegram.

Обёртка над библиотекой Telethon (работает с Telegram как обычный пользователь).
Позволяет отправлять сообщения боту и проверять его ответы в автоматических тестах.

Этот файл содержит только клиент — сами тесты создаются отдельно.
"""

import asyncio
import logging

from telethon import TelegramClient, events

# Время ожидания ответа от бота по умолчанию (секунды)
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 30

logger = logging.getLogger(__name__)


class TelegramTestClient:
    """Клиент для отправки сообщений боту и получения ответов в E2E тестах."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        phone: str,
        bot_username: str,
        session_name: str = "telethon_test",
    ) -> None:
        """Создаёт клиент Telethon с заданными параметрами.

        api_id и api_hash — получить на https://my.telegram.org.
        phone — номер телефона для авторизации.
        bot_username — имя бота, которому будут отправляться сообщения (например "@my_bot").
        session_name — имя файла сессии (Telethon сохраняет авторизацию в файл).
        """
        self._client = TelegramClient(session_name, api_id, api_hash)
        self._phone = phone
        self._bot_username = bot_username
        self._last_response: str | None = None
        # Событие (asyncio.Event) — сигнал о том, что пришёл новый ответ от бота
        self._response_received = asyncio.Event()

    async def connect(self) -> None:
        """Подключается к Telegram. При первом запуске запросит SMS-код."""
        await self._client.start(phone=self._phone)
        logger.info("Telethon подключён к Telegram")

        # Подписываемся на новые сообщения от бота
        @self._client.on(events.NewMessage(from_users=[self._bot_username]))
        async def _on_bot_message(event: events.NewMessage.Event) -> None:
            """Обработчик: сохраняет текст ответа от бота."""
            self._last_response = event.message.text
            self._response_received.set()
            logger.info("Получен ответ от бота: %s", event.message.text[:100])

    async def disconnect(self) -> None:
        """Отключается от Telegram."""
        await self._client.disconnect()
        logger.info("Telethon отключён")

    async def send_message(self, text: str) -> None:
        """Отправляет текстовое сообщение боту."""
        self._reset_response_state()
        await self._client.send_message(self._bot_username, text)
        logger.info("Отправлено сообщение боту: %s", text[:100])

    async def send_command(self, command: str) -> None:
        """Отправляет команду боту (например '/new', '/sessions')."""
        await self.send_message(command)

    async def send_photo(self, path: str, caption: str = "") -> None:
        """Отправляет фото боту с подписью."""
        self._reset_response_state()
        await self._client.send_file(
            self._bot_username,
            path,
            caption=caption,
        )
        logger.info("Отправлено фото боту: %s", path)

    async def wait_for_response(
        self, timeout: int = DEFAULT_RESPONSE_TIMEOUT_SECONDS
    ) -> str:
        """Ждёт ответ от бота и возвращает текст.

        Если за timeout секунд ответ не получен — бросает TimeoutError.
        """
        try:
            await asyncio.wait_for(
                self._response_received.wait(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Бот не ответил за {timeout} секунд"
            ) from None

        # К этому моменту _last_response гарантированно заполнен
        response_text = self._last_response
        if response_text is None:
            raise TimeoutError("Ответ от бота пуст")
        return response_text

    async def get_last_response(self) -> str | None:
        """Возвращает текст последнего ответа от бота (или None)."""
        return self._last_response

    def _reset_response_state(self) -> None:
        """Сбрасывает состояние ожидания перед новым сообщением."""
        self._last_response = None
        self._response_received.clear()
