# Спецификация модуля: claude_code_backend

Дата: 06-05-2026
Слой: 1 (зависит только от `coding_agent_backend` — слой 0)
Файл: `src/claude_manager/claude_code_backend.py`

**Родительская спека:** `dev/docs/specs/coding_agent_backend_spec.md` — определяет абстрактный интерфейс `CodingAgentBackend`, общие DTO (`UnifiedEvent`, `SessionFileInfo`, `SessionMessage`), исключения (`BackendError`, `BackendBinaryNotFoundError`, `BackendProtocolError`), фабрику `get_backend`.

**Парные спеки (написаны/будут написаны отдельно):**
- `dev/docs/specs/codex_backend_spec.md` — реализация интерфейса для Codex CLI (вторая ветка Adapter pattern)
- `dev/docs/specs/current_backend_registry_spec.md` — персистентное хранилище выбранного бэкенда

## Назначение

Конкретная реализация абстрактного интерфейса `CodingAgentBackend` для Claude Code CLI. Класс `ClaudeCodeBackend` инкапсулирует всю «локальную правду» о работе с Claude CLI: формат команды запуска subprocess, формат stdin-сообщения, структура потоковых событий stream-json в stdout, расположение JSONL-файлов сессий на диске, алгоритм кодирования имени папки проекта (sanitize path).

Модуль не запускает subprocess сам — это делает `process_manager`. Не управляет лайфтаймом процессов, не реализует ретраи. Его задача — отвечать на вопросы вызывающей стороны: «какие аргументы команды для запуска?», «как закодировать сообщение в stdin?», «как распарсить строку из stdout?», «где лежит файл сессии?». Это чистый адаптер между внешним инструментом (Claude CLI) и внутренним контрактом (`CodingAgentBackend`).

Реализация переиспользует существующий рабочий код проекта: `claude_runner.py` (формат stdin, флаги CLI, парсинг событий, лимит буфера), `session_reader.py` (sanitize path, чтение JSONL, превью), `process_manager.py` (приоритет text > thinking, маркер `"No response requested."`, извлечение текста из `event["result"]`). Адаптация — не «переписывание»: алгоритмы и константы переносятся побайтно, проверены эмпирически на боевом боте.

## Обслуживаемые сценарии

Сам модуль не обслуживает CJM напрямую (он — инфраструктура), но без его методов не работают:

- **CJM-02 (текстовое сообщение)** — `compose_subprocess_command_args`, `encode_user_message_for_cli_stdin`, `parse_stdout_line_into_event`, `is_turn_complete_event`, `read_session_id_from_event`, `read_assistant_text_from_event`, `read_progress_text_from_event`, `text_markers_indicating_empty_response` используются `process_manager` для запуска CLI и извлечения ответа
- **CJM-03 (фото или файл)** — те же методы; путь к файлу включён в `prompt_text` модулем `claude_interaction`, метод `encode_user_message_for_cli_stdin` оборачивает текст в JSON-сообщение для stdin
- **CJM-04 (`/new`)** — `compose_subprocess_command_args(session_id=None, ...)` формирует команду без флага `--resume`
- **CJM-05 (`/sessions`)** — `locate_session_files_directory_for_project`, `list_session_files_for_project`, `read_messages_from_session_file` используются `session_reader`/потребителем для чтения метаданных сессий с диска
- **CJM-06 (`/N`)** — `compose_subprocess_command_args(session_id=<id>, ...)` формирует команду с `--resume <id>`
- **CJM-07 (`/all`)** — `list_all_session_files_for_project`, `read_messages_from_session_file`, `event_types_meaning_cli_is_busy`, `text_markers_indicating_empty_response` используются `session_watcher` для слежения за файлами в реальном времени
- **CJM-08 (`/stop`)** — модуль участвует через `get_stop_strategy()`: возвращает `StopStrategy` со списком шагов `StopSignalStep`, который `process_manager` применяет к subprocess Claude CLI. Для Claude стратегия короткая (SIGTERM → подождать 5 секунд → SIGKILL), без промежуточного SIGINT-шага. Маркер `"No response requested."` остаётся внутренним fallback-ом, видимым через `text_markers_indicating_empty_response()`, но он сигнал содержательный («turn закончился, ответа не было»), а не сигнал к остановке

Также модуль обслуживает новый сценарий, который будет добавлен в BRD при реализации фичи переключения бэкенда:

- **CJM-NEW (`/agent`)** — фабрика `get_backend(BackendName.CLAUDE)` (определена в `coding_agent_backend`, не в этой спеке) возвращает singleton-инстанс `ClaudeCodeBackend`. Модуль предоставляет свойства `name` и `display_name` для UI команды `/agent`

На момент написания спецификации CJM-NEW в `dev/docs/brd/brd-user-journeys.md` отсутствует — он будет добавлен отдельной задачей перед реализацией модуля.

## Публичный API

### Класс `ClaudeCodeBackend(CodingAgentBackend)`

Реализация абстрактного интерфейса для Claude Code CLI. Без хранимого состояния (stateless) — все методы работают со своими аргументами, не читают и не пишут поля экземпляра. Singleton-инстанс создаётся фабрикой `get_backend(BackendName.CLAUDE)` из `coding_agent_backend`.

```python
class ClaudeCodeBackend(CodingAgentBackend):
    """Adapter pattern для Claude Code CLI. Реализует все 18 методов и 2 свойства интерфейса."""
```

#### Свойство `name`

```python
@property
def name(self) -> BackendName:
    return BackendName.CLAUDE
```

Возвращает идентификатор бэкенда. Используется потребителями для записи в `daily_session_registry`, для сравнения в фабрике, для логов.

#### Свойство `display_name`

```python
@property
def display_name(self) -> str:
    return BACKEND_DISPLAY_NAME_CLAUDE  # "🤖 Claude"
```

Возвращает человекочитаемое имя бэкенда с эмодзи. Используется в UI Telegram: командах `/agent`, `/sessions`, формате ретрая (`#N Ошибка 🤖 Claude, повтор X/10`).

#### `compose_subprocess_command_args(session_id, cwd, prompt_text, image_paths) -> list[str]`

```python
def compose_subprocess_command_args(
    self,
    session_id: str | None,
    cwd: str,
    prompt_text: str,
    image_paths: list[str],
) -> list[str]: ...
```

Формирует список аргументов командной строки для запуска subprocess (включая бинарник `claude` как `args[0]`).

**Аргументы:**
- `session_id` (`str | None`) — UUID сессии для resume или `None` для новой сессии
- `cwd` (`str`) — рабочая директория проекта. Игнорируется этим методом (Claude CLI не имеет флага установки cwd; рабочая директория задаётся через параметр `cwd=` функции `asyncio.create_subprocess_exec`, что делает потребитель `process_manager`). Аргумент сохранён в сигнатуре для совместимости с интерфейсом `CodingAgentBackend`
- `prompt_text` (`str`) — текст пользовательского сообщения. Игнорируется этим методом — Claude получает сообщение через stdin (см. `encode_user_message_for_cli_stdin`)
- `image_paths` (`list[str]`) — пути к изображениям. Игнорируются — путь к файлу уже включён в `prompt_text` модулем `claude_interaction`, Claude сам читает файл инструментом Read

**Возвращает:** `list[str]` — `[CLAUDE_CLI_COMMAND, "-p", "--output-format", "stream-json", "--verbose", "--input-format", "stream-json", "--dangerously-skip-permissions", "--effort", "max"]` плюс `["--resume", session_id]` если `session_id is not None`.

**Исключения:**
- `BackendBinaryNotFoundError` — бинарник `claude` не найден в `PATH` и не существует по пути `/usr/local/bin/claude`. Проверка lazy: происходит при первом вызове метода, а не при импорте модуля (см. раздел «Обработка ошибок»)

#### `encode_user_message_for_cli_stdin(prompt_text, image_paths) -> bytes`

```python
def encode_user_message_for_cli_stdin(
    self,
    prompt_text: str,
    image_paths: list[str],
) -> bytes: ...
```

Кодирует пользовательское сообщение в байты для записи в stdin процесса Claude CLI.

**Аргументы:**
- `prompt_text` (`str`) — текст сообщения
- `image_paths` (`list[str]`) — пути к изображениям. Игнорируются (включаются в `prompt_text` модулем `claude_interaction` как часть текста задачи)

**Возвращает:** `bytes`. Содержимое — одна строка JSONL в UTF-8: `{"type":"user","message":{"role":"user","content":<prompt_text>}}\n`. Не-ASCII символы НЕ экранируются (`json.dumps(..., ensure_ascii=False)`) — это сохраняет кириллицу читаемой в логах stdin и не увеличивает размер сообщения.

**Исключения:** не выбрасывает.

#### `parse_stdout_line_into_event(raw_line) -> UnifiedEvent | None`

```python
def parse_stdout_line_into_event(self, raw_line: str) -> UnifiedEvent | None: ...
```

Парсит одну строку stdout (одна строка JSONL) в унифицированное событие.

**Аргументы:**
- `raw_line` (`str`) — строка из stdout без завершающего `\n`, в UTF-8

**Возвращает:** `UnifiedEvent` (тип-алиас `dict[str, Any]`) — результат `json.loads(raw_line)`. Либо `None`, если строка пустая (после `strip()`).

**Исключения:**
- `BackendProtocolError` — `raw_line` не парсится как валидный JSON. Сообщение исключения содержит первые 200 символов строки. Это контрактное нарушение CLI — Claude CLI обязан выдавать валидный JSON в stream-json режиме

#### `is_turn_complete_event(event) -> bool`

```python
def is_turn_complete_event(self, event: UnifiedEvent) -> bool: ...
```

Возвращает `True`, если событие означает завершение текущего turn-а — после него `process_manager` должен прекратить чтение stdout.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `bool`. Возвращает `True` тогда и только тогда, когда `event.get("type") == EVENT_TYPE_RESULT` (значение константы — строка `"result"`).

#### `read_session_id_from_event(event) -> str | None`

```python
def read_session_id_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает идентификатор сессии из события.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`. Возвращает значение поля `session_id`, если оно есть. Иначе `None`. У Claude CLI поле `session_id` присутствует в событиях `system`, `assistant`, `user`, `result` — то есть почти во всех. Метод устойчив к будущим изменениям протокола: если поле исчезнет в каком-то типе события — вернётся `None`, потребитель просто пропустит это событие при поиске id.

#### `read_assistant_text_from_event(event) -> str | None`

```python
def read_assistant_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает финальный текст ответа ассистента из события.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`.
- Если `event.get("type") != EVENT_TYPE_RESULT` — возвращает `None` (это не финальное событие)
- Если `event.get("type") == EVENT_TYPE_RESULT`:
  - Берёт `text = event.get("result")`
  - Если `text is None` — возвращает `""` (пустая строка — сигнал «turn закончился, но текста не было»)
  - Если `text == EMPTY_RESPONSE_MARKER` (строка `"No response requested."`) — возвращает `""` (синтетическое сообщение CLI, не настоящий ответ модели)
  - Иначе — возвращает `text` как есть

Семантика возврата: `None` означает «событие не финальное, читай дальше», пустая строка — «turn окончен, ответа нет», непустая строка — «вот ответ».

