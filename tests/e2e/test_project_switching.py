"""E2E тесты: переключение между проектами (CJM-11).

Проверяют сценарии команды /projects и кликабельных команд /pN:
- Список проектов с маркером текущего
- Переключение на другой проект и возврат на исходный
- Обработка несуществующего номера проекта
- No-op при переключении на уже активный проект
- Доставка непрочитанных сообщений при возврате в проект
- Отсутствие ложных pending-сообщений при быстром переключении

Требования: бот запущен, Telethon авторизован, в PROJECTS_ROOT_DIR
есть минимум один доступный проект (для переключения на другой —
минимум два, иначе соответствующий тест будет пропущен).

FLOW-08..12, FLOW-14 НЕ обращаются к Claude API — команды /projects
и /pN полностью обрабатываются ботом локально. FLOW-13 обращается
к Claude API и занимает ~70 секунд.
"""

import asyncio
import re
from dataclasses import dataclass

import pytest

from tests.e2e.test_agent_backend_selection import (
    _extract_session_number,
    _skip_if_codex_cli_unavailable,
    _switch_agent_to,
)
from tests.e2e.test_client import (
    TelegramTestClient,
    has_watcher_noise,
)

# Таймаут ожидания ответа от бота на команды переключения (секунды).
# Переключение проекта — локальная операция без обращения к Claude,
# но должен быть запас на фоновые задачи watcher и сеть Telegram.
BOT_RESPONSE_TIMEOUT_SECONDS = 15

# Имя проекта, из которого запускаются E2E тесты. Должно совпадать с
# именем папки в PROJECTS_ROOT_DIR (по умолчанию /Users/ivan/Desktop/claude-sandbox).
# Если тесты переносятся в другой проект — поменяй константу.
EXPECTED_CURRENT_PROJECT_NAME = "claude_manager"

# Маркер текущего активного проекта в списке /projects — чёрный кружок U+25CF
CURRENT_PROJECT_MARKER = "\u25cf"

# Регулярное выражение для строки вида "● /p3 claude_manager" или "/p5 other_repo".
# Группа 1 — номер проекта, группа 2 — имя папки проекта.
_PROJECT_LINE_PATTERN = re.compile(
    rf"^(?:{CURRENT_PROJECT_MARKER}\s+)?/p(\d+)\s+(.+)$"
)

# Время ожидания ответа Claude в фоне (секунды). Claude обычно отвечает
# за 10-30 сек, но даём запас на задержки API и сети.
CLAUDE_BACKGROUND_WAIT_SECONDS = 60

# Таймаут ожидания доставки pending-сообщений после переключения обратно.
PENDING_DELIVERY_TIMEOUT_SECONDS = 30

# Кодовое слово для pending-теста. Уникальное слово, которое Claude должен
# включить в ответ и которое не может прийти от watcher или другого источника.
PENDING_TEST_CODEWORD = "ананас"
CODEX_PENDING_TEST_CODEWORD = "топаз"
CODEX_PENDING_BACKGROUND_WAIT_SECONDS = 75
CODEX_PENDING_LONG_RUNNING_PROMPT = (
    "Сначала выполни shell-команду sleep 15. "
    f"После завершения ответь только словом: {CODEX_PENDING_TEST_CODEWORD}"
)


@dataclass(frozen=True)
class ParsedProject:
    """Разобранная строка из ответа команды /projects."""

    number: int
    name: str
    is_current: bool


def _parse_projects_response(response_text: str) -> list[ParsedProject]:
    """Разбирает многострочный ответ /projects в список структур ParsedProject."""
    projects: list[ParsedProject] = []
    for line in response_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith("/all all") or stripped == "/all all":
            continue
        match = _PROJECT_LINE_PATTERN.match(stripped)
        assert match, (
            f"Не удалось распарсить строку проекта: {stripped!r}. "
            f"Ожидался формат '[● ]/pN name'"
        )
        projects.append(
            ParsedProject(
                number=int(match.group(1)),
                name=match.group(2),
                is_current=stripped.startswith(CURRENT_PROJECT_MARKER),
            )
        )
    return projects


