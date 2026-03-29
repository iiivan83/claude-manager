# Спецификация модуля: process_manager

Дата: 30-03-2026
Слой: 2 (зависит от слоёв 0-1)
Файл: `src/claude_manager/process_manager.py`

## Назначение

Управляет жизненным циклом процессов Claude Code: запускает новые процессы (в том числе с resume), отправляет сообщения пользователя, читает и интерпретирует потоковые события, обрабатывает ошибки с автоматическими ретраями и предоставляет механизм остановки. Поддерживает несколько одновременных процессов для разных сессий и координируется с session_watcher через механизм паузы, чтобы ответы не дублировались.

## Обслуживаемые сценарии

- **CJM-02: Отправка текстового сообщения** — принимает текст от bot.py, отправляет его в процесс Claude через claude_runner, читает поток событий (промежуточные обновления и финальный ответ), возвращает результат. При ошибке Claude автоматически повторяет запрос до 10 раз с интервалом 1 минута.
- **CJM-03: Отправка фотографии или файла** — аналогично CJM-02: получает сформированное сообщение с путём к файлу и инструкцией, отправляет в процесс Claude, возвращает результат. Для process_manager разницы с текстовым сообщением нет.
- **CJM-04: Создание новой сессии (/new)** — запускает новый процесс Claude немедленно (не ждёт первого сообщения). Процесс стартует с рабочей директорией из config.WORKING_DIR и готов принимать сообщения.
- **CJM-08: Остановка Claude (/stop)** — прерывает текущую работу Claude в сессии: завершает процесс и отменяет цикл ретраев, если он активен. Если в сессии нет работающего процесса и нет цикла ретраев — возвращает информацию, что нечего останавливать.

## Публичный API

### `async create_process(session_id: str | None = None) -> str`

Запускает новый процесс Claude. Если передан session_id — запускает с resume (продолжение существующей сессии). Если None — создаёт новую сессию и генерирует временный идентификатор.

**Аргументы:**
- `session_id` (str | None) — идентификатор существующей сессии Claude для возобновления (UUID, например `"84748107-a3de-4314-8c72-4c3b1b6e3605"`). None означает новую сессию.

**Возвращает:** строковый идентификатор сессии — либо переданный session_id (при resume), либо сгенерированный временный (формат `"_new_XXXX"`, где XXXX — последовательный номер, например `"_new_0001"`).

**Исключения:**
- `ProcessManagerError` — если не удалось запустить процесс Claude (оборачивает `ClaudeStartError` от claude_runner)

### `async send_message(session_id: str, text: str, progress_callback: ProgressCallback | None = None, retry_callback: RetryCallback | None = None) -> SendResult`

Отправляет сообщение пользователя в процесс Claude и ожидает полного ответа. Читает потоковые события: извлекает промежуточные обновления (рассуждения Claude) и финальный результат. При ошибке от Claude автоматически повторяет запрос (ретраи).

**Аргументы:**
- `session_id` (str) — идентификатор сессии, в чей процесс отправить сообщение
- `text` (str) — текст сообщения пользователя (например, `"Посмотри файл main.py и скажи что он делает"`)
- `progress_callback` (ProgressCallback | None) — необязательная async-функция обратного вызова для промежуточных обновлений. Сигнатура: `async def callback(session_id: str, text: str) -> None`. Вызывается не чаще раза в 30 секунд. Если None — промежуточные обновления игнорируются.
- `retry_callback` (RetryCallback | None) — необязательная async-функция, вызываемая перед каждой повторной попыткой. Сигнатура: `async def callback(session_id: str, attempt: int, max_attempts: int) -> None`. Позволяет bot.py уведомить пользователя о ретрае. Если None — уведомления не отправляются.

**Возвращает:** объект `SendResult` с полями:
- `text` (str) — текст финального ответа Claude
- `session_id` (str) — актуальный session_id (может отличаться от переданного, если был временный — Claude вернул настоящий)
- `is_error` (bool) — True, если Claude вернул ошибку после всех ретраев
- `retries_used` (int) — сколько повторных попыток было использовано (0 — если ответ получен с первого раза)

**Исключения:**
- `ProcessNotFoundError` — если для указанного session_id нет запущенного процесса
- `ProcessStoppedError` — если запрос был прерван командой /stop во время выполнения или во время ретраев