#### `read_progress_text_from_event(event) -> str | None`

```python
def read_progress_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает текст промежуточного обновления (для отправки пользователю как progress-сообщение `#N ⏳ ...`).

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие

**Возвращает:** `str | None`.
- Если `event.get("type") != EVENT_TYPE_ASSISTANT` — возвращает `None`
- Если событие — assistant: проходит по списку `event["message"]["content"]` (список content-блоков):
  - Запоминает первое значение поля `text` для блока `type == CONTENT_BLOCK_TEXT` (строка `"text"`)
  - Запоминает первое значение поля `thinking` для блока `type == CONTENT_BLOCK_THINKING` (строка `"thinking"`)
  - Возвращает `text_content or thinking_content` — приоритет text над thinking, если оба пустые/отсутствуют, возвращает `None`

Приоритет text > thinking зафиксирован по поведению `process_manager.py:121-138` (функция `_extract_progress_text`). Это эмпирически установленное поведение: когда Claude в одном assistant-событии выдаёт и thinking, и text — пользователю интереснее увидеть text (готовая фраза), а не сырое размышление.

#### `locate_session_files_directory_for_project(project_dir) -> str`

```python
def locate_session_files_directory_for_project(self, project_dir: str) -> str: ...
```

Возвращает абсолютный путь к директории, где Claude CLI хранит JSONL-файлы сессий для данного проекта.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта (например, `/Users/ivan/Desktop/claude-sandbox/claude_manager`)

**Возвращает:** `str`. Формат: `<HOME>/.claude/projects/<sanitized>`, где:
- `HOME` — домашняя директория пользователя (`os.path.expanduser("~")`)
- `sanitized` — результат применения паттерна `SANITIZE_PATH_PATTERN` (`re.compile(r"[^a-zA-Z0-9]")`) к `project_dir`: все символы вне набора `[a-zA-Z0-9]` заменяются на `-`

Пример: `/Users/ivan/Desktop/claude-sandbox/claude_manager` → `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude-manager` (подчёркивание → дефис).

Алгоритм sanitize переносится без изменений из `session_reader.py:48,63-69` (константа `SANITIZE_PATH_PATTERN` и функция `_encode_project_path`).

**Исключения:** не выбрасывает.

#### `list_session_files_for_project(project_dir) -> list[SessionFileInfo]`

```python
async def list_session_files_for_project(
    self, project_dir: str
) -> list[SessionFileInfo]: ...
```

Возвращает список метаданных JSONL-файлов сессий проекта, отсортированный от свежих к старым, не более `MAX_RECENT_SESSIONS` элементов.

**Аргументы:**
- `project_dir` (`str`) — абсолютный путь к директории проекта

**Возвращает:** `list[SessionFileInfo]`. Каждый элемент содержит `session_id` (UUID, имя файла без `.jsonl`), `file_path` (абсолютный путь к JSONL), `last_modified_at` (значение `os.path.getmtime`), `preview` (первое настоящее сообщение пользователя, очищенное от XML-тегов, обрезанное до `PREVIEW_MAX_LENGTH = 120` символов).

**Исключения:** не выбрасывает. Все ошибки обрабатываются:
- Папка проекта не существует или не директория — возвращает `[]` и логирует `warning`
- Ошибка чтения папки (`OSError`) — возвращает `[]` и логирует `error`
- Ошибка чтения отдельного файла — пропускает файл, логирует `warning`, продолжает с остальными
- Транзиентный `OSError` (включая `EDEADLK` — errno 11 на macOS при конкуренции процессов Claude за файлы в одной папке) — пропускает файл, логирует `warning`, продолжает (см. CLAUDE.md → «Транзиентная ошибка EDEADLK»)

**Async** — потому что чтение каталога и файлов (десятки JSONL по 50 строк каждый) может блокировать event loop на десятки миллисекунд. Все блокирующие I/O-операции выполняются через `asyncio.to_thread`.

#### `list_all_session_files_for_project(project_dir) -> list[SessionFileInfo]`

```python
async def list_all_session_files_for_project(
    self, project_dir: str
) -> list[SessionFileInfo]: ...
```

Возвращает все метаданные JSONL-файлов сессий проекта, отсортированные от свежих к старым, без ограничения `MAX_RECENT_SESSIONS`. Это operational API для watcher-а, pending delivery, reset state и ownership-проверок; UI-команда `/sessions` продолжает использовать `list_session_files_for_project`.

**Аргументы:** те же, что у `list_session_files_for_project`.

**Возвращает:** `list[SessionFileInfo]` для всех найденных файлов проекта. Каждый элемент содержит те же поля: `session_id`, `file_path`, `last_modified_at`, `preview`.

**Исключения:** те же, что у `list_session_files_for_project`; метод не выбрасывает и пропускает проблемные файлы.

#### `read_messages_from_session_file(file_path) -> list[SessionMessage]`

```python
async def read_messages_from_session_file(
    self, file_path: str
) -> list[SessionMessage]: ...
```

Читает все сообщения (user + assistant) из JSONL-файла сессии и возвращает унифицированный список.

**Аргументы:**
- `file_path` (`str`) — абсолютный путь к JSONL-файлу сессии

**Возвращает:** `list[SessionMessage]`. Хронологический порядок (как в файле). Для каждой записи `{type, message, timestamp, ...}` в JSONL:
- Если `type == EVENT_TYPE_USER` (строка `"user"`) и запись не помечена `isMeta: true` — извлекается текст из `message.content` (может быть строкой или списком блоков с `type == "text"`), создаётся `SessionMessage(role="user", text=<текст>, timestamp=<timestamp>, is_empty_response=False)`
- Если `type == EVENT_TYPE_ASSISTANT` (строка `"assistant"`) — извлекается текст из `message.content` аналогично, создаётся `SessionMessage(role="assistant", text=<текст>, timestamp=<timestamp>, is_empty_response=<text == EMPTY_RESPONSE_MARKER>)`
- Остальные типы (`system`, `result`, события без `type`) пропускаются
- Записи с пустым/отсутствующим текстом не пропускаются — они попадают в результат как `text=""` (потребитель сам решает, что с ними делать)

**Исключения:** не выбрасывает. Файл не существует или нет прав на чтение — возвращает `[]`, логирует `warning`/`error`. Невалидная JSON-строка внутри файла — пропускается, логируется `warning` (с номером строки), чтение продолжается.

**Async** — по тем же причинам, что и `list_session_files_for_project`. Все блокирующие операции через `asyncio.to_thread`.

#### `text_markers_indicating_empty_response() -> frozenset[str]`

```python
def text_markers_indicating_empty_response(self) -> frozenset[str]: ...
```

Возвращает множество строк, которые Claude CLI использует как маркеры пустого ответа (синтетические placeholder-сообщения, см. протокол).

**Возвращает:** `frozenset({"No response requested."})`.

Значение зафиксировано по `process_manager.py:56` (`EMPTY_RESPONSE_MARKER`) и протокольной документации `dev/docs/claude-cli-stream-json-protocol.md` (раздел «Синтетические сообщения в JSONL-файлах сессий»). Через stream-json эти маркеры обычно фильтруются самим CLI и не попадают в `event["result"]` — но для защиты от регрессии CLI и для чтения JSONL-файлов напрямую (`read_messages_from_session_file`) множество всё равно нужно.

**Полный набор синтетических user-сообщений Claude CLI** (зафиксированы эмпирически из исходников `@anthropic-ai/claude-code@2.1.121`, файл `utils/messages.ts` строки 207-247, константа-Set `SYNTHETIC_MESSAGES` строки 302-308):

- `"No response requested."` — turn не требует ответа от модели (восстановление сессии).
- `"[Request interrupted by user]"` — пользователь нажал `/stop` или Escape вне контекста tool_use.
- `"[Request interrupted by user for tool use]"` — то же, но во время использования инструмента.
- `"The user doesn't want to take this action right now. STOP what you are doing and wait for the user to tell you how to proceed."` — пользователь отменил действие в UI permission-промпте (Cancel).
- `"The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the new_string was NOT written to the file). STOP what you are doing and wait for the user to tell you how to proceed."` — пользователь явно отверг конкретный tool_use (Reject).

Все маркеры записываются в JSONL-файл сессии как user-запись с полем `model: "<synthetic>"` (константа `SYNTHETIC_MODEL`, `utils/messages.ts:300`). Это технический признак «сообщение поставила сама CLI, не модель».

**Что делает метод `text_markers_indicating_empty_response()`.** Возвращает только тот подмножество синтетических маркеров, которое означает «turn закончился без содержательного ответа» — для решения `process_manager` о retry. Текущая реализация: `frozenset({"No response requested."})`. Расширение до полного `SYNTHETIC_MESSAGES` — отдельное архитектурное решение: разные маркеры имеют разную семантику для retry (например, `INTERRUPT_MESSAGE` означает «пользователь прервал сам, retry не нужен» — это решается через `/stop`-flow, а не через `text_markers_indicating_empty_response`). Фиксация полного набора — для будущей реализации watcher'а, которому нужно распознавать оборванный turn в JSONL-файлах сессий и не оставлять их «недозавершёнными» в реестре.

**Источник истины при будущих обновлениях Claude CLI:** при апгрейде версии CLI обязательно перепроверить `utils/messages.ts` — строки могут поменяться. Закрепить новые значения в этой секции, обновить версию пакета.

#### `event_types_meaning_cli_is_busy() -> frozenset[str]`

```python
def event_types_meaning_cli_is_busy(self) -> frozenset[str]: ...
```

Возвращает множество значений поля `type` в JSONL-записях, которые означают «CLI всё ещё работает над текущим turn-ом — не помечать сообщения как финальные». Используется `session_watcher` при чтении JSONL-файлов в реальном времени: если последнее событие в файле — одного из этих типов, это значит, что turn ещё не завершён, и пользователю нельзя показывать промежуточные сообщения как финальные.

**Возвращает:** `frozenset({"assistant", "progress", "queue-operation"})`.

Состав множества:
- `"assistant"` — событие с ответом или рассуждением Claude (промежуточные content-блоки text/thinking/tool_use). Появляется во время работы turn-а
- `"progress"` — событие прогресса (использовалось ранними версиями CLI, оставлено для совместимости)
- `"queue-operation"` — служебное событие очереди задач CLI (некоторые версии CLI пишут такие записи в JSONL, наблюдаемое поведение)

Финальное событие `result` НЕ входит в множество — его появление как раз и означает завершение turn-а.

#### `is_turn_terminal_session_record(record: dict) -> bool`

```python
def is_turn_terminal_session_record(self, record: dict) -> bool: ...
```

Возвращает `True`, если данная запись JSONL-файла сессии — финальная запись штатно завершённого turn-а. Используется `session_watcher` для определения, можно ли пометить накопленные сообщения как финальные.

Метод поднят в общий интерфейс `CodingAgentBackend` после уточнения ревью 06-05, чтобы потребитель (`session_watcher`) не ветвился по `backend.name`. См. `coding_agent_backend_spec.md`, раздел «Расхождение с концепцией от 06-05-2026».

**Аргументы:**
- `record` (`dict`) — одна распарсенная запись JSONL из файла сессии Claude

