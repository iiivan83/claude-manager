# Спецификация модуля: session_watcher

Дата: 30-03-2026
Слой: 3 (зависит от слоёв 0-2)
Файл: `src/claude_manager/session_watcher.py`

## Назначение

Мониторит файлы сессий Claude Code на диске в реальном времени (каждые 2 секунды), обнаруживает новые сообщения Claude в любой сессии текущего проекта и передаёт их в callback-функцию для отправки пользователю. Координируется с обработчиком сообщений (handle_message в bot.py) через механизм паузы, чтобы один и тот же ответ Claude не приходил пользователю дважды.

## Обслуживаемые сценарии

- **CJM-02: Отправка текстового сообщения** — когда бот сам отправил сообщение в Claude и ждёт ответ, watcher координируется с обработчиком через механизм паузы (счётчик active_requests), чтобы не дублировать ответ. Для сообщений из других сессий watcher продолжает работать без паузы
- **CJM-03: Отправка фотографии или файла** — аналогично CJM-02: координация с обработчиком через механизм паузы при ожидании ответа от Claude
- **CJM-07: Мониторинг всех сессий (/all)** — каждые 2 секунды проверяет файлы сессий текущего проекта за последние 2 дня, обнаруживает новые ответы Claude в любой сессии и передаёт полные сообщения (не короткие уведомления) в callback-функцию. Номер сессии оформляется как кликабельная ссылка для чужих сессий и без ссылки для текущей

## Публичный API

### `async def start(callback: MessageCallback, get_current_session: CurrentSessionGetter) -> None`

Запускает бесконечный цикл мониторинга сессий. Каждые 2 секунды проверяет файлы сессий на диске и вызывает callback при обнаружении новых сообщений Claude.

**Аргументы:**
- `callback` (MessageCallback) — async-функция, которую watcher вызывает при обнаружении нового сообщения Claude. Сигнатура: `async def callback(chat_id: int, session_id: str, day_number: int, message_text: str, is_current_session: bool) -> None`. Реализуется в bot.py — она форматирует сообщение (добавляет номер сессии, конвертирует markdown в HTML) и отправляет в Telegram
- `get_current_session` (CurrentSessionGetter) — async-функция, которая возвращает session_id текущей сессии пользователя (или None, если пользователь в режиме /all). Сигнатура: `async def get_current_session(chat_id: int) -> str | None`. Нужна для определения, является ли сессия текущей (чтобы решить, делать ли номер кликабельной ссылкой)

**Возвращает:** ничего. Функция работает бесконечно (до отмены задачи через `asyncio.Task.cancel()`).

**Исключения:**
- `asyncio.CancelledError` — нормальное завершение при отмене задачи (при остановке бота). Не логируется как ошибка

### `def pause_session(session_id: str) -> None`

Ставит мониторинг конкретной сессии на паузу. Вызывается из bot.py/process_manager перед отправкой сообщения в Claude, чтобы watcher не дублировал ответ.

**Аргументы:**
- `session_id` (str) — идентификатор сессии, для которой нужно приостановить мониторинг

**Возвращает:** ничего.

### `async def resume_session(session_id: str) -> None`

Снимает паузу с мониторинга конкретной сессии. Вызывается из bot.py/process_manager после того, как обработчик сам прочитал и отправил ответ Claude пользователю.

**Аргументы:**
- `session_id` (str) — идентификатор сессии, для которой нужно возобновить мониторинг

**Возвращает:** ничего.

**Примечание:** при вызове resume_session watcher должен обновить свой «указатель» (количество уже обработанных сообщений) для этой сессии до актуального значения — чтобы не отправлять пользователю сообщения, которые уже были отправлены обработчиком. Для этого watcher перечитывает файл сессии через `session_reader.get_session_messages()` и записывает текущее количество строк как «уже обработанные».

### `def update_session_id(old_session_id: str, new_session_id: str) -> None`

Обновляет идентификатор сессии во внутренних словарях watcher. Нужна для сквозного механизма «обновление session_id» — когда Claude возвращает настоящий ID вместо временного `_new_XXXX`.

**Аргументы:**
- `old_session_id` (str) — текущий (временный) идентификатор сессии (например, `"_new_0001"`)
- `new_session_id` (str) — новый (реальный) идентификатор сессии от Claude (например, `"eb5ac5bc-2ac6-45ad-8ca9-bb3a1a741f1e"`)