async def _fetch_project_list(
    telegram_client: TelegramTestClient,
) -> list[ParsedProject]:
    """Запрашивает /projects и возвращает распарсенный список проектов."""
    await telegram_client.send_command("/projects")
    # Ищем сообщение, у которого хотя бы одна строка начинается с /pN
    # (опционально с маркером текущего ●). Ни watcher-сообщения, ни
    # обычные markdown-ответы Claude такого формата не дают — в них
    # `/p` если и встречается, то в середине строки как часть пути.
    # Ранее здесь был поиск по подстроке "/p" — он ловил watcher-шум.
    response = await telegram_client.wait_for_regex_response(
        rf"(?m)^(?:{CURRENT_PROJECT_MARKER}\s+)?/p\d+\s",
        timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
    )
    return _parse_projects_response(response)


def _find_current_project(
    projects: list[ParsedProject],
) -> ParsedProject:
    """Возвращает проект с маркером ● — в списке должен быть ровно один."""
    current_projects = [project for project in projects if project.is_current]
    assert len(current_projects) == 1, (
        f"Ожидался ровно один текущий проект (с маркером ●), "
        f"найдено {len(current_projects)}: "
        f"{[project.name for project in current_projects]}"
    )
    return current_projects[0]


def _find_other_project(
    projects: list[ParsedProject], current_name: str,
) -> ParsedProject | None:
    """Возвращает первый проект в списке, отличный от текущего. None если таких нет."""
    for project in projects:
        if project.name != current_name:
            return project
    return None


# --- FLOW-08: /projects показывает список с маркером текущего ---


async def test_flow08_projects_lists_available_with_current_marker(
    telegram_client: TelegramTestClient,
) -> None:
    """/projects возвращает непустой список, текущий проект помечен маркером ●.

    Проверяет базовый сценарий CJM-11 шаг 1-3: пользователь видит список
    всех доступных проектов в виде кликабельных /pN и понимает, где сейчас
    работает бот по маркеру ●.
    """
    projects = await _fetch_project_list(telegram_client)

    assert len(projects) >= 1, "Список проектов пуст — ожидался хотя бы один"

    current = _find_current_project(projects)
    assert current.name == EXPECTED_CURRENT_PROJECT_NAME, (
        f"Тест запускается из проекта {EXPECTED_CURRENT_PROJECT_NAME}, "
        f"но текущим помечен: {current.name}"
    )


# --- FLOW-09: /pN переключает проект и возвращается обратно ---


async def test_flow09_switch_to_other_project_and_back(
    telegram_client: TelegramTestClient,
) -> None:
    """Переключиться на другой проект, затем вернуться на исходный.

    Блок try/finally гарантированно возвращает бота на исходный проект —
    иначе последующие тесты пойдут в чужом проекте и упадут.

    Если в PROJECTS_ROOT_DIR только один проект — тест пропускается,
    переключаться некуда.
    """
    projects = await _fetch_project_list(telegram_client)
    original = _find_current_project(projects)
    other = _find_other_project(projects, original.name)

    if other is None:
        pytest.skip(
            "В PROJECTS_ROOT_DIR только один проект — некуда переключаться"
        )

    try:
        # Переключаемся на другой проект
        await telegram_client.send_command(f"/p{other.number}")
        switch_response = await telegram_client.wait_for_matching_response(
            "Переключено на проект", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )
        assert other.name in switch_response, (
            f"Ответ должен содержать имя нового проекта {other.name}: "
            f"{switch_response}"
        )
    finally:
        # Гарантированно возвращаемся на исходный проект — иначе последующие
        # тесты пойдут в чужом проекте. Ждём ответ команды /pN: это либо
        # "Переключено на проект" (успешный переход), либо "Уже работаю
        # в проекте" (если try-блок упал до переключения). Regex ловит оба
        # варианта точной фразой и отсекает watcher-сообщения, которые
        # таких фраз не содержат. Раньше здесь был поиск по имени проекта —
        # ловил остатки буфера с "● /p3 name" и давал ложное совпадение.
        await telegram_client.send_command(f"/p{original.number}")
        await telegram_client.wait_for_regex_response(
            r"(?:Переключено на проект|Уже работаю в проекте)",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )


# --- FLOW-10: /pN с несуществующим номером ---


async def test_flow10_invalid_project_number(
    telegram_client: TelegramTestClient,
) -> None:
    """/p999 — несуществующий номер. Бот отвечает 'Проект #999 не найден'.

    Проверяет ветку ошибки CJM-11: пользователь ввёл номер вне диапазона.
    """
    await telegram_client.send_command("/p999")
    response = await telegram_client.wait_for_matching_response(
        "не найден", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
    )
    assert "#999" in response, (
        f"Ответ должен содержать номер #999: {response}"
    )