### `async stop_process(session_id: str) -> StopResult`

Принудительно останавливает процесс Claude в указанной сессии. Прерывает текущий запрос и цикл ретраев (если активен).

**Аргументы:**
- `session_id` (str) — идентификатор сессии, чей процесс нужно остановить

**Возвращает:** объект `StopResult` с полями:
- `was_running` (bool) — True, если процесс был запущен и его удалось остановить. False, если процесс не был найден или уже завершён.
- `was_retrying` (bool) — True, если был прерван цикл ретраев.

### `def is_busy(session_id: str) -> bool`

Проверяет, обрабатывает ли процесс в указанной сессии запрос прямо сейчас (включая ожидание ретрая).

**Аргументы:**
- `session_id` (str) — идентификатор сессии

**Возвращает:** True если процесс занят обработкой запроса или ждёт ретрая, False в остальных случаях (процесс свободен, процесс не существует).

### `def has_process(session_id: str) -> bool`

Проверяет, есть ли запущенный процесс для указанной сессии.

**Аргументы:**
- `session_id` (str) — идентификатор сессии

**Возвращает:** True если процесс существует и работает, False если нет процесса или он уже завершился.

### `async update_session_id(old_session_id: str, new_session_id: str) -> None`

Обновляет ключ сессии во внутренних словарях process_manager. Вызывается когда временный session_id (вида `_new_XXXX`) заменяется настоящим UUID, полученным от Claude.

**Аргументы:**
- `old_session_id` (str) — текущий ключ в словарях (например, `"_new_0001"`)
- `new_session_id` (str) — новый ключ (UUID от Claude, например `"84748107-a3de-4314-8c72-4c3b1b6e3605"`)

**Возвращает:** ничего

### `class SendResult`

Результат отправки сообщения в Claude. Dataclass (неизменяемый).

**Поля:**
- `text` (str) — текст ответа Claude
- `session_id` (str) — актуальный идентификатор сессии
- `is_error` (bool) — True, если ответ является ошибкой (все ретраи исчерпаны)
- `retries_used` (int) — количество использованных повторных попыток

### `class StopResult`

Результат остановки процесса. Dataclass (неизменяемый).

**Поля:**
- `was_running` (bool) — был ли процесс запущен
- `was_retrying` (bool) — был ли прерван цикл ретраев

### `class ProcessManagerError(Exception)`

Общая ошибка process_manager. Используется при невозможности запустить процесс.

### `class ProcessNotFoundError(Exception)`

Ошибка: для указанного session_id нет запущенного процесса.

### `class ProcessStoppedError(Exception)`

Ошибка: запрос прерван командой /stop. Вызывающий код (bot.py) перехватывает её и отправляет пользователю подтверждение остановки.

### `ProgressCallback = Callable[[str, str], Awaitable[None]]`

Тип обратного вызова для промежуточных обновлений. Определён через `type` alias (Python 3.12+).

### `RetryCallback = Callable[[str, int, int], Awaitable[None]]`

Тип обратного вызова для уведомлений о повторных попытках. Аргументы: session_id, номер текущей попытки, максимальное число попыток. Определён через `type` alias (Python 3.12+).

## Внутренние функции

### `_generate_temp_session_id() -> str`

Генерирует уникальный временный идентификатор сессии для новых сессий, пока Claude не вернёт настоящий UUID.

**Аргументы:** нет

**Возвращает:** строка вида `"_new_0001"`, `"_new_0002"` и так далее. Счётчик глобальный, монотонно растущий.

### `async _process_events(claude_process: ClaudeProcess, session_id: str, progress_callback: ProgressCallback | None) -> SendResult`

Читает поток событий от Claude и собирает результат. Извлекает промежуточные обновления (рассуждения Claude) и вызывает progress_callback с троттлингом (не чаще раза в 30 секунд). Определяет финальный текст ответа и session_id из события result.

**Аргументы:**
- `claude_process` (ClaudeProcess) — объект процесса из claude_runner
- `session_id` (str) — текущий идентификатор сессии (может обновиться из событий)
- `progress_callback` (ProgressCallback | None) — функция для промежуточных обновлений

**Возвращает:** объект `SendResult`

