# Спецификация модуля: coding_agent_backend

Дата: 06-05-2026
Слой: 0 (нет зависимостей от других модулей проекта — корень графа для Adapter pattern)
Файл: `src/claude_manager/coding_agent_backend.py`

**Связанные спеки (будут написаны отдельно):**
- `claude_code_backend_spec.md` — конкретная реализация для Claude Code CLI (унаследует от `CodingAgentBackend`)
- `codex_backend_spec.md` — конкретная реализация для Codex CLI
- `current_backend_registry_spec.md` — персистентное хранилище выбранного пользователем бэкенда (файл `~/.claude-manager-current-backend`)

## Назначение

Абстрактный интерфейс CLI-бэкенда (Adapter pattern) — изолирует от вышестоящих модулей различия между двумя CLI: Claude Code CLI и Codex CLI. Позволяет в одном процессе бота держать оба бэкенда и переключать активный по команде пользователя `/agent`. Сам модуль не реализует ни одного бэкенда — он определяет контракт (18 методов + 2 свойства), общие DTO (data transfer objects — структуры данных, которыми обмениваются бэкенд и потребитель: `UnifiedEvent`, `SessionFileInfo`, `SessionMessage`, `SessionFileSnapshot`, `TerminalStatus`, `StopSignalStep`, `StopStrategy`) и фабричную функцию выбора реализации по имени.

Кроме контракта самого бэкенда модуль фиксирует **контракт владения сессией** для потребителей (`daily_session_registry`, `session_manager`, `session_watcher`, `unread_buffer`, обработчики команд `/N`, `/new`, `/switch`, orphan cleanup): каждая запись о сессии в реестре дневной нумерации хранит не только UUID, но и `BackendName` своего бэкенда. Существующая сессия всегда открывается тем CLI, который её создал, а текущий глобальный бэкенд (`current_backend_registry.get_current()`) используется только для новых сессий. Без этого правила переключение `/agent` ломает уже открытые сессии. Подробности — в разделе «Контракт владения сессией для потребителей».

## Расхождение с концепцией от 06-05-2026

В концептуальной сессии 06-05 были зафиксированы 12 имён методов с исходными сигнатурами. В ходе ревью спек 06-05 (см. `dev/docs/session-reports/06-05/14-54_codex-specs-review-undone-items.md`) интерфейс расширен — оригинальные 12 методов остаются, но добавлены ещё несколько и пересмотрены два DTO. Каждое расхождение — техническое, имена и семантика «старых» 12 методов не изменились.

- **`compose_subprocess_command_args`** в концепции принимал `(session_id, cwd) -> list[str]`. В спеке принимает `(session_id, cwd, prompt_text, image_paths) -> list[str]`. **Причина:** Codex CLI принимает промпт как позиционный аргумент команды (`codex exec ... <prompt>`), а не через stdin. Без `prompt_text` в сигнатуре `CodexBackend` не сможет сформировать рабочую команду. Для `ClaudeCodeBackend` `prompt_text` игнорируется (ввод идёт через stdin). Аргумент `image_paths` сохраняется в сигнатуре для совместимости интерфейса, но в текущей версии **обоими бэкендами игнорируется**: Claude получает путь к изображению уже включённым в `prompt_text` модулем `claude_interaction` (Claude сам читает файл инструментом Read), Codex получает путь там же (Codex сам вызывает встроенный инструмент `view_image` по пути в тексте). Подробнее — в пункте «Изображения для Codex» ниже.

- **Изображения для Codex — путь в `prompt_text`, а не флаг `-i/--image`.** Codex CLI поддерживает флаг `-i <path>` (позволяет приложить картинку как байты, минуя инструмент `view_image`), и это документируется в `~/.codex/custom-codex-rust-v0.128.0/codex-rs/utils/cli/src/shared_options.rs:9-17`. В первой версии бэкенда **флаг не используется** — путь к файлу включается в `prompt_text` модулем `claude_interaction` ровно так же, как для Claude, и Codex сам вызывает встроенный инструмент `view_image` (capability check на стороне модели). **Причины:**
  - Симметрия с Claude — единый путь обработки изображений для обоих бэкендов (один и тот же `claude_interaction` без условных веток по бэкенду).
  - Меньше изменений в `claude_interaction` и `media_group_handler` — текущий код уже сохраняет файлы на диск и пишет путь в текст.
  - Поведение Codex с `view_image` эмпирически проверено и работает.
  
  Переход на `-i/--image` оставлен на будущее — если эмпирика покажет, что `view_image` ненадёжен (например, модель путает путь с примером кода или игнорирует файл, или ошибается при ресайзе > 2048 px). Контракт текущей версии: **изменение поведения требует контрактного теста** (`test_codex_view_image_path_in_prompt_text` — запустить реальный `codex exec` с тестовой PNG-картинкой по абсолютному пути в промпте, убедиться по файлу сессии, что был вызов `view_image` или `event_msg.payload.type == "view_image_tool_call"`). Тест уже включён в тест-план Codex-спеки. **Причина фиксации в родительской спеке:** ревью 06-05, недоделка №10. См. также раздел «Контракты с внешними системами → Codex CLI — встроенный инструмент view_image».

- **Backend ownership для сессий — обязательный контракт потребителей.** Концепция вводила `current_backend_registry` как глобальное хранилище имени активного CLI, и из неё следовало, что любая сессия открывается через текущий глобальный бэкенд. Это ломается при `/agent`: пользователь создаёт Claude-сессию, переключает бэкенд на Codex, нажимает `/1` → бот пытается открыть Claude-сессию через Codex (`codex exec resume <claude_uuid>`) и получает либо ошибку, либо мусор. Симметрично — Codex-сессия не должна открываться через Claude. **Решение:** каждая запись о сессии в `daily_session_registry._daily_sessions[date_str][number]` хранит структуру `{session_id, backend}`, а не голый UUID. Существующая сессия открывается своим backend'ом (тот, что был активен в момент её создания), новые сессии — текущим глобальным. Полный контракт для всех потребителей (`daily_session_registry`, `session_manager`, `session_watcher`, `unread_buffer`, обработчики `/N`, `/new`, `/switch`, orphan cleanup) собран в новом разделе «Контракт владения сессией для потребителей». **Причина:** ревью 06-05, недоделка №1.

- **Метод `requires_new_process_per_turn` исключён.** Оба CLI эмпирически требуют новый процесс на каждый turn (Claude закрывает stdin после первого сообщения, см. `claude_runner.py:117`), и `process_manager` всегда поднимает новый процесс с `--resume <id>` (для Claude) или `resume <id>` (для Codex). Метод не нужен.

- **Добавлены методы `is_error_event` и `read_error_text_from_event`.** В концепции turn-failure обрабатывался косвенно — через «нет финального текста → пустой ответ → ретрай». Это не работает для Codex: событие `turn.failed` несёт текст ошибки, но без явного метода `process_manager` теряет его и отдаёт пользователю пустой успешный ответ вместо ретрая или внятной ошибки. **Причина добавления:** ревью 06-05, недоделка №3. См. раздел «Публичный API → is_error_event» и «Алгоритм работы → жизненный цикл turn-а».

- **Добавлен DTO `SessionFileSnapshot` и метод `read_session_file_snapshot`.** Старый метод `read_messages_from_session_file` возвращал только `list[SessionMessage]` — этого недостаточно для `session_watcher`, который должен отслеживать счётчик сырых JSONL-строк (для пометки уже доставленных сообщений), последнюю запись (для проверки «turn ещё активен») и факт активного turn-а. Watcher для Codex также должен по последней записи понимать, пришёл ли `event_msg.payload.type == "task_complete"` или это всё ещё промежуточное состояние. Новый snapshot-DTO содержит `messages`, `raw_record_count`, `last_record`, `is_turn_active`. Метод `read_messages_from_session_file` сохраняется как удобный wrapper для `session_reader` (там счётчики не нужны) и возвращает `(await read_session_file_snapshot(...)).messages`. **Причина добавления:** ревью 06-05, недоделка №2.

- **Добавлен метод `get_stop_strategy` и DTO `StopSignalStep`/`StopStrategy`.** В концепции `/stop` описывался как «универсальный `process.terminate()` (SIGTERM)» — это неверно для Codex. Codex CLI обрабатывает именно SIGINT (`tokio::signal::ctrl_c()`, `exec/src/lib.rs:741-843`) и при нём отправляет на сервер `ClientRequest::TurnInterrupt` со штатным завершением; SIGTERM приводит к более жёсткой остановке без `TurnInterrupt`. Для Claude SIGTERM штатен. Стратегия остановки — backend-specific и должна быть выражена в интерфейсе. **Причина добавления:** ревью 06-05, недоделка №4. См. раздел «Алгоритм работы → остановка процесса для команды /stop».

- **Поле `SessionFileInfo.created_at` переименовано в `last_modified_at`.** В предыдущих черновиках встречалось имя `created_at`, что не соответствовало реальному источнику данных (`os.path.getmtime`) и приводило к расхождению в тест-плане. Сортировка `/sessions` опирается на mtime (когда CLI последний раз дописывал в файл), а не на ctime — это корректнее для пользователя.

- **Lookback окна сканирования сессий — backend-specific.** Раньше родительская спека жёстко фиксировала «за последние 2 дня» в описании поведения для Codex. Это противоречит `codex_backend_spec.md` (`LOOKBACK_DAYS_FOR_SESSION_LISTING = 30`) и неоправданно для Codex (один общий каталог `~/.codex/sessions/YYYY/MM/DD/` на все проекты — пользователь за неделю накапливает десятки сессий). Конкретное значение задаёт реализация бэкенда. Родительская спека только декларирует, что окно ограничено — точное значение в кейсе Claude диктуется именованием папки сессии (`~/.claude/projects/<sanitized>`), в кейсе Codex — `LOOKBACK_DAYS_FOR_SESSION_LISTING`. **Причина:** ревью 06-05, недоделка №5.

- **Источник assistant-текста в файле сессии Codex — `response_item`, а не `event_msg.agent_message`.** В первой версии родительской спеки было сказано «для Codex `event_msg` с подтипом `agent_message` — это финальный ответ ассистента». Это семантически неверно: канонический источник — `response_item` (структурно симметричен Claude `assistant`-записи и поддерживает multi-modal content-блоки), а `event_msg.agent_message` — wire-обёртка для трансляции событий, которая дублирует тот же текст. Бэкенд должен читать из `response_item`. **Причина:** ревью 06-05, недоделка №5.

- **`event_types_meaning_cli_is_busy` для Codex — четыре значения, не одно.** Раньше декларировалось «для Codex — `frozenset({"event_msg"})` с дополнительной проверкой подтипа в `read_messages_from_session_file` (исключения: подтипы `task_complete`, `token_count` означают окончание)». Это неверно: `token_count` приходит и до, и после `task_complete` (служебное обновление статистики), и поэтому не маркер завершения; а если последняя запись в файле — `response_item` (ассистент только что записал ответ как `output_text`, но `task_complete` ещё не пришёл), turn нельзя считать завершённым. Корректное множество — все типы `RolloutItem` кроме `session_meta`, а финальность определяется методом `is_turn_terminal_session_record` родительского интерфейса (см. ниже отдельную запись о его поднятии в общий интерфейс). **Причина:** ревью 06-05, недоделка №5.

- **CJM-08 (`/stop`) — модуль участвует, не пропускается.** Раньше говорилось «остановка унифицирована через `process.terminate()`, модуль НЕ участвует». Это противоречит реальному поведению Codex (см. выше — SIGINT vs SIGTERM). Теперь интерфейс предоставляет `get_stop_strategy()`, и `process_manager` именно через этот метод выбирает последовательность сигналов. **Причина:** ревью 06-05, недоделка №4.

