"""Авторизация Telethon — отправляет запрос на SMS-код.

Первый запуск (без аргументов): отправляет запрос на код.
Второй запуск (с кодом): завершает авторизацию.

Использование:
  python auth_telethon.py           # Шаг 1: отправить запрос на код
  python auth_telethon.py 12345     # Шаг 2: ввести полученный код
"""

import asyncio
import os
import sys

sys.path.insert(0, "/Users/ivan/Desktop/claude-sandbox/claude_manager")
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv("/Users/ivan/Desktop/claude-sandbox/claude_manager/.env")

SESSION_PATH = "/Users/ivan/Desktop/claude-sandbox/claude_manager/telethon_test"
API_ID = int(os.getenv("TELETHON_API_ID"))
API_HASH = os.getenv("TELETHON_API_HASH")
PHONE = os.getenv("TELETHON_PHONE")


async def request_code() -> None:
    """Шаг 1: подключиться и запросить SMS-код."""
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        print("ALREADY_AUTHORIZED")
        await client.disconnect()
        return

    result = await client.send_code_request(PHONE)
    print(f"CODE_SENT:{result.phone_code_hash}")
    await client.disconnect()


async def sign_in(code: str, phone_code_hash: str) -> None:
    """Шаг 2: ввести код и hash для завершения авторизации."""
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        print("ALREADY_AUTHORIZED")
        await client.disconnect()
        return

    await client.sign_in(PHONE, code, phone_code_hash=phone_code_hash)
    print("SIGN_IN_SUCCESS")
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        asyncio.run(sign_in(sys.argv[1], sys.argv[2]))
    else:
        asyncio.run(request_code())