### `_extract_progress_text(event: dict) -> str | None`

Извлекает текст промежуточного обновления (рассуждения Claude) из события stream-json. Рассуждения содержатся в событиях типа `assistant`, в блоках `content` с типом `thinking` или `text` (когда Claude размышляет вслух).

**Аргументы:**
- `event` (dict) — одно JSON-событие от Claude

**Возвращает:** текст рассуждения, или None если событие не содержит полезного текста для промежуточного обновления

### `_extract_result_text(event: dict) -> str`

Извлекает финальный текст ответа из события `result`.

**Аргументы:**
- `event` (dict) — JSON-событие типа `result`

**Возвращает:** текст ответа Claude. Если поле `result` пустое или содержит `"No response requested."` — возвращает пустую строку.

### `_is_error_result(event: dict) -> bool`

Проверяет, является ли событие `result` ошибочным (поле `is_error` равно True).

**Аргументы:**
- `event` (dict) — JSON-событие типа `result`

**Возвращает:** True если это ошибка, False если успешный ответ

### `_should_send_progress(last_progress_time: float) -> bool`

Проверяет, прошло ли достаточно времени с последнего промежуточного обновления (не чаще раза в 30 секунд).

**Аргументы:**
- `last_progress_time` (float) — время последней отправки промежуточного обновления (результат `time.monotonic()`)

**Возвращает:** True если прошло 30 или более секунд, или если это первое обновление (last_progress_time == 0)

### `async _retry_loop(session_id: str, text: str, progress_callback: ProgressCallback | None, retry_callback: RetryCallback | None) -> SendResult`

Цикл повторных попыток при ошибке от Claude. Повторяет отправку сообщения до MAX_RETRIES раз с интервалом RETRY_INTERVAL_SECONDS. На каждой итерации проверяет флаг отмены (stop). При успешном ответе — прерывает цикл и возвращает результат.

**Аргументы:**
- `session_id` (str) — идентификатор сессии
- `text` (str) — текст сообщения для повтора
- `progress_callback` (ProgressCallback | None) — функция для промежуточных обновлений
- `retry_callback` (RetryCallback | None) — async-функция, вызываемая перед каждой повторной попыткой. Сигнатура: `async def callback(session_id: str, attempt: int, max_attempts: int) -> None`. Позволяет bot.py уведомить пользователя о ретрае.

**Возвращает:** объект `SendResult`

**Исключения:**
- `ProcessStoppedError` — если во время ожидания ретрая была вызвана команда /stop

## Алгоритм работы

### create_process

1. **Определить session_id** — если передан session_id (resume), использовать его. Если None — вызвать `_generate_temp_session_id()` для создания временного идентификатора (например, `"_new_0001"`)
2. **Запустить процесс Claude** — вызвать `claude_runner.start_process(session_id)` (если resume) или `claude_runner.start_process(None)` (если новая сессия). При `ClaudeStartError` — обернуть в `ProcessManagerError` и выбросить
3. **Сохранить процесс в словарь** — записать пару `session_id → ClaudeProcess` в `_processes`
4. **Инициализировать флаг занятости** — записать `session_id → False` в `_busy_flags`
5. **Инициализировать событие отмены** — создать `asyncio.Event()` и записать в `_stop_events[session_id]`. Событие сброшено по умолчанию (отмена не запрошена)
6. **Залогировать создание** — записать в лог (уровень info): session_id, тип (новый/resume), PID процесса
7. **Вернуть session_id**

### send_message

1. **Проверить наличие процесса** — найти `ClaudeProcess` в `_processes` по session_id. Если не найден — выбросить `ProcessNotFoundError`
2. **Проверить, что процесс не занят** — если `_busy_flags[session_id]` равен True, залогировать предупреждение и дождаться освобождения (или выбросить ошибку — процесс уже обрабатывает другой запрос)
3. **Установить флаг занятости** — `_busy_flags[session_id] = True`
4. **Сбросить событие отмены** — `_stop_events[session_id].clear()` (убедиться, что предыдущая отмена не осталась)
5. **Отправить сообщение** — вызвать `claude_process.send_message(text)`. При `ClaudeProcessError` — перейти к циклу ретраев
6. **Прочитать события** — вызвать `_process_events(claude_process, session_id, progress_callback)` для обработки потока событий
7. **Проверить результат** — если `SendResult.is_error` равен True — перейти к циклу ретраев через `_retry_loop(session_id, text, progress_callback, retry_callback)`
8. **Обновить session_id** — если `SendResult.session_id` отличается от текущего session_id (Claude вернул настоящий UUID вместо временного), вызвать `update_session_id()` для обновления всех словарей
9. **Снять флаг занятости** — `_busy_flags[session_id] = False` (в блоке finally, чтобы снималось и при ошибках)
10. **Вернуть SendResult**