**Возвращает:** ничего.

### Типы-алиасы

```python
MessageCallback = Callable[[int, str, int, str, bool], Awaitable[None]]
CurrentSessionGetter = Callable[[int], Awaitable[str | None]]
```

## Внутренние функции

### `async def _poll_sessions() -> None`

Выполняет один цикл проверки всех сессий. Вызывается из `start()` каждые 2 секунды.

**Аргументы:** нет.

**Возвращает:** ничего.

### `async def _check_session(session_id: str) -> None`

Проверяет одну сессию на наличие новых сообщений Claude. Если новые сообщения обнаружены — вызывает callback для каждого.

**Аргументы:**
- `session_id` (str) — идентификатор сессии для проверки

**Возвращает:** ничего.

### `def _extract_assistant_messages(all_messages: list[dict], already_seen_count: int) -> list[str]`

Извлекает текст новых сообщений Claude (роль `assistant`) из списка всех сообщений сессии, пропуская уже обработанные.

**Аргументы:**
- `all_messages` (list[dict]) — все сообщения сессии (результат `session_reader.get_session_messages()`)
- `already_seen_count` (int) — количество сообщений, которые уже были обработаны в предыдущих циклах

**Возвращает:** список текстов новых сообщений Claude (list[str]). Пустой список, если новых сообщений нет.

### `def _extract_message_text(message: dict) -> str | None`

Извлекает текстовое содержимое из одного сообщения Claude. Обрабатывает разные форматы поля `content` (строка или список).

**Аргументы:**
- `message` (dict) — одна запись из JSONL-файла сессии

**Возвращает:** текст сообщения (str) или None, если сообщение не содержит текста или имеет неподдерживаемый тип.

### `def _is_empty_response(text: str) -> bool`

Проверяет, является ли ответ Claude пустым или служебным (например, `"No response requested."`).

**Аргументы:**
- `text` (str) — текст ответа Claude

**Возвращает:** True, если ответ считается пустым/служебным и не должен отправляться пользователю.

### `async def _get_sessions_to_monitor() -> list[str]`

Получает список session_id всех сессий, которые нужно мониторить. Включает сессии за последние 2 дня текущего проекта.

**Аргументы:** нет.

**Возвращает:** список идентификаторов сессий (list[str]).

## Алгоритм работы

### start

1. **Инициализировать внутреннее состояние** — создать словарь `_seen_message_counts` (session_id -> количество уже обработанных сообщений), множество `_paused_sessions` (session_id сессий на паузе), сохранить ссылки на `callback` и `get_current_session`
2. **Первоначальное сканирование** — вызвать `_get_sessions_to_monitor()` и для каждой сессии прочитать текущее количество сообщений через `session_reader.get_session_messages()`. Записать эти числа в `_seen_message_counts` — это «стартовая точка», watcher не будет отправлять старые сообщения, только новые
3. **Запустить бесконечный цикл:**
   - Вызвать `_poll_sessions()`
   - Подождать `POLL_INTERVAL_SECONDS` (2 секунды) через `asyncio.sleep()`
   - Если поймано `asyncio.CancelledError` — выйти из цикла (нормальное завершение)
   - Если поймана другая ошибка — залогировать через `logging.error`, подождать `ERROR_RETRY_DELAY_SECONDS` (10 секунд) и продолжить цикл (не падать из-за единичной ошибки)

### _poll_sessions

1. **Получить список сессий** — вызвать `_get_sessions_to_monitor()` для получения актуального списка session_id за последние 2 дня
2. **Очистить устаревшие записи** — удалить из `_seen_message_counts` сессии, которых нет в актуальном списке (сессия могла быть удалена с диска)
3. **Проверить каждую сессию** — для каждого session_id из списка вызвать `_check_session(session_id)`. Проверки выполняются последовательно, не параллельно (чтобы не нагружать диск)

### _check_session