- **Метод `is_turn_terminal_session_record` поднят в общий интерфейс.** В первой версии этой спеки `is_turn_terminal_session_record` объявлялся как Codex-only расширение, а Claude-watcher должен был использовать ветку «по умолчанию». Это вынуждало `session_watcher` ветвиться по `backend.name == BackendName.CODEX` или `isinstance(backend, CodexBackend)` — нарушение принципа подмены Лисков (LSP), которое масштабируется на каждый новый бэкенд. Метод поднят в `CodingAgentBackend` как абстрактный. Реализация для Claude — тривиальная (`record.get("type") == "result"`), для Codex — нетривиальная (подтип `event_msg.payload.type == "task_complete"`). Стоимость: одна абстрактная декларация и один тривиальный override в `ClaudeCodeBackend`. Выгода: ни один потребитель не пишет `if backend.name == ...`. **Причина:** уточнение по итогам ревью 06-05, нарушение LSP в потребителе.

## Обслуживаемые сценарии

Модуль не обслуживает CJM напрямую (он — инфраструктура для других модулей), но без его контракта не работают:

- **CJM-02: Отправка текстового сообщения** — `compose_subprocess_command_args`, `encode_user_message_for_cli_stdin`, `parse_stdout_line_into_event`, `is_turn_complete_event`, `read_session_id_from_event`, `read_assistant_text_from_event`, `read_progress_text_from_event`, `text_markers_indicating_empty_response` используются `process_manager` для запуска CLI и извлечения ответа
- **CJM-03: Отправка фото или файла** — оба бэкенда работают через путь к файлу, упомянутый прямо в `prompt_text` модулем `claude_interaction`. Claude вызывает свой инструмент Read, Codex — встроенный `view_image`. Аргумент `image_paths` методов `compose_subprocess_command_args` и `encode_user_message_for_cli_stdin` в первой версии обоими бэкендами игнорируется — детали и обоснование в разделе «Расхождение с концепцией → Изображения для Codex»
- **CJM-04: Создание новой сессии (/new)** — `compose_subprocess_command_args(session_id=None, ...)` формирует команду без `--resume` (Claude) или без `resume <id>` (Codex)
- **CJM-05: Просмотр списка сессий (/sessions)** — `locate_session_files_directory_for_project`, `list_session_files_for_project`, `read_messages_from_session_file` используются `session_reader` для чтения файлов сессий с диска
- **CJM-06: Переключение на сессию (/N)** — `compose_subprocess_command_args(session_id=<id>, ...)` формирует команду с `--resume <id>` (Claude) или `resume <id>` (Codex)
- **CJM-07: Мониторинг всех сессий (/all)** — `list_all_session_files_for_project`, `read_session_file_snapshot` (новый snapshot-метод), `event_types_meaning_cli_is_busy`, `text_markers_indicating_empty_response` используются `session_watcher` для слежения за файлами в реальном времени. Старый метод `read_messages_from_session_file` остаётся в API как удобный wrapper для `session_reader` (там snapshot-поля не нужны), но watcher обязан перейти на `read_session_file_snapshot` — иначе теряются raw-счётчик строк, последняя запись и индикатор активности turn-а (см. «Расширения интерфейса для snapshot, terminal status и stop strategy» и алгоритм работы watcher-а)
- **CJM-08: Остановка процесса (/stop)** — модуль участвует через метод `get_stop_strategy()`. `process_manager` при обработке `/stop` вызывает `backend.get_stop_strategy()` и применяет последовательность сигналов оттуда: для Claude — `SIGTERM → (5 сек ожидания) → SIGKILL`, для Codex — `SIGINT → (5 сек) → SIGTERM → (5 сек) → SIGKILL`. Универсальный `process.terminate()` (только SIGTERM) **запрещён** — Codex CLI обрабатывает именно `SIGINT` (отправляет на сервер `ClientRequest::TurnInterrupt` и закрывает сессию штатно), и только при отсутствии реакции на SIGINT эскалирует до SIGTERM/SIGKILL. У Claude нет отдельной обработки SIGINT, и стратегия для него содержит сразу пару `SIGTERM → SIGKILL`. Маркер `"No response requested."` для Claude остаётся внутренним fallback-ом и виден через `text_markers_indicating_empty_response()`, но не как механизм остановки

Также модуль обслуживает новый сценарий, который будет добавлен в BRD при реализации фичи:

- **CJM-NEW: Переключение бэкенда (/agent)** — фабричная функция `get_backend(name: BackendName) -> CodingAgentBackend` используется при загрузке текущего бэкенда из персистентного хранилища (отдельный модуль `current_backend_registry`, не часть этой спеки)

## Публичный API

### Класс `BackendName` (Enum)

Перечисление имён бэкендов. Используется как тип для фабрики и для записи в персистентные файлы.

```python
class BackendName(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
```

Наследование от `str` — для прямой записи в JSON и файлы (`BackendName.CLAUDE.value == "claude"`).

### Тип `UnifiedEvent` (TypeAlias)

Унифицированное представление одного события из stdout CLI-процесса. Это словарь — точное содержимое зависит от бэкенда. Потребители (`process_manager`) обращаются к нему ТОЛЬКО через методы интерфейса (`is_turn_complete_event`, `read_session_id_from_event` и т.д.), а не индексацией по полям. Вышестоящие модули НЕ знают, какие именно ключи в словаре — это знание принадлежит конкретному бэкенду.

```python
type UnifiedEvent = dict[str, Any]  # PEP 695, Python 3.12+
```

### Dataclass `SessionFileInfo`

Метаданные одной сессии для отображения в `/sessions` и для индексации в watcher.

```python
@dataclass(frozen=True)
class SessionFileInfo:
    session_id: str               # UUID сессии (для Claude и Codex одинаково — UUID)
    file_path: str                # абсолютный путь к JSONL-файлу сессии на диске
    last_modified_at: float       # timestamp последней модификации файла (os.path.getmtime); используется для сортировки от свежих к старым
    preview: str                  # первое очищенное сообщение пользователя для отображения в /sessions, без обязательной обрезки
```

Поле названо `last_modified_at`, а не `created_at`, потому что физически в файловой системе доступно `mtime`, а не creation time. На macOS они часто близки, но не совпадают побайтно. Семантически для сортировки в `/sessions` нужно именно «когда последний раз менялась», что эквивалентно «когда последний раз CLI писал в эту сессию» — это полезнее, чем строгое время создания.

### Dataclass `SessionMessage`

Одно сообщение из истории сессии (читается из JSONL-файла).

```python
@dataclass(frozen=True)
class SessionMessage:
    role: str              # "user" | "assistant"
    text: str              # текст сообщения (для assistant — финальный ответ; для user — запрос)
    timestamp: float | None  # время сообщения, если есть в файле сессии
    is_empty_response: bool  # True если text — это маркер пустого ответа (например, "No response requested.")
```

### Dataclass `SessionFileSnapshot`

Полный «снимок» состояния файла сессии для `session_watcher`. В отличие от `read_messages_from_session_file` (только список сообщений), snapshot содержит служебные поля, без которых watcher не может корректно работать с дельтой между опросами и определять момент завершения turn-а.

```python
@dataclass(frozen=True)
class SessionFileSnapshot:
    messages: list[SessionMessage]   # пользовательские/ассистентские сообщения, как в read_messages_from_session_file
    raw_record_count: int            # количество строк JSONL в файле (включая невалидные, system, result, любые служебные)
    last_record: UnifiedEvent | None # последняя валидная распарсенная запись JSONL (для определения is_turn_active без повторного чтения)
    is_turn_active: bool             # True если turn ещё идёт (CLI пишет в файл), False если turn завершён или файл пуст
```

**Назначение полей:**

- `messages` — то же, что возвращает `read_messages_from_session_file`. Хронологический порядок. Нужно для отображения новых сообщений пользователю.
- `raw_record_count` — количество физических строк в файле (только пустые строки исключаются, всё остальное считается). Watcher использует это число для дельта-чтения: если число выросло с прошлого опроса — появились новые записи, нужно пересчитать. Подсчёт делается по сырым строкам, а не по распарсенным записям, чтобы счётчик был стабильным даже в момент частичной записи последней строки CLI-процессом.
- `last_record` — последняя валидная распарсенная запись JSONL. Watcher использует поле для определения `is_turn_active` без повторного чтения файла; кроме того, потребитель может прочитать `last_record["session_id"]`, `last_record["type"]`, `last_record["payload"]` (для Codex) для своих нужд. `None` если файл пуст или ни одна строка не парсится.
- `is_turn_active` — общий булев индикатор, что CLI-процесс всё ещё пишет в файл. Семантика разная для бэкендов:
  - **Claude:** `is_turn_active = (last_record is not None) and (last_record.get("type") in event_types_meaning_cli_is_busy())`. Финальное событие в файле — `result`, оно не входит в busy-множество, поэтому `is_turn_active = False`.
  - **Codex:** `is_turn_active = (last_record is not None) and (last_record.get("type") in event_types_meaning_cli_is_busy()) and not is_turn_terminal_session_record(last_record)`. Все non-`session_meta` типы попадают в busy-множество, и `is_turn_active` сбрасывается в False **только** при `event_msg.payload.type == "task_complete"`. `token_count` приходит и до, и после `task_complete` — он не маркер завершения, и `is_turn_terminal_session_record` для него возвращает `False`.

Snapshot как тип возврата выбран вместо tuple-or-multiple-return по двум причинам: (а) `frozen=True` гарантирует, что watcher не модифицирует возвращённое значение; (б) добавление новых полей в будущем (например, `last_assistant_text` для оптимизации) не сломает сигнатуру метода.

### Enum `TerminalStatus`

Backend-neutral терминальный статус turn-а — скрывает разницу между «`turn.failed`» (Codex) и «`result` с `is_error: true`» (Claude).

```python
class TerminalStatus(str, Enum):
    SUCCESS = "success"   # turn завершён штатно, есть финальный ответ ассистента (или пустота, но без ошибки)
    FAILED = "failed"     # turn завершён с ошибкой (Codex turn.failed, Claude result с is_error=True)
```

Наследование от `str` — для прямой записи в логи и JSON. Используется методом `read_terminal_status_from_event` (см. ниже) и `process_manager` для решения о retry: при `FAILED` запускается ретрай, при `SUCCESS` ответ доставляется пользователю даже если он пустой.

### Dataclass `StopSignalStep`

Один шаг в стратегии остановки процесса.

```python
@dataclass(frozen=True)
class StopSignalStep:
    signal_to_send: int          # номер сигнала из модуля signal: signal.SIGINT, signal.SIGTERM, signal.SIGKILL
    wait_seconds_before_next: float  # сколько секунд ждать реакции до следующего шага (последний шаг — игнорируется)
```

Используется внутри `StopStrategy` (см. ниже).

### Dataclass `StopStrategy`

Backend-specific стратегия остановки subprocess. Возвращается методом `get_stop_strategy()`. `process_manager` применяет шаги последовательно: отправляет `signal_to_send`, ждёт `wait_seconds_before_next` (или пока процесс не завершится), переходит к следующему шагу. После последнего шага без реакции — поднимает ошибку оператору (это уже не штатная остановка, а зависший процесс).

```python
@dataclass(frozen=True)
class StopStrategy:
    steps: tuple[StopSignalStep, ...]  # упорядоченная последовательность сигналов с интервалами
```

**Конкретные стратегии (заполняются реализациями):**

- **Claude:** `StopStrategy(steps=(StopSignalStep(SIGTERM, 5.0), StopSignalStep(SIGKILL, 0.0)))`. Claude корректно завершается по SIGTERM (закрывает stdin, дописывает последнюю запись в JSONL, выходит). 5 секунд — потолок для штатного завершения, после — добивается SIGKILL. Источник значений: `claude_runner.py:21-22` (`TERMINATE_TIMEOUT_SECONDS = 5`).
- **Codex:** `StopStrategy(steps=(StopSignalStep(SIGINT, 5.0), StopSignalStep(SIGTERM, 5.0), StopSignalStep(SIGKILL, 0.0)))`. Codex CLI обрабатывает именно SIGINT (`tokio::signal::ctrl_c()`, `exec/src/lib.rs:741-843`) — при нём отправляет на сервер `ClientRequest::TurnInterrupt`, что эквивалентно штатному прерыванию turn-а. SIGTERM прерывает процесс жёстче (без `TurnInterrupt`); SIGKILL — последний fallback. Прямой `process.terminate()` (только SIGTERM) для Codex некорректен — turn останется «висящим» в JSONL без события прерывания, watcher не увидит штатного финала.