### stop_process

1. **Определить статус** — проверить, есть ли процесс в `_processes[session_id]` и работает ли он
2. **Установить флаг отмены** — `_stop_events[session_id].set()`. Это прервёт ожидание ретрая в `_retry_loop()`
3. **Завершить процесс** — если процесс существует и работает, вызвать `claude_process.terminate()` (SIGTERM, потом SIGKILL через 5 секунд)
4. **Определить, был ли ретрай прерван** — если `_busy_flags[session_id]` был True (запрос обрабатывался), значит возможно прерван и ретрай
5. **Удалить процесс из словарей** — убрать из `_processes`, `_busy_flags`, `_stop_events`
6. **Залогировать остановку** — записать в лог (уровень info): session_id, was_running, was_retrying
7. **Вернуть StopResult** — с флагами was_running и was_retrying

### is_busy

1. **Проверить флаг** — вернуть `_busy_flags.get(session_id, False)`. Если session_id отсутствует в словаре — вернуть False

### has_process

1. **Проверить наличие** — если session_id есть в `_processes` и `_processes[session_id].is_running()` — вернуть True. Иначе — False

### update_session_id

1. **Перенести данные во всех словарях** — для каждого из `_processes`, `_busy_flags`, `_stop_events`: взять значение по ключу old_session_id, записать по ключу new_session_id, удалить старый ключ
2. **Залогировать обновление** — записать в лог (уровень info): old_session_id → new_session_id

### _generate_temp_session_id

1. **Увеличить счётчик** — инкрементировать глобальный `_temp_session_counter`
2. **Сформировать строку** — `f"_new_{_temp_session_counter:04d}"` (четырёхзначный номер с ведущими нулями)
3. **Вернуть строку**

### _process_events

1. **Инициализировать переменные** — `last_progress_time = 0.0`, `result_text = ""`, `final_session_id = session_id`, `is_error = False`
2. **Начать цикл чтения** — итерировать `async for event in claude_process.read_events()`
3. **Проверить флаг отмены** — если `_stop_events[session_id].is_set()` — выбросить `ProcessStoppedError`
4. **Обработать промежуточное обновление** — вызвать `_extract_progress_text(event)`. Если текст есть и `_should_send_progress(last_progress_time)` возвращает True — вызвать `await progress_callback(session_id, text)`, обновить `last_progress_time = time.monotonic()`
5. **Извлечь session_id из события** — если в событии есть session_id и он отличается от текущего — обновить `final_session_id`
6. **Обработать финальное событие** — если `event["type"] == "result"`: извлечь текст через `_extract_result_text(event)`, проверить ошибку через `_is_error_result(event)`, выйти из цикла
7. **Собрать SendResult** — создать объект с полями: text, session_id=final_session_id, is_error, retries_used=0
8. **Вернуть SendResult**

### _extract_progress_text

1. **Проверить тип события** — если `event.get("type") != "assistant"` — вернуть None
2. **Получить блоки контента** — `content_blocks = event.get("message", {}).get("content", [])`
3. **Найти текст рассуждения** — перебрать блоки контента. Если блок имеет тип `"thinking"` — вернуть его текст. Блоки типа `"text"` и `"tool_use"` — не являются промежуточными обновлениями
4. **Вернуть None** — если рассуждений не найдено

### _extract_result_text

1. **Получить текст** — `text = event.get("result", "")`
2. **Проверить служебный ответ** — если `text == "No response requested."` — вернуть пустую строку
3. **Проверить пустоту** — если text равен None — вернуть пустую строку
4. **Вернуть текст**

### _is_error_result

1. **Проверить флаг** — вернуть `event.get("is_error", False)`