**Возвращает:** `bool`. `record.get("type") == "result"`. У Claude финал turn-а однозначно определяется типом записи: `result` — финал, любой другой тип (`assistant`, `system`, `user`, `progress`, `queue-operation`) — не финал.

**Реализация — тривиальная.** В отличие от Codex (где требуется проверка подтипа `event_msg.payload.type == "task_complete"`), у Claude не нужны вложенные проверки. Метод существует в Claude-бэкенде ради симметрии интерфейса — иначе watcher был бы вынужден знать, что у Claude всё проще, и обходить вызов через `if backend.name == ...`.

**Соотношение с `is_error_event`.** `is_turn_terminal_session_record` отвечает только на вопрос «turn окончен в файле?». Это `True` и при штатном завершении (`is_error: false`), и при ошибочном (`is_error: true`) — оба варианта дают запись типа `result`. Различение «штатно/ошибочно» — задача `is_error_event(event)`, который читает stdout-event, а не файл-record.

### Расширения интерфейса для snapshot, ошибочного завершения и stop strategy

Эти методы соответствуют расширениям родительского интерфейса `CodingAgentBackend`, согласованным после ревью спек 06-05 (см. `dev/docs/session-reports/06-05/14-54_codex-specs-review-undone-items.md`, разделы 2, 3, 4). Расширения нужны, чтобы потребители (`session_watcher`, `process_manager`) могли работать с обоими бэкендами одинаково: получать snapshot файла сессии с raw-счётчиком и индикатором активности turn-а, отличать ошибочное завершение от штатного, единообразно останавливать процесс через явную backend-specific стратегию. Ниже описывается **только Claude-семантика** этих методов; типы DTO (`SessionFileSnapshot`, `StopStrategy`, `StopSignalStep`) объявляются в родительской спеке `coding_agent_backend_spec.md` и импортируются Claude-бэкендом.

#### `read_session_file_snapshot(file_path) -> SessionFileSnapshot`

```python
async def read_session_file_snapshot(
    self, file_path: str
) -> SessionFileSnapshot: ...
```

Возвращает snapshot файла сессии: список сообщений плюс служебные поля для watcher-а. Заменяет прямое использование `read_messages_from_session_file` в `session_watcher` (тот метод остаётся публичным API для `session_reader` и `/sessions`, где snapshot-поля не нужны).

**Аргументы:**
- `file_path` (`str`) — абсолютный путь к JSONL-файлу сессии Claude

**Возвращает:** `SessionFileSnapshot` (DTO из родительской спеки) со следующими полями:
- `messages` (`list[SessionMessage]`) — то же, что возвращает `read_messages_from_session_file` для этого файла. Хронологический порядок
- `raw_record_count` (`int`) — количество строк JSONL в файле, **включая невалидные и служебные** (`system`, `result`, любые типы кроме `user`/`assistant`). Watcher использует это число для дельта-чтения: если число выросло с прошлого опроса — появились новые записи, нужно пересчитать. Подсчёт делается по сырым строкам (после удаления только пустых), а не по распарсенным записям, чтобы счётчик был стабильным даже в момент партиальной записи последней строки CLI-процессом
- `last_record` (`UnifiedEvent | None`) — последняя валидная распарсенная запись JSONL, либо `None` если файл пуст или ни одна строка не парсится. Watcher использует поле для определения `is_turn_active` без повторного чтения файла; кроме того, потребитель может прочитать `last_record["session_id"]` и `last_record["type"]` для своих нужд
- `is_turn_active` (`bool`) — `True` если turn ещё идёт (CLI-процесс пишет в файл), `False` если turn завершён или файл новый/пустой. Семантика для Claude: см. подраздел «Алгоритм работы» ниже

**Исключения:** не выбрасывает; ошибки чтения и парсинга обрабатываются так же, как в `read_messages_from_session_file` (warning-лог, пустой snapshot)

**Async** — по тем же причинам, что и `read_messages_from_session_file`. Все блокирующие I/O через `asyncio.to_thread`.

#### `is_error_event(event) -> bool`

```python
def is_error_event(self, event: UnifiedEvent) -> bool: ...
```

Возвращает `True`, если событие означает ошибочное завершение turn-а (CLI отдал финальный ответ, но он несёт ошибку, а не успешный текст). Используется `process_manager` для решения о retry: пустой ответ + `is_error_event == True` приводит к перезапросу, пустой ответ без ошибки — к доставке пустоты пользователю.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `bool`. Возвращает `True` тогда и только тогда, когда `event.get("type") == EVENT_TYPE_RESULT` **и** `bool(event.get("is_error")) is True`. В остальных случаях (не финальное событие, либо `is_error: false/None/отсутствует`) — `False`.

Семантика Claude-протокола: финальное событие `result` имеет поле `is_error` (см. `claude-cli-stream-json-protocol.md`, раздел `Result event`). При штатном завершении значение `false` или поле отсутствует. При ошибке (превышен max_turns, отказ permission, внутренний сбой CLI) — `is_error: true` и `result` содержит текст ошибки вместо ответа.

#### `read_error_text_from_event(event) -> str | None`

```python
def read_error_text_from_event(self, event: UnifiedEvent) -> str | None: ...
```

Извлекает текст ошибки из финального события, если оно отмечено как ошибочное (`is_error_event == True`).

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `str | None`.
- Если `is_error_event(event) is False` — возвращает `None`
- Иначе — возвращает `event.get("result")` (для Claude текст ошибки лежит в том же поле `result`, что и обычный ответ; флаг `is_error` отличает успех от ошибки). Если поле отсутствует или пустое — `None`

Поведение симметрично `read_assistant_text_from_event`: оба читают `event["result"]`, но разделяют успешный текст и текст ошибки по флагу `is_error`. Дублирование «и текст, и ошибка из одного поля» — особенность Claude-протокола, в Codex текст ошибки лежит в отдельном поле `error.message` события `turn.failed`. Backend-neutral интерфейс скрывает это различие за двумя методами.

#### `read_terminal_status_from_event(event) -> TerminalStatus | None`

```python
def read_terminal_status_from_event(self, event: UnifiedEvent) -> TerminalStatus | None: ...
```

Возвращает обобщённый терминальный статус turn-а для финального события, либо `None` для нефинальных событий. Удобный высокоуровневый метод над парой `is_turn_complete_event` + `is_error_event`. Используется `process_manager` для записи итога turn-а в логи и для решения о retry, когда удобнее работать с enum-статусом, чем с парой булевых флагов.

**Аргументы:**
- `event` (`UnifiedEvent`) — распарсенное событие из stdout

**Возвращает:** `TerminalStatus | None`.
- Если `is_turn_complete_event(event) is False` — возвращает `None` (нефинальное событие)
- Если `is_error_event(event) is True` — возвращает `TerminalStatus.FAILED`
- Иначе — возвращает `TerminalStatus.SUCCESS`

В отличие от Codex, у Claude статус определяется не по типу события (там и успех, и ошибка идут с `type == "result"`), а по флагу `is_error` внутри `result`-события. Этот метод инкапсулирует разницу — потребитель видит единый enum-статус.

#### `get_stop_strategy() -> StopStrategy`

```python
def get_stop_strategy(self) -> StopStrategy: ...
```

Возвращает backend-specific стратегию остановки процесса CLI. Используется `process_manager` при обработке `/stop` и при принудительном завершении по таймауту: сигналы из `StopStrategy.steps` отправляются по очереди, между шагами — пауза `wait_seconds_before_next` для штатного завершения процесса. Если в течение паузы процесс не закрылся — переход к следующему шагу. Последний шаг — обычно SIGKILL — отправляется без ожидания (`wait_seconds_before_next = 0.0`): после `process.kill()` ядро снимает процесс безусловно, дальше ждать нечего.

**Аргументы:** нет.

**Возвращает:** `StopStrategy` (DTO из родительской спеки) с двумя шагами для Claude:
1. `StopSignalStep(signal_to_send=signal.SIGTERM, wait_seconds_before_next=TERMINATE_TIMEOUT_SECONDS)` — отправить SIGTERM, подождать 5 секунд штатного shutdown-а
2. `StopSignalStep(signal_to_send=signal.SIGKILL, wait_seconds_before_next=0.0)` — добить SIGKILL, дальше не ждать

То есть итоговая стратегия — `StopStrategy(steps=(StopSignalStep(signal.SIGTERM, 5.0), StopSignalStep(signal.SIGKILL, 0.0)))`. Поле `steps` — `tuple` (а не `list`), что согласовано с типом `tuple[StopSignalStep, ...]` в родительской спеке и гарантирует неизменяемость стратегии.

Семантика Claude-протокола: Claude CLI корректно завершает работу по `SIGTERM` (закрывает stdin, дописывает последнюю запись в JSONL, выходит). На macOS `process.terminate()` отправляет именно `SIGTERM`. Если CLI завис в неотменяемом сетевом вызове — через 5 секунд процесс добивается `SIGKILL`. Это поведение зафиксировано в `claude_runner.py:21-22` и проверено сотнями реальных запусков.

В отличие от Codex, у Claude CLI нет отдельной обработки `SIGINT` — стандартный shutdown-handler обрабатывает `SIGINT` и `SIGTERM` одинаково. Поэтому стратегия для Claude не содержит промежуточного `SIGINT`-шага: только `SIGTERM → SIGKILL`.

**Singleton:** возвращаемый `StopStrategy` — один и тот же объект для всех вызовов (бэкенд хранит его как поле класса или модуля). Это гарантирует, что `process_manager` и тесты сравнивают стратегии по `is`/`==` без сюрпризов.

### Локальная фабрика модуля

Конкретный класс `ClaudeCodeBackend` создаётся через фабрику `get_backend(BackendName.CLAUDE)` из родительского модуля `coding_agent_backend`. Внутри родительской фабрики — lazy import: `from claude_manager.claude_code_backend import ClaudeCodeBackend`. Сам этот модуль публичной фабрики не предоставляет — это прерогатива интерфейса.

Для удобства тестов и прямого использования (минуя фабрику) класс `ClaudeCodeBackend` экспортируется из модуля и может быть инстанцирован напрямую — он stateless, повторное создание не вредит.

## Внутренние функции

Все приватные функции переиспользуют существующий код проекта. При реализации они переносятся из исходных модулей в `claude_code_backend.py` побайтно, без логических изменений (только при необходимости — переименование во внутренние, чтобы не конфликтовать с паблик API класса).

### `_resolve_claude_binary_path() -> str`

Lazy-резолвер пути к бинарнику `claude`. Возвращает `shutil.which("claude") or CLAUDE_CLI_DEFAULT_PATH`. Если ни `which`, ни путь по умолчанию не находят бинарник — выбрасывает `BackendBinaryNotFoundError`. Вызывается из `compose_subprocess_command_args`, не из `__init__` класса (см. «Обработка ошибок»).

Перенос: эквивалент `claude_runner.py:19` (константа `CLAUDE_CLI_COMMAND = shutil.which("claude") or "/usr/local/bin/claude"`), но с ленивой проверкой существования.

### `_sanitize_project_path(project_dir: str) -> str`

Применяет регулярку `SANITIZE_PATH_PATTERN` (`re.compile(r"[^a-zA-Z0-9]")`) — заменяет все не-буквенно-цифровые символы на `-`.