1. **Проверить паузу** — если session_id есть в `_paused_sessions`, пропустить эту сессию (мониторинг приостановлен, ответ будет отправлен обработчиком)
2. **Прочитать все сообщения** — вызвать `session_reader.get_session_messages(session_id, config.WORKING_DIR)` для получения всех строк JSONL-файла
3. **Посчитать текущее количество** — `len(all_messages)`
4. **Сравнить с предыдущим** — получить `already_seen_count` из `_seen_message_counts.get(session_id, 0)`. Если текущее количество не больше — новых сообщений нет, выйти
5. **Извлечь новые ответы Claude** — вызвать `_extract_assistant_messages(all_messages, already_seen_count)` для получения текстов новых сообщений Claude (не пользователя)
6. **Обновить счётчик** — записать текущее количество сообщений в `_seen_message_counts[session_id]`
7. **Для каждого нового сообщения:**
   - Проверить через `_is_empty_response()` — пропустить пустые/служебные ответы
   - Получить дневной номер сессии через `daily_session_registry.register_session(session_id)` (если сессия ещё не зарегистрирована — будет зарегистрирована)
   - Получить chat_id через `session_manager.get_chat_ids()` (все chat_id, подписанные на обновления)
   - Для каждого chat_id определить, является ли сессия текущей, через вызов `get_current_session(chat_id)`
   - Вызвать `callback(chat_id, session_id, day_number, message_text, is_current_session)`

### pause_session

1. **Добавить session_id в множество пауз** — `_paused_sessions.add(session_id)`
2. **Залогировать** — `logging.debug(f"Мониторинг сессии {session_id} приостановлен")`

### resume_session

1. **Убрать session_id из множества пауз** — `_paused_sessions.discard(session_id)` (discard не выбрасывает ошибку, если элемента нет)
2. **Обновить счётчик обработанных сообщений** — вызвать `session_reader.get_session_messages(session_id, config.WORKING_DIR)` и записать `len(messages)` в `_seen_message_counts[session_id]`. Это предотвращает дублирование: обработчик уже отправил все текущие сообщения, watcher не должен отправлять их повторно
3. **Залогировать** — `logging.debug(f"Мониторинг сессии {session_id} возобновлён, счётчик обновлён до {count}")`

### update_session_id

1. **Перенести счётчик сообщений** — если `old_session_id` есть в `_seen_message_counts`, скопировать значение в `_seen_message_counts[new_session_id]` и удалить запись `old_session_id`
2. **Перенести статус паузы** — если `old_session_id` есть в `_paused_sessions`, удалить его и добавить `new_session_id`
3. **Залогировать** — `logging.info(f"Watcher: session_id обновлён {old_session_id} → {new_session_id}")`
4. **Если old_session_id не найден** — молча завершиться (идемпотентность). Залогировать через `logging.debug`

### _extract_assistant_messages

1. **Взять новые строки** — из `all_messages` взять элементы начиная с индекса `already_seen_count` до конца
2. **Фильтровать по роли** — оставить только строки, где тип сообщения — ответ Claude (роль `assistant`). Пропустить сообщения пользователя, системные, мета-данные
3. **Извлечь текст** — для каждого подходящего сообщения вызвать `_extract_message_text()`. Если вернуло None — пропустить
4. **Вернуть список текстов** — list[str]

### _extract_message_text

1. **Проверить тип записи** — запись должна быть типа `"assistant"` (поле `type == "assistant"`)
2. **Извлечь content** — из поля `message.content` получить содержимое
3. **Обработать формат content:**
   - Если `content` — строка, вернуть её
   - Если `content` — список, найти элементы с `type == "text"`, склеить их поле `text` через пробел
4. **Вернуть текст** или None, если текста нет

### _is_empty_response

1. **Проверить на None/пустую строку** — если `text` пуст или состоит только из пробелов, вернуть True
2. **Проверить на служебный ответ** — если `text.strip()` равен `"No response requested."`, вернуть True
3. **Вернуть False** — ответ непустой и не служебный

### _get_sessions_to_monitor

1. **Получить список последних сессий** — вызвать `session_reader.get_recent_sessions(config.WORKING_DIR)` для получения до 15 свежих сессий
2. **Извлечь session_id** — из каждого объекта `SessionInfo` взять поле `session_id`
3. **Добавить сессии из дневного реестра** — вызвать `daily_session_registry.get_all_today_sessions()` и добавить session_id, которых нет в списке от session_reader (может быть свежая сессия, которая ещё не появилась в файлах, но уже зарегистрирована)
4. **Вернуть объединённый список** — list[str] без дубликатов

## Зависимости

