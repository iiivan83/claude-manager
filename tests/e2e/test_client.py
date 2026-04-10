"""Клиент для E2E тестирования бота через Telegram.

Обёртка над библиотекой Telethon (работает с Telegram как обычный пользователь).
Позволяет отправлять сообщения боту и проверять его ответы в автоматических тестах.

Этот файл содержит только клиент — сами тесты создаются отдельно.
"""

import asyncio
import logging
import re

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
        # Все ответы после последнего send_message (для поиска нужного среди watcher-шума)
        self._all_responses: list[str] = []
        # Событие (asyncio.Event) — сигнал о том, что пришёл новый ответ от бота
        self._response_received = asyncio.Event()

    async def connect(self) -> None:
        """Подключается к Telegram. При первом запуске запросит SMS-код.

        Важно: handler регистрируется ДО start(). Если сделать наоборот,
        start() запускает получение updates раньше, чем навешивается
        обработчик — и первое сообщение после connect может быть потеряно.
        Это особенно заметно при быстрых реконнектах в E2E.
        """

        # Обработчик: сохраняет текст ответа от бота.
        # Определяем функцию сначала, чтобы навесить её ДО start().
        async def _on_bot_message(event: events.NewMessage.Event) -> None:
            self._last_response = event.message.text
            self._all_responses.append(event.message.text)
            self._response_received.set()
            logger.info("Получен ответ от бота: %s", event.message.text[:100])

        # Регистрируем handler ДО start — иначе первые updates теряются
        self._client.add_event_handler(
            _on_bot_message,
            events.NewMessage(from_users=[self._bot_username]),
        )

        await self._client.start(phone=self._phone)
        logger.info("Telethon подключён к Telegram")

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

    async def wait_for_matching_response(
        self,
        match_text: str,
        timeout: int = DEFAULT_RESPONSE_TIMEOUT_SECONDS,
    ) -> str:
        """Ждёт ответ, содержащий match_text. Пропускает посторонние (watcher и др.)."""
        end_time = asyncio.get_event_loop().time() + timeout
        checked_index = 0

        while True:
            # Проверяем все накопившиеся ответы
            while checked_index < len(self._all_responses):
                response = self._all_responses[checked_index]
                checked_index += 1
                if match_text in response:
                    return response

            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                last = self._last_response or "(нет ответов)"
                raise TimeoutError(
                    f"Не получен ответ с '{match_text}' за {timeout} сек. "
                    f"Последний: {last}"
                )

            # Ждём следующее сообщение от бота
            self._response_received.clear()
            try:
                await asyncio.wait_for(
                    self._response_received.wait(), timeout=remaining
                )
            except asyncio.TimeoutError:
                last = self._last_response or "(нет ответов)"
                raise TimeoutError(
                    f"Не получен ответ с '{match_text}' за {timeout} сек. "
                    f"Последний: {last}"
                ) from None

    async def wait_for_regex_response(
        self,
        pattern: str | re.Pattern[str],
        timeout: int = DEFAULT_RESPONSE_TIMEOUT_SECONDS,
    ) -> str:
        """Ждёт ответ, который совпадает с regex. Пропускает посторонние.

        Полезно, когда нужна более точная фильтрация, чем просто подстрока —
        например, чтобы отличить ответ команды `/projects` (строки вида `/p1 name`)
        от watcher-сообщений, содержащих пути типа `src/p...`.

        Возвращает первый ответ, в котором `re.search(pattern, response)` даёт совпадение.
        """
        compiled_pattern = (
            pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        )
        end_time = asyncio.get_event_loop().time() + timeout
        checked_index = 0

        while True:
            while checked_index < len(self._all_responses):
                response = self._all_responses[checked_index]
                checked_index += 1
                if compiled_pattern.search(response):
                    return response

            remaining = end_time - asyncio.get_event_loop().time()
            if remaining <= 0:
                last = self._last_response or "(нет ответов)"
                raise TimeoutError(
                    f"Не получен ответ с паттерном '{compiled_pattern.pattern}' "
                    f"за {timeout} сек. Последний: {last}"
                )

            self._response_received.clear()
            try:
                await asyncio.wait_for(
                    self._response_received.wait(), timeout=remaining
                )
            except asyncio.TimeoutError:
                last = self._last_response or "(нет ответов)"
                raise TimeoutError(
                    f"Не получен ответ с паттерном '{compiled_pattern.pattern}' "
                    f"за {timeout} сек. Последний: {last}"
                ) from None

    def _reset_response_state(self) -> None:
        """Сбрасывает состояние ожидания перед новым сообщением."""
        self._last_response = None
        self._all_responses.clear()
        self._response_received.clear()
