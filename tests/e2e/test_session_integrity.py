"""E2E тесты: целостность сессий — защита от дублирования номеров и гонок.

Тесты проверяют два исправления от 12.04.2026:
1. Race condition при переходе temp ID -> real ID: watcher мог зарегистрировать
   одну и ту же сессию Claude под двумя разными дневными номерами (#N и #N+1).
   Исправлено через session_id_callback — мгновенное обновление всех модулей.
2. Дубликаты в реестре дневных номеров: _remove_duplicate_entries() убирает
   дубли при старте.

Тесты с [Claude] обращаются к Claude API — каждый такой шаг может занять 10-60 сек.

Используется wait_for_matching_response — ищет нужный ответ среди всех сообщений
от бота, пропуская посторонние (watcher-сообщения от терминальных сессий).
"""

import asyncio
import re

from tests.e2e.test_client import (
    TelegramTestClient,
    build_current_session_final_response_pattern,
)

# Таймаут ожидания ответа от Claude (секунды).
# Команды бота (/new, /all) отвечают мгновенно, а Claude думает 10-30 сек.
CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90

# Паттерн строки сессии в ответе /sessions: "/{число} {текст превью}"
SESSION_ENTRY_PATTERN = re.compile(r"^/(\d+)\s+(.+)", re.MULTILINE)


def _extract_session_number(response: str) -> str:
    """Извлекает дневной номер сессии (#N) из ответа бота."""
    match = re.search(r"#(\d+)", response)
    assert match, f"Не найден номер сессии (#N) в ответе: {response}"
    return match.group(1)


def _parse_session_entries(response: str) -> list[tuple[int, str]]:
    """Парсит ответ /sessions в список пар (номер, превью).

    Каждая строка формата "/{число} {текст}" превращается в кортеж (число, текст).
    Возвращает все найденные записи.
    """
    entries = []
    for match in SESSION_ENTRY_PATTERN.finditer(response):
        number = int(match.group(1))
        preview = match.group(2).strip()
        entries.append((number, preview))
    return entries


# --- FLOW-19: Нет дубликатов номеров после отправки сообщения ---


async def test_flow19_no_duplicate_session_number_after_message(
    telegram_client: TelegramTestClient,
) -> None:
    """Создать сессию, поговорить с Claude [Claude] — номер сессии НЕ дублируется.

    Баг (до исправления): при создании новой сессии бот присваивал временный ID
    (_new_XXXX). Когда Claude CLI создавал файл с реальным UUID, между появлением
    файла на диске и обновлением привязок в памяти бота был зазор. За это время
    watcher (фоновый наблюдатель, сканирует каждые 2 секунды) находил UUID, не знал
    что он связан с временным ID, и регистрировал дубликат — та же сессия получала
    второй дневной номер (#N и #N+1).

    Исправление: session_id_callback мгновенно обновляет все модули при получении
    первого события с реальным session_id, до того как watcher успеет проснуться.
    """
    # 1. Чистое состояние — переходим в мониторинг всех сессий
    await telegram_client.send_command("/all")
    await telegram_client.wait_for_matching_response("Режим мониторинга")

    # 2. Создаём новую сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)
    num_int = int(num)

    # 3. Отправляем сообщение Claude — это триггерит переход temp -> real ID [Claude]
    await telegram_client.send_message("Скажи одним словом: кактус")
    response = await telegram_client.wait_for_regex_response(
        build_current_session_final_response_pattern(num),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    # 4. Даём watcher время отработать полный цикл сканирования (интервал 2 сек)
    await asyncio.sleep(3)

    # 5. Запрашиваем список сессий
    await telegram_client.send_command("/sessions")
    sessions_response = await telegram_client.wait_for_matching_response(f"/{num} ")

    # 6. Парсим все записи сессий из ответа
    entries = _parse_session_entries(sessions_response)
    assert entries, (
        f"Не найдено ни одной записи сессии в ответе /sessions: "
        f"{sessions_response[:200]}"
    )

    # 7. КРИТИЧЕСКАЯ ПРОВЕРКА: номер N встречается ровно один раз
    numbers = [entry_number for entry_number, _ in entries]
    occurrences_of_n = numbers.count(num_int)
    assert occurrences_of_n == 1, (
        f"Номер сессии #{num} встречается {occurrences_of_n} раз(а) вместо 1! "
        f"Это признак дубликата из-за гонки temp->real ID. "
        f"Все записи: {entries}"
    )

    # 8. ВТОРИЧНАЯ ПРОВЕРКА: если есть сессия N+1, её превью должно отличаться
    # от превью сессии N (одинаковое превью = дубликат той же сессии Claude)
    next_num = num_int + 1
    previews_for_n = [preview for number, preview in entries if number == num_int]
    previews_for_next = [preview for number, preview in entries if number == next_num]

    if previews_for_n and previews_for_next:
        assert previews_for_n[0] != previews_for_next[0], (
            f"Сессии #{num} и #{next_num} имеют одинаковое превью — "
            f"это дубликат одной сессии Claude! "
            f"Превью: '{previews_for_n[0]}'"
        )

    # 9. Дополнительная проверка: сессия N+1 (если существует) — это другая сессия,
    # а не дубликат нашей. Проверяем, что N и N+1 не имеют ОДИНАКОВЫЙ превью-текст.
    # Примечание: разные сессии МОГУТ иметь одинаковый первый вопрос (например,
    # "Скажи одним словом: привет" из разных прогонов тестов) — это не баг.
    # Мы проверяем только соседнюю пару N и N+1, где совпадение превью —
    # сильный индикатор race condition (одна сессия Claude под двумя номерами).


# --- FLOW-20: Последовательные сессии имеют уникальные номера ---


async def test_flow20_sequential_sessions_have_unique_numbers(
    telegram_client: TelegramTestClient,
) -> None:
    """Три сессии подряд получают строго последовательные уникальные номера.

    Проверяет базовый инвариант дневной нумерации: каждая новая сессия
    получает номер на 1 больше предыдущей, без пропусков и дублей.
    Claude API не используется — только команды бота.
    """
    # 1. Создаём сессию A
    await telegram_client.send_command("/new")
    response_a = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num_a = int(_extract_session_number(response_a))

    # 2. Создаём сессию B
    await telegram_client.send_command("/new")
    response_b = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num_b = int(_extract_session_number(response_b))

    # 3. Создаём сессию C
    await telegram_client.send_command("/new")
    response_c = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num_c = int(_extract_session_number(response_c))

    # 4. Номера строго возрастают
    assert num_a < num_b < num_c, (
        f"Номера сессий должны строго возрастать: "
        f"A=#{num_a}, B=#{num_b}, C=#{num_c}"
    )

    # 5. Номера идут подряд без пропусков
    assert num_b == num_a + 1, (
        f"Между A=#{num_a} и B=#{num_b} есть пропуск — "
        f"ожидали B=#{num_a + 1}"
    )
    assert num_c == num_b + 1, (
        f"Между B=#{num_b} и C=#{num_c} есть пропуск — "
        f"ожидали C=#{num_b + 1}"
    )

    # 6. Все три номера различны (перестраховка — следует из возрастания)
    all_numbers = {num_a, num_b, num_c}
    assert len(all_numbers) == 3, (
        f"Обнаружены дублирующиеся номера среди A=#{num_a}, B=#{num_b}, C=#{num_c}"
    )