Использование `tuple` (а не `list`) и `frozen=True` — гарантия, что стратегия не модифицируется после создания (бэкенды возвращают одни и те же singleton-стратегии).

### Класс `CodingAgentBackend` (abc.ABC)

Абстрактный базовый класс. Конкретные реализации — `ClaudeCodeBackend` и `CodexBackend` — живут в отдельных модулях (`claude_code_backend.py`, `codex_backend.py`) и наследуются от него.

#### `name` (свойство)

```python
@property
@abstractmethod
def name(self) -> BackendName: ...
```

Возвращает имя бэкенда. Используется для записи в `daily_session_registry`, сравнения владельца сессии и логов.

#### `display_name` (свойство)

```python
@property
@abstractmethod
def display_name(self) -> str: ...
```

Возвращает человекочитаемое название с эмодзи для UI: `"🤖 Claude"` или `"⚡ Codex"`. Используется в командах `/agent`, сообщениях `/new`, `/stop`, `/N`, в заголовках ответов и в формате ретрая (`#N Ошибка {display_name}, повтор X/10`). В `/sessions` строка берёт только короткую иконку из этой метки, без слова `Claude` или `Codex`.

#### `compose_subprocess_command_args(session_id: str | None, cwd: str, prompt_text: str, image_paths: list[str]) -> list[str]`

```python
@abstractmethod
def compose_subprocess_command_args(
    self,
    session_id: str | None,
    cwd: str,
    prompt_text: str,
    image_paths: list[str],
) -> list[str]: ...
```

Возвращает полный список аргументов для запуска subprocess (включая бинарник как `argv[0]`).

**Аргументы:**
- `session_id` (`str | None`) — идентификатор сессии для resume или `None` для новой сессии
- `cwd` (`str`) — рабочая директория проекта (передаётся CLI как параметр; не путаем с `cwd` для `subprocess.create_exec` — некоторые CLI хотят рабочую директорию через флаг, а не через `os.chdir`)
- `prompt_text` (`str`) — текст пользовательского сообщения. Может быть включён прямо в команду (Codex), либо игнорироваться методом и передаваться позже через stdin (Claude — этот аргумент в Claude-реализации игнорируется, потому что ввод идёт через `encode_user_message_for_cli_stdin`)
- `image_paths` (`list[str]`) — пути к изображениям. **В текущей версии бэкендов оба адаптера аргумент игнорируют**: путь к файлу уже включён в `prompt_text` модулем `claude_interaction`, дальше Claude вызывает свой инструмент Read, а Codex — встроенный инструмент `view_image` (по пути в тексте). Аргумент сохранён в сигнатуре для совместимости и для будущего перехода на флаг `-i` у Codex (поддерживается CLI, но не задействован в первой версии — см. «Расхождение с концепцией → Изображения для Codex»)

**Возвращает:** список строк для `asyncio.create_subprocess_exec(*args, ...)`. Бинарник — `args[0]` (полный путь, не имя).

**Исключения:**
- `BackendBinaryNotFoundError` — бинарник CLI не найден в PATH

#### `encode_user_message_for_cli_stdin(prompt_text: str, image_paths: list[str]) -> bytes`

```python
@abstractmethod
def encode_user_message_for_cli_stdin(
    self,
    prompt_text: str,
    image_paths: list[str],
) -> bytes: ...
```

Кодирует пользовательское сообщение в байты для записи в stdin процесса.

**Аргументы:**
- `prompt_text` (`str`) — текст сообщения
- `image_paths` (`list[str]`) — пути к изображениям. **В текущей версии обоими бэкендами игнорируются** — путь уже включён в `prompt_text` модулем `claude_interaction` (см. описание `compose_subprocess_command_args` и раздел «Расхождение с концепцией → Изображения для Codex»)

**Возвращает:** `bytes`. Если бэкенду не нужен stdin (промпт уже в args через `compose_subprocess_command_args`) — возвращает `b""`. `process_manager`, увидев пустые байты, не пишет в stdin и сразу закрывает его. Для Claude — JSON-сообщение `{"type":"user","message":{"role":"user","content":<text>}}\n` в UTF-8, не-ASCII символы НЕ экранируются. Для Codex — `b""`.

**Исключения:** не выбрасывает (ошибки кодирования трактуются как программная ошибка — например, отсутствие prompt_text — должны падать с TypeError на этапе вызова).

#### `parse_stdout_line_into_event(raw_line: str) -> UnifiedEvent | None`

```python
@abstractmethod
def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None: ...
```

Разбирает одну строку stdout (одну строку JSONL) в унифицированное событие.

**Аргументы:**
- `raw_line` (`str`) — сырая строка из stdout (без завершающего `\n`, в UTF-8)

**Возвращает:** `UnifiedEvent` (словарь) с распарсенными данными, либо `None`, если строка пустая или не содержит полезного события.

**Исключения:**
- `BackendProtocolError` — строка не парсится как валидный JSON (это контрактное нарушение CLI). Сообщение исключения должно содержать первые 200 символов raw_line для диагностики

#### `is_turn_complete_event(event: UnifiedEvent) -> bool`

```python
@abstractmethod
def is_turn_complete_event(self, event: UnifiedEvent) -> bool: ...
```

Возвращает `True`, если событие означает завершение текущего turn-а (после него больше событий не будет, читатель должен остановиться). Для Claude — событие с `type == "result"`. Для Codex — событие с `type == "turn.completed"` ИЛИ `type == "turn.failed"`.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `bool`.

#### `read_session_id_from_event(event: UnifiedEvent) -> str | None`

```python
@abstractmethod
def read_session_id_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает идентификатор сессии из события, если он там есть. Для Claude — поле `session_id` (присутствует в событиях `system`, `assistant`, `user`, `result`). Для Codex — поле `thread_id` события `thread.started` (есть только в этом одном типе события, в остальных — `None`).

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`. UUID сессии или `None`, если событие не содержит идентификатор.

#### `read_assistant_text_from_event(event: UnifiedEvent) -> str | None`

```python
@abstractmethod
def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает финальный текст ответа ассистента из события. Возвращает текст ТОЛЬКО для финальных событий turn-а. Для промежуточных (потоковых обновлений) — `None`.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`. Текст ответа ассистента, либо `None` если это не финальное событие или ответ пустой/маркер.

**Поведение для Claude:** читает `event["result"]`. Если значение — один из `text_markers_indicating_empty_response()` — возвращает `""` (пустая строка, не `None` — это сигнал «turn закончился, но ответа не было»). Если `event["type"] != "result"` — возвращает `None`.

**Поведение для Codex:** метод stateless. Возвращает текст из любого события `item.completed` с `item.type == "agent_message"` (поле `item.text`). Для других событий (включая `turn.completed`, который не содержит текст) — `None`. Накопление «последнего» финального текста — ответственность вызывающей стороны: `process_manager` запоминает последнее ненулевое возвращённое значение и использует его как окончательный ответ, когда увидит `is_turn_complete_event == True`. См. раздел «Алгоритм работы».

#### `read_progress_text_from_event(event: UnifiedEvent) -> str | None`

