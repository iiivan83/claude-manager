"""Скрипт для запуска E2E тестов Claude Manager бота.

Подключается к Telegram через Telethon, отправляет сообщения боту
и проверяет ответы по сценариям из BRD.
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/Users/ivan/Desktop/claude-sandbox/claude_manager")

from dotenv import load_dotenv
from tests.e2e.test_client import TelegramTestClient

load_dotenv("/Users/ivan/Desktop/claude-sandbox/claude_manager/.env")

# Таймаут ожидания ответа от бота (секунды)
RESPONSE_TIMEOUT_SECONDS = 90

# Результаты тестов
results = []


def record_result(
    scenario_id: str,
    name: str,
    verdict: str,
    bot_response: str,
    reason: str,
    elapsed: float,
    error: str = "",
) -> None:
    """Записывает результат сценария."""
    results.append({
        "id": scenario_id,
        "name": name,
        "verdict": verdict,
        "bot_response": bot_response,
        "reason": reason,
        "elapsed": round(elapsed, 1),
        "error": error,
    })


async def run_all_tests() -> None:
    """Запускает все E2E тест-сценарии последовательно."""
    client = TelegramTestClient(
        api_id=int(os.getenv("TELETHON_API_ID")),
        api_hash=os.getenv("TELETHON_API_HASH"),
        phone=os.getenv("TELETHON_PHONE"),
        bot_username=os.getenv("TELETHON_BOT_USERNAME"),
        session_name="/Users/ivan/Desktop/claude-sandbox/claude_manager/telethon_test",
    )

    print("Connecting to Telegram...")
    await client.connect()
    print("Connected!")

    # Небольшая пауза после подключения
    await asyncio.sleep(2)

    # === CJM-04-01: Создание сессии через /new ===
    print("\n--- CJM-04-01: Создание сессии через /new ---")
    start = time.time()
    try:
        await client.send_command("/new")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-04-01",
            "Создание сессии через /new",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-04-01",
            "Создание сессии через /new",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    # Пауза между тестами
    await asyncio.sleep(3)

    # === CJM-02-01: Обычное текстовое сообщение ===
    print("\n--- CJM-02-01: Текстовое сообщение ---")
    start = time.time()
    try:
        await client.send_message(
            "Ответь одним словом: какой язык программирования используется в этом проекте?"
        )
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-02-01",
            "Текстовое сообщение с ответом",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-02-01",
            "Текстовое сообщение с ответом",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-05-01: Список сессий ===
    print("\n--- CJM-05-01: Список сессий ---")
    start = time.time()
    try:
        await client.send_command("/sessions")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-05-01",
            "Список сессий после создания",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-05-01",
            "Список сессий после создания",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-06-01: Переключение на существующую сессию ===
    print("\n--- CJM-06-01: Переключение на сессию /1 ---")
    start = time.time()
    try:
        await client.send_command("/1")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-06-01",
            "Переключение на существующую сессию",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-06-01",
            "Переключение на существующую сессию",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-06-02: Переключение на несуществующую сессию ===
    print("\n--- CJM-06-02: Переключение на сессию /99 ---")
    start = time.time()
    try:
        await client.send_command("/99")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-06-02",
            "Переключение на несуществующую сессию",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-06-02",
            "Переключение на несуществующую сессию",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-07-01: Режим мониторинга /all ===
    print("\n--- CJM-07-01: Режим мониторинга ---")
    start = time.time()
    try:
        await client.send_command("/all")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-07-01",
            "Включение режима мониторинга",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-07-01",
            "Включение режима мониторинга",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-02-02: Сообщение без активной сессии (после /all) ===
    print("\n--- CJM-02-02: Сообщение без сессии ---")
    start = time.time()
    try:
        await client.send_message("привет")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-02-02",
            "Сообщение без активной сессии",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-02-02",
            "Сообщение без активной сессии",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-08-02: /stop без активной сессии ===
    print("\n--- CJM-08-02: /stop без сессии ---")
    start = time.time()
    try:
        await client.send_command("/stop")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-08-02",
            "/stop без активной сессии",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-08-02",
            "/stop без активной сессии",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-08-01: /stop в сессии ===
    print("\n--- CJM-08-01: /stop в сессии ---")
    start = time.time()
    try:
        # Сначала подключимся к сессии
        await client.send_command("/new")
        await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        await asyncio.sleep(2)

        await client.send_command("/stop")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-08-01",
            "/stop в сессии",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-08-01",
            "/stop в сессии",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === CJM-04-02: Создание второй сессии ===
    print("\n--- CJM-04-02: Вторая сессия через /new ---")
    start = time.time()
    try:
        await client.send_command("/new")
        response = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        print(f"Response: {response}")
        record_result(
            "CJM-04-02",
            "Создание второй сессии через /new",
            "PENDING",
            response,
            "",
            elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result(
            "CJM-04-02",
            "Создание второй сессии через /new",
            "FAIL",
            "",
            "",
            elapsed,
            str(exc),
        )

    await asyncio.sleep(3)

    # === FIX-01: Промежуточные thinking-сообщения ===
    print("\n--- FIX-01: Thinking-сообщения (⏳ перед ✅) ---")
    start = time.time()
    try:
        # Создаём чистую сессию для теста
        await client.send_command("/new")
        new_resp = await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        await asyncio.sleep(2)

        # Отправляем запрос, который заставит Claude «подумать»
        await client.send_message(
            "Прочитай файл src/claude_manager/config.py и перечисли все переменные окружения"
        )

        # Ждём финальный ответ (до 90 секунд)
        final_resp = await client.wait_for_matching_response(
            "\u2705", timeout=RESPONSE_TIMEOUT_SECONDS
        )
        elapsed = time.time() - start

        # Проверяем: среди ответов есть хотя бы одно промежуточное с ⏳
        thinking_found = any(
            "\u23f3" in r for r in client._all_responses
        )
        verdict = "PASS" if thinking_found else "FAIL"
        reason = (
            f"Промежуточных ⏳: {sum(1 for r in client._all_responses if chr(0x23f3) in r)}, "
            f"всего ответов: {len(client._all_responses)}"
        )
        print(f"Response: {final_resp[:100]}")
        print(f"Thinking found: {thinking_found}")
        record_result("FIX-01", "Thinking-сообщения", verdict, final_resp[:200], reason, elapsed)
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result("FIX-01", "Thinking-сообщения", "FAIL", "", "", elapsed, str(exc))

    await asyncio.sleep(3)

    # === FIX-02: Занятая сессия — понятная ошибка ===
    print("\n--- FIX-02: Занятая сессия (ещё обрабатывает) ---")
    start = time.time()
    try:
        await client.send_command("/new")
        await client.wait_for_response(timeout=RESPONSE_TIMEOUT_SECONDS)
        await asyncio.sleep(2)

        # Отправляем долгий запрос
        await client.send_message(
            "Прочитай файл src/claude_manager/bot.py и посчитай количество функций"
        )
        await asyncio.sleep(1)

        # Сразу шлём второе — Claude ещё думает
        await client.send_message("А это второй запрос")

        # Ждём сообщение «обрабатывает»
        busy_resp = await client.wait_for_matching_response(
            "обрабатывает", timeout=RESPONSE_TIMEOUT_SECONDS
        )
        elapsed = time.time() - start

        verdict = "PASS" if "обрабатыва" in busy_resp.lower() else "FAIL"
        print(f"Response: {busy_resp}")
        record_result(
            "FIX-02", "Занятая сессия", verdict, busy_resp[:200],
            "Бот сообщил что занят", elapsed,
        )

        # Дожидаемся финального ответа от первого запроса
        await client.wait_for_matching_response(
            "\u2705", timeout=RESPONSE_TIMEOUT_SECONDS
        )
    except Exception as exc:
        elapsed = time.time() - start
        print(f"ERROR: {exc}")
        record_result("FIX-02", "Занятая сессия", "FAIL", "", "", elapsed, str(exc))

    # Отключаемся
    await client.disconnect()

    # Выводим результаты в JSON
    print("\n\n=== RESULTS JSON ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("=== END RESULTS ===")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
