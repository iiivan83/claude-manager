"""Фикстуры для E2E тестов — подключение к Telegram через Telethon."""

import asyncio
import os

import pytest
import pytest_asyncio
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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def telegram_client():
    """Подключённый Telethon-клиент для отправки сообщений боту.

    Session-scoped: единый connect/disconnect на весь прогон тестов.
    Каждый тест — новый connect/disconnect копит FLOOD_WAIT в Telegram
    и делает E2E тесты нестабильными.

    loop_scope="session" обязателен: Telethon привязывается к event loop
    на connect и падает, если его меняют между тестами. В pytest-asyncio
    1.x дефолт для async фикстур — function-scoped loop.
    """
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


@pytest.fixture(autouse=True)
def _clean_response_buffer_between_tests(telegram_client):
    """Сбрасывает накопленные ответы и состояние ожидания перед каждым тестом.

    Клиент session-scoped, поэтому буфер `_all_responses` и последний ответ
    живут между тестами. Без сброса подстрочный поиск в одном тесте может
    поймать ответ от предыдущего — источник ложных совпадений и flaky.
    """
    telegram_client._reset_response_state()
    yield
