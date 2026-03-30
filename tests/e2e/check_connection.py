"""Скрипт проверки подключения Telethon к Telegram.

Работает в двух режимах:
  1) Без аргументов — отправляет запрос SMS-кода
  2) С кодом — завершает авторизацию и тестирует бота

Использование:
    python tests/e2e/check_connection.py            # шаг 1: запросить SMS
    python tests/e2e/check_connection.py 123456      # шаг 2: ввести код
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient, events

# Время ожидания ответа от бота (секунды)
RESPONSE_TIMEOUT_SECONDS = 30


def _get_config() -> tuple[int, str, str, str, str]:
    """Загружает конфигурацию из .env и возвращает параметры подключения."""
    load_dotenv()
    api_id = int(os.environ["TELETHON_API_ID"])
    api_hash = os.environ["TELETHON_API_HASH"]
    phone = os.environ["TELETHON_PHONE"]
    bot_username = os.environ["TELETHON_BOT_USERNAME"]
    session_path = os.path.join(os.path.dirname(__file__), "telethon_test")
    return api_id, api_hash, phone, bot_username, session_path


async def request_code() -> None:
    """Шаг 1: подключается к Telegram и запрашивает SMS-код."""
    api_id, api_hash, phone, _, session_path = _get_config()
    client = TelegramClient(session_path, api_id, api_hash)

    await client.connect()

    if await client.is_user_authorized():
        print("Уже авторизован! Сессия сохранена. Запусти с аргументом 'test' для проверки бота.")
        await client.disconnect()
        return

    result = await client.send_code_request(phone)
    print(f"SMS-код отправлен на {phone}")
    print(f"phone_code_hash: {result.phone_code_hash}")

    # Сохраняем hash для второго шага
    hash_file = os.path.join(os.path.dirname(__file__), ".phone_code_hash")
    with open(hash_file, "w") as f:
        f.write(result.phone_code_hash)

    print("\nТеперь запусти скрипт ещё раз с кодом из SMS:")
    print(f"  python tests/e2e/check_connection.py XXXXXX")
    await client.disconnect()


async def sign_in_and_test(code: str) -> None:
    """Шаг 2: авторизуется с SMS-кодом и тестирует отправку /new боту."""
    api_id, api_hash, phone, bot_username, session_path = _get_config()

    # Читаем сохранённый hash
    hash_file = os.path.join(os.path.dirname(__file__), ".phone_code_hash")
    if not os.path.exists(hash_file):
        print("Ошибка: сначала запусти без аргументов, чтобы запросить SMS-код")
        sys.exit(1)

    with open(hash_file) as f:
        phone_code_hash = f.read().strip()

    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    # Авторизация с кодом
    print(f"1. Авторизуюсь с кодом {code}...")
    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
    except Exception as error:
        print(f"Ошибка авторизации: {error}")
        await client.disconnect()
        sys.exit(1)

    print("   Авторизация успешна!")

    # Удаляем временный файл с hash
    os.remove(hash_file)

    # Тестируем бота
    await _test_bot(client, bot_username)


async def just_test() -> None:
    """Тестирует бота если уже авторизован."""
    api_id, api_hash, _, bot_username, session_path = _get_config()
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print("Не авторизован. Запусти без аргументов для запроса SMS-кода.")
        await client.disconnect()
        sys.exit(1)

    print("Уже авторизован, тестирую бота...")
    await _test_bot(client, bot_username)


async def _test_bot(client: TelegramClient, bot_username: str) -> None:
    """Отправляет /new боту и ждёт ответ."""
    response_event = asyncio.Event()
    bot_response = {"text": None}

    @client.on(events.NewMessage(from_users=[bot_username]))
    async def on_message(event: events.NewMessage.Event) -> None:
        """Сохраняет ответ бота."""
        bot_response["text"] = event.message.text
        print(f"  Бот ответил: {event.message.text}")
        response_event.set()

    print(f"2. Отправляю /new боту {bot_username}...")
    response_event.clear()
    await client.send_message(bot_username, "/new")

    print(f"3. Жду ответ (до {RESPONSE_TIMEOUT_SECONDS} сек)...")
    try:
        await asyncio.wait_for(response_event.wait(), timeout=RESPONSE_TIMEOUT_SECONDS)
        print(f"\nУспех! Бот работает. Ответ: {bot_response['text']}")
    except asyncio.TimeoutError:
        print(f"\nТаймаут: бот не ответил за {RESPONSE_TIMEOUT_SECONDS} секунд")
        sys.exit(1)
    finally:
        await client.disconnect()
        print("Отключено от Telegram.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "test":
            asyncio.run(just_test())
        else:
            asyncio.run(sign_in_and_test(arg))
    else:
        asyncio.run(request_code())