- **config** — `WORKING_DIR` — абсолютный путь к рабочей директории проекта. Передаётся в session_reader при каждом цикле мониторинга
- **session_reader** — `get_recent_sessions()` — получение списка последних сессий для определения, какие сессии мониторить. `get_session_messages()` — чтение сообщений конкретной сессии для обнаружения новых ответов Claude
- **daily_session_registry** — `register_session()` — получение дневного номера сессии для отображения в сообщении пользователю. `get_all_today_sessions()` — получение списка сессий из реестра для дополнения списка мониторинга
- **session_manager** — `get_chat_ids()` — получение списка chat_id всех пользователей, которым нужно отправлять обновления. Интерфейс зависимости предварительный — спецификация session_manager ещё не создана. Ожидается функция, возвращающая все активные chat_id
- **asyncio** (стандартная библиотека) — `asyncio.sleep()` для интервала между проверками, `asyncio.CancelledError` для корректного завершения
- **logging** (стандартная библиотека) — логирование событий мониторинга и ошибок

## Обработка ошибок

- **Ошибка чтения файлов сессий** — session_reader сам обрабатывает ошибки файловой системы и возвращает пустой список. Watcher получает пустой список и продолжает работу в следующем цикле. Логирование: warning в session_reader
- **Callback вызвал исключение** — watcher ловит исключение от callback, логирует через `logging.error` с текстом `"Ошибка при отправке сообщения из сессии {session_id}: {error}"` и продолжает обработку остальных сессий. Один сбой отправки не должен останавливать весь мониторинг
- **Сессия удалена с диска между итерациями** — session_reader.get_session_messages вернёт пустой список. Watcher обработает это как «сообщений нет» и удалит запись из `_seen_message_counts` при следующей очистке устаревших записей в `_poll_sessions`
- **asyncio.CancelledError** — нормальное завершение. Watcher выходит из бесконечного цикла. Логирование: info `"Мониторинг сессий остановлен"`
- **Непредвиденная ошибка в цикле мониторинга** — watcher ловит Exception, логирует через `logging.error`, ждёт `ERROR_RETRY_DELAY_SECONDS` (10 секунд) и продолжает цикл. Мониторинг не должен падать из-за единичной ошибки
- **session_manager.get_chat_ids() не вернул chat_id** — если нет активных пользователей, watcher пропускает отправку. Сообщение не теряется — при следующем цикле, если пользователь появится, watcher уже обновит свой счётчик и не повторит старые сообщения
- **pause_session для несуществующего session_id** — session_id просто добавляется в множество `_paused_sessions`. Без ошибки — паузы по несуществующему ID не вредят
- **resume_session для не-паузированного session_id** — множество `_paused_sessions.discard(session_id)` молча игнорирует отсутствующий элемент. Дополнительно watcher обновляет `_seen_message_counts` до актуального значения

## Константы

- `POLL_INTERVAL_SECONDS = 2` — интервал между проверками файлов сессий, в секундах. Значение из BRD CJM-07: «каждые 2 секунды проверяет файлы сессий». Слишком короткий интервал создаст нагрузку на диск, слишком длинный — пользователь будет ждать
- `ERROR_RETRY_DELAY_SECONDS = 10` — интервал ожидания после непредвиденной ошибки, прежде чем продолжить мониторинг. 10 секунд — достаточно, чтобы временная проблема (например, блокировка файла) разрешилась, но не слишком долго для пользователя
- `NO_RESPONSE_MARKERS = frozenset({"No response requested."})` — множество служебных ответов Claude, которые не отправляются пользователю. Вынесено во frozenset для быстрого поиска и лёгкого расширения

## Внутреннее состояние модуля

- `_seen_message_counts: dict[str, int]` — для каждой сессии хранит количество уже обработанных сообщений (строк JSONL-файла). Новые сообщения — это строки с индексом >= этого числа
- `_paused_sessions: set[str]` — множество session_id сессий, мониторинг которых приостановлен (handle_message сам обработает ответ)
- `_callback: MessageCallback` — ссылка на callback-функцию для отправки сообщений
- `_get_current_session: CurrentSessionGetter` — ссылка на функцию получения текущей сессии пользователя

## Тест-план

### Юнит-тесты

- **test_start_initializes_seen_counts** — при запуске watcher считывает текущее количество сообщений в каждой сессии и записывает как «уже обработанные»
  - Вход: 2 сессии на диске, в первой 5 сообщений, во второй 3
  - Ожидаемый результат: `_seen_message_counts == {"session-1": 5, "session-2": 3}`, callback не вызван (старые сообщения не отправляются)
  - Тип: unit