Перенос из `session_reader.py:63-69` (`_encode_project_path`).

### `_extract_text_from_message_content(content: str | list) -> str`

Извлекает текст из поля `message.content`, которое в JSONL может быть либо строкой, либо списком content-блоков. Для списка — берёт первый блок с `type == "text"` и возвращает его поле `text`. Если блоков нет или все — не текстовые — возвращает пустую строку.

Перенос из `session_reader.py:96-104` (`_extract_text_from_content`).

### `_is_command_xml_message(text: str) -> bool`

Проверяет, начинается ли текст с одного из XML-тегов команд Claude Code (`<command-name>`, `<command-message>`, `<command-args>`, `<local-command-stdout>`, `<local-command-caveat>`). Такие сообщения — это служебный вывод slash-команд (`/init`, `/clear` и т.п.), а не настоящий пользовательский ввод. При построении превью первого сообщения они пропускаются.

Перенос из `session_reader.py:79-84` (`_is_command_message`) и константы `COMMAND_XML_TAGS`.

### `_extract_first_user_message_text(parsed_lines: list[dict]) -> str`

Проходит по списку распарсенных JSONL-записей и возвращает текст первого «настоящего» пользовательского сообщения — то есть с `type == "user"`, без флага `isMeta`, не содержащего командных XML-тегов, длиной минимум `MIN_MESSAGE_LENGTH = 2` символа после `strip()`. Если такого сообщения нет — возвращает пустую строку.

Перенос из `session_reader.py:107-122` (`_extract_first_user_message`).

### `_clean_preview_text(raw_text: str) -> str`

Очищает превью: удаляет XML-теги (паттерн `<[^>]+>`), сжимает любые подряд идущие whitespace-символы в один пробел (паттерн `\s+`), обрезает до `PREVIEW_MAX_LENGTH = 120` символов с добавлением `...` при обрезке.

Перенос из `session_reader.py:87-93` (`_clean_preview`).

### `_parse_jsonl_string_lines(raw_lines: list[str], file_path: str) -> list[dict]`

Парсит список сырых строк JSONL в список словарей. Пустые строки пропускаются. Невалидные строки логируются (`warning` с номером строки и путём файла) и пропускаются.

Перенос из `session_reader.py:125-140` (`_parse_jsonl_lines`).

### `_read_file_lines_blocking(file_path: str, max_lines: int | None) -> list[str]`

Блокирующая функция чтения строк файла. Если `max_lines is None` — читает все строки, иначе — первые `max_lines`. Вызывается через `asyncio.to_thread`.

Перенос из `session_reader.py:143-154` (`_read_file_lines`).

### `_read_session_file_metadata(file_path: str) -> SessionFileInfo | None`

Читает первые `MAX_LINES_FOR_PREVIEW = 50` строк JSONL, парсит, извлекает session_id (берётся из имени файла без расширения, либо из первой записи поля `sessionId`, если оно есть), извлекает первое настоящее сообщение пользователя для превью. Возвращает `SessionFileInfo` или `None` (если файл невалидный или пустой).

Адаптация из `session_reader.py:157-200` (`_read_session_file`). Возвращаемый тип меняется со старого `SessionInfo` (поля `session_id`, `created_at`, `preview`) на новый `SessionFileInfo` (поля `session_id`, `file_path`, `last_modified_at`, `preview`). Поле `created_at` (timestamp из первой записи с timestamp) больше не извлекается — родительская спека использует `last_modified_at = os.path.getmtime(file_path)` как универсальный, не привязанный к содержимому источник времени.

### `_list_jsonl_file_paths_blocking(directory: str) -> list[str]`

Блокирующая функция: возвращает список абсолютных путей всех файлов с расширением `.jsonl` в директории. Вызывается через `asyncio.to_thread`.

Перенос из `session_reader.py:203-211` (`_list_jsonl_files`).

### `_sort_paths_by_mtime_descending(file_paths: list[str]) -> list[str]`

Сортирует пути по `os.path.getmtime` в убывающем порядке (новые первые). Блокирующая, через `asyncio.to_thread`.

Перенос из `session_reader.py:214-216` (`_sort_files_by_mtime`).

### `_messages_from_jsonl_records(parsed_records: list[dict]) -> list[SessionMessage]`

Преобразует список распарсенных JSONL-записей в список `SessionMessage`. Логика — в описании метода `read_messages_from_session_file`. Это новая функция, без прямого аналога в существующем коде, но логика извлечения текста из `message.content` повторяет `_extract_text_from_content`.

## Алгоритм работы

### compose_subprocess_command_args

1. Резолвить путь к бинарнику через `_resolve_claude_binary_path()`. Если бинарник не найден — выбросить `BackendBinaryNotFoundError`
2. Сформировать базовые аргументы: `[binary_path, "-p", "--output-format", "stream-json", "--verbose", "--input-format", "stream-json", "--dangerously-skip-permissions", "--effort", "max"]`
3. Если `session_id is not None` — добавить `["--resume", session_id]`
4. Аргументы `cwd`, `prompt_text`, `image_paths` игнорировать (Claude CLI не принимает их в команде; cwd задаётся через параметр subprocess, prompt идёт через stdin, image — путём в тексте)
5. Вернуть итоговый список

### encode_user_message_for_cli_stdin

1. Аргумент `image_paths` игнорировать (см. контракт метода)
2. Сформировать словарь: `{"type": "user", "message": {"role": "user", "content": prompt_text}}`
3. Сериализовать в JSON через `json.dumps(message, ensure_ascii=False)` — кириллица сохраняется как есть
4. Добавить завершающий `\n` (Claude CLI разделяет stdin-сообщения по `\n`)
5. Закодировать в UTF-8 и вернуть `bytes`

### parse_stdout_line_into_event

1. Если `raw_line.strip()` пустая — вернуть `None`
2. Попытаться распарсить через `json.loads(raw_line)`. При успехе — вернуть результат как `UnifiedEvent`
3. При `json.JSONDecodeError` — выбросить `BackendProtocolError(f"Невалидный JSON от Claude: '{raw_line[:200]}'")`

### is_turn_complete_event

1. Проверить `event.get("type") == EVENT_TYPE_RESULT`
2. Вернуть `bool` результата

### read_session_id_from_event

1. Вернуть `event.get("session_id")`. Это либо строка-UUID, либо `None`. Никаких других проверок не делать — поле `session_id` всегда либо строка, либо отсутствует

### read_assistant_text_from_event

1. Если `event.get("type") != EVENT_TYPE_RESULT` — вернуть `None`
2. Взять `text = event.get("result")`
3. Если `text is None` — вернуть `""`
4. Если `text == EMPTY_RESPONSE_MARKER` — вернуть `""`
5. Иначе — вернуть `text`

### read_progress_text_from_event

1. Если `event.get("type") != EVENT_TYPE_ASSISTANT` — вернуть `None`
2. Получить `content_blocks = event.get("message", {}).get("content", [])`. Если это не список — рассматривать как пустой
3. Инициализировать `text_content = None`, `thinking_content = None`
4. Пройти по `content_blocks`. Для каждого блока:
   - Если `block.get("type") == CONTENT_BLOCK_TEXT` и `text_content` ещё `None` — записать `text_content = block.get("text")`
   - Иначе если `block.get("type") == CONTENT_BLOCK_THINKING` и `thinking_content` ещё `None` — записать `thinking_content = block.get("thinking")`
5. Вернуть `text_content or thinking_content` (приоритет text, fallback на thinking, в крайнем случае `None`)

### locate_session_files_directory_for_project

1. Получить домашнюю директорию: `home_dir = os.path.expanduser("~")`
2. Кодировать путь проекта: `sanitized = _sanitize_project_path(project_dir)`
3. Собрать абсолютный путь: `os.path.join(home_dir, ".claude", "projects", sanitized)`
4. Вернуть строку

### list_session_files_for_project

1. Вычислить `sessions_dir = locate_session_files_directory_for_project(project_dir)`
2. Через `asyncio.to_thread(os.path.exists, sessions_dir)` проверить существование. Если не существует — залогировать `warning` и вернуть `[]`
3. Через `asyncio.to_thread(os.path.isdir, sessions_dir)` проверить, что это директория. Если нет — залогировать `warning`, вернуть `[]`
4. Получить список JSONL-файлов: `await asyncio.to_thread(_list_jsonl_file_paths_blocking, sessions_dir)`. Если `OSError` — залогировать `error`, вернуть `[]`
5. Если список пуст — залогировать `info`, вернуть `[]`
6. Отсортировать: `sorted_files = await asyncio.to_thread(_sort_paths_by_mtime_descending, file_paths)`. Если `OSError` — залогировать `error`, вернуть `[]`
7. Взять первые `MAX_RECENT_SESSIONS` элементов
8. Для каждого пути из ограниченного списка вызвать `info = await _read_session_file_metadata(file_path)`. Helper сам вычисляет `last_modified_at` через `os.path.getmtime` (внутри `asyncio.to_thread`). Если `info is not None` — добавить в результат
9. Вернуть результат

### list_all_session_files_for_project

1. Выполнить шаги 1-6 из `list_session_files_for_project`.
2. Не применять `MAX_RECENT_SESSIONS`.
3. Для каждого пути из полного отсортированного списка вызвать `info = await _read_session_file_metadata(file_path)`.
4. Вернуть все успешно прочитанные `SessionFileInfo`.

### read_messages_from_session_file

1. Через `asyncio.to_thread(os.path.exists, file_path)` проверить существование. Если нет — залогировать `debug`, вернуть `[]`
2. Прочитать все строки: `raw_lines = await asyncio.to_thread(_read_file_lines_blocking, file_path, None)`. Поймать `PermissionError` и `OSError` — залогировать `error`, вернуть `[]`
3. Парсить: `parsed = _parse_jsonl_string_lines(raw_lines, file_path)`
4. Преобразовать в `SessionMessage` через `_messages_from_jsonl_records(parsed)`:
   - Для записи `{type, message, timestamp, isMeta?}`:
     - Если `type == EVENT_TYPE_USER` и не `isMeta`:
       - `text = _extract_text_from_message_content(message.get("content", ""))`
       - Создать `SessionMessage(role="user", text=text, timestamp=record.get("timestamp"), is_empty_response=False)`
     - Если `type == EVENT_TYPE_ASSISTANT`:
       - `text = _extract_text_from_message_content(message.get("content", ""))`
       - `is_empty = text in self.text_markers_indicating_empty_response()`
       - Создать `SessionMessage(role="assistant", text=text, timestamp=record.get("timestamp"), is_empty_response=is_empty)`
     - Иначе — пропустить запись
5. Вернуть список `SessionMessage` в исходном порядке

### text_markers_indicating_empty_response

1. Вернуть статическое `frozenset({EMPTY_RESPONSE_MARKER})` (значение константы — `"No response requested."`)

### event_types_meaning_cli_is_busy

1. Вернуть статическое `frozenset({"assistant", "progress", "queue-operation"})`

### is_turn_terminal_session_record

1. Вернуть `record.get("type") == "result"`

### read_session_file_snapshot

