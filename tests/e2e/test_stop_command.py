"""E2E тесты: команда /stop и восстановление после остановки.

Проверяют сценарии, связанные с остановкой Claude во время обработки:
- /stop прерывает активную обработку (фикс каскада из 3 дефектов, коммит 7719471)
- Сессия остаётся рабочей после /stop — можно отправить новое сообщение
- Двойной /stop — второй получает «нечего останавливать»

Контекст фикса (коммит 7719471):
  DEV-1: stop_event удалялся в stop_process() раньше, чем retry loop
         успевал его проверить → retry loop не видел отмену и перезапускал Claude.
  DEV-2: _restart_process() не создавал _busy_flags и _stop_events → повторный
         /stop не мог найти stop_event для перезапущенного процесса.
  DEV-3: handle_stop() проверял только has_process(), но не is_busy() →
         /stop во время retry wait (между рестартами) говорил «нечего останавливать».

Тесты с [Claude] обращаются к Claude API — каждый такой шаг может занять 10-60 сек.

Используется wait_for_matching_response — ищет нужный ответ среди всех сообщений
от бота, пропуская посторонние (watcher-сообщения от терминальных сессий).
"""

import asyncio
import re

from tests.e2e.test_client import TelegramTestClient

# Таймаут ожидания ответа от Claude (секунды).
# Команды бота (/new, /stop) отвечают мгновенно, а Claude думает 10-30 сек.
CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90

# Таймаут для быстрых команд бота (не требуют Claude).
BOT_COMMAND_TIMEOUT_SECONDS = 15

# Пауза после отправки сообщения Claude, чтобы процесс успел запуститься.
# За 3 секунды subprocess стартует, busy_flag установлен, процесс в _processes.
PROCESS_STARTUP_SECONDS = 3

# Пауза после /stop, чтобы finally-блок send_message() успел отработать.
# finally очищает busy_flag и stop_event — без этой паузы следующая
# операция может увидеть «занято».
STOP_CLEANUP_SECONDS = 3


def _extract_session_number(response: str) -> str:
    """Извлекает дневной номер сессии (#N) из ответа бота."""
    match = re.search(r"#(\d+)", response)
    assert match, f"Не найден номер сессии (#N) в ответе: {response}"
    return match.group(1)


# --- FLOW-21: /stop прерывает активную обработку Claude ---


async def test_flow21_stop_interrupts_active_processing(
    telegram_client: TelegramTestClient,
) -> None:
    """/stop во время обработки запроса Claude → «Claude остановлен» [Claude].

    Баг (до исправления): команда /stop не прерывала retry loop.
    После /stop бот перезапускал процесс Claude с тем же сообщением.

    Тест: отправляем сложный запрос → /stop пока Claude обрабатывает →
    получаем «Claude остановлен».
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    _extract_session_number(response)

    # 2. Отправляем запрос, который требует времени на обработку [Claude].
    # Claude CLI стартует процесс, читает файл, формирует ответ — это 10-30 сек.
    await telegram_client.send_message(
        "Прочитай файл src/claude_manager/bot.py и посчитай количество функций. "
        "Выведи число."
    )

    # 3. Ждём, пока Claude запустится и начнёт обработку.
    # За это время busy_flag установлен, процесс в _processes.
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)

    # 4. Отправляем /stop — должен прервать обработку
    await telegram_client.send_command("/stop")

    # 5. Ожидаем подтверждение «Claude остановлен»
    response = await telegram_client.wait_for_matching_response(
        "остановлен", timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )
    assert "остановлен" in response.lower(), (
        f"Ожидали 'Claude остановлен', получили: {response}"
    )


# --- FLOW-22: Сессия работает после /stop ---


async def test_flow22_session_usable_after_stop(
    telegram_client: TelegramTestClient,
) -> None:
    """После /stop можно отправить новое сообщение и получить ответ [Claude].

    Проверяет что /stop не оставляет сессию в сломанном состоянии:
    busy_flag сброшен (в finally send_message), stop_event очищен,
    новый процесс Claude может быть создан для следующего сообщения.
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # 2. Отправляем запрос Claude [Claude]
    await telegram_client.send_message(
        "Прочитай файл src/claude_manager/config.py и перечисли все переменные окружения"
    )

    # 3. Ждём, пока Claude начнёт обработку
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)

    # 4. Останавливаем
    await telegram_client.send_command("/stop")
    await telegram_client.wait_for_matching_response(
        "остановлен", timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )

    # 5. Даём время на полную очистку состояния.
    # finally-блок send_message() очищает busy_flag и удаляет stop_event.
    await asyncio.sleep(STOP_CLEANUP_SECONDS)

    # 6. Отправляем НОВОЕ сообщение — сессия должна быть свободна [Claude]
    await telegram_client.send_message("Скажи одним словом: привет")
    response = await telegram_client.wait_for_matching_response(
        f"#{num}", timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    # 7. Claude ответил в нашей сессии — значит сессия работает после /stop
    assert f"#{num}" in response, (
        f"Ответ должен содержать номер сессии #{num}: {response[:120]}"
    )


# --- FLOW-23: Двойной /stop ---


async def test_flow23_double_stop_second_says_nothing_to_stop(
    telegram_client: TelegramTestClient,
) -> None:
    """Первый /stop останавливает Claude, второй — «нечего останавливать» [Claude].

    Проверяет корректную очистку состояния после stop_process():
    процесс удалён из _processes, busy_flag сброшен (в finally send_message),
    повторный /stop видит отсутствие процесса и busy=False.
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    _extract_session_number(response)

    # 2. Запускаем обработку [Claude]
    await telegram_client.send_message(
        "Прочитай файл src/claude_manager/bot.py и перечисли все функции"
    )

    # 3. Claude начал обработку
    await asyncio.sleep(PROCESS_STARTUP_SECONDS)

    # 4. Первый /stop — останавливает
    await telegram_client.send_command("/stop")
    await telegram_client.wait_for_matching_response(
        "остановлен", timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )

    # 5. Ждём полную очистку (finally-блок очищает busy_flag + stop_event)
    await asyncio.sleep(STOP_CLEANUP_SECONDS)

    # 6. Второй /stop — уже нечего останавливать
    await telegram_client.send_command("/stop")
    response = await telegram_client.wait_for_matching_response(
        "не работает", timeout=BOT_COMMAND_TIMEOUT_SECONDS,
    )
    assert "не работает" in response.lower(), (
        f"Второй /stop должен сообщить что Claude не работает: {response}"
    )
