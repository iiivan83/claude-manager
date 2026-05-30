# Архитектура Claude Manager

## Контекст

Описание архитектуры Telegram-бота для управления Claude Code с телефона. Бот работает локально на компьютере пользователя, общается с Claude Code CLI через протокол stream-json (потоковый JSON через stdin/stdout).

Дата: 29-03-2026

## Общая схема

Бот принимает сообщения из Telegram, передаёт их в долгоживущий процесс Claude Code CLI, читает ответы из stdout и отправляет обратно в Telegram. Параллельно работает система мониторинга: `session_watcher` следит за файлами сессий активного проекта, а `all_projects_monitor` в режиме `/all` проверяет сессии всех доступных проектов.

## Слоёная архитектура

Код разделён на четыре слоя с чёткими границами ответственности. Верхний слой может вызывать нижний, но не наоборот.

### Транспортный слой — `bot.py`

Точка входа для всех событий из Telegram. Содержит обработчики команд (`/new`, `/sessions`, `/connect` и другие), обработчики текстовых сообщений и фотографий. Управляет watchers (мониторингом), очередями сообщений, эпохами сессий и координацией между компонентами.

Импортирует все остальные модули проекта. Это единственный модуль, который знает о Telegram API (`python-telegram-bot`).

### Слой бизнес-логики — `session_manager.py`, `daily_session_registry.py`