```python
@abstractmethod
def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает текст промежуточного обновления (для отправки пользователю как progress-сообщение `#N ⏳ ...`).

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`. Текст для показа как progress, либо `None` если событие не содержит progress-данных.

**Поведение для Claude:** читает события `assistant`. Если в `event["message"]["content"]` есть блок `type == "text"` — возвращает его `text`. Иначе если есть блок `type == "thinking"` — возвращает его `thinking`. Иначе `None`. **Приоритет text > thinking** (зафиксировано по реальному поведению `process_manager.py:121-138`, спека предыдущего поколения ошибочно описывала обратный приоритет — этот баг исправляется в новой спеке, поведение остаётся как в существующем коде).

**Поведение для Codex:** Codex не имеет потоковых thinking-блоков (reasoning зашифрован, эмпирически проверено). Возвращает текст из событий `item.completed` с `item.type == "reasoning"` — это будет ОТРЫВКИ суммаризации, не полный thinking. Для `item.type == "agent_message"` — возвращает `None`, потому что это уже финальный ответ (он попадёт в `read_assistant_text_from_event`).

#### `locate_session_files_directory_for_project(project_dir: str) -> str`

```python
@abstractmethod
def locate_session_files_directory_for_project(self, project_dir: str) -> str: ...
```

Возвращает абсолютный путь к директории, где CLI хранит файлы сессий для данного проекта.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта (например, `/Users/ivan/Desktop/claude-sandbox/claude_manager`)

**Возвращает:** `str`. Абсолютный путь.

**Поведение для Claude:** `~/.claude/projects/<sanitized-path>/`, где `sanitized-path` получается заменой всех символов вне `[a-zA-Z0-9]` на `-`.

**Поведение для Codex:** Codex хранит сессии глобально, не по проектам — `~/.codex/sessions/`. Метод возвращает корень `~/.codex/sessions/`. Фильтрация по проекту делается в методах перечисления сессий через чтение поля `cwd` из `session_meta` каждого файла.

#### `list_session_files_for_project(project_dir: str) -> list[SessionFileInfo]`

```python
@abstractmethod
async def list_session_files_for_project(self, project_dir: str) -> list[SessionFileInfo]: ...
```

Возвращает список метаданных файлов сессий, относящихся к данному проекту, отсортированный от свежих к старым.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта

**Возвращает:** `list[SessionFileInfo]`. Не более `MAX_RECENT_SESSIONS = 15` элементов.

**Поведение для Claude:** `os.listdir(<projects-dir>)` → отфильтровать `.jsonl` → отсортировать по `mtime` → взять первые 15 → для каждого прочитать первое сообщение пользователя как preview.

**Поведение для Codex:** обойти `~/.codex/sessions/YYYY/MM/DD/` за последние `LOOKBACK_DAYS_FOR_SESSION_LISTING` дней (значение задаёт реализация Codex-бэкенда — текущее `30`, см. `codex_backend_spec.md` → «Константы») → отфильтровать `rollout-*.jsonl` → для каждого прочитать первую запись `session_meta`, проверить `payload.cwd == project_dir` → отсортировать по `mtime` → взять первые 15 → для каждого прочитать первое сообщение пользователя как preview. **Lookback-окно — backend-specific:** Codex хранит сессии в одной плоской директории по дате (`YYYY/MM/DD`), и пользователь за неделю-две может накопить десятки сессий, среди которых нужно выбрать 15 свежайших; маленькое окно теряет валидные сессии. Для Claude lookback не применим — папка сессий уже отфильтрована по проекту самим CLI через имя директории.

**Исключения:** не выбрасывает; ошибки чтения отдельных файлов логируются и пропускаются (файл может быть в момент записи Codex'ом).

**Async** — потому что чтение файлов в больших директориях может блокировать на десятки миллисекунд; `process_manager` и `session_watcher` async-only, и блокирующие операции там запрещены (см. CLAUDE.md → «Асинхронность»).

#### `list_all_session_files_for_project(project_dir: str) -> list[SessionFileInfo]`

```python
@abstractmethod
async def list_all_session_files_for_project(self, project_dir: str) -> list[SessionFileInfo]: ...
```

Возвращает все известные файлы сессий проекта для operational flow — внутренних сценариев, где потеря старой сессии важнее компактного UI. Этот метод используется watcher-ом, pending delivery при переключении проекта, reset state и точечными ownership-проверками. Он не применяет `MAX_RECENT_SESSIONS = 15` и для Codex не ограничивается `LOOKBACK_DAYS_FOR_SESSION_LISTING`.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта

**Возвращает:** `list[SessionFileInfo]`. Список отсортирован от свежих к старым, но не обрезан до 15 элементов.

**Отличие от `list_session_files_for_project`:** `list_session_files_for_project` — UI API для `/sessions`, поэтому возвращает только 15 свежих сессий. `list_all_session_files_for_project` — operational API для фоновых и консистентностных задач, поэтому обязан вернуть все найденные сессии проекта.

**Поведение для Claude:** читает ту же project-specific директорию `~/.claude/projects/<sanitized-path>/`, но не обрезает результат до `MAX_RECENT_SESSIONS`.

**Поведение для Codex:** обходит всю доступную историю `~/.codex/sessions/YYYY/MM/DD/`, фильтрует файлы по `payload.cwd == project_dir`, сортирует по `mtime` и возвращает все подходящие файлы.

**Исключения:** не выбрасывает; ошибки чтения отдельных файлов логируются и пропускаются по тем же правилам, что у recent-метода.

#### `read_messages_from_session_file(file_path: str) -> list[SessionMessage]`

```python
@abstractmethod
async def read_messages_from_session_file(self, file_path: str) -> list[SessionMessage]: ...
```

Читает все сообщения (user + assistant) из файла сессии, разворачивая внутренний формат бэкенда в унифицированный список `SessionMessage`.

**Аргументы:**
- `file_path` (`str`) — абсолютный путь к JSONL-файлу сессии

**Возвращает:** `list[SessionMessage]`. Сохраняется хронологический порядок.

**Поведение для Claude:** читает все строки JSONL, парсит каждую через `json.loads`, для записей `type == "user"` или `type == "assistant"` извлекает `message.content` (может быть строкой или списком блоков), извлекает текст. Каждое получившееся сообщение оборачивается в `SessionMessage`; поле `is_empty_response = True` устанавливается, когда `text in self.text_markers_indicating_empty_response()`.

**Поведение для Codex:** читает все строки JSONL, парсит как `RolloutLine` (`{timestamp, type, payload}`). Канонический источник текста — записи `type == "response_item"` с `payload.type == "message"`:
- `payload.role == "user"` → `SessionMessage(role="user", text=<input_text-блоки>, ...)`
- `payload.role == "assistant"` → `SessionMessage(role="assistant", text=<output_text-блоки>, ...)`
- `payload.role` ∈ `{"developer", "system"}` — пропускается (это автоматические системные сообщения Codex, не пользовательский ввод)

Записи `type == "event_msg"` с подтипом `agent_message` или `user_message` **пропускаются** — они содержат тот же текст, что и `response_item`, но как wire-протокольные обёртки для трансляции событий, не как каноническую запись истории. Чтение `response_item` симметрично Claude (где сообщения хранятся в `type ∈ {"user","assistant"}` с массивом content-блоков), что упрощает мысленную модель и поддерживает multi-modal ввод в будущем. `is_empty_response` для Codex всегда `False` (множество маркеров пустое — у Codex нет аналога Claude-маркера `"No response requested."`).

**Исключения:** не выбрасывает; невалидные строки пропускаются с логированием warning.

**Async** — по тем же причинам, что и `list_session_files_for_project`.

#### `text_markers_indicating_empty_response() -> frozenset[str]`

```python
@abstractmethod
def text_markers_indicating_empty_response(self) -> frozenset[str]: ...
```

Возвращает множество строк, которые CLI выдаёт как ответ, но семантически они означают «ответа нет» (используется когда CLI получает запрос вроде «не отвечай» — Claude формулирует это как `"No response requested."`).

**Аргументы:** нет.

**Возвращает:** `frozenset[str]`. Для Claude — `frozenset({"No response requested."})`. Для Codex — `frozenset()` (пустое множество, эмпирически Codex не использует подобных маркеров).

#### `event_types_meaning_cli_is_busy() -> frozenset[str]`

```python
@abstractmethod
def event_types_meaning_cli_is_busy(self) -> frozenset[str]: ...
```

Возвращает множество значений поля `type` в JSONL-записях, которые означают «CLI всё ещё работает над текущим turn-ом, не помечать сообщения как финальные». Используется `session_watcher` при чтении JSONL-файлов сессии: если последнее событие в файле имеет тип из этого множества — все промежуточные сообщения в файле помечаются `is_final=False`, и пользователь не получает их как финальные (они заблокированы, пока CLI не завершит turn).

**Аргументы:** нет.

**Возвращает:** `frozenset[str]`. Для Claude — `frozenset({"assistant", "progress", "queue-operation"})` (`result` НЕ входит — он маркер завершения). Для Codex — `frozenset({"event_msg", "response_item", "turn_context", "compacted"})` — все типы записей `RolloutItem` кроме `session_meta`. Это значит, что **для Codex верхний `type` записи busy-значение НЕ определяет финальность turn-а** — он только говорит «это не первая запись session_meta». Финальность turn-а в файле определяется методом `is_turn_terminal_session_record(record)` интерфейса (см. ниже): для Codex он проверяет подтип `event_msg.payload.type == "task_complete"`, для Claude — тривиально `record.get("type") == "result"`. Подтип `token_count` — служебное обновление статистики, приходит и до, и после `task_complete`, **не маркер завершения**. Watcher вместо чтения busy-множества и подтипов вручную обязан читать `SessionFileSnapshot.is_turn_active` — там вся эта логика инкапсулирована.

#### `is_turn_terminal_session_record(record: dict) -> bool`

```python
@abstractmethod
def is_turn_terminal_session_record(self, record: dict) -> bool: ...
```

Возвращает `True`, если данная запись JSONL-файла сессии — финальная запись штатно завершённого turn-а. Используется `session_watcher` совместно с `event_types_meaning_cli_is_busy` для определения активности turn-а в файле сессии: запись попадает в busy-множество, но отдельно проверяется на «эта запись и есть финал turn-а».

**Метод обязателен в общем интерфейсе** (а не Codex-only расширение), чтобы `session_watcher` оставался полностью backend-агностичным и не ветвился по `backend.name` или `isinstance(backend, ConcreteBackend)`. Стоимость — один тривиальный override в `ClaudeCodeBackend`. Выгода — добавление третьего бэкенда не потребует править ни одну строчку в `session_watcher`.

**Аргументы:**
- `record` (`dict`) — одна распарсенная запись JSONL из файла сессии (внутренняя структура backend-specific)

**Возвращает:** `bool`. `True` если запись означает «turn штатно завершён, ничего больше не будет дописано в этом turn-е».

**Поведение для Claude:** возвращает `record.get("type") == "result"`. Финал turn-а у Claude однозначно определяется типом записи `result`.

**Поведение для Codex:** `True` тогда и только тогда, когда `record.get("type") == "event_msg"` И `record.get("payload", {}).get("type") == "task_complete"` (serde rename `TurnComplete -> task_complete`, `protocol/src/protocol.rs:1351-1357`). Подтипы `token_count` (служебная статистика, приходит и до, и после), `error`, `turn_aborted` (ошибочное завершение, обрабатывается через `is_error_event`) — возвращают `False`.

**Соотношение с `is_turn_complete_event`:** оба метода отвечают на вопрос «это финал turn-а?», но из разных источников. `is_turn_complete_event(event)` смотрит на event из stdout (потоковое чтение subprocess); `is_turn_terminal_session_record(record)` смотрит на запись из JSONL-файла сессии (post-factum, читается watcher-ом). Stdout-событие и файл-запись — РАЗНЫЕ структуры (особенно для Codex, где stdout-формат `ThreadEvent` отличается от файлового `RolloutItem`).

### Расширения интерфейса для snapshot, terminal status и stop strategy

Эти методы добавлены в интерфейс после ревью спек 06-05 (`dev/docs/session-reports/06-05/14-54_codex-specs-review-undone-items.md`, недоделки №2, №3, №4). Они закрывают три недостающих контракта: snapshot файла сессии для watcher, backend-neutral признак ошибки turn-а, backend-specific стратегию остановки процесса. Реализуются ОБОИМИ конкретными бэкендами (Claude и Codex) — backend-neutral потребитель не должен знать, какой именно бэкенд активен.

#### `read_session_file_snapshot(file_path: str) -> SessionFileSnapshot`

```python
@abstractmethod
async def read_session_file_snapshot(self, file_path: str) -> SessionFileSnapshot: ...
```

Возвращает snapshot файла сессии — пользовательские/ассистентские сообщения плюс служебные поля для дельта-чтения watcher-ом. Заменяет прямое использование `read_messages_from_session_file` в `session_watcher`.

**Аргументы:**
- `file_path` (`str`) — абсолютный путь к JSONL-файлу сессии

**Возвращает:** `SessionFileSnapshot` (см. DTO выше). Все четыре поля заполнены: `messages` (как в `read_messages_from_session_file`), `raw_record_count` (число строк в файле), `last_record` (последняя валидная распарсенная запись или `None`), `is_turn_active` (булев индикатор активности turn-а).

**Async** — те же причины, что и `read_messages_from_session_file`. Все блокирующие I/O через `asyncio.to_thread`.

**Исключения:** не выбрасывает; ошибки чтения и парсинга обрабатываются так же, как в `read_messages_from_session_file` (warning-лог, snapshot с пустыми полями).

**Связь с `read_messages_from_session_file`:** старый метод **сохраняется** в API как удобный wrapper для `session_reader` и `/sessions`, где snapshot-поля не нужны. Реализация по умолчанию: `(await self.read_session_file_snapshot(path)).messages`. Бэкенды могут переопределить для оптимизации (читать без подсчёта `raw_record_count`), но семантика возвращаемых `messages` обязана совпадать.

#### `is_error_event(event: UnifiedEvent) -> bool`

```python
@abstractmethod
def is_error_event(self, event: UnifiedEvent) -> bool: ...
```

Возвращает `True`, если событие означает **ошибочное завершение** turn-а (CLI отдал финал, но он несёт ошибку, а не успешный текст). Используется `process_manager` для решения о retry: turn с `is_error_event == True` запускает повторную попытку (до `MAX_RETRIES = 10`, как сейчас), turn без ошибки доставляет ответ пользователю даже если текст пустой.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `bool`.

**Поведение для Claude:** `event.get("type") == "result"` И `bool(event.get("is_error")) is True`. Источник: финальное событие `result` имеет поле `is_error`, при штатном завершении `False`/отсутствует, при ошибке (превышен max_turns, отказ permission, внутренний сбой CLI) — `True`.

**Поведение для Codex:** `event.get("type") == "turn.failed"`. Источник: enum `ThreadEvent::TurnFailed` (`exec/src/exec_events.rs:23-24`) — отдельный тип события, в отличие от Claude, где успех/ошибка различаются полем внутри одного события.

**Соотношение с `is_turn_complete_event`:** `is_error_event(event) == True` влечёт `is_turn_complete_event(event) == True` (ошибочное завершение — тоже завершение). Обратное не верно: успешный финал тоже завершает turn.

#### `read_error_text_from_event(event: UnifiedEvent) -> str | None`

```python
@abstractmethod
def read_error_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает текст ошибки из финального события, если оно отмечено как ошибочное (`is_error_event == True`). Используется `process_manager` для логирования причины ретрая и для отображения в финальном сообщении бота, если все ретраи исчерпаны.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `str | None`. `None` если `is_error_event(event) == False` (нечего читать). Иначе:
- **Claude:** возвращает `event.get("result")` — у Claude текст ошибки лежит в том же поле `result`, что и обычный ответ; флаг `is_error` отличает успех от ошибки.
- **Codex:** возвращает `event.get("error", {}).get("message")` — текст ошибки лежит в отдельном поле `error.message` события `turn.failed` (`exec_events.rs:54-57`).

Дублирование «и текст, и ошибка из одного поля» — особенность Claude-протокола; backend-neutral интерфейс скрывает это различие за двумя методами (`read_assistant_text_from_event` для успеха, `read_error_text_from_event` для ошибки).

#### `read_terminal_status_from_event(event: UnifiedEvent) -> TerminalStatus | None`

```python
@abstractmethod
def read_terminal_status_from_event(self, event: UnifiedEvent) -> TerminalStatus | None: ...
```