### _should_send_progress

1. **Первое обновление** — если `last_progress_time == 0.0` — вернуть True (ни разу не отправлялось)
2. **Проверить интервал** — если `time.monotonic() - last_progress_time >= PROGRESS_THROTTLE_SECONDS` — вернуть True
3. **Иначе** — вернуть False

### _retry_loop

1. **Инициализировать счётчик** — `attempt = 0`
2. **Начать цикл** — от 1 до MAX_RETRIES (включительно)
3. **Проверить флаг отмены** — если `_stop_events[session_id].is_set()` — выбросить `ProcessStoppedError`
4. **Уведомить о повторе** — если `retry_callback` задан, вызвать `await retry_callback(session_id, attempt + 1, MAX_RETRIES)`
5. **Подождать интервал с проверкой отмены** — ожидать `RETRY_INTERVAL_SECONDS` (60 секунд), но каждую секунду проверять `_stop_events[session_id].is_set()`. Если отмена — выбросить `ProcessStoppedError`
6. **Перезапустить процесс** — если предыдущий процесс завершился, вызвать `claude_runner.start_process(session_id)` для resume. Обновить `_processes[session_id]`
7. **Отправить сообщение** — `claude_process.send_message(text)`
8. **Прочитать события** — вызвать `_process_events(claude_process, session_id, progress_callback)`
9. **Проверить результат** — если `SendResult.is_error` равен False — обновить `retries_used` и вернуть результат
10. **Увеличить счётчик** — `attempt += 1`
11. **Ретраи исчерпаны** — залогировать error. Вернуть последний `SendResult` с `is_error=True` и `retries_used=MAX_RETRIES`

## Зависимости

- **claude_runner** — `start_process()`, `ClaudeProcess`, `ClaudeStartError`, `ClaudeProcessError` — запуск процессов Claude и взаимодействие с ними через stream-json
- **config** — `WORKING_DIR` — рабочая директория (не используется напрямую — передаётся в claude_runner через его внутреннюю логику)
- **asyncio** (стандартная библиотека) — `Event`, `sleep`, `wait_for` — координация отмены и ожидание ретраев
- **time** (стандартная библиотека) — `monotonic()` — измерение интервалов для троттлинга промежуточных обновлений
- **dataclasses** (стандартная библиотека) — `dataclass` — определение SendResult и StopResult
- **logging** (стандартная библиотека) — логирование запуска, остановки, ретраев и ошибок

**Примечание:** спецификации модулей-потребителей (bot, session_watcher) ещё не созданы. Публичный API process_manager спроектирован на основе требований BRD и графа зависимостей. При создании спецификаций потребителей может потребоваться корректировка API.

## Обработка ошибок

- **Claude CLI не найден** — `ClaudeStartError` от claude_runner оборачивается в `ProcessManagerError("Не удалось запустить Claude: {описание}")`. Логируется на уровне error
- **Процесс не найден по session_id** — `ProcessNotFoundError("Нет запущенного процесса для сессии '{session_id}'")`. Логируется на уровне warning
- **Ошибка отправки сообщения (ClaudeProcessError)** — процесс мог завершиться. Переход к циклу ретраев: перезапуск процесса с resume и повторная отправка. Логируется на уровне warning
- **Ошибка от Claude (is_error в result)** — Claude вернул ошибку (сбой API, обрыв соединения). Автоматический ретрай до 10 раз с интервалом 1 минута. Каждая попытка логируется на уровне warning. После исчерпания ретраев — логируется на уровне error, возвращается SendResult с is_error=True
- **Запрос прерван /stop** — `ProcessStoppedError`. Не является ошибкой в привычном смысле — это штатное поведение по команде пользователя. Логируется на уровне info
- **Пустой ответ от Claude** — result с пустым текстом или `"No response requested."`. Возвращается пустая строка в SendResult.text. Bot.py решает, что показать пользователю. Логируется на уровне info
- **Процесс завершился во время чтения событий** — read_events() от claude_runner завершит итерацию. Если событие result не было получено — это ошибка. Переход к циклу ретраев. Логируется на уровне warning
- **Невалидный JSON в потоке** — ClaudeProcessError от claude_runner. Логируется на уровне error. Прерывается чтение событий, переход к ретраям
- **Попытка отправить в занятый процесс** — если `_busy_flags[session_id]` равен True (процесс уже обрабатывает другой запрос), залогировать warning и отказать в отправке. Вернуть ошибку вызывающему коду