Управление привязками chat_id к session_id и дневная нумерация сессий (#1, #2, #3...). Не знает ни про Telegram, ни про процессы Claude. Импортирует только `config`.

- **SessionManager** — хранит связку chat_id к session_id в памяти и на диске (`sessions.json`)
- **DailySessionRegistry** — присваивает сессиям последовательные номера в пределах дня, сбрасывает нумерацию в полночь, хранит данные в `daily_sessions.json`

### Слой инфраструктуры — `process_manager.py`, `process_state.py`, `claude_runner.py`

Запуск и управление долгоживущими процессами Claude Code CLI. Протокол stream-json: запись в stdin, чтение из stdout, маршрутизация событий. Не знает про Telegram.

- **process_manager** — оркестратор lifecycle: запуск CLI-процессов, отправка сообщений, чтение событий, retry и `/stop`
- **process_state** — in-memory state процессов: словари процессов, busy-флаги, stop-events, alias temp→real session_id и атомарный перенос ключей
- **claude_runner** — тонкая обёртка: предоставляет функцию `run_claude()` и синглтон ProcessManager

### Утилиты и мониторинг — `message_splitter.py`, `session_reader.py`, `session_watcher.py`, `all_projects_monitor.py`

Вспомогательные модули без состояния или с минимальным состоянием.

- **message_splitter** — разбивка длинных сообщений на части до 4096 символов с починкой HTML-тегов
- **session_reader** — чтение файлов сессий Claude с диска (`~/.claude/projects/<encoded_path>/`)
- **session_watcher** — мониторинг файлов сессий активного проекта в реальном времени
- **all_projects_monitor** — глобальный мониторинг всех проектов для режима `/all`; хранит отдельные cursor-счётчики и link registry для команд вида `/3s12`

### Конфигурация — `config.py`

Загрузка настроек из `.env` через `python-dotenv`. Экспортирует константы: токен бота, белый список пользователей, рабочую папку, пути к файлам, таймауты. Функция `validate_config()` проверяет обязательные параметры при запуске.

## Модули и их ответственности

### `config.py` — загрузка настроек

Экспортируемые константы:
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота
- `ALLOWED_USER_IDS` — множество разрешённых пользователей
- `CLAUDE_WORKING_DIR` — рабочая папка проекта для Claude
- `DEFAULT_TOOLS_MODE` — режим инструментов по умолчанию (`"project_only"` или `"full"`)
- `SESSIONS_FILE_PATH` — путь к `sessions.json`
- `PID_FILE_PATH` — путь к `bot.pid`
- `DAILY_SESSIONS_FILE_PATH` — путь к `daily_sessions.json`
- `IDLE_TIMEOUT_SECONDS` — таймаут бездействия (по умолчанию 7200 секунд, то есть 2 часа)
- `EXTERNAL_SESSION_ACTIVITY_THRESHOLD` — порог определения активной внешней сессии (по умолчанию 30 секунд)

### `bot.py` — обработчики Telegram-команд

Глобальное состояние распределено по профильным модулям:
- `session_manager` — активная сессия чата и backend этой сессии
- `daily_session_registry` — дневные номера сессий
- `process_manager` — живые процессы CLI-агентов и флаги занятости
- `session_watcher` — cursor-счётчики активного проекта и пауза мониторинга
- `silence_mode_registry` — глобальный режим подавления промежуточных сообщений
- `all_projects_monitor._enabled_chat_ids` — чаты, которые сейчас получают глобальный `/all`
- `all_projects_monitor._states` — cursor-счётчики глобального мониторинга по ключу `(project_path, session_id, backend)`
- `all_projects_monitor._links` — соответствие кликабельных команд `/<project_number>s<session_number>` точному проекту, session_id и backend

Команды:
- `/start` — приветствие или подключение по deep link (`/start connect_SESSION_ID`)
- `/new` — сбросить сессию (увеличить эпоху, остановить процесс, очистить очередь)
- `/all` — включить глобальный мониторинг всех проектов через `all_projects_monitor`
- `/sessions` — показать 5 последних сессий с дневными номерами
- `/connect N` или `/N` — подключиться к сессии по дневному номеру
- `/status` — текущее состояние: сессия, режим, рабочая папка
- `/project /path` — сменить рабочую папку
- `/full` и `/project_only` — переключение режима инструментов
- `/stop` — остановить текущий процесс Claude
- `/watch` — включить/выключить мониторинг текущей сессии
- `/help` — список доступных команд

Ключевые константы:
- `PROGRESS_MIN_INTERVAL_SECONDS = 30` — минимальный интервал между промежуточными сообщениями
- `STOP_WORDS = {"стоп", "отмена", "stop", "cancel"}` — слова для мгновенной остановки Claude

### `process_manager.py` и `process_state.py` — управление процессами Claude

Центральная инфраструктурная пара модулей. `process_manager.py` управляет поведением процесса: запуском Claude/Codex CLI, отправкой сообщения, чтением stream-json событий, retry-циклом и `/stop`. `process_state.py` хранит только in-memory state процесса и атомарные helper-функции для ключей и alias-ов.

Ключевые структуры данных:
- `SendResult` (dataclass) — результат отправки сообщения в CLI: текст ответа, session_id, backend, флаг ошибки, число retry и тип постоянной ошибки
- `StopResult` (dataclass) — результат `/stop`: был ли процесс живым, был ли retry, какой backend остановлен
- `ProcessKey` — ключ состояния: строковый session_id для legacy Claude-only пути или tuple `(session_id, backend)` для backend-aware пути
- `ManagedProcess` — общий тип для старого `ClaudeProcess` и нового `BackendSubprocess`

Внутреннее состояние:
- `process_state._processes` — ключ процесса к живому subprocess wrapper
- `process_state._busy_flags` — флаги занятости процесса
- `process_state._stop_events` — события отмены для `/stop` и прерывания ожидания retry
- `process_state._session_id_aliases` — alias temp session id → real session id после первого события CLI
- `process_state._busy_lock` — общий `asyncio.Lock` для коротких атомарных секций

Ключевые константы:
- `MAX_RETRIES = 10` — максимум повторов при временной ошибке CLI
- `RETRY_INTERVAL_SECONDS = 60` — пауза между retry
- `PROGRESS_THROTTLE_SECONDS = 30` — минимальный интервал между промежуточными progress-сообщениями

`process_manager.py` реэкспортирует state-объекты из `process_state.py`, чтобы старые тесты и приватный доступ вида `process_manager._processes` продолжали работать. Это временная compatibility-граница первого разреза: поведение запуска, retry и `/stop` не меняется.

### `claude_runner.py` — обёртка над ProcessManager

Тонкий фасад: предоставляет функцию `run_claude()` и синглтон ProcessManager через `get_process_manager()`. Функция `shutdown()` останавливает все процессы при выключении бота.

### `session_manager.py` — привязка chat_id к session_id

Класс `SessionManager` хранит связку chat_id к session_id в памяти (словарь) и на диске (`sessions.json`). Методы: `get_session()`, `save_session()`, `reset_session()`.

### `daily_session_registry.py` — дневная нумерация сессий

Класс `DailySessionRegistry` присваивает сессиям последовательные номера (#1, #2, #3...) в пределах дня. Автоматически сбрасывает нумерацию в полночь через `_check_date_rollover()`. Поддерживает предварительное резервирование номера через `allocate_number()` с последующей привязкой через `bind_session()`.

Формат `daily_sessions.json`:
- `date` — текущая дата (ISO)
- `next_number` — следующий свободный номер
- `sessions` — словарь session_id к дневному номеру

### `message_splitter.py` — разбивка длинных сообщений

Функция `split_message()` разбивает текст на части до 4096 символов (лимит Telegram). Приоритет точек разрыва: пустая строка, перенос строки, пробел, жёсткий разрез. После разрыва автоматически чинит разорванные HTML-теги (`<b>`, `<i>`, `<code>`, `<pre>`) — закрывает в конце текущей части, открывает в начале следующей.

### `session_reader.py` — чтение сессий с диска

Читает файлы сессий Claude из `~/.claude/projects/<encoded_path>/`. Класс `SessionInfo` (dataclass): `session_id`, `slug`, `timestamp`, `first_message`, `cwd`. Функция `list_recent_sessions()` возвращает последние 5 сессий. Кодирование пути проекта: `/Users/ivan/Desktop/project` превращается в `-Users-ivan-Desktop-project`.

### `session_watcher.py` — мониторинг активного проекта в реальном времени

Наблюдатель за файлами сессий активного проекта:

- **SessionWatcher** — следит за одним файлом `.jsonl`. Каждые 2 секунды проверяет, вырос ли файл. Читает новые строки, ищет сообщения assistant, конвертирует Markdown в HTML через `markdown_to_html()` и отправляет через callback. Поддерживает паузу/возобновление для координации с handle_message.

Функция `markdown_to_html()`: разделяет текст на блоки кода и обычный текст. Блоки кода оборачиваются в `<pre>`, обычный текст: `**жирный**` в `<b>`, инлайн-код в `<code>`.

### `all_projects_monitor.py` — глобальный мониторинг всех проектов

Отдельный монитор для режима `/all`. Каждые 2 секунды сканирует все проекты из `PROJECTS_ROOT_DIR` и все настроенные CLI-бэкенды, но не продвигает cursor-счётчики обычного `session_watcher`.

Ключевые правила:
- Показывает сообщения с префиксом `/<project_number>s<session_number> <project_name>`
- Хранит link registry, чтобы команда `/3s12` резолвилась в точный проект, session_id и backend
- Перед показом сообщения сохраняет snapshot в `unread_buffer`, поэтому сообщение остаётся pending для исходного проекта
- При ошибке сканирования одного проекта или backend-а логирует ошибку и продолжает проверять остальные
- При неудачном выходе из `/all` через `/pN` или `/3s12` global all восстанавливается, а обычный watcher не возобновляется поверх него

## Поток данных: сообщение пользователя

Полный путь сообщения от Telegram до Claude Code CLI и обратно:

**1. Приём сообщения (bot.py)**
- Telegram отправляет update в бота
- `handle_message()` проверяет доступ пользователя (белый список)
- Проверяет наличие активной сессии и режим наблюдения
- Если Claude занят — проверяет на команду стопа, иначе добавляет в очередь

**2. Подготовка к отправке (bot.py)**
- Проверяется, не находится ли чат в global all-mode; из этого режима отправка агенту блокируется до выбора проекта и сессии
- `claude_interaction.send_message_to_agent()` координирует отправку с `session_watcher`, чтобы пользователь не получил один ответ дважды
- Вызывается `_send_to_claude_and_respond()` — основная логика доставки запроса агенту и ответа пользователю

**3. Отправка в Claude (claude_runner.py, process_manager.py, process_state.py)**
- `run_claude()` вызывает `ProcessManager.send_message()`
- Если нет живого процесса — создаёт новый через `subprocess.Popen`:
  ```
  claude -p --input-format stream-json --output-format stream-json --verbose
  --resume SESSION_ID  (для существующих сессий)
  --name "#1"          (для новых сессий)
  --allowedTools ...   (в режиме project_only)
  ```
- Формирует JSON-сообщение и записывает в stdin процесса
- Запускает фоновую задачу `_stdout_reader_loop()` для чтения ответов

**4. Обработка ответов (process_manager.py, process_state.py)**
- Фоновый reader читает строки из stdout, парсит JSON
- Маршрутизирует по типу события:
  - `system (init)` — первое событие с реальным session_id, обновляет ключи во всех словарях
  - `assistant` — промежуточные ответы, вызывает callback `on_progress`
  - `result` — финальный ответ, кладёт в `response_queue`
  - `control_request` — запрос разрешения на инструмент, автоматически отвечает по режиму
- `send_message()` ожидает результат из очереди с таймаутом 30 минут

**5. Возврат ответа (bot.py)**
- Проверяет эпоху — если пользователь переключил сессию, ответ не сохраняется
- Сохраняет session_id через SessionManager
- Регистрирует в DailySessionRegistry (получает номер #N)
- Конвертирует Markdown в HTML через `markdown_to_html()`
- Добавляет заголовок `<b>#N</b>` с дневным номером сессии
- Разбивает на части через `split_message()` (лимит 4096 символов)
- Отправляет в Telegram через `_send_html_with_fallback()` (при ошибке парсинга HTML — fallback на plain text)
- Обрабатывает очередь накопленных сообщений через `_drain_message_queue()`
- Возобновляет watchers через `_resume_watcher()`

## Протокол stream-json

Бот общается с Claude Code CLI через потоковый JSON — каждая строка stdin/stdout содержит один JSON-объект.

### Отправка в Claude (stdin)

Пользовательское сообщение:
```json
{
  "type": "user",
  "session_id": "",
  "message": {"role": "user", "content": "текст пользователя"},
  "parent_tool_use_id": null
}
```

Ответ на запрос разрешения:
```json
{
  "type": "control_response",
  "request_id": "req-123",
  "permission": "allow"
}
```

### Получение от Claude (stdout)

Инициализация (первое событие с реальным session_id):
```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "реальный-session-id"
}
```

Промежуточный ответ:
```json
{
  "type": "assistant",
  "session_id": "...",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "thinking", "thinking": "рассуждения Claude"},
      {"type": "text", "text": "видимый текст ответа"}
    ]
  }
}
```

Финальный результат:
```json
{
  "type": "result",
  "session_id": "...",
  "result": "финальный текст ответа",
  "is_error": false,
  "subtype": ""
}
```

Запрос разрешения на инструмент:
```json
{
  "type": "control_request",
  "request_id": "req-456",
  "request": {"tool_name": "Bash"}
}
```

## Управление состоянием

### Эпоха сессии — защита от перезаписи

Проблема: пользователь отправляет сообщение, а пока Claude обрабатывает ответ — переключает сессию командой `/new` или `/connect`. Без защиты ответ от старой сессии перезапишет привязку новой.

Решение — счётчик эпохи (`chat_session_epoch`):
- При `/new`, `/all`, `/connect` — эпоха увеличивается
- Перед отправкой запроса — запоминается текущая эпоха
- Когда ответ пришёл — сравниваются эпохи
- Если совпадают — всё в порядке, session_id сохраняется
- Если не совпадают — пользователь переключился, ответ отправляется но session_id не сохраняется

### Координация watchers и handle_message

Проблема: SessionWatcher читает тот же файл сессии, в который Claude пишет ответ. Без координации пользователь получит ответ дважды — от watcher и от handle_message.

Решение — счётчик активных запросов (`chat_active_requests`):
- `_pause_watcher()` увеличивает счётчик и вызывает `pause()` на watchers
- Watchers продолжают читать файл (отслеживают позицию), но не отправляют сообщения
- `_resume_watcher()` уменьшает счётчик
- При счётчике = 0 — watchers возобновляются и перемещают позицию на конец файла, пропуская уже отправленный ответ

### Очередь сообщений

Когда Claude занят обработкой запроса:
- Новое сообщение добавляется в `chat_pending_messages[chat_id]`
- Пользователь получает подтверждение ("в очереди")
- Исключение: команда стопа ("стоп", "отмена", "stop", "cancel") — мгновенно убивает процесс
- После ответа Claude — `_drain_message_queue()` достаёт все накопленные сообщения, склеивает их в одно ("Пока ты работал... Сообщение 1: ... Сообщение 2: ...") и отправляет
- Если за время обработки очереди пришли новые сообщения — рекурсивная обработка

### Режим наблюдения

Когда пользователь подключается к сессии, которая активно работает в терминале:
- Проверяется время последнего обновления файла сессии
- Если менее 30 секунд — сессия активна в терминале
- Устанавливается `chat_monitor_only[chat_id] = True`
- Текстовые сообщения и фотографии блокируются
- SessionWatcher работает — пользователь видит ответы Claude из терминала в Telegram
- Если файл не обновлялся более 30 секунд — флаг автоматически снимается

## Жизненный цикл процесса Claude

### Создание

При первом сообщении в сессию (или после смерти процесса) ProcessManager создаёт новый процесс:
- `subprocess.Popen()` с `stdin=PIPE`, `stdout=PIPE`, `stderr=PIPE`
- Для новых сессий — временный ключ `_new_XXXX` (первые 12 символов UUID)
- Для существующих — флаг `--resume SESSION_ID`
- Запускается фоновая задача `_stdout_reader_loop()`

### Обновление session_id

При получении `system (init)` с реальным session_id — переключаются ключи во всех словарях:
- `_processes[real_id] = _processes.pop(_new_XXXX)`
- `_reader_tasks[real_id] = _reader_tasks.pop(_new_XXXX)`
- `_session_locks[real_id] = _session_locks.pop(_new_XXXX)`

### Жизнь

Процесс живёт и принимает сообщения через stdin. Между запросами `is_busy = False`. `last_activity_at` обновляется при каждом сообщении.

### Очистка

Каждые 60 секунд `_idle_cleanup_loop()` проверяет все процессы:
- Мёртвые — удаляются из отслеживания
- Бездействующие (более 2 часов без запроса) — убиваются
- Живые и занятые — оставляются

### Остановка

При `stop_process()`:
- Закрывается stdin
- Отправляется SIGTERM, ожидание 5 секунд
- Если не завершился — SIGKILL
- Удаление из всех словарей

## Режимы инструментов

### `project_only` (по умолчанию)

Безопасный режим — Claude может работать только с файлами:
- Разрешённые инструменты: Read, Glob, Grep, Edit, Write
- Все остальные (Bash, BrowserUse и другие) — запрещены

Механизм: Claude отправляет `control_request` с названием инструмента. ProcessManager проверяет, входит ли инструмент в список `PROJECT_ONLY_TOOLS`, и отвечает `"allow"` или `"deny"`.

### `full`

Полный доступ — все инструменты разрешены. Claude может выполнять команды в терминале. Переключается командой `/full`, обратно — `/project_only`.

## Асинхронная модель

Весь код — async/await на asyncio. Ключевые точки:

- **Обработчики Telegram** — все async, вызываются фреймворком python-telegram-bot
- **Чтение stdout** — `_stdout_reader_loop()` работает в отдельном потоке через `asyncio.to_thread()` (блокирующий `readline()`)
- **Ожидание ответа** — `asyncio.wait_for(response_queue.get(), timeout=1800)` — неблокирующее ожидание с таймаутом
- **Watcher polling** — `asyncio.sleep(2)` между проверками файлов
- **Cleanup loop** — `asyncio.sleep(60)` между проверками бездействующих процессов
- **Блокировки** — `asyncio.Lock()` для предотвращения одновременных запросов в один процесс
- **stdin** — защита через `stdin_lock` (threading.Lock), так как запись происходит из разных async-задач

## Файлы, создаваемые при работе

- `sessions.json` — привязка chat_id к session_id (в памяти + на диске)
- `daily_sessions.json` — дневная нумерация (дата, следующий номер, session_id к номеру)
- `bot.pid` — PID файл с блокировкой fcntl (защита от двойного запуска)
- `bot.log` — логи работы
- `received_photos/` — фотографии от пользователя (`photo_YYYYMMDD_HHMMSS_uniqueId.jpg`)
- `system_prompt.md` — системный промпт для Claude

## Обработка ошибок

- **Claude CLI не найден** — ProcessDeadError, сообщение пользователю
- **BrokenPipeError** — процесс умер, предложение повторить (перезапустится автоматически)
- **Таймаут 30 минут** — убийство процесса, сообщение о зависании
- **Пустой result** — логирование и fallback-сообщение
- **HTML не парсится** — fallback на plain text (удаление тегов)
- **Conflict от Telegram** — завершение бота (два экземпляра на одном токене)
- **Синтетический ответ "No response requested."** — игнорируется как пустой
- **Потеря сессии** — если ошибка содержит "session", сброс session_id, следующий запрос создаст новую сессию

## Защита от двойного запуска

Файловая блокировка через `fcntl.flock()` на файле `bot.pid`. Функция `_acquire_pid_lock()` захватывает эксклюзивную блокировку при старте — если другой экземпляр уже работает, запуск невозможен. Дескриптор хранится в глобальной переменной `_lock_file_descriptor`, чтобы блокировка не снялась преждевременно при сборке мусора.