Возвращает обобщённый терминальный статус turn-а для финального события, либо `None` для нефинальных событий. Удобный высокоуровневый метод над парой `is_turn_complete_event`/`is_error_event`.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `TerminalStatus | None`. `None` если `is_turn_complete_event(event) is False`. Иначе:
- `TerminalStatus.FAILED` если `is_error_event(event) is True`
- `TerminalStatus.SUCCESS` иначе

`process_manager` может использовать либо этот метод (одной проверкой), либо пару `is_turn_complete_event` + `is_error_event` (более явно). Семантика идентична.

#### `get_stop_strategy() -> StopStrategy`

```python
@abstractmethod
def get_stop_strategy(self) -> StopStrategy: ...
```

Возвращает backend-specific стратегию остановки subprocess. Используется `process_manager` при обработке команды `/stop` и при принудительном завершении по таймауту.

**Аргументы:** нет.

**Возвращает:** `StopStrategy` — упорядоченный список шагов `StopSignalStep(signal_to_send, wait_seconds_before_next)`, который `process_manager` применяет последовательно (отправляет сигнал, ждёт указанное число секунд или пока процесс не завершится, переходит к следующему шагу).

**Конкретные стратегии:**
- **Claude:** `StopStrategy(steps=(StopSignalStep(SIGTERM, 5.0), StopSignalStep(SIGKILL, 0.0)))`. Claude корректно завершается по SIGTERM. Источник 5 сек: `claude_runner.py:21-22` (`TERMINATE_TIMEOUT_SECONDS = 5`).
- **Codex:** `StopStrategy(steps=(StopSignalStep(SIGINT, 5.0), StopSignalStep(SIGTERM, 5.0), StopSignalStep(SIGKILL, 0.0)))`. Codex обрабатывает SIGINT штатно (отправляет на сервер `ClientRequest::TurnInterrupt`, дописывает событие прерывания в JSONL). SIGTERM — fallback, SIGKILL — крайний случай.

**Singleton:** возвращаемый `StopStrategy` — один и тот же объект для всех вызовов одного бэкенда (бэкенд хранит его как поле класса или модуля). Это гарантирует, что `process_manager` и тесты сравнивают стратегии по `is`/`==` без сюрпризов.

### Функция `get_backend(name: BackendName) -> CodingAgentBackend`

```python
def get_backend(name: BackendName) -> CodingAgentBackend: ...
```

Фабрика — возвращает singleton-инстанс конкретного бэкенда по имени.

**Аргументы:**
- `name` (`BackendName`) — имя нужного бэкенда

**Возвращает:** `CodingAgentBackend`. Singleton (повторные вызовы возвращают тот же объект, чтобы `requires_new_process_per_turn` и т.п. не пересоздавались).

**Исключения:**
- `UnknownBackendError` — если `name` не один из известных значений `BackendName` (на практике невозможно, потому что Enum гарантирует валидность; нужно для случая, когда строка из файла `~/.claude-manager-current-backend` повреждена и парсинг в Enum падает раньше)

### Функция `get_all_backends() -> list[CodingAgentBackend]`

```python
def get_all_backends() -> list[CodingAgentBackend]: ...
```

Возвращает список всех зарегистрированных бэкендов. Используется `session_watcher` для запуска по одной watcher-инстанции на каждый бэкенд (решение из сессии 06-05: «две независимые инстанции watcher вместо одной»), и в команде `/agent` для отображения всех доступных вариантов в inline-клавиатуре.

**Аргументы:** нет.

**Возвращает:** `list[CodingAgentBackend]`. Длина — 2 (`ClaudeCodeBackend`, `CodexBackend`).

### Исключения

```python
class BackendError(Exception):
    """Базовое исключение модуля."""

class BackendBinaryNotFoundError(BackendError):
    """Бинарник CLI не найден в PATH."""

class BackendProtocolError(BackendError):
    """CLI вернул невалидный JSON в stdout."""

class UnknownBackendError(BackendError):
    """Запрошен бэкенд с неизвестным именем."""
```

## Внутренние функции

### `_create_backend_instance(name: BackendName) -> CodingAgentBackend`

Создаёт новый инстанс бэкенда по имени через **lazy import** конкретного модуля (чтобы избежать circular import: бэкенды импортируют интерфейс, интерфейс не должен импортировать бэкенды на уровне модуля). Используется внутри `get_backend`.

Алгоритм:
1. Если `name == BackendName.CLAUDE` — `from claude_manager.claude_code_backend import ClaudeCodeBackend; return ClaudeCodeBackend()`
2. Если `name == BackendName.CODEX` — `from claude_manager.codex_backend import CodexBackend; return CodexBackend()`
3. Иначе — выбросить `UnknownBackendError(f"Неизвестный бэкенд: {name}. Доступны: {[b.value for b in BackendName]}")`

## Контракт владения сессией для потребителей

Каждая существующая сессия принадлежит тому CLI, который её создал. Этот контракт обязаны соблюдать ВСЕ модули, которые на основании `session_id` решают, какой subprocess запустить или какой файл сессии прочитать. Без него `/agent` ломает уже открытые сессии (Claude-сессия открывается через Codex и наоборот).

### Главное правило

**Существующая сессия → backend сессии. Новая сессия → текущий глобальный backend.**

Текущий глобальный backend (`current_backend_registry.get_current()`) используется ТОЛЬКО:
- При создании новой сессии (`session_id is None`)
- В UI команды `/agent` для подсветки текущего выбора
- В `/sessions` опционально для значка/метки активного бэкенда

При работе с существующей сессией — `session_id` всегда сопровождается известным `BackendName`, который и определяет subprocess/файл.

### `daily_session_registry`

Реестр дневной нумерации хранит для каждого номера структуру `DailySessionEntry(session_id, backend)`, а не голый UUID:

```python
@dataclass(frozen=True)
class DailySessionEntry:
    session_id: str
    backend: BackendName

# In-memory state:
_daily_sessions: dict[str, dict[int, DailySessionEntry]]  # date_str → number → entry
```

JSON-файл `daily_sessions.json` в новом формате:

```json
{
  "2026-05-06": {
    "1": {"session_id": "uuid-1", "backend": "claude"},
    "2": {"session_id": "uuid-2", "backend": "codex"}
  }
}
```

**Миграция со старого формата** `{"2026-05-06": {"1": "uuid-1"}}` (только UUID): при загрузке файла модуль детектирует строковое значение вместо словаря, конвертирует в `DailySessionEntry(session_id=uuid, backend=BackendName.CLAUDE)` (дефолтный бэкенд для старых записей — Claude, потому что до внедрения этой фичи существовал только Claude-бэкенд), записывает обратно в новом формате при первом `_save_state`. Защита `_loaded_from_disk` сохраняется — миграция не выполняется, если загрузка упала.

**Публичный API регистра расширяется:**
- `register_session(date_str, session_id, backend) -> int` — добавлен параметр `backend`
- `lookup_by_number(date_str, number) -> DailySessionEntry | None` — возвращает entry, не строку
- `get_backend_for_session(session_id) -> BackendName | None` — обратный поиск по UUID (нужен watcher-у и process_manager-у при работе с уже зарегистрированной сессией)

### `session_manager`

Связка `chat_id ↔ session_id` расширяется до `chat_id ↔ (session_id, backend)`:

```python
@dataclass(frozen=True)
class ActiveSession:
    session_id: str
    backend: BackendName

_chat_to_active: dict[int, ActiveSession]
```

Файл `sessions.json`:

```json
{"123456789": {"session_id": "uuid-1", "backend": "claude"}}
```

**Миграция:** при загрузке детектирует строковое значение → `ActiveSession(session_id=uuid, backend=BackendName.CLAUDE)`.

**API:**
- `set_active_session(chat_id, session_id, backend) -> None` — добавлен параметр `backend`
- `get_active_session(chat_id) -> ActiveSession | None` — возвращает обе компоненты
- `get_active_session_id(chat_id) -> str | None` — старый удобный wrapper для тех мест, где `backend` не нужен

### Команды `/N`, `/new`, `/switch`

Обработчики в `bot.py`:

- **`/N`** (где `N` — номер дневной сессии): `entry = daily_session_registry.lookup_by_number(today, N)`. Если `entry is None` — пользователь видит «Сессия #N не найдена». Иначе — `process_manager.send_message(session_id=entry.session_id, backend=entry.backend, ...)` с захваченным `entry.backend`, **не** с `current_backend_registry.get_current()`.

- **`/new`**: создание новой сессии. Backend = `current_backend_registry.get_current()` (текущий глобальный). После того, как Codex/Claude создаст сессию и `session_id_callback` обновит `session_manager` и `daily_session_registry`, новый entry сразу содержит `backend = <текущий глобальный>`.

- **`/switch`** (если такой команды нет — этот пункт описывает контракт на будущее): принимает UUID сессии, ищет её через `daily_session_registry.get_backend_for_session(uuid)`. Если бэкенд найден — открывает с ним. Если UUID незнакомый — отказывает («Неизвестная сессия»), не пытается «угадать» бэкенд через текущий.

### `session_watcher`

Watcher запускается **по одной инстанции на каждый бэкенд** (решение из концепции 06-05: «две независимые инстанции вместо одной»). Каждая инстанция:

- Знает свой `BackendName` и работает только с файлами своего бэкенда
- Использует `backend.list_all_session_files_for_project(project_dir)` для получения полного списка файлов operational flow (Claude — папка проекта, Codex — глобальная директория с фильтрацией по `cwd`)
- Использует `backend.read_session_file_snapshot(file_path)` для дельта-чтения (см. ниже алгоритм)
- При обнаружении нового файла регистрирует сессию через `daily_session_registry.register_session(date, session_id, backend=self.backend)` — backend известен из принадлежности инстанции
- Шлёт уведомления только владельцу сессии (через `session_manager.find_chat_by_session_id`) — backend владельца совпадает с backend-ом инстанции (иначе сессия не была бы зарегистрирована в этой инстанции)

### `unread_buffer`

Буфер непрочитанных сообщений хранит снапшот счётчиков **с ключом `(session_id, backend)`**, а не одного `session_id`. Это нужно потому, что после переключения проекта ключ должен однозначно адресовать конкретный файл сессии конкретного CLI — Codex и Claude могут одновременно иметь сессии с одинаковым UUID-форматом, и отождествлять их по UUID нельзя.

```python
_snapshot: dict[tuple[str, BackendName], SessionUnreadState]
```

При сохранении — `save_snapshot(session_id, backend, raw_record_count, last_seen_message_idx)`. При восстановлении — `restore_snapshot(session_id, backend) -> SessionUnreadState | None`.

### Orphan cleanup в `daily_session_registry`

«Сирота» — запись в реестре, для которой нет файла сессии на диске. Cleanup запускается при загрузке реестра нового проекта.

**Старая логика (Claude-only):** для каждого entry проверять `os.path.exists(<claude_projects_dir>/<sanitized>/<session_id>.jsonl)`. Если нет — удалять.

**Новая логика (backend-aware):** для каждого entry брать `entry.backend`, получить инстанс через `get_backend(entry.backend)`, вызвать operational-метод `await backend.list_all_session_files_for_project(project_dir)` и искать `entry.session_id` среди `SessionFileInfo.session_id`. Этот путь не ограничен UI-лимитом 15 сессий и не теряет старые Codex-файлы за пределами recent/lookback-окна. Это гарантирует:
- Codex-сессия (с UUID, которого нет в `~/.claude/projects/`) НЕ удаляется как сирота — её файл лежит в `~/.codex/sessions/YYYY/MM/DD/`
- Claude-сессия проверяется в правильной директории
- Если бэкенд entry повреждён или неизвестен (миграция упала) — entry оставляется (консервативное поведение: лучше показать «лишнюю» сессию, чем удалить валидную)

## Алгоритм работы

### Жизненный цикл одного turn-а через интерфейс

Это не функция модуля, а описание того, как `process_manager` использует интерфейс при отправке одного сообщения. **Инвариант:** на каждый turn — новый subprocess (оба CLI — Claude и Codex — закрывают stdin после первого сообщения, повторное использование процесса не работает).