- **test_detects_new_assistant_message** — watcher обнаруживает новое сообщение Claude и вызывает callback
  - Вход: сессия с 5 сообщениями (уже видены). После первого цикла в файл добавляется новое сообщение Claude: `{"type": "assistant", "message": {"content": "Файл main.py содержит точку входа"}}`
  - Ожидаемый результат: callback вызван с `message_text="Файл main.py содержит точку входа"`
  - Тип: unit

- **test_ignores_new_user_message** — watcher не отправляет сообщения пользователя
  - Вход: в файл сессии добавляется новое сообщение пользователя: `{"type": "user", "message": {"content": "Посмотри файл main.py"}}`
  - Ожидаемый результат: callback не вызван
  - Тип: unit

- **test_pause_session_skips_monitoring** — приостановленная сессия не проверяется
  - Вход: `pause_session("session-1")`, затем в сессию добавляется новое сообщение Claude
  - Ожидаемый результат: callback не вызван для "session-1"
  - Тип: unit

- **test_resume_session_updates_seen_count** — при снятии паузы watcher обновляет счётчик до актуального значения
  - Вход: сессия с 5 сообщениями. `pause_session("session-1")`. В файл добавляется 2 новых сообщения (обработчик их отправил). `resume_session("session-1")`
  - Ожидаемый результат: `_seen_message_counts["session-1"] == 7`, callback не вызван (сообщения уже обработаны обработчиком)
  - Тип: unit

- **test_callback_receives_correct_day_number** — callback получает правильный дневной номер сессии
  - Вход: сессия зарегистрирована в daily_session_registry под номером 3. Watcher обнаруживает новое сообщение
  - Ожидаемый результат: callback вызван с `day_number=3`
  - Тип: unit

- **test_callback_receives_is_current_session_true** — callback получает is_current_session=True для текущей сессии
  - Вход: `get_current_session(chat_id)` возвращает `"session-1"`. Watcher обнаруживает новое сообщение в "session-1"
  - Ожидаемый результат: callback вызван с `is_current_session=True`
  - Тип: unit

- **test_callback_receives_is_current_session_false** — callback получает is_current_session=False для чужой сессии
  - Вход: `get_current_session(chat_id)` возвращает `"session-2"`. Watcher обнаруживает новое сообщение в "session-1"
  - Ожидаемый результат: callback вызван с `is_current_session=False`
  - Тип: unit

- **test_extract_message_text_string_content** — извлечение текста из сообщения со строковым content
  - Вход: `{"type": "assistant", "message": {"content": "Привет, вот ответ"}}`
  - Ожидаемый результат: `"Привет, вот ответ"`
  - Тип: unit

- **test_extract_message_text_list_content** — извлечение текста из сообщения с content в виде списка
  - Вход: `{"type": "assistant", "message": {"content": [{"type": "text", "text": "Часть 1"}, {"type": "text", "text": "Часть 2"}]}}`
  - Ожидаемый результат: `"Часть 1 Часть 2"`
  - Тип: unit

- **test_is_empty_response_no_response_requested** — проверка распознавания служебного ответа
  - Вход: `"No response requested."`
  - Ожидаемый результат: `True`
  - Тип: unit

- **test_is_empty_response_normal_text** — нормальный текст не считается пустым ответом
  - Вход: `"Файл main.py содержит точку входа"`
  - Ожидаемый результат: `False`
  - Тип: unit

- **test_update_session_id_transfers_state** — обновление session_id переносит счётчик и статус паузы
  - Вход: `_seen_message_counts["_new_0001"] = 5`, `pause_session("_new_0001")`. Вызов `update_session_id("_new_0001", "real-session-id")`
  - Ожидаемый результат: `_seen_message_counts["real-session-id"] == 5`, `"real-session-id" in _paused_sessions`, `"_new_0001" not in _seen_message_counts`
  - Тип: unit

- **test_get_sessions_to_monitor_combines_reader_and_registry** — объединяет сессии из session_reader и daily_session_registry
  - Вход: session_reader возвращает сессии ["A", "B"], daily_session_registry возвращает {1: "B", 2: "C"}
  - Ожидаемый результат: список содержит "A", "B", "C" (без дубликатов)
  - Тип: unit

### Граничные случаи

