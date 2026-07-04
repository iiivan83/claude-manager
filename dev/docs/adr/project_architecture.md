# Архитектура Claude Manager

## Контекст

Описание архитектуры Telegram-бота для управления Claude Code с телефона. Бот работает локально на компьютере пользователя, общается с Claude Code CLI через протокол stream-json (потоковый JSON через stdin/stdout).

Дата: 29-03-2026

## Общая схема

Бот принимает сообщения из Telegram, передаёт их в долгоживущий процесс Claude Code CLI, читает ответы из stdout и отправляет обратно в Telegram. Параллельно работает система мониторинга: `session_watcher` следит за файлами сессий активного проекта, а `all_projects_monitor` в режиме `/all` проверяет сессии всех доступных проектов.

## Слоёная архитектура

Код разделён на четыре слоя с чёткими границами ответственности. Верхний слой может вызывать нижний, но не наоборот.

### Транспортный слой — `bot.py` и `telegram_*_handlers.py`

Точка входа для событий из Telegram разделена на facade и профильные handler-модули. `bot.py` остаётся тонкой точкой сборки: создаёт `Application` из `python-telegram-bot`, регистрирует handlers, подключает глобальный error handler, инициализирует callback-зависимости и сохраняет compatibility re-export старых публичных имён.

Логика Telegram-сценариев живёт в отдельных модулях по ответственности:

- **`telegram_agent_handlers.py`** — выбор CLI-бэкенда через `/agent` и inline-кнопки
- **`telegram_session_handlers.py`** — `/new`, `/sessions`, `/all`, `/stop` и переключение сессий по дневному номеру
- **`telegram_input_handlers.py`** — текстовые сообщения, фото, документы и reply anchor для прямого ответа
- **`telegram_lifecycle_handlers.py`** — `post_init`, watcher callbacks, `/restart`, `silence mode`
- **`telegram_project_handlers.py`** — `/projects`, `/pN` и команды вида `/<project>s<session>`
- **`telegram_response_delivery.py`** — доставка ответов в Telegram: заголовки, markdown/html, файловые маркеры и reply anchors
- **`reply_route_handler.py`** — входящие Telegram reply на сообщения бота: поиск исходной сессии, отправка текста туда без переключения проекта, понятные ошибки
- **`reply_route_registry.py`** — bounded реестр маршрутов `chat_id + bot_message_id -> project_path + session_id + backend`, который держит оперативную копию в памяти и сохраняет последние 200 маршрутов на диск

Handler-модули не импортируют `bot.py`. Если им нужен Telegram `Application` или проверка доступа, `bot.py` передаёт эти зависимости через `init_callbacks`. Так транспортный слой знает о Telegram API, но не складывает все сценарии в один большой god-module.

### Слой бизнес-логики — `session_manager.py`, `daily_session_registry.py`