1. Верхний слой (`bot.py` / `claude_interaction.py`) заранее определяет backend. Для новой сессии он читает `current_backend_registry.get_current()`, создаёт temp session_id через `session_manager.create_new_session(chat_id, backend)` и передаёт этот backend дальше. Для существующей сессии он берёт backend из `ActiveSession` или `DailySessionEntry`.
2. `process_manager` получает `chat_id`, `text`, `image_paths`, актуальный `session_id` (обычно temp id для новой сессии или реальный id для существующей), `cwd`, **`backend: BackendName`**. `backend=None` в backend-aware пути — контрактная ошибка; `process_manager` не читает `current_backend_registry` как fallback.
3. Определяет `backend_name = backend`
4. Получает инстанс через `get_backend(backend_name)` → `CodingAgentBackend`
5. Создаёт новый subprocess:
   - `args = backend.compose_subprocess_command_args(session_id, cwd, text, image_paths)`
   - `process = await asyncio.create_subprocess_exec(*args, stdin=PIPE, stdout=PIPE, cwd=cwd, limit=STREAM_BUFFER_LIMIT_BYTES)`
6. Кодирует сообщение для stdin: `stdin_bytes = backend.encode_user_message_for_cli_stdin(text, image_paths)`
7. Если `len(stdin_bytes) > 0` — записывает в stdin: `process.stdin.write(stdin_bytes); await process.stdin.drain(); process.stdin.close()` (EOF сигнал начать обработку — нужен Claude). Если `len(stdin_bytes) == 0` — сразу закрывает stdin (Codex — промпт уже в args)
8. Инициализирует `last_assistant_text: str | None = None`, `terminal_event: UnifiedEvent | None = None`, `terminal_status: TerminalStatus | None = None`
9. Цикл чтения stdout — построчно (`asyncio.wait_for(process.stdout.readline(), timeout=READ_LINE_TIMEOUT_SECONDS)`):
   - `event = backend.parse_stdout_line_into_event(line)`
   - Если `event is None` — пропустить
   - Если `new_id := backend.read_session_id_from_event(event)` и `new_id != session_id` — вызвать `session_id_callback(old=session_id, new=new_id, backend=backend_name)` и обновить `session_id` локально (callback атомарно обновляет `session_manager`, `daily_session_registry`, ремаппинг ключей в `process_manager`)
   - Если `progress := backend.read_progress_text_from_event(event)` — вызвать `progress_callback(session_id, progress)` (с throttle 30 сек, см. CJM-02)
   - Если `text := backend.read_assistant_text_from_event(event)` — обновить `last_assistant_text = text` (последнее ненулевое значение)
   - Если `backend.is_turn_complete_event(event)` — `terminal_event = event`, `terminal_status = backend.read_terminal_status_from_event(event)`, выйти из цикла
10. Анализ итога turn-а:
   - Если `terminal_status is None` — turn оборвался (процесс упал, EOF без `result`/`turn.completed`/`turn.failed`). `is_error = True`, текст ошибки — диагностика «turn оборван без финального события». Решение: retry (до `MAX_RETRIES = 10`)
   - Если `terminal_status == TerminalStatus.FAILED`:
     - `error_text = backend.read_error_text_from_event(terminal_event)` — для логов
     - `is_error = True`, текст для пользователя — финальный ретрай-сообщением `#N Ошибка {display_name}, повтор X/MAX_RETRIES` (как сейчас)
     - Решение: retry (с тем же `session_id` через `--resume`/`resume`, тем же бэкендом)
   - Если `terminal_status == TerminalStatus.SUCCESS`:
     - `is_error = False`, текст — `last_assistant_text or ""` (пустая строка — валидный результат, например, если пользователь спросил «не отвечай»)
     - Решение: доставить пользователю
10. Вернуть `SendResult(text=last_assistant_text or "", session_id=session_id, backend=backend_name, is_error=is_error, error_text=error_text, retries_used=N)`. Поле `backend` нужно потребителю для записи в `daily_session_registry` через `session_id_callback` и для диагностики

### Жизненный цикл watcher-итерации (один опрос файла)

Это описание того, как `session_watcher` использует `read_session_file_snapshot` для дельта-чтения. На каждой итерации (раз в `WATCHER_POLL_INTERVAL_SECONDS = 2`) для каждого отслеживаемого файла:

1. Получить snapshot: `snapshot = await backend.read_session_file_snapshot(file_path)`
2. Прочитать предыдущее состояние из in-memory счётчика: `previous = _watcher_state.get((session_id, backend), default=SessionWatcherState(raw_count=0, last_delivered_idx=-1))`
3. Если `snapshot.raw_record_count == previous.raw_count` — файл не менялся, перейти к следующему файлу
4. Дельта-сообщения: `new_messages = snapshot.messages[previous.last_delivered_idx + 1:]` — те, что появились после последней доставки
5. Для каждого `msg` в `new_messages`:
   - Если `msg.role == "assistant"` и `not msg.is_empty_response` — отправить пользователю (только владельцу сессии, см. `session_manager.find_chat_by_session_id`)
   - `is_final = not snapshot.is_turn_active` — это и есть тот самый признак «turn закончился, можно метить как финал»; в silence mode промежуточные `is_final=False` подавляются (см. CLAUDE.md → «Silence mode»)
6. Обновить состояние: `_watcher_state[(session_id, backend)] = SessionWatcherState(raw_count=snapshot.raw_record_count, last_delivered_idx=len(snapshot.messages) - 1)`

### Алгоритм остановки turn-а (`/stop`)

Это описание того, как `process_manager` использует `get_stop_strategy()` при обработке `/stop`. На основе backend-specific стратегии:

1. Получить стратегию: `strategy = backend.get_stop_strategy()`
2. Для каждого `step` в `strategy.steps[:-1]` (все шаги кроме последнего):
   - `process.send_signal(step.signal_to_send)`
   - `try: await asyncio.wait_for(process.wait(), timeout=step.wait_seconds_before_next); return` (процесс корректно завершился — выходим)
   - `except asyncio.TimeoutError:` — переходим к следующему шагу
3. Последний шаг (`strategy.steps[-1]`) — обычно SIGKILL: `process.send_signal(step.signal_to_send); await process.wait()` (без таймаута — SIGKILL не игнорируется)

### get_backend

1. Если `name` — строка (а не `BackendName`), попытаться сконвертировать через `name = BackendName(name)`. При неудаче конверсии (`ValueError`) — выбросить `UnknownBackendError`
2. Проверить кеш `_INSTANCES_CACHE.get(name)`. Если есть — вернуть из кеша
3. Иначе вызвать `_create_backend_instance(name)`, положить результат в `_INSTANCES_CACHE[name]`, вернуть

### get_all_backends

1. Вернуть `[get_backend(BackendName.CLAUDE), get_backend(BackendName.CODEX)]` (фиксированный порядок)

## Зависимости

**От модулей проекта:** нет. `coding_agent_backend.py` — корневой модуль интерфейса, не импортирует другие модули проекта на уровне модуля. `ClaudeCodeBackend` и `CodexBackend` (живут в отдельных файлах) сами импортируют `config.WORKING_DIR` и другие нужные им вещи; интерфейс ничего об этом не знает.

`_create_backend_instance` использует **lazy import** конкретных бэкендов внутри функции — это сознательное архитектурное решение, чтобы `coding_agent_backend.py` оставался корнем графа зависимостей и не создавал циклов.

**Стандартная библиотека:**
- `abc` — для `ABC`, `abstractmethod`
- `dataclasses` — для `@dataclass(frozen=True)`
- `enum` — для `Enum`
- `signal` — для значений `signal.SIGTERM`, `signal.SIGINT`, `signal.SIGKILL` в `StopSignalStep.signal_to_send` (на macOS значения POSIX-стандартные)
- `typing` — для `Any`

## Обработка ошибок

- **Запрос неизвестного бэкенда (`get_backend("unknown")`)** — выбросить `UnknownBackendError` с сообщением, содержащим запрошенное имя и список доступных
- **Невалидный JSON в stdout (`parse_stdout_line_into_event`)** — выбросить `BackendProtocolError` с первыми 200 символами строки. Это контрактное нарушение CLI, не должно молча проглатываться
- **Файл сессии не существует или не читается (`read_messages_from_session_file` и `read_session_file_snapshot`)** — НЕ выбрасывать; вернуть пустой список / `SessionFileSnapshot` с пустыми полями и залогировать `warning` (файл может быть удалён между listing и чтением — гонка с Claude/Codex)
- **Файл сессии содержит невалидную JSON-строку** — пропустить эту строку, залогировать `debug`, продолжить чтение остальных. Для `read_session_file_snapshot` `raw_record_count` всё равно учитывает такие строки (счётчик стабилен для дельта-чтения watcher-ом)
- **Бинарник CLI не найден** — выбрасывает `BackendBinaryNotFoundError` НЕ при импорте, а при первом вызове `compose_subprocess_command_args` (lazy check; импорт модуля не должен падать, если у пользователя установлен только один из CLI)
- **`turn.failed` (Codex) или `result` с `is_error=True` (Claude)** — НЕ молчаливое превращение в пустой успешный ответ. `process_manager` обязан проверить `is_error_event(event)` или `read_terminal_status_from_event(event) == TerminalStatus.FAILED` и выставить `is_error=True` в `SendResult`. После этого работает штатный retry-цикл (до `MAX_RETRIES = 10`). Текст ошибки сохраняется через `read_error_text_from_event(event)` и попадает в логи и (при исчерпании ретраев) в финальное сообщение пользователю
- **Зависший процесс при `/stop`** — стратегия `get_stop_strategy()` гарантирует SIGKILL последним шагом, который ОС не может игнорировать. Если даже после SIGKILL `process.wait()` не возвращается за разумное время — это аномалия уровня ОС/zombie-процесс, на этот случай `process_manager` логирует `error` и продолжает работу (бот не должен зависнуть из-за одной зомби-сессии)

## Контракты с внешними системами

### Claude Code CLI — формат имени папки сессий

**Источник правды:** `~/Desktop/claude-sandbox/claude-code-sourcecode/sessionStoragePortable.ts:311` (функция `sanitizePath`).

**Алгоритм:** заменить все символы вне `[a-zA-Z0-9]` на `-`. Реализация в `session_reader.py:63-69` (паттерн `SANITIZE_PATH_PATTERN = re.compile(r"[^a-zA-Z0-9]")`, замена `SANITIZE_PATH_PATTERN.sub("-", project_dir)`).

**Перенос в `ClaudeCodeBackend`:** метод `locate_session_files_directory_for_project` использует тот же алгоритм. Тест-план обязан включать сверку с реальной папкой Claude CLI: создать сессию в каталоге с пробелами/русскими символами через CLI, прочитать имя созданной папки, сравнить с результатом `locate_session_files_directory_for_project` — должно совпадать побайтно.

### Claude Code CLI — формат stream-json в stdout

**Источник правды:** документ проекта `dev/docs/claude-cli-stream-json-protocol.md` + эмпирические наблюдения через реальный запуск CLI.

**Типы событий:**
- `system` — `{type, subtype, session_id, cwd, model, tools, claude_code_version}`
- `assistant` — `{type, message: {role, content: [{type, text|thinking|...}]}, session_id}`
- `user` — `{type, message: {role, content: [...]}, session_id}`
- `result` — `{type, subtype, is_error, result, session_id, duration_ms, num_turns}`

Финальное событие — `result`. Текст ответа — `event["result"]`.

### Claude Code CLI — формат stdin-сообщения

**Источник правды:** реальный код `claude_runner.py:102-118`. **КРИТИЧНО:** Claude CLI ТРЕБУЕТ формат `{"type": "user", "message": {"role": "user", "content": "<text>"}}`. Альтернативный формат `{"type": "user_message", "content": "<text>"}` (как было в предыдущей черновой спеке) приводит к молчаливому зависанию CLI без ошибки. После строки сообщения — `\n`, затем `stdin.close()` (EOF) — это сигнал CLI начать обработку.