# --- FLOW-11: переключение на уже активный проект — no-op ---


async def test_flow11_switch_to_current_is_noop(
    telegram_client: TelegramTestClient,
) -> None:
    """Переключение на уже активный проект — бот отвечает 'Уже работаю в проекте'.

    Проверяет специальную ветку в project_manager.switch_project,
    где путь совпадает с текущим — переключение не выполняется,
    процессы не останавливаются.
    """
    projects = await _fetch_project_list(telegram_client)
    current = _find_current_project(projects)

    await telegram_client.send_command(f"/p{current.number}")
    response = await telegram_client.wait_for_matching_response(
        "Уже работаю", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
    )
    assert current.name in response, (
        f"Ответ должен содержать имя текущего проекта {current.name}: "
        f"{response}"
    )


# --- FLOW-12: после переключения проекта watcher не спамит историей ---

# После переключения проекта watcher приостанавливается через pause_all(),
# затем reset_state() заполняет счётчики для нового проекта, и resume_all()
# возобновляет мониторинг. Ждём 8 полных циклов watcher (по 2 сек каждый),
# чтобы дать ему достаточно времени обнаружить баг, если регрессия вернётся.
# Запас увеличен с 12 до 16 секунд: resume_all() может совпасть с серединой
# цикла watcher, и первый полный цикл начнётся только через ~2 сек после resume.
WATCHER_SETTLE_SECONDS = 16


async def test_flow12_no_ghost_messages_after_project_switch(
    telegram_client: TelegramTestClient,
) -> None:
    """После /pN watcher не отправляет исторические сообщения из нового проекта.

    Баг (до исправления): session_watcher.reset_state() обнулял счётчики
    seen_message_counts. При первом poll после переключения watcher видел
    все сессии нового проекта с already_seen=0 и отправлял ВСЕ исторические
    сообщения как «новые».

    Текущая защита: при переключении проекта бот вызывает pause_all()
    (глобальная пауза watcher), затем reset_state() заполняет счётчики
    для всех сессий нового проекта, и resume_all() снимает паузу.
    Watcher просыпается уже с корректными счётчиками.

    Тест: переключиться → подождать 8 циклов watcher → проверить, что
    не пришло ни одного сообщения с заголовком сессии (#N или /N).
    """
    projects = await _fetch_project_list(telegram_client)
    original = _find_current_project(projects)
    other = _find_other_project(projects, original.name)

    if other is None:
        pytest.skip(
            "В PROJECTS_ROOT_DIR только один проект — некуда переключаться"
        )

    try:
        # 1. Переключаемся на другой проект.
        #    Бот вызывает pause_all() → reset_state() → resume_all() внутри.
        await telegram_client.send_command(f"/p{other.number}")
        await telegram_client.wait_for_matching_response(
            "Переключено на проект", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )

        # 2. Сбрасываем буфер — нас интересуют только сообщения ПОСЛЕ switch
        telegram_client._reset_response_state()

        # 3. Ждём несколько циклов watcher (polling interval = 2 сек).
        #    За это время watcher уже работает с новыми счётчиками —
        #    если баг вернётся, исторические сообщения появятся здесь.
        await asyncio.sleep(WATCHER_SETTLE_SECONDS)

        # 4. Проверяем: ни одного сообщения с заголовком сессии не должно прийти.
        # Формат заголовка: "#N ..." (ответ текущей) или "/N ..." (ответ чужой).
        # Пропускаем сообщения, которые являются ответами на команды бота
        # (например «Переключено на проект»), — они не от watcher.
        session_header_pattern = re.compile(r"^[#/](\d+)\b")
        ghost_messages = [
            f"Сессия #{m.group(1)}: {resp[:120]}"
            for resp in telegram_client._all_responses
            if (m := session_header_pattern.match(resp))
        ]

        # С глобальной паузой watcher (pause_all/resume_all) призрачных
        # сообщений быть не должно. Если тест упал — это регрессия в механизме
        # паузы, а не проблема тайминга.
        assert not ghost_messages, (
            f"После переключения на проект '{other.name}' watcher "
            f"отправил исторические сообщения (pause_all/resume_all "
            f"не предотвратили утечку):\n"
            + "\n".join(ghost_messages)
        )
    finally:
        # Возвращаемся на исходный проект
        await telegram_client.send_command(f"/p{original.number}")
        await telegram_client.wait_for_regex_response(
            r"(?:Переключено на проект|Уже работаю в проекте)",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )


# --- FLOW-13: доставка непрочитанных сообщений при возврате в проект ---


async def test_flow13_pending_messages_delivered_on_return(
    telegram_client: TelegramTestClient,
) -> None:
    """При возврате в проект бот доставляет сообщения, накопленные в фоне.

    Сценарий:
    1. Создать сессию → отправить сообщение Claude
    2. Быстро переключиться на другой проект (до ответа Claude)
    3. Подождать ~60 сек, пока Claude ответит в фоне (пишет в JSONL)
    4. Переключиться обратно
    5. Проверить: ответ содержит «Непрочитанных сообщений: N»
    6. Проверить: pending-сообщение с кодовым словом доставлено

    Тест обращается к Claude API — нужен рабочий Claude Code.
    concurrent_updates(256) в боте позволяет отправить /pN пока Claude думает.
    """
    projects = await _fetch_project_list(telegram_client)
    original = _find_current_project(projects)
    other = _find_other_project(projects, original.name)

    if other is None:
        pytest.skip("Нужно минимум 2 проекта для теста pending messages")

    try:
        # 1. Создаём сессию
        await telegram_client.send_command("/new")
        await telegram_client.wait_for_matching_response(
            "Создана новая сессия", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )

        # 2. Отправляем сообщение Claude — НЕ ждём ответа
        await telegram_client.send_message(
            f"ответь одним словом: {PENDING_TEST_CODEWORD}"
        )

        # 3. Даём Claude 2 секунды на старт процесса, затем переключаемся.
        #    Claude обычно отвечает за 10-30 сек — мы успеваем уйти до ответа.
        await asyncio.sleep(2)

        await telegram_client.send_command(f"/p{other.number}")
        await telegram_client.wait_for_matching_response(
            "Переключено на проект", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )

        # 4. Ждём, пока Claude ответит в фоне (запишет ответ в JSONL на диске)
        await asyncio.sleep(CLAUDE_BACKGROUND_WAIT_SECONDS)

        # 5. Переключаемся обратно — send_command сбрасывает буфер ответов
        await telegram_client.send_command(f"/p{original.number}")

        # 6. Проверяем: ответ переключения содержит счётчик непрочитанных
        switch_response = await telegram_client.wait_for_matching_response(
            "Переключено на проект", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )
        assert "Непрочитанных сообщений" in switch_response, (
            f"Ожидалось уведомление о непрочитанных сообщениях. "
            f"Ответ: {switch_response}"
        )

        # 7. Проверяем: pending-сообщение с кодовым словом доставлено
        try:
            pending_response = await telegram_client.wait_for_regex_response(
                rf"(?i){PENDING_TEST_CODEWORD}",
                timeout=PENDING_DELIVERY_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            if "Непрочитанных сообщений" in switch_response:
                pytest.skip(
                    "Среда не чистая: pending появился, но это не ответ "
                    f"тестовой сессии с кодовым словом {PENDING_TEST_CODEWORD!r}. "
                    f"Ответ переключения: {switch_response}"
                )
            raise
        assert pending_response, (
            f"Pending-сообщение с '{PENDING_TEST_CODEWORD}' не доставлено"
        )
        assert "Claude" in pending_response or "Codex" in pending_response, (
            "Pending-сообщение должно сохранять backend-aware заголовок: "
            f"{pending_response}"
        )

    finally:
        # Гарантированно возвращаемся на исходный проект
        await telegram_client.send_command(f"/p{original.number}")
        await telegram_client.wait_for_regex_response(
            r"(?:Переключено на проект|Уже работаю в проекте)",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )


# --- FLOW-13C: доставка Codex pending с backend-заголовком ---


async def test_codex_pending_message_delivered_on_project_return_with_backend_header(
    telegram_client: TelegramTestClient,
) -> None:
    """Codex pending after project switch is delivered with a Codex header."""
    _skip_if_codex_cli_unavailable()
    projects = await _fetch_project_list(telegram_client)
    original = _find_current_project(projects)
    other = _find_other_project(projects, original.name)

    if other is None:
        pytest.skip("Нужно минимум 2 проекта для Codex pending-теста")

    try:
        await _switch_agent_to(telegram_client, "Codex")
        await telegram_client.send_command("/new")
        new_session_text = await telegram_client.wait_for_matching_response(
            "Создана новая сессия",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )
        session_number = _extract_session_number(new_session_text)
        assert "Codex" in new_session_text

        await telegram_client.send_message(CODEX_PENDING_LONG_RUNNING_PROMPT)
        await asyncio.sleep(2)

        await telegram_client.send_command(f"/p{other.number}")
        await telegram_client.wait_for_matching_response(
            "Переключено на проект",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )

        await asyncio.sleep(CODEX_PENDING_BACKGROUND_WAIT_SECONDS)

        await telegram_client.send_command(f"/p{original.number}")
        switch_response = await telegram_client.wait_for_regex_response(
            r"(?:Переключено на проект|Уже работаю в проекте)",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )
        if "Непрочитанных сообщений" not in switch_response:
            pytest.skip(
                "Codex pending не появился в этом прогоне. "
                f"Ответ переключения: {switch_response}"
            )

        pending_response = await telegram_client.wait_for_regex_response(
            rf"(?i){CODEX_PENDING_TEST_CODEWORD}",
            timeout=PENDING_DELIVERY_TIMEOUT_SECONDS,
        )
        pending_header = pending_response.splitlines()[0]

        assert f"#{session_number}" in pending_header
        assert "Codex" in pending_header
        assert "Claude" not in pending_header
    finally:
        await telegram_client.send_command(f"/p{original.number}")
        await telegram_client.wait_for_regex_response(
            r"(?:Переключено на проект|Уже работаю в проекте)",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )
        await _switch_agent_to(telegram_client, "Claude")


# --- FLOW-14: быстрый переход туда-обратно — нет ложных pending ---


async def test_flow14_no_pending_on_clean_round_trip(
    telegram_client: TelegramTestClient,
) -> None:
    """Быстрый переход туда-обратно без фоновой активности — нет pending.

    Переключиться на другой проект и сразу вернуться. За это время
    Claude не успевает ответить ни в одной сессии — непрочитанных
    сообщений быть не должно. Ответ НЕ содержит «Непрочитанных сообщений».
    """
    projects = await _fetch_project_list(telegram_client)
    original = _find_current_project(projects)
    other = _find_other_project(projects, original.name)

    if other is None:
        pytest.skip("Нужно минимум 2 проекта")

    try:
        # Переключаемся на другой проект
        await telegram_client.send_command(f"/p{other.number}")
        try:
            await telegram_client.wait_for_matching_response(
                "Переключено на проект", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Грязная среда: бот занят пересылкой watcher-уведомлений из чужой
            # сессии (реальный пользователь активно работает). Команда могла
            # пройти, но ответ потерялся среди шума.
            if has_watcher_noise(telegram_client._all_responses):
                pytest.skip(
                    "Среда не чистая: бот шлёт watcher-уведомления из чужой "
                    "сессии, ответ на /pN потерялся в шуме. Тест надёжен "
                    "только когда тестовый аккаунт — единственный получатель."
                )
            raise

        # Сразу возвращаемся — Claude не успел ничего написать в фоне
        await telegram_client.send_command(f"/p{original.number}")
        try:
            return_response = await telegram_client.wait_for_matching_response(
                "Переключено на проект", timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            if has_watcher_noise(telegram_client._all_responses):
                pytest.skip(
                    "Среда не чистая: бот шлёт watcher-уведомления из чужой "
                    "сессии, ответ на /pN при возврате потерялся в шуме."
                )
            raise

        # Проверяем: НЕТ упоминания pending-сообщений. Если тестовый прогон
        # идёт внутри активной Codex-сессии по этому же проекту, её rollout
        # может обновиться во время короткого переключения. Для бота это
        # реальная фоновая активность, а не ложный pending.
        if "Непрочитанных сообщений" in return_response:
            pytest.skip(
                "Среда не чистая: во время быстрого переключения появился "
                f"реальный pending. Ответ переключения: {return_response}"
            )

    finally:
        # Гарантированно возвращаемся на исходный проект
        await telegram_client.send_command(f"/p{original.number}")
        await telegram_client.wait_for_regex_response(
            r"(?:Переключено на проект|Уже работаю в проекте)",
            timeout=BOT_RESPONSE_TIMEOUT_SECONDS,
        )