Управление привязками chat_id к session_id и дневная нумерация сессий (#1, #2, #3...). Не знает ни про Telegram, ни про процессы Claude. Импортирует только `config`.

- **SessionManager** — хранит связку chat_id к session_id в памяти и на диске (`sessions.json`)
- **DailySessionRegistry** — присваивает сессиям последовательные номера в пределах дня, сбрасывает нумерацию в полночь, хранит данные в `daily_sessions.json`

### Слой инфраструктуры — `process_manager.py`, `process_*`, `process_state.py`, `claude_runner.py`

Запуск и управление долгоживущими процессами Claude Code CLI. Протокол stream-json: запись в stdin, чтение из stdout, маршрутизация событий. Не знает про Telegram.

- **process_manager** — compatibility facade: сохраняет старые импорты и собирает публичный контракт lifecycle-модулей
- **process_types** — общие типы, dataclass-результаты и callback-типы process lifecycle
- **process_events** — чтение stream-json событий, progress/result и обновление session_id
- **process_stop** — `/stop`, стратегия остановки процессов и остановка всех процессов при shutdown
- **process_lifecycle** — temp session id, запуск subprocess и restart
- **process_retry** — retry-loop, ожидание с проверкой `/stop` и классификация permanent errors
- **process_send** — `send_message` dispatcher и legacy Claude send path
- **process_backend_send** — backend-aware Claude/Codex send path
- **process_state** — in-memory state процессов: словари процессов, busy-флаги, stop-events, alias temp→real session_id и атомарный перенос ключей
- **claude_runner** — тонкая обёртка: предоставляет функцию `run_claude()` и синглтон ProcessManager

### Утилиты, индексы и мониторинг

Вспомогательные модули без состояния или с минимальным состоянием.

- **message_splitter** — разбивка длинных сообщений на части до 4096 символов с починкой HTML-тегов
- **session_reader** — чтение файлов сессий Claude с диска (`~/.claude/projects/<encoded_path>/`)
- **recent_sessions_store** — постоянный SQLite-индекс быстрых заголовков последних сессий
- **recent_sessions_refresh** — ограниченное обновление индекса без полного scan-а истории в пользовательском hot path
- **session_watcher** — мониторинг файлов сессий активного проекта в реальном времени
- **all_projects_monitor** — глобальный мониторинг всех проектов для режима `/all`; берёт snapshot кандидатов из `recent_sessions`, хранит отдельные cursor-счётчики и link registry для команд вида `/3s12`

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

### `bot.py` и Telegram handler-модули

`bot.py` больше не владеет реализацией пользовательских Telegram-сценариев. Он отвечает за wiring:

- создание `Application`
- регистрацию `CommandHandler`, `MessageHandler` и `CallbackQueryHandler`
- подключение `_global_error_handler`
- передачу callback-зависимостей в handler-модули
- совместимые re-export-ы старых имён из `claude_manager.bot`

Глобальное состояние распределено по профильным модулям:

- `session_manager` — активная сессия чата и backend этой сессии
- `daily_session_registry` — дневные номера сессий
- `process_manager`, `process_*` и `process_state` — живые процессы CLI-агентов, busy-флаги, stop-events, retry, `/stop` и alias temp→real session id
- `session_watcher` — cursor-счётчики активного проекта и пауза мониторинга
- `silence_mode_registry` — глобальный режим подавления промежуточных сообщений
- `all_projects_monitor` — чаты в global `/all`, cursor-счётчики по проекту/сессии/backend и link registry для команд `/<project_number>s<session_number>`

Команды и входы распределены так:

- **`telegram_agent_handlers.py`** — `/agent` и callback data `agent:<backend>`
- **`telegram_session_handlers.py`** — `/new`, `/sessions`, `/all`, `/all_projects`, `/stop`, `/N`
- **`telegram_input_handlers.py`** — обычный текст, фото, документы, текстовые команды `silence on/off`; перед обычной обработкой проверяет routed reply на сообщение бота
- **`telegram_lifecycle_handlers.py`** — `/restart`, `/silence_on`, `/silence_off`, `post_init` и callbacks watchers
- **`telegram_project_handlers.py`** — `/projects`, `/pN`, `/<project>s<session>`

### `process_manager.py`, `process_*` и `process_state.py` — управление процессами Claude

Центральная инфраструктурная группа модулей. `process_manager.py` остаётся compatibility facade для старых импортов, а реальное поведение процесса разнесено по ответственности: запуск и restart, отправка, чтение stream-json событий, retry-цикл и `/stop`. `process_state.py` хранит только in-memory state процесса и атомарные helper-функции для ключей и alias-ов.

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

`process_manager.py` реэкспортирует state-объекты из `process_state.py` и lifecycle-имена из `process_*` модулей, чтобы старые тесты и приватный доступ вида `process_manager._processes` продолжали работать. Это compatibility-граница: внешний контракт сохраняется, но внутри инфраструктурный слой больше не держит запуск, retry, чтение событий и `/stop` в одном файле.

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

- **SessionWatcher** — следит за одним файлом `.jsonl`. Каждые 2 секунды проверяет, вырос ли файл. Читает новые строки, ищет сообщения assistant, конвертирует Markdown в HTML через `markdown_to_html()` и отправляет через callback. Поддерживает паузу/возобновление для координации с `telegram_input_handlers.handle_message`.
- **Reset baseline** — при переключении проекта watcher не должен разбирать всю историю сообщений заново. `reset_state()` берёт лёгкий cursor через backend-метод `read_session_file_cursor()`: это счётчик JSONL-записей, `mtime` и признак активного хода без полного списка сообщений. Для таких baseline используется sentinel `parsed_message_count = -1`; следующий poll доставляет только сообщения с `raw_record_index` больше сохранённого raw cursor, чтобы старые ответы не приехали повторно.

Функция `markdown_to_html()`: разделяет текст на блоки кода и обычный текст. Блоки кода оборачиваются в `<pre>`, обычный текст: `**жирный**` в `<b>`, инлайн-код в `<code>`.

### `recent_sessions_store.py` и `recent_sessions_refresh.py` — быстрый индекс последних сессий

Эта пара модулей нужна, чтобы пользовательские команды `/sessions` и `/all` не запускали повторный полный обход истории внешних CLI. Для Codex это особенно важно: rollout-файлы лежат в общей папке `~/.codex/sessions/YYYY/MM/DD/`, а принадлежность к проекту определяется только после чтения metadata из JSONL.

`recent_sessions_store.py` хранит быстрые заголовки сессий в SQLite-файле `~/.local/state/claude-manager/recent_sessions.sqlite3`. Таблица `recent_sessions` содержит только metadata: `project_path`, `backend`, `session_id`, `file_path`, `last_modified_at`, preview и технические поля refresh. Полный текст сообщений остаётся в исходных session files и не дублируется в SQLite.

Ключевые правила storage:
- ключ строки — `(project_path, backend, session_id)`;
- после upsert таблица хранит максимум 30 самых свежих сессий на проект across all backends;
- user-facing индекс содержит только пользовательские сессии. Codex rollout считается субагентским, если в `session_meta.payload` стоит `thread_source: subagent` или есть `source.subagent`; такие строки не попадают в bounded listing, operational index и скрываются из уже сохранённого `recent_sessions`;
- сортировка стабильная: сначала `last_modified_at DESC`, затем `updated_at DESC`, затем backend и session_id;
- cursor-состояние хранится отдельно в `session_cursor_state`, чтобы pruning заголовков не удалил данные, нужные для защиты от повторной доставки;
- SQLite-операции идут через async facade с `asyncio.to_thread`, write lock и `busy_timeout`, чтобы не блокировать event loop Telegram-бота.

`recent_sessions_refresh.py` отвечает за bounded refresh. Он может обновить один проект для `/sessions` или список проектов для `/all`, но не превращает пользовательский hot path в полный historical scan. Если индекс уже содержит строки, `/sessions` отвечает из storage сразу и может запустить фоновое обновление.

### `all_projects_monitor.py` — глобальный мониторинг всех проектов

Отдельный монитор для режима `/all`. При включении режима он берёт capped snapshot кандидатов из `recent_sessions`, строит baseline cursor для каждого файла и дальше каждые 2 секунды проверяет только известные session files по `mtime` и cursor-ам. Он не пересобирает список проектов и не вызывает backend discovery в poll-loop.

Ключевые правила:
- Показывает сообщения с префиксом `/<project_number>s<session_number> <project_name>`
- Хранит link registry, чтобы команда `/3s12` резолвилась в точный проект, session_id и backend
- Перед показом сообщения сохраняет snapshot в `unread_buffer`, поэтому сообщение остаётся pending для исходного проекта
- Если файл кандидата исчез, монитор убирает его из snapshot и продолжает проверять остальные
- При неудачном выходе из `/all` через `/pN` или `/3s12` global all восстанавливается, а обычный watcher не возобновляется поверх него

### `reply_route_registry.py` и `reply_route_handler.py` — адресные Telegram reply

Эта пара модулей отвечает за обратный маршрут от сообщения бота в Telegram к исходной проектной сессии.

`reply_route_registry.py` держит оперативную копию route-данных в памяти процесса и сохраняет последние 200 маршрутов в `~/.local/state/claude-manager/reply_routes.json`. Ключ — Telegram `chat_id` и `message_id` отправленного ботом сообщения. Значение — `project_path`, `session_id`, `backend`, локальный номер сессии и, если доступно, номер проекта для ссылки вида `/3s12`. На старте `telegram_lifecycle_handlers.post_init()` загружает файл обратно, чтобы reply на сообщение бота до штатного `/restart` продолжал попадать в исходную сессию.

Если persisted route неизвестен, handler не должен угадывать цель по текущей активной сессии. Для reply на сообщение текущего бота он показывает подсказку перейти по ссылке вручную, а обычные reply на пользовательские сообщения продолжает отдавать стандартному обработчику текста.

`reply_route_handler.py` обрабатывает входящие reply до обычных `/all`-ограничений. Если пользователь ответил текстом на маршрутизируемое сообщение бота, handler сразу подтверждает передачу, помечает цель во внутреннем in-flight registry и запускает фоновую отправку текста в сохранённые `cwd`, `session_id` и `backend`. Handler не вызывает переключение проекта и не меняет активную сессию пользователя. Если reply содержит фото, документ или альбом, handler отклоняет его до скачивания файла: в первой версии адресные reply поддерживают только текст.

## Поток данных: сообщение пользователя

Полный путь сообщения от Telegram до Claude Code CLI и обратно:

**1. Приём сообщения (`telegram_input_handlers.py`)**
- Telegram отправляет update в бота
- `telegram_input_handlers.handle_message()` проверяет доступ пользователя (белый список)
- Проверяет наличие активной сессии и режим наблюдения
- Если Claude занят — проверяет на команду стопа, иначе добавляет в очередь

**2. Подготовка к отправке (`telegram_input_handlers.py`, `claude_interaction.py`)**
- Проверяется, не находится ли чат в global all-mode; из этого режима отправка агенту блокируется до выбора проекта и сессии
- `claude_interaction.send_message_to_agent()` координирует отправку с `session_watcher`, чтобы пользователь не получил один ответ дважды
- Вызывается `claude_interaction.send_to_claude_and_respond()` — основная логика доставки запроса агенту и ответа пользователю

**3. Отправка в Claude (claude_runner.py, process_manager.py, process_send.py, process_lifecycle.py, process_state.py)**
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

**4. Обработка ответов (process_events.py, process_retry.py, process_state.py)**
- Фоновый reader читает строки из stdout, парсит JSON
- Маршрутизирует по типу события:
  - `system (init)` — первое событие с реальным session_id, обновляет ключи во всех словарях
  - `assistant` — промежуточные ответы, вызывает callback `on_progress`
  - `result` — финальный ответ, кладёт в `response_queue`
  - `control_request` — запрос разрешения на инструмент, автоматически отвечает по режиму
- `send_message()` ожидает результат из очереди с таймаутом 30 минут

**5. Возврат ответа (`claude_interaction.py`, `telegram_response_delivery.py`)**
- Проверяет эпоху — если пользователь переключил сессию, ответ не сохраняется
- Сохраняет session_id через SessionManager
- Регистрирует в DailySessionRegistry (получает номер #N)
- Передаёт текст в `telegram_response_delivery.send_response()`
- `telegram_response_delivery.py` добавляет заголовок с дневным номером сессии, готовит markdown/html, разбивает длинные сообщения и отправляет их через Telegram
- После успешной отправки Telegram-сообщения `telegram_response_delivery.py` регистрирует reply-route для каждого отправленного chunk-а, если известны исходные `project_path`, `session_id` и `backend`
- Обрабатывает очередь накопленных сообщений через `_drain_message_queue()`
- Возобновляет watchers через `_resume_watcher()`

## Поток данных: адресный reply на сообщение бота

Адресный reply — это входящее сообщение пользователя, которое является Telegram reply на сообщение, ранее отправленное ботом.

**1. Регистрация исходящего сообщения**
- `telegram_response_delivery.py` получает исходный `project_path`, `session_id`, `backend` и номер сессии
- Telegram sender возвращает отправленное сообщение или его `message_id`
- После успешной отправки delivery-слой сохраняет route в `reply_route_registry.py`
- Registry сохраняет последние 200 route-записей в `reply_routes.json`; при добавлении 201-й записи самая старая удаляется
- Если отправка в Telegram не удалась, route не регистрируется

**2. Входящий reply**
- `telegram_input_handlers.py` первым делом проверяет, есть ли у входящего сообщения `reply_to_message`
- `reply_route_handler.py` ищет route по `chat_id + reply_to_message.message_id`
- Если route найден, обычная блокировка текстовых сообщений в `/all` не применяется, потому что адрес уже известен

**3. Отправка без переключения**
- Handler добавляет цель в in-flight registry, чтобы быстрый второй reply не прошёл до того, как `process_manager` выставит свой busy-флаг
- Пользователь сразу получает короткое подтверждение `Передал в /N` или `Передал в /PsN`
- Handler запускает фоновую задачу, которая вызывает `process_manager.send_message()` с сохранёнными `cwd=project_path`, `session_id` и `backend`
- `config.WORKING_DIR`, активная сессия в `session_manager` и текущий режим пользователя не меняются
- После завершения фоновой задачи in-flight marker снимается

**4. Ошибки**
- Если route неизвестен, бот не угадывает цель по текущей сессии
- Если route неизвестен после restart, но сообщение было отправлено уже при включённом persistent registry и не вытеснено лимитом 200 записей, это считается ошибкой загрузки или повреждением файла
- Если целевая сессия занята, сообщение не ставится в очередь
- Если фоновая отправка после подтверждения получает ошибку backend-а, бот отдельным сообщением показывает `Не передал ...` с причиной
- Если backend вернул переполнение контекста, пользователь получает подсказку начать новую сессию через `/new`
- Фото, документы и альбомы в адресном reply отклоняются до скачивания файла

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

**Обезвреживание ведущего слэша.** CLI трактует `content`, начинающийся с `/`, как обращение к slash-команде и на нераспознанное слово отвечает `result` `Unknown command` ещё до вызова модели. Это ломало пересланные сообщения со слэша (`/8 ...`, путь `/home/...`, произвольный текст `/-new-things ...`). Единый диспетчер `process_send.send_message` перед отправкой прогоняет текст через `_prevent_cli_slash_command_misparse`: сообщение, начинающееся с `/`, получает один ведущий пробел — CLI перестаёт видеть команду, видимое содержимое не меняется. Точка одна, поэтому правило действует для всех путей (обычный текст, reply-route, ретраи) и обоих бэкендов. Подробнее — ADR `04.07_23.41-...-cli-leading-slash-command-neutralization.md`.

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

### Координация watchers и `telegram_input_handlers.handle_message`

Проблема: SessionWatcher читает тот же файл сессии, в который Claude пишет ответ. Без координации пользователь получит ответ дважды — от watcher и от прямого обработчика пользовательского сообщения.

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