### Codex CLI — формат команды запуска

**Источник правды:**
- Subcommands: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/cli/src/main.rs:102-176` (enum `Subcommand`)
- Флаги `codex exec`: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/cli.rs:14-82` и `~/.codex/custom-codex-rust-v0.128.0/codex-rs/utils/cli/src/shared_options.rs:8-57`
- Resume: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/cli.rs:169-218` (`ResumeArgs`)

**Команда новой сессии:** `codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C <cwd> [-i <image>]... <prompt>`

**Команда resume:** `codex exec resume <session_id> --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C <cwd> [-i <image>]... <prompt>`

Без `--skip-git-repo-check` Codex отказывается работать в не-git директориях.

### Codex CLI — формат событий stdout

**Источник правды:** `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/exec_events.rs:9-37` (enum `ThreadEvent`).

**Типы событий:**
- `thread.started` — `{type, thread_id}` (UUID v7)
- `turn.started` — `{type}`
- `turn.completed` — `{type, usage: {input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens}}`
- `turn.failed` — `{type, error: {message}}`
- `item.started` / `item.updated` / `item.completed` — `{type, item: {id, type: "<kind>", ...details}}`, где `<kind>` ∈ `{agent_message, reasoning, command_execution, file_change, mcp_tool_call, web_search, todo_list, error}`
- `error` — `{type, message}`

Финальное событие — `turn.completed` или `turn.failed`. Текст ответа — последний `item.completed` с `item.type == "agent_message"`, поле `item.text`.

### Codex CLI — формат файла сессии на диске

**Источник правды:**
- Путь: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/rollout/src/recorder.rs:1363-1393` (функция `precompute_log_file_info`) — `~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDTHH-MM-SS-<UUID>.jsonl`
- Формат записей: `~/.codex/custom-codex-rust-v0.128.0/codex-rs/protocol/src/protocol.rs:2808-2815` (enum `RolloutItem`) и `:2958-2962` (`RolloutLine`)
- `SessionMeta`: `protocol.rs:2748-2776` (поля `id`, `cwd`, `timestamp`, `originator`, `cli_version`, `source`, `model_provider`, `base_instructions`)

**Структура одной строки JSONL:** `{timestamp, type, payload}`, где `type ∈ {session_meta, response_item, compacted, turn_context, event_msg}`.

### Codex CLI — инструмент view_image

**Источник правды:** `~/.codex/custom-codex-rust-v0.128.0/codex-rs/core/src/tools/handlers/view_image.rs:47-187` и `~/.codex/custom-codex-rust-v0.128.0/codex-rs/utils/image/src/lib.rs:19,68-122`.

- Поддерживаемые форматы: PNG, JPEG, GIF, WebP
- Изображения с шириной/высотой > 2048 px ресайзятся до 2048
- Возвращает data URL `data:<mime>;base64,<encoded>`
- Capability check: если модель не поддерживает `InputModality::Image` — отказ с сообщением "view_image is not allowed because you do not support image inputs"

**Применимость к спеке:** в первой версии `CodexBackend.compose_subprocess_command_args` НЕ использует флаг `-i <path>` (хотя CLI его поддерживает через `~/.codex/custom-codex-rust-v0.128.0/codex-rs/utils/cli/src/shared_options.rs:9-17`). Путь к файлу включается в `prompt_text` модулем `claude_interaction` ровно так же, как для Claude, и Codex сам вызывает встроенный инструмент `view_image` по этому пути. Это сохраняет симметрию с Claude и не требует условных веток в `claude_interaction`. Capability check проверяется на уровне Codex; бэкенд не делает предварительной проверки модели — пользователь увидит ошибку в `turn.failed`. Контрактный тест `test_codex_view_image_path_in_prompt_text` проверяет, что Codex действительно вызывает `view_image` по пути в тексте промпта (см. контрактный тест-план Codex-спеки). Подробное обоснование решения и условия для перехода на `-i/--image` в будущем — в разделе «Расхождение с концепцией → Изображения для Codex».

### Codex CLI — отсутствие subcommand для остановки turn, корректный сигнал — SIGINT

**Источник правды:**
- `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/lib.rs:741-843` (обработка `tokio::signal::ctrl_c()`)
- `~/.codex/custom-codex-rust-v0.128.0/codex-rs/exec/src/lib.rs:521,543` (`InitiateShutdown` после `TurnInterrupt`)

**Точное поведение Codex:** CLI слушает только `SIGINT` (Ctrl+C). При получении SIGINT — отправляет на сервер `ClientRequest::TurnInterrupt` с `thread_id` и `turn_id`, дописывает в файл сессии событие прерывания и инициирует shutdown. **SIGTERM такой обработки не имеет** — процесс завершается жёстче, без `TurnInterrupt`, в JSONL не появляется штатное событие прерывания. SIGKILL — мгновенный kill без shutdown-handler-ов. Других механизмов прерывания извне НЕТ — нет CLI-флага, subcommand-а или API-эндпоинта.

**Применимость к спеке:** `process_manager` НЕ использует общий `process.terminate()` для всех бэкендов. Вместо этого вызывает `backend.get_stop_strategy()` и применяет последовательность сигналов. Для Codex: `SIGINT (5 сек ожидания) → SIGTERM (5 сек) → SIGKILL`. Первый шаг (SIGINT) даёт Codex шанс штатно прерваться, второй (SIGTERM) — fallback при зависании в неотменяемом сетевом вызове, третий (SIGKILL) — последний рубеж. Для Claude стратегия проще: `SIGTERM → SIGKILL` (Claude корректно отрабатывает SIGTERM). Backend-specific стратегия задана методом `get_stop_strategy()` и DTO `StopStrategy`/`StopSignalStep` — см. раздел «Расширения интерфейса для snapshot, terminal status и stop strategy».

### Эмпирические эксперименты, требуемые для подтверждения контрактов

Контракты с внешними CLI должны быть проверены реальными вызовами, а не догадками. Конкретные тест-кейсы (с конкретными командами и проверками) описаны в разделе «Тест-план» → подраздел «Контрактные тесты с CLI». Каждый тест соответствует одному контракту:

- Sanitize path Claude → `test_claude_sanitize_matches_real_cli_folder`
- Формат stdin Claude → `test_claude_stdin_format_is_accepted_by_real_cli`
- thread.started как первое событие Codex → `test_codex_thread_started_is_first_event`
- turn.completed как финальное событие Codex → `test_codex_turn_completed_is_terminal`
- session_meta.cwd Codex → `test_codex_session_meta_cwd_matches`
- Codex штатно прерывает turn по SIGINT и пишет событие в JSONL → `test_codex_sigint_writes_turn_interrupt_to_session_file`
- Codex ставит view_image по абсолютному пути из prompt-текста → `test_codex_view_image_path_in_prompt_text` (см. также `codex_backend_spec.md` → одноимённый тест в его контрактном разделе)

## Константы

- `MAX_RECENT_SESSIONS = 15` — максимум файлов сессий, возвращаемых из UI-метода `list_session_files_for_project`. Значение взято из существующей реализации `session_reader.py` (постоянная `MAX_RECENT_SESSIONS = 15`). Объяснение: BRD-требование к `/sessions` — «15 самых свежих сессий». Operational-метод `list_all_session_files_for_project` этот лимит не применяет.
- `PREVIEW_MAX_LENGTH = None` — превью для `/sessions` больше не имеет обязательной обрезки. Старые сохранённые строки с `...` могут быть заменены полным текстом при показе списка через дочитывание файла сессии.
- `BACKEND_DISPLAY_NAME_CLAUDE = "🤖 Claude"` — UI-метка бэкенда Claude. Эмодзи 🤖 (робот) — нейтральный «AI помощник», узнаваемый.
- `BACKEND_DISPLAY_NAME_CODEX = "⚡ Codex"` — UI-метка бэкенда Codex. Эмодзи ⚡ (молния) — намёк на скорость.
- `_INSTANCES_CACHE: dict[BackendName, CodingAgentBackend] = {}` — кеш singleton-инстансов на уровне модуля. Заполняется лениво при первом вызове `get_backend`. Не очищается за время жизни процесса (бэкенды без состояния, держать один инстанс безопасно).

## Тест-план

### Юнит-тесты

- **test_backend_name_enum_values** — проверка что `BackendName.CLAUDE.value == "claude"` и `BackendName.CODEX.value == "codex"`. Тип: unit.

- **test_backend_name_inherits_str** — `isinstance(BackendName.CLAUDE, str)` должно быть `True` (нужно для прямой записи в JSON). Тип: unit.

- **test_session_file_info_is_frozen** — попытка `info.session_id = "x"` должна выбросить `dataclasses.FrozenInstanceError`. Тип: unit.

- **test_session_message_is_frozen** — аналогично для `SessionMessage`. Тип: unit.

- **test_session_file_snapshot_is_frozen** — попытка `snapshot.raw_record_count = 99` должна выбросить `dataclasses.FrozenInstanceError`. Тип: unit.

- **test_session_file_snapshot_with_empty_messages** — `SessionFileSnapshot(messages=[], raw_record_count=0, last_record=None, is_turn_active=False)` создаётся без ошибки (валидное представление пустого файла). Тип: unit.

- **test_session_file_snapshot_with_active_turn** — `SessionFileSnapshot(messages=[SessionMessage(...)], raw_record_count=5, last_record={"type":"assistant",...}, is_turn_active=True)` — все поля доступны через атрибуты. Тип: unit.

- **test_terminal_status_enum_values** — `TerminalStatus.SUCCESS.value == "success"`, `TerminalStatus.FAILED.value == "failed"`. Тип: unit.

- **test_terminal_status_inherits_str** — `isinstance(TerminalStatus.SUCCESS, str)` → `True` (для прямой записи в логи и JSON). Тип: unit.

- **test_stop_signal_step_is_frozen** — попытка `step.signal_to_send = 9` должна падать с `FrozenInstanceError`. Тип: unit.

- **test_stop_strategy_is_frozen** — попытка `strategy.steps = ()` должна падать. Тип: unit.

- **test_stop_strategy_steps_is_tuple** — `isinstance(strategy.steps, tuple)` → `True` (не list, чтобы исключить мутацию). Тип: unit.

- **test_coding_agent_backend_cannot_be_instantiated** — `CodingAgentBackend()` должен выбрасывать `TypeError` (потому что abstract). Тип: unit.

- **test_subclass_without_all_methods_cannot_be_instantiated** — определить `class IncompleteBackend(CodingAgentBackend): pass` — `IncompleteBackend()` должен падать с `TypeError`, перечисляя нереализованные абстрактные методы. В перечне обязательно присутствуют новые методы: `read_session_file_snapshot`, `is_error_event`, `read_error_text_from_event`, `read_terminal_status_from_event`, `get_stop_strategy`. Тип: unit.

- **test_get_backend_returns_singleton** — два вызова `get_backend(BackendName.CLAUDE)` должны вернуть один и тот же объект (`is` comparison). Тип: unit.

- **test_get_backend_returns_correct_type_claude** — `get_backend(BackendName.CLAUDE)` должен вернуть инстанс `ClaudeCodeBackend`. Тип: unit (требует импорт реализации).

- **test_get_backend_returns_correct_type_codex** — `get_backend(BackendName.CODEX)` должен вернуть инстанс `CodexBackend`. Тип: unit (требует импорт реализации).

- **test_get_all_backends_returns_two_in_fixed_order** — `[b.name for b in get_all_backends()] == [BackendName.CLAUDE, BackendName.CODEX]`. Тип: unit.

- **test_unknown_backend_raises** — `_create_backend_instance("not_a_backend")` (передавая строку, минуя Enum) или `get_backend("not_a_backend")` должен выбросить `UnknownBackendError` с сообщением, содержащим список доступных. Тип: unit.

- **test_backend_error_hierarchy** — `BackendBinaryNotFoundError`, `BackendProtocolError`, `UnknownBackendError` все должны быть подклассами `BackendError`, который — подкласс `Exception`. Тип: unit.