1. Через `asyncio.to_thread(os.path.exists, file_path)` проверить существование. Если файла нет — залогировать `debug` и вернуть `SessionFileSnapshot(messages=[], raw_record_count=0, last_record=None, is_turn_active=False)`
2. Прочитать сырые строки: `raw_lines = await asyncio.to_thread(_read_file_lines_blocking, file_path, None)`. При `OSError`/`PermissionError` (включая транзиентный `EDEADLK` на macOS) — залогировать `warning` и вернуть пустой snapshot, как в шаге 1
3. Посчитать сырые записи: `raw_record_count = sum(1 for line in raw_lines if line.strip())`. Считаются ВСЕ непустые строки JSONL: `system`, `result`, `assistant`, `user`, любые служебные типы, **включая невалидные строки**, которые не парсятся (важно для watcher: если CLI пишет последнюю строку прямо сейчас и она ещё битая, счётчик всё равно вырос). Чистые пустые строки (`""`, `"\n"`) не считаются — их в JSONL быть не должно, это просто шум
4. Парсить через `parsed_records = _parse_jsonl_string_lines(raw_lines, file_path)` — невалидные строки пропускаются с `warning` (см. контракт helper-а)
5. Преобразовать в сообщения: `messages = _messages_from_jsonl_records(parsed_records)` — та же логика, что в `read_messages_from_session_file`
6. Вычислить `last_record`:
   - Если `parsed_records` пустой — `last_record = None`
   - Иначе — `last_record = parsed_records[-1]` (последняя валидная распарсенная запись)
7. Вычислить `is_turn_active`:
   - Если `last_record is None` — `False` (файл пуст или ни одна строка не парсится)
   - Иначе — `is_turn_active = last_record.get("type") in self.event_types_meaning_cli_is_busy()`. Множество `{"assistant", "progress", "queue-operation"}` означает «turn ещё идёт». Если последняя запись — `result`, `system`, `user` или любой другой тип, не входящий в это множество — `is_turn_active = False`
8. Вернуть `SessionFileSnapshot(messages=messages, raw_record_count=raw_record_count, last_record=last_record, is_turn_active=is_turn_active)`

Метод-wrapper `read_messages_from_session_file` после реализации snapshot-метода реализуется как `return (await self.read_session_file_snapshot(file_path)).messages` — так гарантируется единая трактовка файла потребителями `session_reader` и `session_watcher`.

### is_error_event

1. Если `event.get("type") != EVENT_TYPE_RESULT` — вернуть `False` (нефинальные события ошибочными не считаются)
2. Иначе — вернуть `bool(event.get("is_error"))`. Поле `is_error` либо `True`, либо `False`, либо отсутствует (`None`); приведение `bool(None)` даёт `False`, `bool(False)` — `False`, `bool(True)` — `True`. Это устойчиво к будущему добавлению значений или пропуску поля

### read_error_text_from_event

1. Если `is_error_event(event) is False` — вернуть `None`
2. Иначе — взять `text = event.get("result")`. У Claude текст ошибки лежит в том же поле `result`, что и обычный ответ; флаг `is_error` отличает успех от ошибки
3. Если `text` — `None` или пустая строка — вернуть `None` (CLI пометил событие ошибочным, но не дал текста; вызывающая сторона использует общую формулировку)
4. Иначе — вернуть `text` как есть (это содержательное сообщение CLI: «превышен max_turns», «permission denied», «internal error» и т.п.)

### read_terminal_status_from_event

1. Если `is_turn_complete_event(event) is False` — вернуть `None` (нефинальное событие)
2. Если `is_error_event(event) is True` — вернуть `TerminalStatus.FAILED`
3. Иначе — вернуть `TerminalStatus.SUCCESS`

Метод тонкая обёртка над парой `is_turn_complete_event` + `is_error_event`. Существует ради симметрии интерфейса с Codex, у которого статус определяется по типу события — там этот метод инкапсулирует логику, не выводимую тривиально. У Claude же логика тривиальна, но потребители (`process_manager`, `session_watcher`) пользуются им единообразно для обоих backend.

### get_stop_strategy

1. Вернуть фиксированную стратегию `StopStrategy` с двумя шагами в указанном порядке:
   - `StopSignalStep(signal_to_send=signal.SIGTERM, wait_seconds_before_next=TERMINATE_TIMEOUT_SECONDS)` — штатный shutdown, ждать 5 секунд
   - `StopSignalStep(signal_to_send=signal.SIGKILL, wait_seconds_before_next=0.0)` — форсированное завершение, не ждать
2. Реализационная деталь: стратегию можно хранить как модульную константу (`_STOP_STRATEGY`) и возвращать тот же immutable-объект на каждом вызове. Для потребителя это безопасно: `StopStrategy` frozen, а `steps` — tuple

## Зависимости

**От модулей проекта:**
- `coding_agent_backend` — импортирует абстрактный класс `CodingAgentBackend`, enum `BackendName`, тип `UnifiedEvent`, dataclass-ы `SessionFileInfo` и `SessionMessage`, исключения `BackendBinaryNotFoundError`, `BackendProtocolError`. Потребляет: `class ClaudeCodeBackend(CodingAgentBackend)` — наследование, типы из DTO — параметры/возвраты методов.

**От стандартной библиотеки:**
- `asyncio` — `asyncio.to_thread` для оборачивания блокирующих I/O в async-методах
- `json` — `json.loads` (парсинг событий из stdout, парсинг JSONL), `json.dumps(..., ensure_ascii=False)` (формирование stdin-сообщения)
- `logging` — `logging.getLogger(__name__)` для логирования ошибок чтения файлов и невалидного JSON
- `os` — `os.path.expanduser`, `os.path.join`, `os.path.exists`, `os.path.isdir`, `os.path.getmtime`, `os.listdir`, `os.path.basename` (работа с путями, перечисление файлов в каталоге, mtime)
- `re` — `re.compile` для трёх паттернов (`SANITIZE_PATH_PATTERN`, `XML_TAG_PATTERN`, `WHITESPACE_PATTERN`)
- `shutil` — `shutil.which` для резолва пути к бинарнику `claude`
- `signal` — константы `signal.SIGTERM` и `signal.SIGKILL` для возврата из `get_stop_strategy`

**Не зависит:**
- `claude_runner.py`, `session_reader.py`, `process_manager.py` — НЕ импортируются. Логика переносится копированием, не импортом. Это сознательное архитектурное решение: после реализации Adapter pattern эти три модуля либо упростятся (превратятся в тонкие обёртки), либо будут заменены на бэкенд-агностичных потребителей. Прямой импорт связал бы новый интерфейс со старой реализацией и сломал бы Adapter pattern (потребитель не должен знать о конкретной реализации, а через цепочку импортов узнал бы)

**Lazy import при импорте модуля:** нет. Все импорты — статические, на верхнем уровне модуля. `claude_code_backend.py` не импортирует `codex_backend.py` и не должен импортироваться родительским `coding_agent_backend.py` на верхнем уровне (тот делает lazy import внутри `_create_backend_instance`).

## Обработка ошибок

- **Бинарник `claude` не найден.** При первом вызове `compose_subprocess_command_args` — `_resolve_claude_binary_path` пытается `shutil.which("claude")`, при неудаче — проверить существование `/usr/local/bin/claude`. Если оба не сработали — выбросить `BackendBinaryNotFoundError` с сообщением: «Claude Code CLI не найден. Убедитесь, что 'claude' доступен в PATH или установлен в /usr/local/bin/claude». **Lazy-проверка обязательна:** проверка выполняется при первом вызове метода, а не при импорте модуля. Это требование родительской спеки (раздел «Обработка ошибок»): импорт `claude_code_backend.py` не должен падать у пользователя, у которого установлен только Codex CLI

- **Невалидный JSON в stdout (`parse_stdout_line_into_event`).** Выбросить `BackendProtocolError` с сообщением, содержащим первые 200 символов строки. Это контрактное нарушение — Claude CLI обязан выдавать валидный JSON. Молчаливое проглатывание скрыло бы реальные проблемы (поломка протокола после обновления CLI)

- **Файл сессии не существует или нет прав (`read_messages_from_session_file`).** НЕ выбрасывать. Вернуть `[]`. Залогировать `warning` (при `OSError`/`PermissionError`) или `debug` (если файл просто не существует — это норма, файл может быть удалён между листингом и чтением, гонка с Claude CLI)

- **Невалидная JSON-строка внутри файла сессии.** НЕ прерывать чтение. Пропустить строку. Залогировать `warning` с номером строки и путём файла. Это нужно для устойчивости при чтении файлов, в которые Claude CLI пишет прямо сейчас (партиальная запись последней строки)

- **Транзиентная `OSError` (включая `EDEADLK`, errno 11) при чтении папки сессий или файлов.** macOS может вернуть `EDEADLK` на обычный `read()` при высокой конкуренции процессов Claude за файлы в одной папке (см. CLAUDE.md → «Транзиентная ошибка EDEADLK»). Метод `list_session_files_for_project` должен ловить `OSError`, логировать `warning`/`error` и возвращать пустой список (или пропускать конкретный файл) — никогда не падать

- **Папка сессий проекта не существует.** Это валидное состояние: пользователь только что переключил проект, в котором ещё не было ни одной сессии. `list_session_files_for_project` возвращает `[]`, `warning` пишется в лог (для диагностики случаев, когда папка ожидалась)

## Контракты с внешними системами

### Claude Code CLI — алгоритм sanitize пути проекта

**Источник правды:** функция `sanitizePath` в исходниках Claude Code CLI. На момент написания спеки исходники доступны в проекте только опосредованно — через документацию протокола (`dev/docs/claude-cli-stream-json-protocol.md`) и эмпирическое наблюдение поведения CLI в продакшене. Родительская спека `coding_agent_backend_spec.md` ссылается на путь `~/Desktop/claude-sandbox/claude-code-sourcecode/sessionStoragePortable.ts:311`, но папки `claude-code-sourcecode/` сейчас нет на диске. Реализация должна **не догадываться об алгоритме**, а проверить его эмпирически.

**Точный алгоритм (проверен в `session_reader.py:48,63-69`, работает в продакшене):** заменить все символы вне набора `[a-zA-Z0-9]` на `-`. Регулярка: `re.compile(r"[^a-zA-Z0-9]")`, замена через `pattern.sub("-", project_dir)`. Подтверждение в BRD CJM-05 (строки 228 файла `dev/docs/brd/brd-user-journeys.md`): «все символы, кроме латинских букв и цифр, заменяются дефисом. Это касается не только слешей и пробелов, но и точек, подчёркиваний, кириллицы».

**Эмпирическая проверка (обязательная):** интеграционный тест `test_claude_sanitize_matches_real_cli_folder` (см. тест-план):
- Запустить `claude -p "say x" --output-format stream-json` через subprocess в директории `/tmp/test session 一二三` (содержит пробелы и не-ASCII)
- Дождаться завершения процесса
- Найти созданную папку в `~/.claude/projects/`
- Сравнить её имя с результатом `ClaudeCodeBackend().locate_session_files_directory_for_project("/tmp/test session 一二三")` — должно совпадать побайтно

Тест опциональный — пропускается через `pytest.mark.skipif(shutil.which("claude") is None)`. Без эмпирической проверки реализация считается недостоверной.

### Claude Code CLI — формат команды запуска

