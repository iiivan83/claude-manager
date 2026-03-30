"""Фикстуры для E2E тестов — подключение к Telegram через Telethon."""

import asyncio
import os

import pytest
from dotenv import load_dotenv

from tests.e2e.test_client import TelegramTestClient

# Максимальное время на подключение к Telegram (секунды)
CONNECT_TIMEOUT_SECONDS = 10

# Переменные окружения, необходимые для работы Telethon
REQUIRED_ENV_VARS = [
    "TELETHON_API_ID",
    "TELETHON_API_HASH",
    "TELETHON_PHONE",
    "TELETHON_BOT_USERNAME",
]


@pytest.fixture
async def telegram_client():
    """Подключённый Telethon-клиент для отправки сообщений боту."""
    load_dotenv()

    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        pytest.skip(f"Не заданы переменные: {', '.join(missing)}")

    session_path = os.path.join(os.path.dirname(__file__), "telethon_test")
    if not os.path.exists(session_path + ".session"):
        pytest.skip("Нет файла сессии Telethon — запусти check_connection.py")

    client = TelegramTestClient(
        api_id=int(os.environ["TELETHON_API_ID"]),
        api_hash=os.environ["TELETHON_API_HASH"],
        phone=os.environ["TELETHON_PHONE"],
        bot_username=os.environ["TELETHON_BOT_USERNAME"],
        session_name=session_path,
    )

    try:
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, Exception) as error:
        pytest.skip(f"Не удалось подключиться к Telegram: {error}")

    yield client
    await client.disconnect()
