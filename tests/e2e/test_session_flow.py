"""E2E тесты: сценарии работы с сессиями.

Тесты отправляют реальные сообщения боту через Telegram.
Требования: бот запущен, Telethon авторизован, переменные окружения настроены.

Тесты с [Claude] обращаются к Claude API — каждый такой шаг может занять 10-60 сек.

Используется wait_for_matching_response — ищет нужный ответ среди всех сообщений
от бота, пропуская посторонние (watcher-сообщения от терминальных сессий).
"""

import re

from tests.e2e.test_client import TelegramTestClient

# Таймаут ожидания ответа от Claude (секунды).
# Команды бота (/new, /all) отвечают мгновенно, а Claude думает 10-30 сек.
CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90


def _extract_session_number(response: str) -> str:
    """Извлекает дневной номер сессии (#N) из ответа бота."""
    match = re.search(r"#(\d+)", response)
    assert match, f"Не найден номер сессии (#N) в ответе: {response}"
    return match.group(1)


# --- Простой тест ---


async def test_new_session_and_back_to_all(
    telegram_client: TelegramTestClient,
) -> None:
    """Сценарий: /new создаёт сессию, /all возвращает в общий режим."""
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    assert "#" in response

    await telegram_client.send_command("/all")
    response = await telegram_client.wait_for_matching_response("Режим мониторинга")


# --- FLOW-01: Полный жизненный цикл сессии ---


async def test_flow01_full_session_lifecycle(
    telegram_client: TelegramTestClient,
) -> None:
    """Создать → поговорить [Claude] → /all → блокировка → /sessions → вернуться → поговорить [Claude]."""
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # 2. Отправляем сообщение Claude [Claude]
    await telegram_client.send_message("Скажи одним словом: привет")
    response = await telegram_client.wait_for_matching_response(
        f"#{num}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )
    # Ответ содержит номер нашей сессии — значит Claude ответил
    assert f"#{num}" in response

    # 3. Переходим в мониторинг
    await telegram_client.send_command("/all")
    await telegram_client.wait_for_matching_response("Режим мониторинга")

    # 4. Пробуем написать текст — бот блокирует (нет активной сессии)
    await telegram_client.send_message("Привет")
    response = await telegram_client.wait_for_matching_response("мониторинг")
    assert "сесси" in response.lower() or "/new" in response

    # 5. Смотрим список сессий — наша должна быть там
    await telegram_client.send_command("/sessions")
    response = await telegram_client.wait_for_matching_response(f"/{num}")

    # 6. Возвращаемся в сессию по номеру
    await telegram_client.send_command(f"/{num}")
    response = await telegram_client.wait_for_matching_response("Подключён")
    assert f"#{num}" in response

    # 7. Снова пишем Claude [Claude]
    await telegram_client.send_message("Сколько будет 2+2? Ответь одним числом")
    response = await telegram_client.wait_for_matching_response(
        f"#{num}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )
    assert "4" in response, f"Ожидали '4' в ответе: {response}"


# --- FLOW-02: Две сессии и навигация ---


async def test_flow02_two_sessions_and_navigation(
    telegram_client: TelegramTestClient,
) -> None:
    """Создать 2 сессии, запомнить разные слова, переключаться — контекст отдельный."""
    # 1. Создаём сессию A
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num_a = _extract_session_number(response)

    # 2. Просим Claude запомнить слово в сессии A [Claude]
    await telegram_client.send_message(
        "Запомни кодовое слово: яблоко. Ответь ТОЛЬКО: ок"
    )
    response = await telegram_client.wait_for_matching_response(
        f"#{num_a}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )

    # 3. Создаём сессию B
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num_b = _extract_session_number(response)
    assert int(num_b) > int(num_a), f"B ({num_b}) должен быть > A ({num_a})"

    # 4. Просим Claude запомнить слово в сессии B [Claude]
    await telegram_client.send_message(
        "Запомни кодовое слово: банан. Ответь ТОЛЬКО: ок"
    )
    response = await telegram_client.wait_for_matching_response(
        f"#{num_b}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )

    # 5. /sessions — обе сессии в списке
    await telegram_client.send_command("/sessions")
    response = await telegram_client.wait_for_matching_response(f"/{num_a}")
    assert f"/{num_b}" in response, f"Сессия B (/{num_b}) не в списке: {response}"

    # 6. Переключаемся на сессию A
    await telegram_client.send_command(f"/{num_a}")
    response = await telegram_client.wait_for_matching_response("Подключён")

    # 7. Спрашиваем кодовое слово в A [Claude] — должно быть «яблоко»
    await telegram_client.send_message(
        "Какое кодовое слово я просил тебя запомнить? Ответь одним словом"
    )
    response = await telegram_client.wait_for_matching_response(
        f"#{num_a}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )
    assert "яблоко" in response.lower(), f"Ожидали 'яблоко': {response}"

    # 8. Переключаемся на сессию B
    await telegram_client.send_command(f"/{num_b}")
    response = await telegram_client.wait_for_matching_response("Подключён")

    # 9. Спрашиваем кодовое слово в B [Claude] — должно быть «банан»
    await telegram_client.send_message(
        "Какое кодовое слово я просил тебя запомнить? Ответь одним словом"
    )
    response = await telegram_client.wait_for_matching_response(
        f"#{num_b}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )
    assert "банан" in response.lower(), f"Ожидали 'банан': {response}"


# --- FLOW-03: Ошибки и ограничения ---


async def test_flow03_errors_and_constraints(
    telegram_client: TelegramTestClient,
) -> None:
    """Все действия, которые бот должен заблокировать с подсказкой."""
    # 1. Переходим в мониторинг
    await telegram_client.send_command("/all")
    await telegram_client.wait_for_matching_response("Режим мониторинга")

    # 2. Текст в мониторинге — заблокирован
    await telegram_client.send_message("Привет")
    response = await telegram_client.wait_for_matching_response("мониторинг")
    assert "сесси" in response.lower() or "/new" in response

    # 3. /stop без активной сессии
    await telegram_client.send_command("/stop")
    response = await telegram_client.wait_for_matching_response("/stop работает")

    # 4. /99 — несуществующая сессия
    await telegram_client.send_command("/99")
    response = await telegram_client.wait_for_matching_response("не найдена")

    # 5. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")

    # 6. /stop — сессия есть, но Claude не запущен (нечего останавливать)
    await telegram_client.send_command("/stop")
    response = await telegram_client.wait_for_matching_response("не работает")


# --- FLOW-04: Формат заголовков ответов ---


async def test_flow04_response_header_format(
    telegram_client: TelegramTestClient,
) -> None:
    """Ответ текущей сессии начинается с #N ✅ (не /N — тот формат для чужих сессий)."""
    # Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # Отправляем простой вопрос [Claude]
    await telegram_client.send_message("Ответь одним словом: да")
    response = await telegram_client.wait_for_matching_response(
        f"#{num}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS
    )

    # Заголовок текущей сессии — #N (знак #, а не /)
    assert response.startswith(f"#{num}"), (
        f"Ответ должен начинаться с #{num}, получили: {response[:40]}"
    )
    # Финальный ответ содержит галочку ✅
    assert "\u2705" in response, f"Финальный ответ должен содержать ✅: {response[:40]}"
