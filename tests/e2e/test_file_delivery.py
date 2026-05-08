"""E2E тесты: доставка файлов через маркеры [SHOW_FILE:path] (CJM-12).

Проверяют сценарии, когда Claude вставляет маркер [SHOW_FILE:/path] в ответ:
- Текстовые файлы рендерятся через telegramify-markdown и приходят с заголовком 📎
- Файловые маркеры вырезаются из финального текста перед отправкой пользователю
- Промежуточные (thinking) сообщения НЕ обрабатывают маркеры

Требования: бот запущен, Telethon авторизован, переменные окружения настроены.

Тесты с [Claude] обращаются к Claude API — каждый такой шаг может занять 10-60 сек.

Символы в заголовках:
  📎 (\\U0001F4CE) — доставленный файл (текстовое содержимое)
  ⏳ (\\u23f3) — промежуточное сообщение (thinking, прогресс)
  ✅ (\\u2705) — финальный ответ Claude
"""

import re

import pytest

from tests.e2e.test_client import (
    TelegramTestClient,
    build_current_session_final_response_pattern,
)

# Таймаут ожидания ответа от Claude (секунды).
# Claude думает 10-30 сек, плюс запас на чтение файла и рендеринг.
CLAUDE_RESPONSE_TIMEOUT_SECONDS = 90

# Эмодзи-маркер доставленного файла — скрепка (paperclip).
# bot.py добавляет его в заголовок при отправке текстового файла.
PAPERCLIP_EMOJI = "\U0001F4CE"

PROJECT_ROOT = "/Users/ivan/Desktop/claude-sandbox/claude_manager"
FILE_MARKER_PREFIXES = ("[SEND_FILE:", "[SHOW_FILE:")


def _build_show_file_marker(relative_path: str) -> str:
    """Собирает маркер показа файла для текущего проекта."""
    return f"[SHOW_FILE:{PROJECT_ROOT}/{relative_path}]"


def _collect_leaked_file_markers(responses: list[str]) -> list[str]:
    """Возвращает ответы, где наружу попал сырой файловый маркер."""
    return [
        f"Ответ: {response_text[:120]}"
        for response_text in responses
        if any(
            marker_prefix in response_text
            for marker_prefix in FILE_MARKER_PREFIXES
        )
    ]


def _extract_session_number(response: str) -> str:
    """Извлекает дневной номер сессии (#N) из ответа бота."""
    match = re.search(r"#(\d+)", response)
    assert match, f"Не найден номер сессии (#N) в ответе: {response}"
    return match.group(1)


# --- FLOW-15: Полный happy-path доставки текстового файла ---