**Источник правды:** рабочий код `claude_runner.py:57-72` (функция `_build_command_args`). Команда стабильна с момента создания проекта, проверена сотнями реальных запусков бота.

**Точный список флагов:**
- `claude` (бинарник, путь резолвится через `shutil.which("claude") or "/usr/local/bin/claude"`)
- `-p` — print-режим (неинтерактивный)
- `--output-format stream-json` — JSONL на stdout
- `--verbose` — расширенный вывод (нужен для прогресса в `assistant`-событиях)
- `--input-format stream-json` — JSONL на stdin
- `--dangerously-skip-permissions` — пропуск запросов разрешений (бот авторизует все инструменты)
- `--effort max` — максимальная глубина extended thinking
- `--resume <session_id>` — добавляется только если `session_id is not None`

Изменение любого флага без сопроводительного эмпирического теста на реальном CLI запрещено.

### Claude Code CLI — формат stdin-сообщения

**Источник правды:** `claude_runner.py:107-117` (метод `send_message`) + протокольная документация `dev/docs/claude-cli-stream-json-protocol.md` (раздел «Входящие сообщения»).

**Точный формат:** `{"type": "user", "message": {"role": "user", "content": "<текст>"}}\n`. Сериализация через `json.dumps(..., ensure_ascii=False)` (без экранирования не-ASCII), добавляется `\n`, кодируется в UTF-8.

**КРИТИЧНО (зафиксировано в CLAUDE.md → «Важные детали для разработки»):** альтернативный формат `{"type": "user_message", "content": "..."}` — невалидный. Claude CLI молча зависает в ожидании правильного сообщения, никаких ошибок не пишет. Тест `test_claude_stdin_format_is_accepted_by_real_cli` обязателен.

### Claude Code CLI — формат событий stdout

**Источник правды:** `dev/docs/claude-cli-stream-json-protocol.md` + рабочий код `claude_runner.py:120-153` (метод `read_events`) и `process_manager.py:121-152` (`_extract_progress_text`, `_extract_result_text`).

**Типы событий:**
- `system` — `{type: "system", subtype: "init", session_id, cwd, model, tools, claude_code_version, permissionMode}`. Первое событие после запуска
- `assistant` — `{type: "assistant", message: {role: "assistant", content: [<блоки>]}, session_id}`. Блоки в `content`: `{type: "text", text}`, `{type: "thinking", thinking, signature}`, `{type: "tool_use", id, name, input}`. Может быть несколько за один turn
- `user` — `{type: "user", message: {role: "user", content: [{tool_use_id, type: "tool_result", content}]}, session_id}`. Результат вызова инструмента
- `result` — `{type: "result", subtype, is_error, result, duration_ms, num_turns, session_id, ...}`. Финальное событие. После него Claude ждёт следующего сообщения (но в боте каждый запрос — новый процесс)

**Семантика:**
- `is_turn_complete = (type == "result")`
- `session_id` присутствует во всех типах
- Текст ответа — `event.result` для `type == "result"`. Может быть `None`, пустой строкой, синтетическим маркером `"No response requested."` (нормализовать в `""`)
- Прогресс — content-блоки `text` или `thinking` в `assistant`-событиях. Приоритет text над thinking

### Claude Code CLI — формат JSONL-файла сессии на диске

**Источник правды:** рабочий код `session_reader.py:107-200` (`_extract_first_user_message`, `_read_session_file`) + протокольная документация (раздел «Синтетические сообщения»).

**Расположение файла:** `~/.claude/projects/<sanitized>/<session_uuid>.jsonl`, где `<sanitized>` получен через `_sanitize_project_path`. Имя файла без расширения = UUID сессии (источник `session_id`, если в записи нет поля `sessionId`).

**Структура одной строки:** валидный JSON с обязательным полем `type` (значения: `"user"`, `"assistant"`, `"system"`, `"result"`, и другие — служебные). Опциональные поля: `message` (для user/assistant), `timestamp` (есть в большинстве записей, но НЕ в первой записи начиная с CLI 2.1.96 — там идёт `permission-mode` без timestamp), `isMeta`, `sessionId`, `parentUuid`, `cwd` и другие.