### Граничные случаи

- **test_get_all_backends_does_not_create_duplicates** — повторный вызов `get_all_backends()` возвращает те же инстансы (singleton-инвариант сохраняется). Тип: edge case.

- **test_session_message_with_none_timestamp** — `SessionMessage(role="user", text="x", timestamp=None, is_empty_response=False)` должен создаваться без ошибки. Тип: edge case.

- **test_session_file_info_with_empty_preview** — `SessionFileInfo(session_id="abc", file_path="/x", last_modified_at=0.0, preview="")` — preview пустой допустим (сессия без сообщений). Тип: edge case.

- **test_unified_event_can_be_empty_dict** — `UnifiedEvent` это `dict[str, Any]`; пустой словарь `{}` — валидный `UnifiedEvent`. Никаких runtime-проверок. Тип: edge case.

### Тесты для расширений (snapshot, error event, terminal status, stop strategy)

Эти тесты применяются к ОБОИМ конкретным бэкендам через параметризованные фикстуры (`@pytest.fixture(params=[BackendName.CLAUDE, BackendName.CODEX])`). Конкретные данные событий/файлов — в спеках бэкендов; здесь фиксируются только инвариантные свойства интерфейса.

- **test_read_session_file_snapshot_returns_snapshot_dataclass** — для каждого бэкенда: `result = await backend.read_session_file_snapshot("/tmp/empty.jsonl")` → `isinstance(result, SessionFileSnapshot)`. Тип: unit.

- **test_read_session_file_snapshot_for_empty_file_has_zero_count_and_inactive_turn** — async-тест: создать пустой JSONL, вызвать snapshot — `messages == []`, `raw_record_count == 0`, `last_record is None`, `is_turn_active is False`. Тип: edge case.

- **test_read_session_file_snapshot_messages_match_read_messages_from_session_file** — async-тест: для непустого файла `(await backend.read_session_file_snapshot(path)).messages == await backend.read_messages_from_session_file(path)`. Гарантия совместимости двух API на одинаковых данных. Тип: unit.

- **test_read_session_file_snapshot_raw_record_count_includes_non_message_lines** — async-тест: создать JSONL с двумя `user`-записями и одной `system`-записью между ними — `raw_record_count == 3`, `len(messages) == 2`. Гарантия, что счётчик считает сырые строки, а не отфильтрованные сообщения. Тип: edge case.

- **test_is_error_event_false_for_non_terminal_event** — для `{}`, `{"type": "thread.started"}` (Codex), `{"type": "assistant"}` (Claude), `{"type": "system"}` — `is_error_event` возвращает `False`. Тип: unit.

- **test_is_error_event_implies_is_turn_complete_event** — параметризованный тест: для каждого «ошибочного финала» (Claude `{"type": "result", "is_error": True, "result": "x"}`, Codex `{"type": "turn.failed", "error": {"message": "x"}}`) — `is_error_event == True` И `is_turn_complete_event == True`. Тип: unit (контракт интерфейса).

- **test_read_error_text_from_event_returns_none_for_non_error_event** — для всех событий, где `is_error_event == False` — `read_error_text_from_event` возвращает `None`. Тип: unit.

- **test_read_error_text_from_event_returns_text_for_error_event** — для ошибочного финала — `read_error_text_from_event` возвращает непустую строку с текстом ошибки. Тип: unit.

- **test_read_terminal_status_from_event_returns_none_for_non_terminal** — для нефинального события — `None`. Тип: unit.

- **test_read_terminal_status_from_event_returns_failed_for_error_event** — для ошибочного финала — `TerminalStatus.FAILED`. Тип: unit.

- **test_read_terminal_status_from_event_returns_success_for_clean_completion** — для штатного финала (Claude `{"type": "result", "is_error": False, "result": "ok"}`, Codex `{"type": "turn.completed", "usage": {...}}`) — `TerminalStatus.SUCCESS`. Тип: unit.

- **test_get_stop_strategy_returns_stop_strategy_dataclass** — `isinstance(backend.get_stop_strategy(), StopStrategy)` для обоих бэкендов. Тип: unit.

- **test_get_stop_strategy_steps_non_empty** — `len(backend.get_stop_strategy().steps) >= 1` для обоих бэкендов. Тип: unit.

- **test_get_stop_strategy_last_step_is_sigkill** — последний шаг стратегии для обоих бэкендов — `signal.SIGKILL` (последний рубеж унифицирован). Тип: unit.

- **test_get_stop_strategy_claude_starts_with_sigterm** — для Claude: `backend.get_stop_strategy().steps[0].signal_to_send == signal.SIGTERM`. Тип: unit.

- **test_get_stop_strategy_codex_starts_with_sigint** — для Codex: `backend.get_stop_strategy().steps[0].signal_to_send == signal.SIGINT` (этот шаг — критичная backend-specific деталь, см. ревью 06-05 недоделка №4). Тип: unit.

- **test_get_stop_strategy_codex_has_sigterm_before_sigkill** — для Codex: `signal.SIGTERM` присутствует среди `steps` ДО `signal.SIGKILL` (промежуточный fallback). Тип: unit.

- **test_get_stop_strategy_returns_singleton** — `backend.get_stop_strategy() is backend.get_stop_strategy()` (один и тот же объект, чтобы process_manager не пересоздавал). Тип: unit.

### Тесты владения сессией для потребителей (контракт)

Эти тесты применяются к самому `coding_agent_backend` опосредованно — через структуру DTO и через документированный контракт. Их полный набор — в спеках потребителей (`daily_session_registry_spec.md`, `session_manager_spec.md`, `process_manager_spec.md`, `session_watcher_spec.md`, `unread_buffer_spec.md`), но критические инварианты фиксируются и здесь:

- **test_daily_session_entry_has_session_id_and_backend** — `DailySessionEntry(session_id="x", backend=BackendName.CLAUDE)` — оба поля обязательны и доступны. (DTO определён в `daily_session_registry_spec.md`, но контракт интерфейса требует, чтобы `BackendName` использовался как тип поля.) Тип: unit.

- **test_active_session_has_session_id_and_backend** — аналогично для `ActiveSession` из `session_manager_spec.md`. Тип: unit.

- **test_existing_session_uses_its_own_backend_not_current_global** — интеграционный поверх `process_manager.send_message`: создать сессию через Claude, переключить `current_backend_registry` на Codex, отправить сообщение со старым `session_id` и `backend=BackendName.CLAUDE` — `process_manager` запускает Claude-subprocess, не Codex. Тип: integration (контракт владения).

- **test_new_session_uses_current_global_backend** — верхний слой читает `current_backend_registry.get_current()`, создаёт новую temp-сессию с этим backend-ом и вызывает `process_manager.send_message(..., session_id=temp_id, backend=<current>)`. `process_manager` получает явный backend и не читает registry сам. Тип: integration.

- **test_orphan_cleanup_does_not_remove_codex_session_with_existing_file** — `daily_session_registry.cleanup_orphans(project_dir)` для Codex-entry с существующим файлом в `~/.codex/sessions/...` НЕ удаляет запись. Защита от регрессии «cleanup ищет всё в Claude-папке». Тип: integration.

- **test_unread_buffer_keys_are_session_id_plus_backend** — `unread_buffer.save_snapshot(session_id="uuid-1", backend=BackendName.CLAUDE, ...)` и `save_snapshot(session_id="uuid-1", backend=BackendName.CODEX, ...)` — два разных ключа, snapshot не перетирается. Тип: unit.

### Тесты ошибок

- **test_get_backend_accepts_string_through_enum_conversion** — `get_backend("claude")` (строка вместо Enum) должен вернуть тот же singleton, что и `get_backend(BackendName.CLAUDE)`. Это заявленный контракт (см. алгоритм `get_backend` шаг 1 — автоконверсия). Тип: error (проверка ошибочного использования API).

- **test_get_backend_with_invalid_string_raises_unknown_backend_error** — `get_backend("not_a_backend")` должен выбросить `UnknownBackendError`, потому что `BackendName("not_a_backend")` падает с ValueError. Тип: error.

- **test_unknown_backend_error_message_lists_available** — сообщение `UnknownBackendError` должно перечислять все доступные имена бэкендов («claude», «codex»), чтобы разработчик мог быстро увидеть опечатку. Тип: error.

### Контрактные тесты с CLI (опциональные интеграционные)

Эти тесты НЕ должны падать в CI без установленных CLI. Используй `pytest.mark.skipif(shutil.which("claude") is None, reason="Claude CLI not installed")` и `pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI not installed")`.

- **test_claude_sanitize_matches_real_cli_folder** — создать tmp директорию с пробелами и не-ASCII (`/tmp/test session 一二三`), запустить `claude -p "say x" --output-format stream-json` в этой директории через subprocess, дождаться завершения, найти созданную папку в `~/.claude/projects/`, сравнить её имя с результатом `ClaudeCodeBackend().locate_session_files_directory_for_project("/tmp/test session 一二三")`. Должно совпадать. Тип: contract / integration.

- **test_claude_stdin_format_is_accepted_by_real_cli** — запустить `claude -p --input-format stream-json --output-format stream-json`, передать в stdin байты, возвращаемые `ClaudeCodeBackend().encode_user_message_for_cli_stdin("banana", [])` + EOF, прочитать stdout, убедиться, что есть событие `result` с непустым `result`. Тип: contract / integration.

- **test_codex_thread_started_is_first_event** — запустить `codex exec --json --skip-git-repo-check -C /tmp "say hi"` через subprocess, прочитать первую строку stdout, разобрать через `CodexBackend().parse_stdout_line_into_event`, проверить что `read_session_id_from_event` возвращает UUID-строку. Тип: contract / integration.

- **test_codex_turn_completed_is_terminal** — продолжая предыдущий тест, прочитать все строки до конца, найти событие где `is_turn_complete_event() == True`, убедиться что это `turn.completed` или `turn.failed` и что после него процесс завершается (process.wait() возвращает в течение 2 сек). Тип: contract / integration.

- **test_codex_session_meta_cwd_matches** — после теста выше найти файл `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (последний по mtime), прочитать первую строку через `CodexBackend().read_messages_from_session_file` (или прямо через `json.loads`), убедиться что `payload.cwd == "/tmp"`. Тип: contract / integration.

- **test_codex_sigint_writes_turn_interrupt_to_session_file** — запустить долгий Codex turn (`codex exec --json -C /tmp "посчитай простые числа до миллиона"`), дождаться `thread.started`, через ~5 секунд отправить процессу `SIGINT` (`process.send_signal(signal.SIGINT)`), дождаться завершения. Найти файл сессии (по UUID из `thread.started`), прочитать содержимое — должно быть событие прерывания turn-а (`event_msg.payload.type == "turn_aborted"` или аналогичный маркер из `~/.codex/custom-codex-rust-v0.128.0/codex-rs/protocol/src/protocol.rs`). Это эмпирическое подтверждение: SIGINT → штатное прерывание (отличается от SIGTERM, который завершает процесс без события прерывания). Прямой тест критичной разницы из ревью №4. Тип: contract / integration.

- **test_claude_sigterm_completes_session_file_cleanly** — запустить Claude turn (`claude -p ... --resume <id>`), через стандартное время отправить `process.terminate()` (= SIGTERM). Дождаться `process.wait()` — процесс должен завершиться без зомби. Файл сессии `~/.claude/projects/<sanitized>/<id>.jsonl` должен быть валидным JSONL до последней строки (без частично записанных JSON). Подтверждение, что для Claude SIGTERM — штатный сигнал. Тип: contract / integration.

- **test_stop_strategy_actually_stops_long_running_turn** — параметризованный тест на оба бэкенда: запустить долгий turn, через секунду применить `backend.get_stop_strategy()` через хелпер `process_manager._apply_stop_strategy(process, strategy)` (или прямо вручную в тесте). Убедиться, что процесс завершился в пределах суммы таймаутов всех шагов + запас 2 секунды. Тип: contract / integration.