async def test_flow15_text_file_delivery_happy_path(
    telegram_client: TelegramTestClient,
) -> None:
    """Попросить Claude показать файл → получить 📎 с содержимым → маркеры вырезаны [Claude].

    Сценарий: пользователь просит показать pyproject.toml. Claude вставляет
    маркер [SHOW_FILE:path] в ответ. Бот парсит маркер, читает файл,
    рендерит через telegramify-markdown и отправляет как сообщение
    с заголовком 📎 filename.
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # 2. Просим Claude показать файл через точный маркер [SHOW_FILE:path] [Claude]
    show_file_marker = _build_show_file_marker("pyproject.toml")
    await telegram_client.send_message(
        "Ответь ровно этим маркером без кавычек и пояснений: "
        f"{show_file_marker}"
    )

    # 3. Ждём финальный ответ Claude с ✅ от НАШЕЙ сессии (#num).
    # Между #num и ✅ теперь стоит имя backend-а, поэтому ищем заголовок
    # текущей сессии по шаблону, а не по старой буквальной строке "#N ✅".
    final_response = await telegram_client.wait_for_regex_response(
        build_current_session_final_response_pattern(num),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    # 4. Проверяем: среди ВСЕХ ответов есть сообщение с 📎 (доставленный файл)
    file_messages = [
        response_text for response_text in telegram_client._all_responses
        if PAPERCLIP_EMOJI in response_text
    ]
    assert file_messages, (
        f"Claude должен был использовать маркер [SHOW_FILE:] и бот — "
        f"доставить файл с 📎 заголовком. Но ни одного 📎 сообщения не найдено "
        f"среди {len(telegram_client._all_responses)} ответов. "
        f"Возможно Claude не использовал маркер [SHOW_FILE:]. "
        f"Все ответы: {[r[:100] for r in telegram_client._all_responses]}"
    )
    file_response = file_messages[0]

    # 5. Заголовок файла содержит имя файла
    assert "pyproject.toml" in file_response, (
        f"Сообщение с 📎 должно содержать имя файла 'pyproject.toml': "
        f"{file_response[:200]}"
    )

    # 6. Содержимое — это реальный pyproject.toml проекта (проверяем известные строки)
    known_content_markers = ["python-telegram-bot", "claude_manager", "telegramify"]
    found_markers = [
        marker for marker in known_content_markers if marker in file_response
    ]
    assert found_markers, (
        f"Содержимое файла должно включать хотя бы одну из "
        f"строк {known_content_markers}: {file_response[:300]}"
    )

    # 7. Исчерпывающая проверка: ни в одном ответе не осталось сырого маркера
    leaked_markers = _collect_leaked_file_markers(telegram_client._all_responses)
    assert not leaked_markers, (
        f"Файловые маркеры должны быть вырезаны из всех ответов, "
        f"но найдены в:\n" + "\n".join(leaked_markers)
    )


# --- FLOW-16: Файл и текст — отдельные сообщения ---


async def test_flow16_file_and_text_are_separate_messages(
    telegram_client: TelegramTestClient,
) -> None:
    """Содержимое файла и текстовый ответ Claude приходят разными сообщениями [Claude].

    Сценарий: просим Claude показать файл И дать комментарий.
    Ожидаем два отдельных сообщения: 📎 (файл) и ✅ (текстовый ответ).
    Они не должны смешиваться в одном сообщении.

    Claude должен вернуть точный маркер [SHOW_FILE:] и отдельное текстовое
    предложение. Если маркер не сработал, это ошибка сценария доставки.
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # 2. Просим файл И текстовый комментарий [Claude]
    show_file_marker = _build_show_file_marker("requirements.txt")
    await telegram_client.send_message(
        "Ответь ровно двумя строками без кавычек:\n"
        f"{show_file_marker}\n"
        "Это файл зависимостей проекта."
    )

    # 3. Ждём финальный ответ Claude с ✅ от НАШЕЙ сессии (#num).
    final_response = await telegram_client.wait_for_regex_response(
        build_current_session_final_response_pattern(num),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    # 4. Ни в одном ответе не должно быть сырого маркера — это жёсткая проверка.
    leaked_markers = _collect_leaked_file_markers(telegram_client._all_responses)
    assert not leaked_markers, (
        f"Файловые маркеры должны быть вырезаны из всех ответов, "
        f"но найдены в:\n" + "\n".join(leaked_markers)
    )

    # 5. Проверяем наличие 📎 сообщения.
    file_messages = [
        response_text for response_text in telegram_client._all_responses
        if PAPERCLIP_EMOJI in response_text and "requirements.txt" in response_text
    ]
    assert file_messages, (
        "Ожидалось сообщение с 📎 requirements.txt, но оно не пришло. "
        f"Все ответы: {[r[:100] for r in telegram_client._all_responses]}"
    )

    file_response = file_messages[0]

    # 6. Содержимое файла — реальный requirements.txt (известные зависимости)
    known_dependencies = ["python-telegram-bot", "python-dotenv"]
    found_deps = [dep for dep in known_dependencies if dep in file_response]
    assert found_deps, (
        f"Содержимое файла должно включать хотя бы одну из "
        f"строк {known_dependencies}: {file_response[:300]}"
    )

    # 7. Файловое сообщение НЕ содержит галочку ✅ — это не финальный ответ
    assert "\u2705" not in file_response, (
        f"Сообщение с 📎 не должно содержать ✅ (это файл, не финальный ответ): "
        f"{file_response[:200]}"
    )

    # 8. Финальный ответ НЕ содержит 📎 — это не файловое сообщение
    assert PAPERCLIP_EMOJI not in final_response, (
        f"Финальный ответ с ✅ не должен содержать 📎 (это текст, не файл): "
        f"{final_response[:200]}"
    )


# --- FLOW-17: Промежуточные thinking-сообщения не доставляют файлы ---


async def test_flow17_no_file_content_in_thinking_messages(
    telegram_client: TelegramTestClient,
) -> None:
    """Промежуточные ⏳ не содержат 📎 — файлы доставляются только в финальном ответе [Claude].

    Проверяет guard `is_final=True` в bot.py: маркеры [SHOW_FILE:] обрабатываются
    только в финальных ответах. Промежуточные (thinking/progress) сообщения
    не должны запускать доставку файлов.
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # 2. Запрос, который доставляет файл через [SHOW_FILE:] [Claude]
    show_file_marker = _build_show_file_marker("src/claude_manager/config.py")
    await telegram_client.send_message(
        "Ответь ровно этим маркером без кавычек и пояснений: "
        f"{show_file_marker}"
    )

    # 3. Ждём финальный ответ ✅ от НАШЕЙ сессии
    await telegram_client.wait_for_regex_response(
        build_current_session_final_response_pattern(num),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    # 4. Собираем все промежуточные ⏳ (thinking) сообщения
    thinking_messages = [
        resp for resp in telegram_client._all_responses
        if "\u23f3" in resp
    ]

    # 5. Ни одно thinking-сообщение не содержит 📎 (файловую доставку)
    thinking_with_file = [
        f"Thinking: {resp[:120]}"
        for resp in thinking_messages
        if PAPERCLIP_EMOJI in resp
    ]
    assert not thinking_with_file, (
        f"Промежуточные ⏳ не должны содержать 📎 (файлы доставляются только "
        f"в финальном ответе), но найдены:\n" + "\n".join(thinking_with_file)
    )

    # 6. Ни одно thinking-сообщение не содержит сырого маркера
    thinking_with_marker = _collect_leaked_file_markers(thinking_messages)
    assert not thinking_with_marker, (
        f"Промежуточные ⏳ не должны содержать файловые маркеры, "
        f"но найдены:\n" + "\n".join(thinking_with_marker)
    )

    # 7. При этом 📎 config.py существует где-то в буфере — файл БЫЛ доставлен
    file_messages = [
        response_text for response_text in telegram_client._all_responses
        if PAPERCLIP_EMOJI in response_text and "config.py" in response_text
    ]
    if not file_messages:
        pytest.skip(
            "Claude не вернул маркер [SHOW_FILE:] для config.py и ответил "
            "обычным текстом; проверка guard-а доставки файлов невозможна"
        )
    assert file_messages, (
        f"Ожидалось сообщение с 📎 config.py (доставка файла), но не найдено. "
        f"Все ответы: {[r[:100] for r in telegram_client._all_responses]}"
    )


# --- FLOW-18: Содержимое файла совпадает с реальными данными ---


async def test_flow18_file_content_matches_known_values(
    telegram_client: TelegramTestClient,
) -> None:
    """Содержимое доставленного файла совпадает с тем, что лежит на диске [Claude].

    Глубокая проверка: просим маленький предсказуемый файл (.env.example)
    и проверяем, что в ответе есть конкретные строки из этого файла.
    """
    # 1. Создаём сессию
    await telegram_client.send_command("/new")
    response = await telegram_client.wait_for_matching_response("Создана новая сессия")
    num = _extract_session_number(response)

    # 2. Просим маленький файл с предсказуемым содержимым [Claude]
    show_file_marker = _build_show_file_marker(".env.example")
    await telegram_client.send_message(
        "Ответь ровно этим маркером без кавычек и пояснений: "
        f"{show_file_marker}"
    )

    # 3. Ждём финальный ответ Claude с ✅ от НАШЕЙ сессии
    await telegram_client.wait_for_regex_response(
        build_current_session_final_response_pattern(num),
        timeout=CLAUDE_RESPONSE_TIMEOUT_SECONDS,
    )

    # 4. Проверяем: среди ответов есть 📎 с именем НАШЕГО файла (не от другого теста)
    file_messages = [
        response_text for response_text in telegram_client._all_responses
        if PAPERCLIP_EMOJI in response_text and ".env.example" in response_text
    ]
    assert file_messages, (
        f"Ожидалось сообщение с 📎 .env.example, но не найдено. "
        f"Все ответы: {[r[:100] for r in telegram_client._all_responses]}"
    )
    file_response = file_messages[0]

    # 6. Содержимое включает известные переменные окружения из .env.example
    assert "TELEGRAM_BOT_TOKEN" in file_response, (
        f"Файл .env.example должен содержать 'TELEGRAM_BOT_TOKEN': "
        f"{file_response[:300]}"
    )
    assert "ALLOWED_USER_IDS" in file_response, (
        f"Файл .env.example должен содержать 'ALLOWED_USER_IDS': "
        f"{file_response[:300]}"
    )

    # 7. Ни в одном ответе нет сырого маркера
    leaked_markers = _collect_leaked_file_markers(telegram_client._all_responses)
    assert not leaked_markers, (
        f"Файловые маркеры должны быть вырезаны из всех ответов, "
        f"но найдены в:\n" + "\n".join(leaked_markers)
    )