**Ловушки:**
- **Первая строка JSONL без timestamp** (регрессия CLI 2.1.96, описана в протокольной документации). Любой код, который жёстко берёт `parsed_lines[0]["timestamp"]`, потеряет сессию. Решение в `session_reader.py:_read_session_file` — итерировать строки до первой с timestamp. В этой спеке поле `last_modified_at` берётся из `os.path.getmtime(file_path)`, а не из содержимого, что обходит проблему. Поле `timestamp` в `SessionMessage` извлекается из той же записи, что и `text` — если в записи нет `timestamp`, в `SessionMessage` будет `None` (это легитимное значение поля)
- **Дубликаты записей** (issue #5034 Claude CLI). Спека не дедуплицирует — это ответственность вышестоящего слоя. Метод `read_messages_from_session_file` возвращает все записи как есть
- **Партиальная последняя строка.** Claude пишет в файл, не закрывая его. Чтение в момент записи может вернуть неполный JSON последней строки. `_parse_jsonl_string_lines` логирует `warning` и пропускает её — следующее чтение прочитает уже целую строку

### Лимиты и таймауты

- **Лимит буфера StreamReader.** При запуске subprocess `process_manager` обязан передать `limit=STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024` в `asyncio.create_subprocess_exec`. Дефолт 64 KB слишком мал для реалистичных событий stream-json (длинные markdown-ответы, результаты Bash для больших файлов). Источник: `claude_runner.py:31-38` (комментарий + константа)
- **Таймаут чтения строки stdout.** Claude может молчать до 30 минут (extended thinking, длинные Bash). Потребитель `process_manager` обязан использовать `asyncio.wait_for` с таймаутом `READ_LINE_TIMEOUT_SECONDS = 1800`. Источник: `claude_runner.py:24-29`
- **Таймаут SIGTERM перед SIGKILL.** Потребитель использует `TERMINATE_TIMEOUT_SECONDS = 5` секунд между `process.terminate()` и `process.kill()`. Источник: `claude_runner.py:21-22`

Эти константы экспортируются из модуля `claude_code_backend.py` для использования потребителем (`process_manager`), даже если сам бэкенд subprocess не запускает.

## Константы

Все константы определяются на уровне модуля `claude_code_backend.py`. Их значения зафиксированы существующим кодом и протоколом — менять без эмпирической проверки запрещено.

- `BACKEND_DISPLAY_NAME_CLAUDE = "🤖 Claude"` — UI-метка бэкенда. Эмодзи 🤖 (робот) — нейтральный «AI помощник», узнаваемый. Возвращается из свойства `display_name`
- `CLAUDE_CLI_DEFAULT_PATH = "/usr/local/bin/claude"` — fallback-путь к бинарнику, если `shutil.which("claude")` не нашёл в `PATH`. Источник: `claude_runner.py:19`
- `EVENT_TYPE_SYSTEM = "system"` — тип события инициализации. Источник: `claude_runner.py:45`
- `EVENT_TYPE_ASSISTANT = "assistant"` — тип события с ответом Claude (промежуточные блоки). Источник: `process_manager.py:46`
- `EVENT_TYPE_USER = "user"` — тип события результата tool_use. Источник: `claude-cli-stream-json-protocol.md`
- `EVENT_TYPE_RESULT = "result"` — тип финального события turn. Источник: `claude_runner.py:46`, `process_manager.py:43`
- `CONTENT_BLOCK_TEXT = "text"` — тип content-блока с текстом. Источник: `process_manager.py:49`
- `CONTENT_BLOCK_THINKING = "thinking"` — тип content-блока с размышлением. Источник: `process_manager.py:50`
- `EMPTY_RESPONSE_MARKER = "No response requested."` — синтетический маркер пустого ответа. Источник: `process_manager.py:56`, протокольная документация (раздел «Синтетические сообщения»)
- `BUSY_EVENT_TYPES = frozenset({"assistant", "progress", "queue-operation"})` — типы событий, означающих «turn ещё идёт». Возвращается из `event_types_meaning_cli_is_busy`. Источник: эмпирическое поведение CLI + родительская спека
- `EMPTY_RESPONSE_MARKERS = frozenset({EMPTY_RESPONSE_MARKER})` — множество синтетических маркеров. Возвращается из `text_markers_indicating_empty_response`
- `MAX_RECENT_SESSIONS = 15` — максимум сессий из UI-метода `list_session_files_for_project`. Источник: BRD CJM-05 («15 самых свежих сессий»), `session_reader.py:22`. Operational-метод `list_all_session_files_for_project` этот лимит не применяет.
- `PREVIEW_MAX_LENGTH = 120` — максимум символов в `preview` поле `SessionFileInfo`. Источник: BRD CJM-05 («первое сообщение пользователя, до 120 символов»), `session_reader.py:25`
- `MAX_LINES_FOR_PREVIEW = 50` — сколько строк JSONL читать для извлечения превью. Источник: `session_reader.py:28`. Достаточно: первое настоящее user-сообщение всегда в первых десятках строк (после `permission-mode`, `file-history-snapshot` и других служебных)
- `MIN_MESSAGE_LENGTH = 2` — минимальная длина сообщения, чтобы считать его «настоящим» (не пустым). Источник: `session_reader.py:51`
- `COMMAND_XML_TAGS = frozenset({"command-name", "command-message", "command-args", "local-command-stdout", "local-command-caveat"})` — XML-теги, помечающие сообщения slash-команд (пропускаются при поиске первого user-сообщения). Источник: `session_reader.py:31-37`
- `SANITIZE_PATH_PATTERN = re.compile(r"[^a-zA-Z0-9]")` — регулярка для замены не-буквенно-цифровых символов на дефис. Источник: `session_reader.py:48`
- `XML_TAG_PATTERN = re.compile(r"<[^>]+>")` — регулярка для удаления XML-тегов из превью. Источник: `session_reader.py:40`
- `WHITESPACE_PATTERN = re.compile(r"\s+")` — регулярка для сжатия whitespace в один пробел. Источник: `session_reader.py:43`
- `STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024` — лимит буфера StreamReader для stdout/stderr. Экспортируется для потребителя `process_manager`. Источник: `claude_runner.py:38`
- `READ_LINE_TIMEOUT_SECONDS = 1800` — таймаут одного `readline` на stdout. Экспортируется для потребителя. Источник: `claude_runner.py:29`
- `TERMINATE_TIMEOUT_SECONDS = 5` — таймаут SIGTERM перед SIGKILL. Экспортируется для потребителя. Источник: `claude_runner.py:22`
- `CLAUDE_PROJECTS_RELATIVE_DIR = ".claude/projects"` — относительный путь от домашней директории к папке проектов Claude CLI. Источник: `session_reader.py:19`. Используется в `locate_session_files_directory_for_project`

## Тест-план

Тесты живут в `tests/test_claude_code_backend.py` (юнит/edge/error) и `tests/integration/test_claude_code_backend_contracts.py` (контрактные интеграционные с реальным CLI).

### Юнит-тесты

- **test_name_returns_claude_enum** — `ClaudeCodeBackend().name == BackendName.CLAUDE`. Тип: unit
- **test_display_name_is_robot_emoji_claude** — `ClaudeCodeBackend().display_name == "🤖 Claude"`. Тип: unit
- **test_compose_args_for_new_session_no_resume_flag** — `ClaudeCodeBackend().compose_subprocess_command_args(None, "/tmp", "hello", [])` не содержит `"--resume"`. Тип: unit
  - Вход: `session_id=None, cwd="/tmp", prompt_text="hello", image_paths=[]`
  - Ожидаемый результат: список содержит `"-p"`, `"--output-format"`, `"stream-json"`, `"--verbose"`, `"--input-format"`, `"stream-json"`, `"--dangerously-skip-permissions"`, `"--effort"`, `"max"`. НЕ содержит `"--resume"`
- **test_compose_args_for_resume_session_appends_resume_flag** — для существующего session_id команда заканчивается на `"--resume", "<id>"`
  - Вход: `session_id="abc-uuid-123", cwd="/tmp", prompt_text="hi", image_paths=[]`
  - Ожидаемый результат: последние два элемента списка — `"--resume"`, `"abc-uuid-123"`
- **test_compose_args_ignores_prompt_text_and_image_paths** — для двух разных вызовов с одинаковыми session_id/cwd, но разными prompt_text и image_paths — результат идентичен
- **test_encode_user_message_uses_correct_json_format** — `ClaudeCodeBackend().encode_user_message_for_cli_stdin("привет", [])` возвращает `b'{"type": "user", "message": {"role": "user", "content": "привет"}}\n'` ИЛИ (с `ensure_ascii=False`) — кириллица как UTF-8 байты. Проверка: распарсить результат JSON-ом — структура `{"type": "user", "message": {"role": "user", "content": "привет"}}`
- **test_encode_user_message_does_not_use_user_message_type** — результат после JSON-парсинга НЕ содержит ключа `"type": "user_message"` (это известная ловушка CLI)
- **test_encode_user_message_keeps_cyrillic_unescaped** — `encode_user_message_for_cli_stdin("привет", [])` содержит байты UTF-8 для «привет», а не `\u`-эскейпы
- **test_encode_user_message_ends_with_newline** — последний байт результата — `b"\n"`
- **test_encode_user_message_ignores_image_paths** — два вызова с разными `image_paths`, но одинаковым `prompt_text` дают одинаковый результат
- **test_parse_stdout_line_returns_dict_for_valid_json** — `ClaudeCodeBackend().parse_stdout_line_into_event('{"type":"system","session_id":"abc"}')` возвращает `{"type":"system","session_id":"abc"}`
- **test_parse_stdout_line_returns_none_for_empty_line** — для `""`, `"   "`, `"\t\n"` — возвращает `None`
- **test_is_turn_complete_event_true_for_result** — `is_turn_complete_event({"type": "result"})` → `True`
- **test_is_turn_complete_event_false_for_assistant** — `is_turn_complete_event({"type": "assistant"})` → `False`
- **test_is_turn_complete_event_false_for_empty_dict** — `is_turn_complete_event({})` → `False`
- **test_read_session_id_from_event_returns_value** — `read_session_id_from_event({"session_id": "uuid-1"})` → `"uuid-1"`
- **test_read_session_id_from_event_returns_none_when_missing** — `read_session_id_from_event({})` → `None`
- **test_read_assistant_text_returns_none_for_non_result_event** — `read_assistant_text_from_event({"type": "assistant"})` → `None`
- **test_read_assistant_text_returns_text_from_result** — `read_assistant_text_from_event({"type": "result", "result": "Готово"})` → `"Готово"`
- **test_read_assistant_text_returns_empty_for_no_response_marker** — для `{"type": "result", "result": "No response requested."}` → `""` (пустая строка, не None — turn завершён)
- **test_read_assistant_text_returns_empty_for_none_result** — для `{"type": "result", "result": None}` → `""`
- **test_read_progress_text_returns_text_block** — для `{"type": "assistant", "message": {"content": [{"type": "text", "text": "Читаю файл"}]}}` → `"Читаю файл"`
- **test_read_progress_text_returns_thinking_block_when_no_text** — для `{"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Размышляю"}]}}` → `"Размышляю"`
- **test_read_progress_text_prefers_text_over_thinking** — для содержимого с обоими блоками одновременно — возвращает значение `text`-блока, не `thinking`. Это критическая семантика, фиксируется тестом отдельно
  - Вход: `{"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "глубже"}, {"type": "text", "text": "пишу"}]}}`
  - Ожидаемый результат: `"пишу"`
- **test_read_progress_text_returns_none_for_non_assistant** — `read_progress_text_from_event({"type": "result"})` → `None`
- **test_read_progress_text_returns_none_for_empty_content** — для `{"type": "assistant", "message": {"content": []}}` → `None`
- **test_locate_session_files_directory_replaces_non_alphanumeric** — `locate_session_files_directory_for_project("/Users/ivan/My Project")` оканчивается на `-Users-ivan-My-Project` (пробел и слеши заменены на `-`)
- **test_locate_session_files_directory_handles_underscores** — `/path/with_underscore` → `-path-with-underscore` (подчёркивание заменяется)
- **test_locate_session_files_directory_handles_cyrillic** — `/path/Проект` → `-path-` плюс шесть дефисов на месте кириллических букв
- **test_locate_session_files_directory_uses_home_dir** — результат начинается с `os.path.expanduser("~") + "/.claude/projects/"`
- **test_text_markers_indicating_empty_response_contains_no_response_requested** — `"No response requested." in ClaudeCodeBackend().text_markers_indicating_empty_response()` → `True`
- **test_text_markers_indicating_empty_response_returns_frozenset** — результат — экземпляр `frozenset` (попытка добавить элемент должна падать с `AttributeError`)
- **test_event_types_meaning_cli_is_busy_contains_assistant** — `"assistant" in ClaudeCodeBackend().event_types_meaning_cli_is_busy()` → `True`
- **test_event_types_meaning_cli_is_busy_does_not_contain_result** — `"result" not in busy_types` → `True` (result означает завершение, не busy)
- **test_event_types_meaning_cli_is_busy_returns_frozenset** — результат — экземпляр `frozenset`
- **test_is_turn_terminal_session_record_true_for_result** — `is_turn_terminal_session_record({"type": "result", "is_error": False, "result": "ok"})` → `True`. Покрывает штатное завершение turn-а
- **test_is_turn_terminal_session_record_false_for_assistant** — `is_turn_terminal_session_record({"type": "assistant", "message": {"content": []}})` → `False`. Промежуточные записи не финал
- **test_is_turn_terminal_session_record_false_for_system** — `is_turn_terminal_session_record({"type": "system", "subtype": "init"})` → `False`. Системная запись не финал
- **test_is_turn_terminal_session_record_false_for_user** — `is_turn_terminal_session_record({"type": "user", "message": {"content": "hi"}})` → `False`
- **test_is_turn_terminal_session_record_false_for_empty_dict** — `is_turn_terminal_session_record({})` → `False`. Битая или пустая запись не финал
- **test_is_turn_terminal_session_record_returns_true_even_for_error_result** — `is_turn_terminal_session_record({"type": "result", "is_error": True, "result": "max turns exceeded"})` → `True`. Финал turn-а — и при штатном, и при ошибочном завершении (для разделения штатно/ошибочно есть `is_error_event`; этот метод отвечает только на вопрос «turn окончен в файле?»)
- **test_is_error_event_true_for_result_with_is_error_true** — `is_error_event({"type": "result", "is_error": True, "result": "max turns exceeded"})` → `True`
- **test_is_error_event_false_for_result_with_is_error_false** — `is_error_event({"type": "result", "is_error": False, "result": "ok"})` → `False`
- **test_is_error_event_false_for_result_without_is_error_field** — `is_error_event({"type": "result", "result": "ok"})` → `False` (отсутствие поля = `bool(None) == False`)
- **test_is_error_event_false_for_assistant_event** — `is_error_event({"type": "assistant", "is_error": True})` → `False` (нефинальные события не считаются ошибкой, даже если поле пришло случайно)
- **test_is_error_event_false_for_empty_dict** — `is_error_event({})` → `False`
- **test_read_error_text_returns_none_for_non_error_event** — `read_error_text_from_event({"type": "result", "result": "Готово"})` (без `is_error`) → `None`
- **test_read_error_text_returns_none_for_non_result_event** — `read_error_text_from_event({"type": "assistant"})` → `None`
- **test_read_error_text_returns_text_for_error_result** — `read_error_text_from_event({"type": "result", "is_error": True, "result": "max turns exceeded"})` → `"max turns exceeded"`
- **test_read_error_text_returns_none_for_error_with_empty_result** — `read_error_text_from_event({"type": "result", "is_error": True, "result": ""})` → `None`
- **test_read_error_text_returns_none_for_error_with_none_result** — `read_error_text_from_event({"type": "result", "is_error": True, "result": None})` → `None`
- **test_read_terminal_status_returns_none_for_non_terminal** — `read_terminal_status_from_event({"type": "assistant", "message": {"role": "assistant", "content": "..."}})` → `None`. Нефинальное событие не имеет статуса
- **test_read_terminal_status_returns_failed_for_error_result** — `read_terminal_status_from_event({"type": "result", "is_error": True, "result": "max turns exceeded"})` → `TerminalStatus.FAILED`
- **test_read_terminal_status_returns_success_for_clean_result** — `read_terminal_status_from_event({"type": "result", "is_error": False, "result": "ok"})` → `TerminalStatus.SUCCESS`
- **test_read_terminal_status_returns_success_for_result_without_is_error_field** — `read_terminal_status_from_event({"type": "result", "result": "ok"})` → `TerminalStatus.SUCCESS`. Отсутствие флага трактуется как штатное завершение (`bool(None) == False`)
- **test_get_stop_strategy_returns_two_steps** — `len(ClaudeCodeBackend().get_stop_strategy().steps)` → `2`
- **test_get_stop_strategy_first_step_is_sigterm_with_timeout** — первый шаг стратегии — `StopSignalStep(signal_to_send=signal.SIGTERM, wait_seconds_before_next=5)` (значение `TERMINATE_TIMEOUT_SECONDS`)
- **test_get_stop_strategy_last_step_is_sigkill_no_wait** — второй (последний) шаг — `StopSignalStep(signal_to_send=signal.SIGKILL, wait_seconds_before_next=0.0)`
- **test_get_stop_strategy_does_not_contain_sigint** — ни в одном шаге `step.signal_to_send != signal.SIGINT` (Claude обрабатывает SIGINT и SIGTERM одинаково — отдельный SIGINT-шаг не нужен)
- **test_get_stop_strategy_is_singleton** — два вызова возвращают один и тот же объект `StopStrategy` (`is`), потому что стратегия неизменяема (`frozen=True`, `tuple` внутри)
- **test_read_session_file_snapshot_returns_empty_for_missing_file** — async-тест: `read_session_file_snapshot("/nonexistent/file.jsonl")` → `SessionFileSnapshot(messages=[], raw_record_count=0, last_record=None, is_turn_active=False)`. Исключение НЕ выбрасывается, в логе `debug`/`warning`
- **test_read_session_file_snapshot_returns_empty_for_empty_file** — async-тест: создать пустой файл (0 байт) → snapshot со всеми пустыми полями (как выше)
- **test_read_session_file_snapshot_counts_raw_records_including_invalid** — async-тест: JSONL-файл с 4 строками: 2 валидные `assistant`-записи, 1 невалидная (битый JSON), 1 валидная `result`-запись → `raw_record_count == 4`, `len(messages) == 1` (только assistant попадает в `_messages_from_jsonl_records`, result — нет; невалидная пропускается на стадии парсинга)
- **test_read_session_file_snapshot_counts_only_non_empty_lines** — async-тест: файл с тремя непустыми строками и одной пустой (`"\n"`) → `raw_record_count == 3`
- **test_read_session_file_snapshot_last_record_is_last_parsed** — async-тест: JSONL-файл с тремя записями `system → assistant → result` → `last_record["type"] == "result"`
- **test_read_session_file_snapshot_last_record_is_none_for_empty_parse** — async-тест: файл из одной невалидной строки → `last_record is None`, `raw_record_count == 1`
- **test_read_session_file_snapshot_is_turn_active_true_for_assistant_last** — async-тест: последняя валидная запись имеет `type == "assistant"` → `is_turn_active is True`
- **test_read_session_file_snapshot_is_turn_active_true_for_progress_last** — async-тест: последняя валидная запись имеет `type == "progress"` → `is_turn_active is True`
- **test_read_session_file_snapshot_is_turn_active_true_for_queue_operation_last** — async-тест: последняя валидная запись имеет `type == "queue-operation"` → `is_turn_active is True`
- **test_read_session_file_snapshot_is_turn_active_false_for_result_last** — async-тест: последняя валидная запись имеет `type == "result"` → `is_turn_active is False` (turn завершён)
- **test_read_session_file_snapshot_is_turn_active_false_for_system_last** — async-тест: последняя валидная запись имеет `type == "system"` → `is_turn_active is False` (system не входит в busy-множество)
- **test_read_session_file_snapshot_messages_match_read_messages_from_session_file** — async-тест: для одного и того же файла `(await read_session_file_snapshot(path)).messages == await read_messages_from_session_file(path)`. Гарантия, что wrapper-метод не расходится со snapshot-ом

### Граничные случаи

- **test_compose_args_with_empty_string_session_id** — `compose_subprocess_command_args("", ...)` — пустая строка считается ненулевым session_id, добавляет `--resume ""`. Это легитимное поведение интерфейса (валидация id не входит в задачу метода)
- **test_encode_user_message_with_empty_text** — `encode_user_message_for_cli_stdin("", [])` возвращает байты для `{"type":"user","message":{"role":"user","content":""}}\n` (пустая строка — допустимый content)
- **test_encode_user_message_with_unicode_emoji** — `encode_user_message_for_cli_stdin("Привет 🚀", [])` сохраняет эмодзи UTF-8 байтами (не эскейпами), проверяется обратной декодировкой и сравнением со строкой
- **test_parse_stdout_line_handles_nested_json** — `parse_stdout_line_into_event('{"a":{"b":{"c":[1,2,3]}}}')` возвращает корректную вложенную структуру (проверка делегирования в `json.loads`)
- **test_read_progress_text_handles_missing_message_key** — `read_progress_text_from_event({"type": "assistant"})` (без `message`) → `None` (не падает с `KeyError`)
- **test_read_progress_text_handles_content_not_list** — `read_progress_text_from_event({"type": "assistant", "message": {"content": "просто строка"}})` → `None` (защита от регрессии формата)
- **test_read_messages_from_empty_file_returns_empty_list** — async-тест: создать пустой файл, `read_messages_from_session_file(path)` → `[]`. Тип: edge case
- **test_read_messages_skips_meta_user_messages** — JSONL с записью `{"type":"user","isMeta":true,"message":{"content":"meta"}}` — эта запись пропускается, `[]` (или содержит только нормальные сообщения, если они есть)
- **test_read_messages_extracts_text_from_content_list** — JSONL с записью `{"type":"user","message":{"content":[{"type":"text","text":"hi"}]}}` — извлечённый `text == "hi"`
- **test_read_messages_extracts_text_from_string_content** — JSONL с записью `{"type":"user","message":{"content":"hello"}}` — `text == "hello"`
- **test_read_messages_marks_no_response_marker_as_empty** — assistant-запись с `text == "No response requested."` → `is_empty_response is True`
- **test_list_session_files_returns_empty_for_nonexistent_project_dir** — async-тест: вызвать с путём, для которого папка `~/.claude/projects/<sanitized>` не существует — возвращает `[]`, в логе `warning`
- **test_list_session_files_limits_to_max_recent_sessions** — async-тест: создать 20 JSONL-файлов в tmp-папке (через мок `os.path.expanduser` или через монкипатчинг `CLAUDE_PROJECTS_RELATIVE_DIR`) — результат содержит ровно 15 элементов
- **test_list_all_session_files_ignores_recent_limit** — async-тест: создать 20 JSONL-файлов в tmp-папке; `list_all_session_files_for_project` возвращает все 20, отсортированные по `mtime DESC`
- **test_list_session_files_sorts_by_mtime_desc** — два файла с разным mtime — порядок: новый первым
- **test_session_file_info_contains_file_path_session_id_mtime_preview** — для созданного валидного JSONL-файла все четыре поля заполнены корректно

### Тесты ошибок

- **test_compose_args_raises_when_binary_not_found** — мок `shutil.which` возвращает `None`, мок `os.path.exists("/usr/local/bin/claude")` возвращает `False` — `compose_subprocess_command_args(None, "/tmp", "x", [])` выбрасывает `BackendBinaryNotFoundError` с сообщением, содержащим «Claude Code CLI не найден»
- **test_module_import_does_not_check_binary** — простой `import claude_manager.claude_code_backend` не должен проверять наличие бинарника (lazy-проверка). Реализуется через мок `shutil.which`, который выбрасывает `RuntimeError` при вызове — импорт модуля и инстанцирование класса не должны вызвать `shutil.which`. Это страховка контракта родительской спеки: «импорт модуля не должен падать у пользователя, у которого установлен только Codex CLI»
- **test_parse_stdout_line_raises_protocol_error_for_invalid_json** — `parse_stdout_line_into_event("это не json")` выбрасывает `BackendProtocolError`, сообщение содержит первые 200 символов строки
- **test_parse_stdout_line_truncates_long_invalid_json_to_200_chars** — для строки длиной 500 символов невалидного JSON — сообщение `BackendProtocolError` содержит ровно первые 200 символов
- **test_read_messages_returns_empty_when_file_does_not_exist** — async-тест: `read_messages_from_session_file("/nonexistent/file.jsonl")` → `[]`, в логе `debug`/`warning`, исключение НЕ выбрасывается
- **test_read_messages_skips_invalid_json_lines** — async-тест: JSONL-файл с тремя строками, средняя — невалидный JSON — результат содержит две корректные записи, средняя пропущена с `warning`
- **test_list_session_files_handles_oserror** — async-тест: мок `os.listdir` выбрасывает `OSError` — метод возвращает `[]`, в логе `error`, исключение НЕ выбрасывается
- **test_list_session_files_handles_edeadlk** — async-тест: мок `_read_file_lines_blocking` выбрасывает `OSError(11, "Resource deadlock avoided")` — метод пропускает файл, продолжает с другими, в логе `warning`. Защита от транзиентной ошибки macOS

### Контрактные тесты с реальным CLI (опциональные интеграционные)

Все тесты этого раздела пропускаются через `pytest.mark.skipif(shutil.which("claude") is None, reason="Claude CLI not installed")`.

- **test_claude_sanitize_matches_real_cli_folder** — создать tmp-директорию с пробелами и не-ASCII (`/tmp/test session 一二三 _underscore`), запустить `claude -p "say x" --output-format stream-json --dangerously-skip-permissions` через `subprocess.run` в этой директории, дождаться завершения, найти созданную папку в `~/.claude/projects/` (по mtime — самая свежая), сравнить её имя с результатом `ClaudeCodeBackend().locate_session_files_directory_for_project("/tmp/test session 一二三 _underscore")`. Должны совпадать побайтно. Это критический тест контракта sanitize
- **test_claude_stdin_format_is_accepted_by_real_cli** — запустить `claude -p --input-format stream-json --output-format stream-json --dangerously-skip-permissions` через `asyncio.create_subprocess_exec`, передать в stdin байты, возвращаемые `ClaudeCodeBackend().encode_user_message_for_cli_stdin("Скажи слово 'banana'", [])`, закрыть stdin, прочитать stdout до события `result`, проверить что `event["result"]` непустое и содержит «banana». Это критический тест контракта stdin-формата
- **test_claude_command_args_run_real_cli_to_completion** — запустить subprocess с args от `compose_subprocess_command_args(None, "/tmp", "x", [])` плюс stdin от `encode_user_message_for_cli_stdin("hi", [])`, дождаться завершения процесса с `wait_for(timeout=120)`, проверить что код возврата 0 и в stdout была строка с `type == "result"`. Это интеграционный тест полного цикла команды + stdin
- **test_claude_event_protocol_emits_session_id_in_system_event** — пропарсить первое событие stdout через `parse_stdout_line_into_event`, проверить что `read_session_id_from_event(event)` возвращает строку UUID. Тест устойчив к изменению ID, проверяет только структуру
- **test_claude_event_protocol_emits_result_as_terminal** — прочитать все события stdout до конца, найти событие с `is_turn_complete_event(event) == True`, убедиться что после него процесс завершается в течение 5 секунд. Тест контракта «result — последнее событие»

### Связанность тестов с разделами спеки

Каждый абстрактный метод родительской спеки покрыт хотя бы одним юнит-тестом — оригинальные 12 методов + 2 свойства плюс расширения интерфейса (`is_turn_terminal_session_record`, `read_session_file_snapshot`, `is_error_event`, `read_error_text_from_event`, `read_terminal_status_from_event`, `get_stop_strategy`). Расширения покрываются отдельными группами тестов:
- snapshot — пустой файл, несуществующий файл, raw-счётчик включая невалидные строки, последняя запись, флаг `is_turn_active` для всех типов last_record, согласованность с `read_messages_from_session_file`
- ошибочное завершение — `is_error_event` и `read_error_text_from_event` для `result`+`is_error=true`, `result`+`is_error=false`, `result` без поля, `assistant`-события, и пустого/`None` `result`
- терминальный статус — `read_terminal_status_from_event` для нефинальных событий (`None`), для `result`+`is_error=true` (`FAILED`), для штатного `result` (`SUCCESS`), для `result` без флага (`SUCCESS` через `bool(None) == False`)
- stop signal sequence — длина, первый шаг SIGTERM с таймаутом, последний шаг SIGKILL без ожидания, отсутствие SIGINT, независимость возвращаемых списков

Каждое значение константы из родительской спеки покрыто прямой проверкой (`MAX_RECENT_SESSIONS`, `PREVIEW_MAX_LENGTH`, `EMPTY_RESPONSE_MARKER`, типы событий, `TERMINATE_TIMEOUT_SECONDS`). Каждый контракт с внешним CLI покрыт интеграционным тестом (sanitize path, stdin format, события, файлы сессий). Все тест-кейсы готовы к реализации без дополнительных уточнений — даны конкретные входы и ожидаемые результаты.