## Состояние модуля

Модуль хранит состояние в словарях уровня модуля:

- `_processes: dict[str, ClaudeProcess]` — запущенные процессы, ключ — session_id
- `_busy_flags: dict[str, bool]` — флаги занятости (True = процесс обрабатывает запрос или ждёт ретрая)
- `_stop_events: dict[str, asyncio.Event]` — события отмены для прерывания ретраев через /stop
- `_temp_session_counter: int` — счётчик для генерации временных session_id (начинается с 0, монотонно растёт)

## Константы

- `MAX_RETRIES = 10` — максимальное количество повторных попыток при ошибке от Claude. 10 попыток с интервалом 1 минута — суммарно до 10 минут ожидания, что покрывает большинство временных сбоев Claude API
- `RETRY_INTERVAL_SECONDS = 60` — интервал между повторными попытками в секундах. 1 минута — достаточно, чтобы временный сбой успел восстановиться, но не слишком долго для пользователя
- `PROGRESS_THROTTLE_SECONDS = 30` — минимальный интервал между промежуточными обновлениями (в секундах). Защита от спама — Claude может генерировать рассуждения каждую секунду, но пользователю показывается не чаще раза в 30 секунд
- `EVENT_TYPE_RESULT = "result"` — тип финального события от Claude (дублирует константу из claude_runner для удобства, чтобы не импортировать)
- `EVENT_TYPE_ASSISTANT = "assistant"` — тип события с ответом или рассуждением Claude
- `TEMP_SESSION_PREFIX = "_new_"` — префикс временных идентификаторов сессий. Позволяет отличить временный ID от настоящего UUID
- `EMPTY_RESPONSE_MARKER = "No response requested."` — служебный ответ Claude, который не нужно пересылать пользователю

## Координация с session_watcher

Когда пользователь отправляет сообщение через бота, ответ приходит и через process_manager (который сам читает stdout процесса), и через session_watcher (который отслеживает файлы сессий на диске). Чтобы пользователь не получил ответ дважды, используется механизм координации:

- **`is_busy(session_id)`** — session_watcher вызывает эту функцию перед отправкой обнаруженного обновления. Если process_manager занят обработкой запроса в этой сессии — watcher пропускает обновление (знает, что process_manager сам отправит ответ)
- **Флаг `_busy_flags`** — устанавливается в True при начале send_message(), снимается в False при завершении (включая ошибки и ретраи). Это позволяет watcher точно знать, обрабатывается ли запрос в данный момент

## Тест-план

### Юнит-тесты

- **test_create_process_new_session** — проверяет создание нового процесса без resume
  - Вход: `session_id=None`, mock `claude_runner.start_process` возвращает mock ClaudeProcess
  - Ожидаемый результат: возвращён временный session_id (начинается с `"_new_"`), процесс сохранён в `_processes`
  - Тип: unit

