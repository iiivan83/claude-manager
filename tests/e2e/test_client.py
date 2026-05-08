"""Клиент для E2E тестирования бота через Telegram.

Обёртка над библиотекой Telethon (работает с Telegram как обычный пользователь).
Позволяет отправлять сообщения боту и проверять его ответы в автоматических тестах.

Этот файл содержит только клиент — сами тесты создаются отдельно.
"""

import asyncio
import logging
import re
import sqlite3

from telethon import TelegramClient, events

# Время ожидания ответа от бота по умолчанию (секунды)
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 30

# Пауза перед попыткой переподключения после разрыва Telethon-сессии (секунды).
# SQLite-журнал успевает закрыться, Telegram не считает это flood.
RECONNECT_PAUSE_SECONDS = 2

# Максимум попыток переподключения внутри одного send_message.
# Один retry достаточно: если повторное подключение не помогло — это уже не
# транзиентный сбой, а настоящая ошибка.
RECONNECT_MAX_ATTEMPTS = 1

logger = logging.getLogger(__name__)


# Заголовок ответа из ЧУЖОЙ сессии: "<b>/N</b> 🤖 Claude ✅ ..." после рендера
# Telethon приходит как "**/N** ..." или "/N ...". Регулярка ловит оба варианта.
_FOREIGN_SESSION_HEADER_PATTERN = re.compile(r"^\**\s*/(\d+)\b")


def is_foreign_watcher_response(
    response_text: str,
    own_session_number: str | int,
) -> bool:
    """True, если сообщение — watcher-уведомление от ЧУЖОЙ сессии.

    Бот форматирует watcher-ответы из чужих сессий через `<b>/N</b>`
    (см. _format_clickable_session_header в bot.py). После рендера в Telegram
    такие сообщения начинаются с `/N` (или `**/N**` в Markdown-виде).
    Свой ответ начинается с `#N`.
    """
    match = _FOREIGN_SESSION_HEADER_PATTERN.match(response_text)
    if match is None:
        return False
    return match.group(1) != str(own_session_number)


def has_foreign_watcher_noise(
    responses: list[str],
    own_session_number: str | int,
) -> bool:
    """True, если в буфере есть хотя бы одно сообщение от чужой сессии.

    Используется тестами, чтобы решить — упало из-за регресса или среда грязная
    (одновременная активность реального пользователя и тестового аккаунта).
    """
    return any(
        is_foreign_watcher_response(response_text, own_session_number)
        for response_text in responses
    )


def is_watcher_response(response_text: str) -> bool:
    """True, если сообщение — watcher-уведомление от ЛЮБОЙ сессии (заголовок /N).

    Пригодно для тестов, у которых нет своей сессии (например, переключение
    проектов), но они хотят отличить ответ команды от фоновых уведомлений.
    """
    return _FOREIGN_SESSION_HEADER_PATTERN.match(response_text) is not None


def has_watcher_noise(responses: list[str]) -> bool:
    """True, если в буфере есть хотя бы одно watcher-уведомление."""
    return any(is_watcher_response(response_text) for response_text in responses)


