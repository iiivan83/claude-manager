"""Базовые фикстуры для всех тестов проекта.

Каждая фикстура — готовый инструмент, который тест может запросить по имени.
Фикстуры создают фейковые объекты вместо настоящих (Telegram, Claude CLI),
чтобы тесты работали быстро и без подключения к интернету.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# E2E тесты требуют telethon и запускаются отдельно — исключаем из обычного сбора
collect_ignore = ["e2e"]

import pytest


# ID пользователя, который используется во всех тестах как "разрешённый"
DEFAULT_TEST_USER_ID = 12345
DEFAULT_TEST_CHAT_ID = 12345


@pytest.fixture()
def mock_bot() -> MagicMock:
    """Фейковый Telegram-бот, который записывает отправленные сообщения.

    Вместо реальной отправки в Telegram — сохраняет каждое сообщение в список.
    Это позволяет проверить: бот отправил правильный текст нужному пользователю.

    Использование в тесте:
        response_text = mock_bot.sent_messages[0]["text"]
        assert "Привет" in response_text
    """
    bot = MagicMock()
    bot.sent_messages = []

    async def record_sent_message(chat_id: int, text: str, **kwargs) -> MagicMock:
        """Записывает отправленное сообщение в список вместо реальной отправки."""
        message_record = {"chat_id": chat_id, "text": text, **kwargs}
        bot.sent_messages.append(message_record)
        # Возвращаем фейковый объект Message (как настоящий Telegram API)
        fake_message = MagicMock()
        fake_message.message_id = len(bot.sent_messages)
        fake_message.text = text
        fake_message.chat.id = chat_id
        return fake_message

    bot.send_message = AsyncMock(side_effect=record_sent_message)

    return bot


@pytest.fixture()
def mock_update():
    """Фабрика фейковых входящих сообщений Telegram.

    Возвращает функцию-генератор. Каждый вызов создаёт новый объект Update
    с нужными параметрами (текст, ID чата, ID пользователя).

    Использование в тесте:
        update = mock_update(text="/start", user_id=99999)
        assert update.message.text == "/start"
    """

    def create_update(
        text: str = "test message",
        chat_id: int = DEFAULT_TEST_CHAT_ID,
        user_id: int = DEFAULT_TEST_USER_ID,
    ) -> MagicMock:
        """Создаёт фейковый объект Update с заданными параметрами."""
        update = MagicMock()

        # Текст сообщения
        update.message.text = text

        # ID чата (доступен несколькими способами — как в настоящем Telegram API)
        update.message.chat.id = chat_id
        update.message.chat_id = chat_id
        update.effective_chat.id = chat_id

        # ID отправителя
        update.message.from_user.id = user_id
        update.effective_user.id = user_id

        # Метод для ответа на сообщение (async, как в настоящем боте)
        update.message.reply_text = AsyncMock()

        return update

    return create_update


@pytest.fixture()
def mock_claude_process():
    """Фабрика фейковых процессов Claude CLI.

    Возвращает функцию-генератор. Каждый вызов создаёт фейковый subprocess,
    который принимает сообщения через stdin и отдаёт заготовленные ответы через stdout.

    Использование в тесте:
        process = mock_claude_process(responses=[
            {"type": "assistant", "message": {"content": [{"text": "Привет!"}]}}
        ])
        line = await process.stdout.readline()  # Получить первый ответ
    """

    def create_process(responses: list[dict] | None = None) -> MagicMock:
        """Создаёт фейковый процесс с заранее заданными ответами."""
        process = MagicMock()

        # Готовим список JSON-строк, которые процесс "отдаст"
        encoded_responses: list[bytes] = []
        for response_dict in (responses or []):
            json_line = json.dumps(response_dict) + "\n"
            encoded_responses.append(json_line.encode("utf-8"))

        # Пустая строка — сигнал конца потока (процесс завершился)
        encoded_responses.append(b"")

        # stdout.readline() — отдаёт строки по одной
        process.stdout.readline = AsyncMock(side_effect=encoded_responses)

        # stdin — принимает данные (write + drain)
        process.stdin.write = MagicMock()
        process.stdin.drain = AsyncMock()
        process.stdin.close = MagicMock()

        # stderr — пустой
        process.stderr.readline = AsyncMock(return_value=b"")

        # Завершение процесса
        process.wait = AsyncMock(return_value=0)
        process.returncode = 0

        return process

    return create_process


@pytest.fixture()
def session_dir(tmp_path: Path) -> Path:
    """Временная папка, имитирующая директорию сессий Claude.

    Создаётся автоматически перед тестом и удаляется после.
    Внутри можно создавать файлы сессий для тестирования.

    Использование в тесте:
        session_file = session_dir / "session_abc123.json"
        session_file.write_text('{"id": "abc123"}')
    """
    sessions_path = tmp_path / "claude_sessions"
    sessions_path.mkdir()
    return sessions_path


@pytest.fixture()
def allowed_user_id() -> int:
    """ID пользователя из белого списка (проходит проверку авторизации).

    Использование в тесте:
        update = mock_update(user_id=allowed_user_id)
        # Этот пользователь будет считаться разрешённым
    """
    return DEFAULT_TEST_USER_ID