- **test_create_process_resume** — проверяет создание процесса с resume существующей сессии
  - Вход: `session_id="84748107-a3de-4314-8c72-4c3b1b6e3605"`, mock claude_runner
  - Ожидаемый результат: возвращён тот же session_id, `claude_runner.start_process` вызван с `session_id="84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Тип: unit

- **test_send_message_success** — проверяет успешную отправку сообщения и получение ответа
  - Вход: запущенный процесс для session_id `"_new_0001"`, mock read_events возвращает события [system (session_id="abc-123"), result (text="Привет!", is_error=False)]
  - Ожидаемый результат: `SendResult(text="Привет!", session_id="abc-123", is_error=False, retries_used=0)`
  - Тип: unit

- **test_send_message_with_progress** — проверяет, что промежуточные обновления передаются через progress_callback
  - Вход: mock read_events возвращает события [system, assistant (thinking: "Анализирую файл..."), result], progress_callback — mock-функция
  - Ожидаемый результат: progress_callback вызван с аргументами `(session_id, "Анализирую файл...")`
  - Тип: unit

- **test_stop_process_running** — проверяет остановку работающего процесса
  - Вход: session_id с запущенным mock-процессом (is_running=True)
  - Ожидаемый результат: `StopResult(was_running=True, was_retrying=False)`, вызван `claude_process.terminate()`
  - Тип: unit

- **test_is_busy_during_request** — проверяет, что is_busy возвращает True во время обработки запроса
  - Вход: session_id, send_message запущен (mock не завершает чтение событий сразу)
  - Ожидаемый результат: `is_busy(session_id) == True`
  - Тип: unit

- **test_is_busy_after_completion** — проверяет, что is_busy возвращает False после завершения запроса
  - Вход: session_id, send_message завершён
  - Ожидаемый результат: `is_busy(session_id) == False`
  - Тип: unit

- **test_has_process_existing** — проверяет наличие запущенного процесса
  - Вход: session_id с созданным процессом
  - Ожидаемый результат: `has_process(session_id) == True`
  - Тип: unit

- **test_has_process_nonexistent** — проверяет отсутствие процесса
  - Вход: session_id, для которого процесс не создавался
  - Ожидаемый результат: `has_process(session_id) == False`
  - Тип: unit

- **test_update_session_id** — проверяет обновление ключа сессии во всех словарях
  - Вход: процесс создан с `"_new_0001"`, вызов `update_session_id("_new_0001", "abc-123")`
  - Ожидаемый результат: `_processes["abc-123"]` существует, `_processes.get("_new_0001")` возвращает None. Аналогично для `_busy_flags` и `_stop_events`
  - Тип: unit

- **test_generate_temp_session_id_sequential** — проверяет последовательную генерацию временных ID
  - Вход: три последовательных вызова `_generate_temp_session_id()`
  - Ожидаемый результат: `"_new_0001"`, `"_new_0002"`, `"_new_0003"`
  - Тип: unit

- **test_extract_result_text_success** — проверяет извлечение текста из успешного result-события
  - Вход: `{"type": "result", "subtype": "success", "is_error": False, "result": "Файл main.py содержит точку входа"}`
  - Ожидаемый результат: `"Файл main.py содержит точку входа"`
  - Тип: unit

- **test_extract_progress_text_thinking** — проверяет извлечение текста рассуждений из thinking-блока
  - Вход: `{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "text": "Сначала прочитаю файл..."}]}}`
  - Ожидаемый результат: `"Сначала прочитаю файл..."`
  - Тип: unit

- **test_is_error_result_true** — проверяет определение ошибочного result
  - Вход: `{"type": "result", "is_error": True, "result": "Error: connection reset"}`
  - Ожидаемый результат: `True`
  - Тип: unit

- **test_is_error_result_false** — проверяет определение успешного result
  - Вход: `{"type": "result", "is_error": False, "result": "Готово"}`
  - Ожидаемый результат: `False`
  - Тип: unit

### Граничные случаи

- **test_send_message_empty_result** — проверяет обработку пустого ответа от Claude
  - Вход: mock result-событие с `"result": ""`
  - Ожидаемый результат: `SendResult(text="", is_error=False, ...)`
  - Тип: edge case

- **test_send_message_no_response_requested** — проверяет фильтрацию служебного ответа
  - Вход: mock result-событие с `"result": "No response requested."`
  - Ожидаемый результат: `SendResult(text="", is_error=False, ...)`
  - Тип: edge case

- **test_progress_throttle_blocks_fast_updates** — проверяет, что промежуточные обновления не отправляются чаще раза в 30 секунд
  - Вход: два события assistant (thinking) с разницей менее 30 секунд, progress_callback — mock
  - Ожидаемый результат: progress_callback вызван только один раз (для первого обновления)
  - Тип: edge case

- **test_progress_throttle_allows_after_interval** — проверяет, что обновление отправляется через 30 секунд
  - Вход: два события assistant (thinking), mock `time.monotonic` возвращает значения с разницей 31 секунда
  - Ожидаемый результат: progress_callback вызван дважды
  - Тип: edge case

- **test_session_id_updated_from_event** — проверяет обновление session_id из потока событий (временный → настоящий)
  - Вход: процесс создан с `"_new_0001"`, mock result-событие содержит `session_id="84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Ожидаемый результат: `SendResult.session_id == "84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Тип: edge case