def build_current_session_final_response_pattern(
    session_number: str | int,
) -> re.Pattern[str]:
    """Builds a regex for a final response from the active #N session."""
    escaped_session_number = re.escape(str(session_number))
    return re.compile(rf"(?m)^#{escaped_session_number}\b.*\u2705")


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
        # Сохраняем все параметры, чтобы _reconnect_after_failure мог пересоздать
        # TelegramClient после разрыва SQLite-сессии без повторного приёма параметров.
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name
        self._client = TelegramClient(session_name, api_id, api_hash)
        self._phone = phone
        self._bot_username = bot_username
        self._last_response: str | None = None
        self._last_response_message = None
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
        self._register_handlers()
        await self._client.start(phone=self._phone)
        logger.info("Telethon подключён к Telegram")

    def _register_handlers(self) -> None:
        """Регистрирует обработчик новых и отредактированных сообщений от бота."""
        self._client.add_event_handler(
            self._handle_bot_message,
            events.NewMessage(from_users=[self._bot_username]),
        )
        self._client.add_event_handler(
            self._handle_bot_message,
            events.MessageEdited(from_users=[self._bot_username]),
        )

    async def _handle_bot_message(self, event) -> None:
        """Сохраняет текст входящего сообщения от бота в буфер ответов."""
        self._last_response = event.message.text
        self._last_response_message = event.message
        self._all_responses.append(event.message.text)
        self._response_received.set()
        logger.info("Получен ответ от бота: %s", event.message.text[:100])

    async def disconnect(self) -> None:
        """Отключается от Telegram."""
        await self._client.disconnect()
        logger.info("Telethon отключён")

    async def _reconnect_after_failure(self) -> None:
        """Переподключает Telethon-сессию после разрыва.

        Telethon хранит state в SQLite. При длинных таймаутах SQLite иногда
        остаётся залоченным, а соединение mtproto — закрытым. Тогда любая
        следующая отправка падает каскадом. Этот метод корректно закрывает
        соединение, ждёт RECONNECT_PAUSE_SECONDS чтобы SQLite-журнал успел
        отпустить блокировку, и поднимает соединение заново.
        """
        try:
            await self._client.disconnect()
        except Exception:
            logger.warning("Не удалось корректно закрыть Telethon", exc_info=True)
        await asyncio.sleep(RECONNECT_PAUSE_SECONDS)
        # Сброс внутреннего флага, чтобы start() прошёл новый цикл подключения
        self._client = TelegramClient(
            self._session_name,
            self._api_id,
            self._api_hash,
        )
        self._register_handlers()
        await self._client.start(phone=self._phone)
        logger.info("Telethon переподключён к Telegram")

    async def _send_with_reconnect(self, action_callable, action_label: str) -> None:
        """Выполняет отправку через Telethon с одной попыткой реконнекта.

        Любую ConnectionError или sqlite OperationalError считаем транзиентной:
        переподключаемся и повторяем один раз. Это лечит каскад «database is
        locked» после длинного таймаута предыдущего теста.
        """
        attempts_left = RECONNECT_MAX_ATTEMPTS + 1
        while attempts_left > 0:
            try:
                await action_callable()
                return
            except (ConnectionError, sqlite3.OperationalError) as transient_error:
                attempts_left -= 1
                logger.warning(
                    "%s: транзиентный сбой Telethon (%s). Осталось попыток: %d",
                    action_label, transient_error, attempts_left,
                )
                if attempts_left <= 0:
                    raise
                await self._reconnect_after_failure()

    async def send_message(self, text: str) -> None:
        """Отправляет текстовое сообщение боту."""
        self._reset_response_state()

        async def _do_send() -> None:
            await self._client.send_message(self._bot_username, text)

        await self._send_with_reconnect(_do_send, "send_message")
        logger.info("Отправлено сообщение боту: %s", text[:100])

    async def send_command(self, command: str) -> None:
        """Отправляет команду боту (например '/new', '/sessions')."""
        await self.send_message(command)

    async def send_photo(self, path: str, caption: str = "") -> None:
        """Отправляет фото боту с подписью."""
        self._reset_response_state()

        async def _do_send_file() -> None:
            await self._client.send_file(
                self._bot_username,
                path,
                caption=caption,
            )

        await self._send_with_reconnect(_do_send_file, "send_photo")
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

    async def click_last_button_containing(self, text: str) -> None:
        """Нажимает последнюю inline-кнопку, чей текст содержит заданную строку."""
        message = self._last_response_message
        if message is None:
            raise AssertionError("Нет последнего сообщения с inline-кнопками")
        if not message.buttons:
            raise AssertionError("В последнем сообщении нет inline-кнопок")

        for row in message.buttons:
            for button in row:
                if text in button.text:
                    self._reset_response_state()
                    await message.click(text=button.text)
                    return

        button_texts = [
            button.text
            for row in message.buttons
            for button in row
        ]
        raise AssertionError(
            f"Кнопка с текстом {text!r} не найдена. "
            f"Доступные кнопки: {button_texts}"
        )

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
        self._last_response_message = None
        self._all_responses.clear()
        self._response_received.clear()