- **test_no_sessions_on_disk** — при отсутствии сессий watcher продолжает работать
  - Вход: session_reader возвращает пустой список, daily_session_registry возвращает пустой словарь
  - Ожидаемый результат: callback не вызван, watcher не упал, продолжает цикл
  - Тип: edge case

- **test_session_deleted_between_cycles** — сессия удалена с диска между циклами мониторинга
  - Вход: в первом цикле session_reader возвращает ["session-1"], во втором — пустой список. session_reader.get_session_messages для "session-1" возвращает пустой список
  - Ожидаемый результат: запись "session-1" удалена из `_seen_message_counts`, ошибки нет
  - Тип: edge case

- **test_multiple_new_messages_in_one_cycle** — несколько новых сообщений Claude обнаружены за один цикл
  - Вход: в файл сессии добавлено 3 новых сообщения Claude за период между проверками
  - Ожидаемый результат: callback вызван 3 раза, по одному для каждого сообщения, в правильном порядке
  - Тип: edge case

- **test_paused_session_other_sessions_monitored** — пауза одной сессии не влияет на мониторинг других
  - Вход: `pause_session("session-1")`. В "session-2" появляется новое сообщение Claude
  - Ожидаемый результат: callback вызван для "session-2", не вызван для "session-1"
  - Тип: edge case

- **test_empty_response_not_sent** — пустой ответ Claude не отправляется пользователю
  - Вход: в файл сессии добавляется сообщение Claude с текстом `""` (пустая строка)
  - Ожидаемый результат: callback не вызван
  - Тип: edge case

- **test_no_response_requested_not_sent** — служебный ответ "No response requested." не отправляется
  - Вход: в файл сессии добавляется сообщение Claude с текстом `"No response requested."`
  - Ожидаемый результат: callback не вызван
  - Тип: edge case

- **test_resume_without_pause_no_error** — вызов resume_session без предшествующего pause_session не вызывает ошибку
  - Вход: `resume_session("session-1")` — сессия не была на паузе
  - Ожидаемый результат: нет ошибки, `_paused_sessions` не содержит "session-1"
  - Тип: edge case

- **test_pause_nonexistent_session_no_error** — пауза несуществующей сессии не вызывает ошибку
  - Вход: `pause_session("nonexistent-session")`
  - Ожидаемый результат: "nonexistent-session" добавлена в `_paused_sessions`, ошибки нет
  - Тип: edge case

- **test_update_session_id_nonexistent_old_id** — обновление несуществующего session_id ничего не ломает
  - Вход: `update_session_id("nonexistent", "new-id")`
  - Ожидаемый результат: нет ошибки, `_seen_message_counts` не содержит "nonexistent" и не содержит "new-id"
  - Тип: edge case

### Тесты ошибок

- **test_callback_exception_does_not_stop_monitoring** — ошибка в callback не останавливает мониторинг
  - Вход: callback выбрасывает `RuntimeError("Telegram API error")`. В двух сессиях появляются новые сообщения
  - Ожидаемый результат: callback вызван для обеих сессий, ошибка залогирована через `logging.error`, мониторинг продолжается
  - Тип: error

- **test_session_reader_error_does_not_stop_monitoring** — ошибка чтения файла не останавливает мониторинг
  - Вход: session_reader.get_session_messages выбрасывает `OSError` для одной сессии
  - Ожидаемый результат: ошибка залогирована, мониторинг остальных сессий продолжается
  - Тип: error

- **test_cancelled_error_stops_gracefully** — asyncio.CancelledError корректно завершает мониторинг
  - Вход: задача watcher отменяется через `task.cancel()`
  - Ожидаемый результат: watcher завершается без ошибки, в логах info `"Мониторинг сессий остановлен"`
  - Тип: error

- **test_unexpected_error_retries_after_delay** — непредвиденная ошибка не останавливает мониторинг
  - Вход: `_poll_sessions` выбрасывает `RuntimeError`. Мокнуть asyncio.sleep
  - Ожидаемый результат: ошибка залогирована через `logging.error`, `asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)` вызван, мониторинг продолжается
  - Тип: error

- **test_daily_registry_error_does_not_stop_monitoring** — ошибка в daily_session_registry не останавливает мониторинг
  - Вход: `daily_session_registry.register_session` выбрасывает `OSError`. Watcher обнаруживает новое сообщение
  - Ожидаемый результат: ошибка залогирована, мониторинг продолжается в следующем цикле
  - Тип: error