- **test_stop_process_already_stopped** — проверяет остановку уже завершённого процесса
  - Вход: session_id, mock-процесс с `is_running=False`
  - Ожидаемый результат: `StopResult(was_running=False, was_retrying=False)`
  - Тип: edge case

- **test_stop_nonexistent_session** — проверяет остановку несуществующей сессии
  - Вход: session_id, для которого нет процесса
  - Ожидаемый результат: `StopResult(was_running=False, was_retrying=False)`
  - Тип: edge case

- **test_is_busy_nonexistent_session** — проверяет is_busy для несуществующей сессии
  - Вход: session_id, не зарегистрированный в process_manager
  - Ожидаемый результат: `False`
  - Тип: edge case

- **test_has_process_finished** — проверяет has_process для завершившегося процесса
  - Вход: session_id с процессом, у которого `is_running() == False`
  - Ожидаемый результат: `False`
  - Тип: edge case

- **test_extract_progress_text_tool_use_ignored** — проверяет, что события tool_use не считаются промежуточными обновлениями
  - Вход: `{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/test.py"}}]}}`
  - Ожидаемый результат: `None`
  - Тип: edge case

- **test_extract_progress_text_non_assistant** — проверяет, что события не-assistant типа возвращают None
  - Вход: `{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}}`
  - Ожидаемый результат: `None`
  - Тип: edge case

- **test_busy_flag_cleared_on_error** — проверяет, что флаг занятости снимается даже при ошибке
  - Вход: mock read_events выбрасывает `ClaudeProcessError`
  - Ожидаемый результат: после исключения `_busy_flags[session_id] == False`
  - Тип: edge case

### Тесты ошибок

- **test_create_process_claude_not_found** — проверяет ошибку при отсутствии Claude CLI
  - Вход: mock `claude_runner.start_process` выбрасывает `ClaudeStartError("Claude Code CLI не найден")`
  - Ожидаемый результат: `ProcessManagerError` с текстом, содержащим `"Не удалось запустить Claude"`
  - Тип: error

- **test_send_message_no_process** — проверяет ошибку при отправке в несуществующую сессию
  - Вход: `session_id="nonexistent"`, процесс не создан
  - Ожидаемый результат: `ProcessNotFoundError` с текстом, содержащим `"nonexistent"`
  - Тип: error

- **test_send_message_retry_on_error** — проверяет автоматический ретрай при ошибке от Claude
  - Вход: первая попытка — result с `is_error=True`, вторая попытка — result с `is_error=False`, text="Ответ после ретрая"
  - Ожидаемый результат: `SendResult(text="Ответ после ретрая", is_error=False, retries_used=1)`
  - Тип: error

- **test_send_message_all_retries_exhausted** — проверяет исчерпание всех ретраев
  - Вход: все 10 попыток возвращают result с `is_error=True`, text="Error: service unavailable"
  - Ожидаемый результат: `SendResult(text="Error: service unavailable", is_error=True, retries_used=10)`
  - Тип: error

- **test_stop_interrupts_retry_loop** — проверяет прерывание цикла ретраев командой /stop
  - Вход: первая попытка — ошибка, во время ожидания ретрая вызывается `stop_process(session_id)` (из другой корутины)
  - Ожидаемый результат: `send_message` выбрасывает `ProcessStoppedError`, процесс остановлен
  - Тип: error

- **test_retry_callback_called** — проверяет, что retry_callback вызывается перед каждой повторной попыткой
  - Вход: две попытки (первая — ошибка, вторая — успех), retry_callback — mock
  - Ожидаемый результат: retry_callback вызван один раз с аргументами `(session_id, 1, 10)`
  - Тип: error

- **test_process_crash_during_events** — проверяет обработку неожиданного завершения процесса
  - Вход: mock read_events завершается без события result (процесс рухнул)
  - Ожидаемый результат: переход к циклу ретраев, процесс перезапускается с resume
  - Тип: error

- **test_broken_pipe_triggers_retry** — проверяет, что BrokenPipeError при отправке приводит к ретраю
  - Вход: mock send_message выбрасывает `ClaudeProcessError` (BrokenPipe), повторная попытка — успех
  - Ожидаемый результат: `SendResult(is_error=False, retries_used=1)`
  - Тип: error
