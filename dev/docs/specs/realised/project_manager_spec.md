# Спецификация модуля: project_manager

Дата: 06-05-2026
Слой: 4 (зависит от слоёв 0-3: `config`, `coding_agent_backend`, `unread_buffer`, `current_backend_registry`, `session_manager`, `daily_session_registry`, `session_watcher`)
Файл: `src/claude_manager/project_manager.py`

**Связанные спеки:**

- `coding_agent_backend_spec.md` — абстрактный интерфейс CLI-бэкенда. Источник `BackendName`, `SessionFileInfo`, `SessionFileSnapshot`. Используется для operational-перечисления всех сессий нового проекта (`backend.list_all_session_files_for_project`) и для дельта-чтения JSONL (`backend.read_session_file_snapshot`). UI-метод `list_session_files_for_project` остаётся для `/sessions`.
- `claude_code_backend_spec.md`, `codex_backend_spec.md` — конкретные реализации, выбираются через `get_backend(BackendName)` для backend-aware доставки непрочитанных.
- `current_backend_registry_spec.md` — глобальный реестр выбранного бэкенда. **НЕ сбрасывается** при переключении проекта (явное архитектурное решение, см. раздел «Однопользовательский инвариант и неизменяемость глобальных модулей»).
- `unread_buffer_spec.md` — тонкое in-memory хранилище счётчиков непрочитанных. Новый API: `save_snapshot(session_id, backend, raw_record_count)`, `restore_snapshot(session_id, backend) -> int | None`, `clear_expired()`. Все старые функции (`has_pending`, `get_pending_messages`, `clear_snapshot` по `project_path`) отброшены.
- `session_manager_spec.md` — backend-aware привязки `chat_id ↔ ActiveSession(session_id, backend)`, `reset_state` (async).
- `daily_session_registry_spec.md` — backend-aware дневной реестр `DailySessionEntry(session_id, backend)`, `reset_state` (async, внутри запускает orphan cleanup).
- `session_watcher_spec.md` — две независимые инстанции (по одной на каждый `BackendName`). `pause_all()` / `resume_all()` — **синхронные**, паузят/возобновляют обе инстанции. `reset_state()` — async. `get_seen_counts_snapshot(backend)` — sync, отдаёт `dict[session_id, raw_count]` для одного бэкенда.
- `process_manager_spec.md` — composite key `(session_id, BackendName)`. Процессы при переключении проекта **не останавливаются** — это явное решение, продолжают работать в фоне.
- `dev/docs/specs/realised/project_manager_spec.md` (после реализации backend-абстракции) — старая Claude-only версия, сохраняется в `realised/` для трейсабельности.

## Назначение

Оркестратор фичи «Переключение между проектами». Модуль решает три задачи:

1. **Сканирование PROJECTS_ROOT_DIR** — возвращает упорядоченный список проектов (директорий), исключая скрытые имена и символические ссылки. Помечает текущий активный проект.
2. **Атомарное переключение между проектами** — координирует ДВЕ инстанции `session_watcher` (Claude и Codex), снапшоты `unread_buffer` для активных сессий обоих бэкендов, сброс state-модулей (`session_manager`, `daily_session_registry`, `session_watcher`), смену `config.WORKING_DIR`, доставку непрочитанных при возврате. Подразумевает строгую транзакционную семантику: либо переключились полностью (включая обе инстанции watcher), либо откатились.
3. **Персистентность последнего выбора** — пишет путь активного проекта в `~/.claude-manager-current-project` и читает его при старте бота для восстановления выбора между запусками.

Модуль не знает о Telegram API — он только работает с файловой системой, координирует state-модули и возвращает структурированный результат `SwitchResult`. Верхний слой (`bot.py`) превращает результат в Telegram-сообщения и доставляет непрочитанные пользователю.

**Контракт «не убиваем процессы».** При переключении проекта `process_manager._processes` НЕ модифицируется. Уже запущенные subprocess'ы Claude/Codex продолжают писать ответы в JSONL-файлы своего исходного проекта. При возврате в проект эти ответы становятся «непрочитанными» и доставляются через дельту по `unread_buffer`. Это согласовано с CLAUDE.md → «Переключение проектов» и с `process_manager_spec.md` → «Контракт ключей и захват backend» (захваченный backend и cwd держатся до завершения turn-а независимо от глобальных мутаций).

## Расхождение с Claude-only версией спеки (10-04-2026)

В прежней Claude-only версии (`dev/docs/specs/realised/project_manager_spec.md` после реализации backend-абстракции) модуль работал с одной инстанцией `session_watcher` и старым «толстым» `unread_buffer`. Новая спека сохраняет внешний API на уровне UX (CJM-11 не меняется, команды `/projects` и `/pN` продолжают работать), но переписывает внутренний контракт под backend-абстракцию. Перечень изменений:

- **Snapshot непрочитанных — для каждой сессии каждого бэкенда отдельно.** Старый код вызывал `unread_buffer.save_snapshot(project_path, dict[session_id, count])`. Новый — итерируется по активным сессиям обоих `BackendName`, для каждой вызывает `unread_buffer.save_snapshot(session_id, backend, raw_record_count)`. Источник `raw_record_count` — `session_watcher.get_seen_counts_snapshot(backend)` (отдельные вызовы для Claude и Codex). Если активная сессия в `session_manager` есть, но в snapshot watcher её нет (новая сессия, ещё не отслеживалась) — пропускается.
- **Доставка непрочитанных — backend-aware дельта-чтение.** Старый `unread_buffer.get_pending_messages(project_path)` отброшен. Новый алгоритм в `_collect_pending_deliveries`:
  1. Перебрать все backends через `coding_agent_backend.get_all_backends()`.
  2. Для каждого backend получить полный operational-список файлов нового проекта через `backend.list_all_session_files_for_project(new_project_path)`.
  3. Для каждого файла позвать `unread_buffer.restore_snapshot(session_id, backend)` — получить старый счётчик или `None`.
  4. Если `None` — пропустить (сессия не отслеживалась через переключение или TTL истёк).
  5. Если есть — позвать `await backend.read_session_file_snapshot(file_path)`, сравнить `snapshot.raw_record_count` с `old_count`. Если новых строк нет — пропустить.
  6. Иначе — отрезать дельту `snapshot.messages[<offset>:]`, отфильтровать только assistant-сообщения с непустым текстом, превратить в `PendingDeliveryItem`.
- **Pause/resume — на ВСЕ инстанции через единый вызов.** `session_watcher.pause_all()` и `session_watcher.resume_all()` внутри пробегаются по обеим инстанциям (Claude и Codex). Это снимает риск «полу-переключённого» состояния, при котором одна инстанция всё ещё активна и видит изменённый `WORKING_DIR`, а другая ещё нет.
- **Pause/resume — синхронные, без await.** Это не опечатка: по `session_watcher_spec.md` обе функции синхронные (внутри они только устанавливают флаг паузы, не делают I/O). Соответственно во всём алгоритме `await` для них не используется.
- **`current_backend_registry` НЕ сбрасывается.** Глобальный реестр имени активного бэкенда переезжает в новый проект как есть. После `/projects` пользователь продолжает работать с тем же CLI, который выбрал ранее. Это сознательное решение — гранулярность переключения LLM глобальная, а не per-project. См. `current_backend_registry_spec.md` → «Глобальный модуль, НЕ сбрасывается».
- **`PendingDeliveryItem` (DTO для bot.py) — backend-aware.** Содержит `backend: BackendName`, чтобы `bot.py` мог зарегистрировать сессию в `daily_session_registry` через правильный backend и получить корректный `day_number` для заголовка сообщения.
- **Циклический обход — внутренний API `resolve_neighbor_project`.** В прежней версии модуля был только `/projects` + `/pN`. Новая функция `resolve_neighbor_project(direction)` оставляет в модуле готовую логику выбора соседнего проекта, но пользовательские команды `/next` и `/prev` не входят в текущий UI-контракт.
- **Защита от частичных сбоев — детально расписана.** Раздел «Алгоритм работы → Атомарная процедура переключения» явно покрывает четыре сценария частичного сбоя: pause второй инстанции упал, save_snapshot на одной сессии упал, reset_state одного модуля упал, load нового state не удался. Гарантия: `resume_all()` всегда выполняется в `finally`, даже если внутри `try` сработало исключение.

## Обслуживаемые сценарии

- **CJM-11: Переключение между проектами** — пользователь выбирает другой проект через `/projects` + `/pN`, бот атомарно переключает контекст, доставляет непрочитанные сообщения за время отсутствия (TTL 3 часа).

  В рамках сценария модуль выполняет:
  - **Сканирование** — `scan_available_projects()` строит список из `PROJECTS_ROOT_DIR`, помечает текущий маркером `is_current=True`.
  - **Переключение** — `switch_project(target_path)` атомарно меняет контекст, сбрасывает state-модули, сохраняет последний выбор в файл, собирает непрочитанные.
  - **Циклический обход** — `resolve_neighbor_project("next")` / `resolve_neighbor_project("prev")` возвращают соседний проект для внутреннего или будущего пользовательского использования.
  - **Восстановление при старте** — `load_last_selected_project()` возвращает путь из `~/.claude-manager-current-project` или `None` (если файла нет / он повреждён / путь невалиден).

## Команды и пользовательский интерфейс

Команды живут в обработчиках `bot.py`. Здесь зафиксирован UI-контракт, который должен соблюдаться при реализации.

### Команда `/projects`

- **Триггер:** `CommandHandler("projects", ...)` в `bot.py`.
- **Действие:** вызвать `await project_manager.scan_available_projects()`.
- **Если список пуст:** ответ — `Проекты не найдены в папке {root}` (где `{root}` — `config.PROJECTS_ROOT_DIR`).
- **Если список непуст:** ответ — построчно, формат строки `{marker}/p{number} {name}`, где:
  - `marker` — `● ` (точка U+25CF + пробел) для текущего проекта, иначе пустая строка.
  - `number` — позиция в списке, от 1.
  - `name` — `ProjectInfo.name`.
- **`parse_mode=None`** — без HTML-форматирования (имена проектов могут содержать `<`, `>`, `&`).

### Команда `/pN`

- **Триггер:** `MessageHandler(filters.Regex(r"^/p\d+$"), ...)`. `N` — натуральное число.
- **Действие в `bot.py`:**
  1. Извлечь `project_number = int(text[2:])`.
  2. Получить список через `await project_manager.scan_available_projects()`.
  3. Если `project_number < 1` или `project_number > len(projects)` — ответ `Проект #{number} не найден`.
  4. Иначе вызвать `result = await project_manager.switch_project(projects[project_number - 1].absolute_path)`.
  5. Сформировать текст по `result`:
     - `result.already_active` → `Уже работаю в проекте: {name}`
     - `result.success and pending_messages_count == 0` → `Переключено на проект: {name}`
     - `result.success and pending_messages_count > 0` → `Переключено на проект: {name}\nНепрочитанных сообщений: {count}`
     - `not result.success` → `Ошибка переключения: {error_message}`
  6. Если `result.success and pending_messages_count > 0` — для каждого `PendingDeliveryItem` зарегистрировать сессию через `daily_session_registry.register_session(item.session_id, item.backend)` (получить `day_number`), отправить пользователю отдельным сообщением через `send_response(chat_id, item.text, day_number, item.backend, is_final=item.is_final)`.

### Команды `/next` и `/prev`

Команды `/next` и `/prev` не входят в текущий пользовательский контракт. Модуль `project_manager` оставляет только внутреннюю функцию `resolve_neighbor_project(direction)`, чтобы будущая пользовательская доработка могла подключить циклический обход без изменения нижнего слоя.

### Доставка непрочитанных

- **Заголовок:** `#{day_number} ✅ {text}` (галочка U+2705) — формат, единый со всеми финальными ответами Claude. Принцип «#N + ✅» зафиксирован в CJM-02 BRD; `project_manager` его не определяет — формирует только `text` и помечает `is_final=True`.
- **Дробление:** длинные сообщения разбиваются `message_splitter` на части ≤ 4096 символов. Это ответственность `bot.py`/`message_splitter`, не `project_manager`.
- **TTL:** если все непрочитанные старше 3 часов — `pending_messages_count == 0`, отдельных сообщений нет.

## Публичный API

### Dataclass `ProjectInfo`

```python
@dataclass(frozen=True)
class ProjectInfo:
    name: str               # имя папки проекта (последний компонент пути)
    absolute_path: str      # абсолютный путь к папке
    is_current: bool        # True, если путь совпадает с config.WORKING_DIR
```

Иммутабельный (`frozen=True`).

### Dataclass `PendingDeliveryItem`

```python
@dataclass(frozen=True)
class PendingDeliveryItem:
    session_id: str         # UUID сессии
    backend: BackendName    # backend-владелец сессии (обязательно для регистрации в daily_session_registry)
    text: str               # содержимое сообщения (последняя assistant-реплика дельты)
    is_final: bool          # True, если на момент сохранения снапшота turn у этой сессии завершён
```

Иммутабельный (`frozen=True`). Используется как элемент `SwitchResult.pending_messages`. `bot.py` итерируется по нему и доставляет каждый элемент отдельным Telegram-сообщением.

### Dataclass `SwitchResult`

```python
@dataclass(frozen=True)
class SwitchResult:
    success: bool                              # True при успехе или already_active
    already_active: bool                       # True при no-op (уже в этом проекте)
    old_path: str                              # путь до переключения
    new_path: str                              # путь, на который переключили (или пытались)
    pending_messages_count: int                # длина pending_messages (для формирования заголовка)
    pending_messages: list[PendingDeliveryItem]  # непрочитанные на момент возврата
    error_message: str                         # причина ошибки или ""
```

### Исключение `ProjectSwitchError`

```python
class ProjectSwitchError(Exception):
    """Ошибка валидации пути: невалидный путь, выход за PROJECTS_ROOT_DIR, отказ доступа."""
```

Бросается только из внутренней `_validate_target_path`, наружу не выходит — ловится в `_precheck_switch` и превращается в `SwitchResult(success=False, error_message=...)`.

### Функция `scan_available_projects`

```python
async def scan_available_projects() -> list[ProjectInfo]
```

Возвращает отсортированный по имени (lowercase) список проектов из `config.PROJECTS_ROOT_DIR`. Фильтрация: только директории (не файлы), не скрытые (имя не начинается с `.`), не символические ссылки (защита от выхода за `PROJECTS_ROOT_DIR`).

**Аргументы:** нет.

**Возвращает:** `list[ProjectInfo]`. Пустой список, если `PROJECTS_ROOT_DIR` не существует, нет прав, или папка пуста.

**Исключения:** не выбрасывает. `OSError` при чтении логируется, возвращается пустой список.

### Функция `switch_project`

```python
async def switch_project(target_path: str) -> SwitchResult
```

Атомарно переключает бот на `target_path`. Гарантирует, что обе инстанции `session_watcher` (Claude и Codex) либо обе паузнуты до завершения транзакции, либо обе возобновлены — независимо от исхода. Подробный алгоритм — в разделе «Алгоритм работы → Атомарная процедура переключения».

**Аргументы:**

- `target_path` (`str`) — абсолютный путь к целевой папке проекта. Должен быть директорией внутри `config.PROJECTS_ROOT_DIR`.

**Возвращает:** `SwitchResult` — заполненный результат. Никогда не возвращает `None`.

**Исключения:** не выбрасывает. Все ошибки (валидация, сбой reset_state, битый sessions.json) превращаются в `SwitchResult(success=False, error_message=<причина>)`.

**Сериализация:** параллельные вызовы выполняются последовательно через module-level `asyncio.Lock` (`_switch_lock`).

### Функция `resolve_neighbor_project`

```python
async def resolve_neighbor_project(direction: str) -> ProjectInfo | None
```

Возвращает соседний проект относительно текущего активного, по списку из `scan_available_projects()`. Циклический обход: после последнего идёт первый, перед первым — последний.

**Аргументы:**

- `direction` (`str`) — `"next"` или `"prev"`. Любое другое значение трактуется как `"next"` (без выброса исключения; имя ограничено документацией, не runtime-проверкой).

**Возвращает:** `ProjectInfo` соседнего проекта или `None`, если список пуст или содержит ровно один проект (нет соседа).

**Исключения:** не выбрасывает.

**Поведение:**

- **Список пуст** → `None`.
- **Список из одного проекта** → `None` (нет соседа).
- **Текущий проект не найден в списке** (например, `WORKING_DIR` указывает вне `PROJECTS_ROOT_DIR`) → возвращается первый проект (для `"next"`) или последний (для `"prev"`) — поведение «начни с края».
- **Список из двух и более проектов, текущий найден** → возвращается следующий или предыдущий по индексу с обходом по модулю длины.

### Функция `get_current_project_path`

```python
def get_current_project_path() -> str
```

Возвращает текущее значение `config.WORKING_DIR`. Тонкая обёртка для удобства потребителей (`bot.py`), чтобы не импортировать `config` напрямую.

**Аргументы:** нет. **Возвращает:** `str`. **Исключения:** нет.

### Функция `save_selected_project`

```python
async def save_selected_project(path: str) -> None
```

Атомарно (через `<file>.tmp` + `os.replace`) записывает `path` в `config.LAST_PROJECT_FILE` (`~/.claude-manager-current-project`). Используется внутри `switch_project` после успешного переключения; может вызываться отдельно при необходимости.

**Аргументы:**

- `path` (`str`) — абсолютный путь к проекту для сохранения.

**Возвращает:** `None`.

**Исключения:** не выбрасывает. `OSError` логируется на уровне `error` с `exc_info`. Невозможность сохранить файл не должна отменять успешное переключение в памяти.

### Функция `load_last_selected_project`

```python
async def load_last_selected_project() -> str | None
```

Читает `config.LAST_PROJECT_FILE` и возвращает валидный путь к последнему выбранному проекту. Вызывается в `main.py` при старте бота для восстановления выбранного проекта. Валидирует путь (через `_validate_target_path`) — если проект удалён или вне корня, возвращает `None` (с warning в логе).

**Аргументы:** нет.

**Возвращает:** `str` (валидный абсолютный путь) или `None`.

**Исключения:** не выбрасывает.

## Внутренние функции

### `_paths_point_to_same_dir(first_path: str, second_path: str) -> bool`

Сравнивает пути по `os.path.realpath` (раскрытие симлинков). Используется для проверки `is_current` и для no-op случая в `switch_project`.

### `_is_path_inside_root(target_path: str, root_path: str) -> bool`

Проверяет, что `realpath(target)` строго внутри `realpath(root)`. Защита от path traversal. Использует `startswith(root + os.sep)`, чтобы `/root/foo-bar` не совпал с `/root/foo`. Совпадение с самим корнем возвращает `False`.

### `_validate_target_path(target_path: str) -> None`

Проверяет: путь существует, это директория, внутри `PROJECTS_ROOT_DIR`, есть права на чтение. При нарушении бросает `ProjectSwitchError(<сообщение на русском>)`.

### `_should_include_project(entry_name: str, entry_full_path: str) -> bool`

Решает, попадает ли запись в результат `scan_available_projects`. Возвращает `False` для скрытых (точка), симлинков, не-директорий.

### `_list_project_entries() -> list[str]`

Блокирующая обёртка над `os.listdir(config.PROJECTS_ROOT_DIR)`. Выносится в поток через `asyncio.to_thread` в `scan_available_projects`.

### `_build_project_info(entry_name: str) -> ProjectInfo`

Собирает `ProjectInfo` для одной записи: вычисляет полный путь, определяет `is_current` через `_paths_point_to_same_dir`.

### `async _capture_unread_snapshots() -> None`

Снимает счётчики непрочитанных для всех активных сессий обоих бэкендов и кладёт их в `unread_buffer`. Алгоритм:

1. Получить `bindings = session_manager.get_all_bindings()` (sync).
2. Получить snapshot-словари watcher отдельно для каждого бэкенда:
   - `seen_claude = session_watcher.get_seen_counts_snapshot(BackendName.CLAUDE)`
   - `seen_codex = session_watcher.get_seen_counts_snapshot(BackendName.CODEX)`
3. Для каждой `active_session` из `bindings.values()`:
   - Выбрать словарь по `active_session.backend`.
   - Получить `raw_count = словарь.get(active_session.session_id)`. Если `None` — сессия ещё не отслеживалась watcher-ом (новая, не успела попасть в дельта-чтение); пропустить (логируется debug, не ошибка).
   - Вызвать `unread_buffer.save_snapshot(active_session.session_id, active_session.backend, raw_count)`.
4. **Best-effort семантика:** любое исключение в шаге 3 для одной сессии перехватывается, логируется на уровне `warning`, цикл продолжается. Это сознательное решение — потеря снапшота для одной сессии хуже, чем потеря для всех.

### `async _reset_all_state_modules() -> None`

Последовательно сбрасывает state-модули в **строгом порядке**:

1. `await session_manager.reset_state()` — внутри сам перечитает `sessions.json` нового проекта (см. `session_manager_spec.md`).
2. `await daily_session_registry.reset_state()` — внутри сам перечитает `daily_sessions.json` нового проекта и запустит orphan cleanup.
3. `await session_watcher.reset_state()` — внутри пройдёт по обеим инстанциям, перечитает файлы сессий нового проекта через `backend.list_all_session_files_for_project` для каждого backend и инициализирует `last_delivered_idx = len(messages) - 1` для каждой сессии.

**Best-effort при сбое одного модуля:** каждый `reset_state` обёрнут в `try/except Exception`. Если один сбойнул — логируется `error` с `exc_info`, остальные всё равно запускаются. Это критично, потому что без вызова всех трёх система останется в полу-сброшенном состоянии (например, `session_manager` уже на новом проекте, а `session_watcher` — ещё нет). После завершения функции, если хоть один сбойнул — наружу пробрасывается `RuntimeError("Один или несколько state-модулей не сбросились: <details>")`, чтобы `_perform_switch` мог откатиться.

**Порядок имеет значение:** `session_watcher` сбрасывается последним, потому что его `reset_state` использует `backend.list_all_session_files_for_project(new_path)` — а `new_path` уже актуален к этому моменту через `config.WORKING_DIR`. Но он не зависит от `session_manager`/`daily_session_registry`; порядок `manager → registry → watcher` выбран ради читаемости (от высокого уровня к низкому).

### `async _perform_switch(target_path: str) -> None`

Главное действие переключения. Подробный алгоритм — в разделе «Алгоритм работы → Атомарная процедура переключения».

### `async _rollback_switch(old_path: str) -> None`

Восстанавливает старое значение `config.WORKING_DIR`, перезагружает state старого проекта, удаляет снапшоты `unread_buffer` для активных сессий старого проекта (обходом `session_manager.get_all_bindings()` после повторного `reset_state`). Ошибка в самом откате логируется на уровне `error`, но не пробрасывается — иначе пользователь не получит исходное сообщение.

### `_make_error_result(old_path, target_path, error_message) -> SwitchResult`

Собирает `SwitchResult(success=False, already_active=False, ...)`. `pending_messages_count = 0`, `pending_messages = []`.

### `_make_success_result(old_path, target_path, pending_messages_count, pending_messages, already_active) -> SwitchResult`

Собирает `SwitchResult(success=True, ...)`.

### `_precheck_switch(target_path: str, old_path: str) -> SwitchResult | None`

Валидирует путь и ловит no-op «уже активен». Возвращает готовый `SwitchResult` или `None`, если нужно реально переключаться.

### `async _try_switch_with_rollback(target_path: str, old_path: str) -> tuple[bool, SwitchResult | None]`

Вызывает `_perform_switch`, при исключении — `_rollback_switch`. Возвращает `(True, None)` при успехе или `(False, error_result)` при сбое.

### `async _collect_pending_deliveries() -> list[PendingDeliveryItem]`

Собирает непрочитанные сообщения для проекта, в который только что вернулись. Алгоритм:

1. Перебрать `for backend in [BackendName.CLAUDE, BackendName.CODEX]`.
2. Для каждого backend:
   - `backend_obj = get_backend(backend)`
   - `files = await backend_obj.list_all_session_files_for_project(config.WORKING_DIR)` (актуальный путь нового проекта, полный operational-список без UI-лимита 15 сессий)
   - Для каждого `file: SessionFileInfo`:
     - `old_count = unread_buffer.restore_snapshot(file.session_id, backend)` (если `None` — пропустить).
     - `snapshot = await backend_obj.read_session_file_snapshot(file.file_path)`.
     - Если `snapshot.raw_record_count <= old_count` — нет новых строк, пропустить.
     - Найти границу `messages` по числу raw-строк (см. ниже «Алгоритм отрезания дельты»), отрезать новые `delta = snapshot.messages[boundary:]`.
     - Для каждого `msg in delta`:
       - Если `msg.role != "assistant"` — пропустить (доставляем только реплики ассистента).
       - Если `msg.is_empty_response` (служебная пустая реплика, см. `coding_agent_backend_spec.md`) — пропустить.
       - `is_final = (msg is delta[-1]) and (not snapshot.is_turn_active)` — последнее сообщение и turn завершён → финальное.
       - Добавить `PendingDeliveryItem(session_id=file.session_id, backend=backend, text=msg.text, is_final=is_final)` в результирующий список.
3. После цикла — вызвать `unread_buffer.clear_expired()` для гигиены (удалить старые записи, которые не подняли через `restore_snapshot`).
4. Вернуть собранный список (порядок: сначала Claude-сессии, потом Codex; внутри backend — порядок файлов из `list_all_session_files_for_project`, обычно от свежих к старым).

**Алгоритм отрезания дельты по `raw_record_count`:** `unread_buffer` хранит счётчик СТРОК JSONL (включая системные), а не индекс сообщений. Граница в `snapshot.messages` определяется через свойство `SessionFileSnapshot.messages_count_when_raw_count_was(old_count)` или (при его отсутствии) — упрощённо: если `len(snapshot.messages) >= 1`, то `boundary = max(0, len(snapshot.messages) - (snapshot.raw_record_count - old_count))`. **Это упрощение работает, только если каждая «новая» raw-строка соответствует не более чем одному элементу `messages`** — что справедливо для текущих форматов Claude и Codex. Если в будущем формат изменится (одна raw-строка → несколько messages), потребуется backend-специфичный helper. Пока — простой математический расчёт; помечен в спеке как «известная упрощённая модель», тест покрывает обе границы (вся дельта, ни одной).

### `async _finalize_successful_switch(old_path: str, target_path: str) -> SwitchResult`

Завершает успешное переключение:

1. `await save_selected_project(target_path)` — записать в `LAST_PROJECT_FILE`. Ошибка не отменяет успех.
2. `pending = await _collect_pending_deliveries()` — собрать дельту.
3. Логировать `info`: «Переключение проекта выполнено: %s -> %s (непрочитанных=%d)».
4. Вернуть `_make_success_result(old_path, target_path, len(pending), pending, already_active=False)`.

## Алгоритм работы

### scan_available_projects

1. Если `not os.path.isdir(config.PROJECTS_ROOT_DIR)` — лог `warning` «Папка проектов не существует: %s», вернуть `[]`.
2. Прочитать содержимое через `asyncio.to_thread(_list_project_entries)`. При `OSError` — лог `warning`, вернуть `[]`.
3. Для каждого `entry_name` собрать полный путь, применить `_should_include_project`. Если прошёл — построить `_build_project_info`.
4. Отсортировать по `name.lower()`.
5. Вернуть.

### switch_project

1. Войти в `async with _switch_lock` — сериализация.
2. `old_path = config.WORKING_DIR`.
3. `early = _precheck_switch(target_path, old_path)`. Если не `None` — вернуть.
4. `success, error_result = await _try_switch_with_rollback(target_path, old_path)`. Если `error_result is not None` — вернуть.
5. Вернуть `await _finalize_successful_switch(old_path, target_path)`.

### Атомарная процедура переключения (`_perform_switch`) — ЯДРО СПЕКИ

Задача: переключиться так, чтобы обе инстанции `session_watcher` (Claude и Codex) либо были паузнуты до завершения смены `WORKING_DIR` и `reset_state` всех state-модулей, либо все были возобновлены — независимо от исхода. Алгоритм построен по принципу «сначала пауза → затем все мутации в `try` → возобновление в `finally`».

```python
async def _perform_switch(target_path: str) -> None:
    # 1. Снять снапшоты unread для всех активных сессий обоих бэкендов
    #    Это делается ДО pause_all, потому что get_seen_counts_snapshot читает live-state watcher,
    #    а после pause_all он зафиксирован — оба варианта работают, но "до" чище: если pause упал,
    #    мы хотя бы записали последние известные счётчики.
    await _capture_unread_snapshots()

    # 2. Pause обеих инстанций watcher (Claude и Codex)
    #    pause_all — синхронная функция (внутри только установка флагов).
    session_watcher.pause_all()

    try:
        # 3. Сменить WORKING_DIR. После этого момента state-модули будут перечитывать файлы
        #    из НОВОГО проекта.
        config.WORKING_DIR = target_path

        # 4. Сбросить state всех потребителей.
        #    _reset_all_state_modules внутри проходит по всем трём, при сбое одного
        #    логирует и продолжает остальные, в конце пробрасывает RuntimeError если был сбой.
        await _reset_all_state_modules()
    finally:
        # 5. ВСЕГДА возобновить обе watcher-инстанции. Даже при исключении в try.
        #    resume_all — синхронная функция.
        session_watcher.resume_all()
```

**Если `_perform_switch` бросает исключение** — `_try_switch_with_rollback` вызывает `_rollback_switch(old_path)`:

1. `config.WORKING_DIR = old_path` — восстановить путь.
2. `await _reset_all_state_modules()` — перезагрузить state старого проекта (sessions.json и daily_sessions.json **старого** проекта снова в памяти).
3. Удалить снапшоты `unread_buffer` для всех активных сессий старого проекта (через `session_manager.get_all_bindings()` после reset). Удаление через перезапись: `unread_buffer.save_snapshot(session_id, backend, current_raw_count)` — записывает свежий счётчик, что эквивалентно «сейчас всё прочитано, ничего не накопилось». Альтернатива — добавить `clear_snapshot` в `unread_buffer` — отвергнута: новый `unread_buffer_spec.md` его не предоставляет. На практике сценарий редкий, перезапись через свежий счётчик корректна.
4. Любая ошибка внутри отката логируется (`logger.error(..., exc_info=True)`), но НЕ пробрасывается — иначе вызывающая сторона не получит исходное сообщение об ошибке. Вместо этого ошибка отката учитывается в `error_message` исходного `SwitchResult`.

**Важный гарант:** `resume_all()` вызывается **в `finally` блоке внутри `_perform_switch`** — не наружу. Это значит: даже если `_rollback_switch` сбойнёт, watcher уже возобновлён до начала отката. После `_rollback_switch` watcher работает на **старом** проекте (обе инстанции), и пользователь может продолжать работу как ни в чём не бывало.

### Сценарии частичных сбоев

Раздел отвечает на «что произойдёт, если...». Для каждого сценария — последовательность событий и итоговое наблюдаемое состояние.

**Сценарий 1: `_capture_unread_snapshots` упал на одной сессии.**

- `_capture_unread_snapshots` обёрнута в `try/except` внутри цикла по сессиям. Сбой одной — лог `warning`, цикл продолжается.
- Снапшоты для остальных сессий сохраняются.
- При возврате в проект сессия, для которой не было снапшота, не получит дельту — её непрочитанные потеряются. Это допустимый риск: одна сессия из десятков, и потеря — не критическая (пользователь увидит сообщения через `/sessions` или `/N`).
- `pause_all` и далее по плану — переключение продолжается.

**Сценарий 2: `pause_all()` упал внутри (например, одна из инстанций кинула).**

- `pause_all` синхронная — если упадёт, `try` не начнётся, `finally` не сработает (нет `try/finally` для самого `pause_all`).
- **Решение:** `pause_all` оборачивается в свой `try/except` ВНУТРИ `_perform_switch`:

  ```python
  try:
      session_watcher.pause_all()
  except Exception as e:
      session_watcher.resume_all()  # симметричный resume на всякий случай
      raise RuntimeError(f"Не удалось приостановить watcher: {e}")
  ```

- На практике `pause_all` по `session_watcher_spec.md` не делает I/O и не должен бросать — но защита оставлена для консервативности (если в будущем появится локирование).

**Сценарий 3: Pause Claude-watcher успешен, pause Codex-watcher упал → resume вызван для обоих.**

- `pause_all` внутри проходит по обеим инстанциям. Если сбой на второй — реализация `session_watcher.pause_all` сама решает: либо она атомарна (перед бросанием откатывает первую), либо нет.
- В нашей спецификации мы полагаемся на контракт `session_watcher_spec.md`: `pause_all` идемпотентна, `resume_all` всегда работает. Поэтому в `finally` блок `resume_all` отрабатывает корректно для обеих, даже если одна была паузнута, а другая нет (на не-паузнутой `resume_all` — no-op).
- Итог: обе инстанции в активном состоянии, `_perform_switch` пробросил `RuntimeError`, `_try_switch_with_rollback` выполняет откат, пользователь получает `SwitchResult(success=False, error_message=...)`.

**Сценарий 4: `reset_state` одного из state-модулей упал → watcher всё равно возобновлён.**

- `_reset_all_state_modules` внутри обёрнут в `try/except` для каждого модуля, в конце пробрасывает агрегированный `RuntimeError` если хоть один сбойнул.
- `_perform_switch` ловит этот `RuntimeError` и идёт в `finally` → `resume_all`.
- Watcher обеих инстанций работает, но на «полу-сброшенном» state (например, `session_manager` уже сбросился, а `daily_session_registry` нет).
- `_try_switch_with_rollback` ловит исключение, вызывает `_rollback_switch(old_path)` — это перезагружает state старого проекта в памяти. Восстановление полное.

**Сценарий 5: Загрузка `sessions.json` нового проекта не удалась (битый файл).**

- `session_manager.reset_state` внутри вызывает `load_bindings`. По спеке `session_manager_spec.md`: если файл повреждён → лог `warning`, `_bindings = {}`, `_bindings_path` установлен (запись разрешена). **Исключения наружу не пробрасываются.**
- `_reset_all_state_modules` отработает успешно.
- `_perform_switch` завершится без ошибок.
- Пользователь получит `SwitchResult(success=True)`, но привязок к сессиям не будет (бот в режиме `/all`).
- **Уведомление пользователя о повреждённом файле** — НЕ задача `project_manager`. По концепции state-модулей: лог `warning` достаточен, пользователь увидит «empty bindings» через первое же сообщение в чате (бот ответит `/all`-логикой). Если потребуется явное уведомление — это расширение `bot.py`, не `project_manager`.

**Сценарий 6: Все три `reset_state` упали.**

- `_reset_all_state_modules` пробросит `RuntimeError` с агрегированным описанием.
- `_perform_switch` уйдёт в `finally` → `resume_all`.
- `_try_switch_with_rollback` вызывает `_rollback_switch(old_path)`.
- `_rollback_switch` пытается ещё раз `_reset_all_state_modules` для старого пути — если опять сбой, ловит и логирует.
- Возможный итог: state-модули в неопределённом состоянии. Это catastrophic failure уровня нечитаемой файловой системы — `SwitchResult(success=False, error_message=...)` с понятным описанием. Пользователю предлагается перезапустить бота.

### resolve_neighbor_project

1. `projects = await scan_available_projects()`.
2. Если `len(projects) <= 1` — вернуть `None`.
3. Найти индекс текущего: `current_idx = next((i for i, p in enumerate(projects) if p.is_current), None)`.
4. Если `current_idx is None` — вернуть `projects[0]` (для `"next"`) или `projects[-1]` (для `"prev"`).
5. Вычислить `target_idx`:
   - `"next"` → `(current_idx + 1) % len(projects)`
   - иначе (`"prev"` и любой другой) → `(current_idx - 1) % len(projects)`
6. Вернуть `projects[target_idx]`.

### load_last_selected_project

1. Если `not config.LAST_PROJECT_FILE.exists()` — вернуть `None`.
2. `content = await asyncio.to_thread(LAST_PROJECT_FILE.read_text, "utf-8")`. При `OSError` — лог `warning`, вернуть `None`.
3. `stored = content.strip()`. Если пусто — вернуть `None`.
4. `try: _validate_target_path(stored); except ProjectSwitchError: лог warning, вернуть None`.
5. Вернуть `stored`.

### save_selected_project

1. `temp = LAST_PROJECT_FILE.with_name(LAST_PROJECT_FILE.name + ".tmp")`.
2. `await asyncio.to_thread(temp.write_text, path, "utf-8")`.
3. `await asyncio.to_thread(os.replace, str(temp), str(LAST_PROJECT_FILE))`.
4. При `OSError` в любом шаге — лог `error` с `exc_info`, НЕ бросать наружу.

## Однопользовательский инвариант и неизменяемость глобальных модулей

Бот архитектурно однопользовательский (см. CLAUDE.md → «Однопользовательский инвариант»). `project_manager` опирается на это:

- `config.WORKING_DIR` — один глобальный путь на весь процесс. Не per-`chat_id`.
- `config.PROJECTS_ROOT_DIR` — одна корневая папка на весь процесс.
- `LAST_PROJECT_FILE` — один файл персистентности.
- `session_manager.get_all_bindings()` возвращает все привязки всех `chat_id` (на практике — одного пользователя с разных устройств, один и тот же пользователь).

**`current_backend_registry` НЕ сбрасывается** при переключении проекта. Это явное архитектурное решение:

- Гранулярность переключения LLM — глобальная, по всему боту, а не per-project (см. `current_backend_registry_spec.md`).
- Файл хранится в `~/.claude-manager-current-backend` (хоум-директория), путь не зависит от `WORKING_DIR`.
- `_reset_all_state_modules` НЕ вызывает функций `current_backend_registry` — это доказательство контрактом.

Тест `test_current_backend_preserved_across_switch` проверяет это явно: пользователь в проекте A выбирает Codex через `/agent`, переключается на проект B, в B `current_backend_registry.get_current() == BackendName.CODEX` — без перезагрузки.

## Зависимости

**Стандартная библиотека:**

- `asyncio` — `Lock`, `to_thread`.
- `os` — `path.exists`, `path.isdir`, `path.islink`, `path.realpath`, `path.join`, `access`, `listdir`, `replace`, `sep`.
- `logging` — `logger = logging.getLogger(__name__)`.
- `dataclasses` — `dataclass(frozen=True)`.
- `pathlib.Path` — для `LAST_PROJECT_FILE`.

**Модули проекта:**

- `claude_manager.config` — `WORKING_DIR` (мутируется), `PROJECTS_ROOT_DIR`, `LAST_PROJECT_FILE`.
- `claude_manager.coding_agent_backend` — `BackendName`, `get_backend(name)`, `get_all_backends()`. Используется в `_collect_pending_deliveries` для backend-aware чтения файлов сессий.
- `claude_manager.unread_buffer` — `save_snapshot(session_id, backend, raw_record_count)`, `restore_snapshot(session_id, backend) -> int | None`, `clear_expired()`. Старые функции (`has_pending`, `get_pending_messages`, `clear_snapshot`) НЕ используются (отброшены в новом контракте).
- `claude_manager.session_manager` — `get_all_bindings() -> dict[int, ActiveSession]` (sync), `reset_state()` (async).
- `claude_manager.daily_session_registry` — `reset_state()` (async). `register_session` НЕ вызывается из `project_manager` (её вызывает `bot.py` при доставке pending).
- `claude_manager.session_watcher` — `pause_all()` (sync), `resume_all()` (sync), `reset_state()` (async), `get_seen_counts_snapshot(backend)` (sync).
- `claude_manager.process_manager` — **НЕ импортируется**. Процессы при переключении проекта не трогаются — это архитектурное решение, и `project_manager` его поддерживает отсутствием соответствующих вызовов.
- `claude_manager.current_backend_registry` — **НЕ импортируется**. Реестр глобальный, его состояние при переключении проекта не меняется.

## Обработка ошибок

- **`PROJECTS_ROOT_DIR` не существует** → `scan_available_projects` лог `warning`, возвращает `[]`. `bot.py` показывает «Проекты не найдены в папке {root}».
- **Нет прав на чтение `PROJECTS_ROOT_DIR`** → `scan_available_projects` ловит `OSError`, лог `warning`, возвращает `[]`.
- **Целевая папка не существует** → `_validate_target_path` бросает `ProjectSwitchError("Папка не существует: <путь>")`, `_precheck_switch` превращает в `SwitchResult(success=False)`.
- **Целевой путь — файл** → `ProjectSwitchError("Это не папка: <путь>")`.
- **Целевой путь вне `PROJECTS_ROOT_DIR`** → `ProjectSwitchError("Путь вне корневой папки проектов: <путь>")` (защита от path traversal).
- **Нет прав на чтение целевой папки** → `ProjectSwitchError("Нет прав на чтение папки: <путь>")`.
- **Сбой одного `reset_state`** → `_reset_all_state_modules` логирует `error`, продолжает остальные, в конце пробрасывает `RuntimeError`. `_perform_switch` уходит в `finally` (resume_all), `_try_switch_with_rollback` инициирует `_rollback_switch`.
- **Сбой `_rollback_switch`** → лог `error` с `exc_info`, не пробрасывается. `SwitchResult(success=False, error_message=<исходная ошибка>)`.
- **Сбой `save_selected_project`** → лог `error`, переключение всё равно считается успешным. При следующем рестарте бот стартует с `CLAUDE_WORKING_DIR` из `.env`.
- **Битый `LAST_PROJECT_FILE`** → `load_last_selected_project` возвращает `None` (через невалидную валидацию пути).
- **Путь в `LAST_PROJECT_FILE` указывает на удалённый проект** → `_validate_target_path` бросает, `load_last_selected_project` ловит и возвращает `None`.
- **`unread_buffer.save_snapshot` бросил для одной сессии** → `_capture_unread_snapshots` ловит, лог `warning`, продолжает остальные.
- **`backend.read_session_file_snapshot` бросил для одного файла в `_collect_pending_deliveries`** → ловится, лог `warning`, файл пропускается. Дельта для других файлов не теряется.
- **Битый `sessions.json` нового проекта** → `session_manager.reset_state` (через `load_bindings`) логирует `warning`, оставляет `_bindings = {}`, не пробрасывает. `SwitchResult(success=True)`, но привязок нет.
- **Битый `daily_sessions.json` нового проекта** → `daily_session_registry.reset_state` (через `load_registry`) логирует `warning`, оставляет `_registry = {}`. `SwitchResult(success=True)`.

## Контракты с внешними системами

Модуль работает с тремя внешними системами:

### Файловая система macOS — атомарное переименование

**Источник правды:** POSIX `rename(2)`, реализация в Python — `os.replace()`. На macOS APFS `os.replace` гарантирует атомарность: сторонний читатель `LAST_PROJECT_FILE` либо видит старый файл, либо новый — без полу-записанного состояния.

**Алгоритм:** `temp.write_text(path, "utf-8")` → `os.replace(temp, target)`. Применяется в `save_selected_project`. Тест-план проверяет наличие `tmp`-файла промежуточно (через моки), чтобы исключить регрессию.

### Файловая система macOS — TCC и path traversal

**Источник правды:** проверка `realpath(target).startswith(realpath(root) + os.sep)` — стандартный приём защиты от path traversal в Python.

**Особый случай — симлинки.** Если в `PROJECTS_ROOT_DIR` лежит симлинк на папку вне корня (например, `/Users/ivan/Desktop/claude-sandbox/external` → `/tmp/foo`), `_should_include_project` исключает симлинки целиком — пользователь не увидит их в `/projects`. Это консервативная защита: даже валидный симлинк на папку внутри корня будет скрыт. Альтернатива (раскрывать realpath и проверять, что внутри) — отвергнута: усложняет код ради редкого case.

### Файлы сессий Claude и Codex (через бэкенды)

**Источник правды:** `coding_agent_backend_spec.md` → `list_all_session_files_for_project(project_dir)` для pending delivery и других operational flow. Claude хранит файлы в `~/.claude/projects/<sanitized>/<session_id>.jsonl` (sanitization путя — `re.sub(r"[^a-zA-Z0-9]", "-", path)`, проверено в `claude_code_backend_spec.md`). Codex — в `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` глобально, фильтрация по проекту через `payload.cwd` записи `session_meta`.

**`project_manager` сам формат не парсит** — делегирует через `backend.list_all_session_files_for_project` и `backend.read_session_file_snapshot`. Тест-план для контракта формата файлов — на стороне backend-спек, не здесь. Здесь — только тест, что `_collect_pending_deliveries` корректно вызывает оба бэкенда (моки `get_backend`, проверка вызова с правильным `project_dir`).

## Константы

- `_LAST_PROJECT_TEMP_SUFFIX: str = ".tmp"` — суффикс временного файла при атомарной записи `LAST_PROJECT_FILE`. Module-level.
- `_switch_lock: asyncio.Lock = asyncio.Lock()` — module-level блокировка для сериализации `switch_project`.
- `_NEIGHBOR_DIRECTION_NEXT: str = "next"`, `_NEIGHBOR_DIRECTION_PREV: str = "prev"` — допустимые значения параметра `direction` в `resolve_neighbor_project`. Используются в коде вместо литералов.

Все остальные константы — снаружи модуля:

- `config.PROJECTS_ROOT_DIR` (`"/Users/ivan/Desktop/claude-sandbox"` дефолт, переопределяется через env-переменную `PROJECTS_ROOT_DIR`).
- `config.LAST_PROJECT_FILE` (`Path.home() / ".claude-manager-current-project"`).
- `config.WORKING_DIR` (мутируется в `_perform_switch`).
- `config.UNREAD_BUFFER_TTL_HOURS` (`3`) — используется внутри `unread_buffer`, `project_manager` его не читает.

## Тест-план

Файл: `tests/test_project_manager.py`. Юнит-тесты — `pytest`, `asyncio_mode = "auto"`.

Интеграционные тесты — `tests/integration/test_project_switching.py` (см. конец раздела).

### Сканирование (TestScanAvailableProjects, 6 кейсов)

- **test_returns_only_directories** — папки с `.txt` и `.zip` файлами, проверка что только директории попали в результат.
  - Вход: `tmp_root/proj_a/`, `tmp_root/proj_b/`, `tmp_root/file.txt`. `config.PROJECTS_ROOT_DIR = tmp_root`.
  - Ожидаемо: `len(result) == 2`, имена `proj_a`, `proj_b`.
  - Тип: unit.
- **test_filters_hidden_dirs** — папки `.git`, `.cache`, `.venv` исключены.
  - Вход: `tmp_root/.git/`, `tmp_root/.venv/`, `tmp_root/visible/`.
  - Ожидаемо: только `visible`.
  - Тип: unit.
- **test_filters_symlinks** — симлинки на другие папки исключены, даже если они валидны.
  - Вход: `tmp_root/real/`, `tmp_root/link → /tmp/foo` (симлинк).
  - Ожидаемо: только `real`.
  - Тип: unit (security).
- **test_empty_root_returns_empty_list** — пустая корневая папка.
  - Вход: `tmp_root` пуст.
  - Ожидаемо: `result == []`.
  - Тип: edge case.
- **test_nonexistent_root_returns_empty_list** — несуществующий корень.
  - Вход: `config.PROJECTS_ROOT_DIR = "/nonexistent/path"`.
  - Ожидаемо: `result == []`, в логах `warning`.
  - Тип: edge case.
- **test_marks_current_project** — проект, путь которого равен `config.WORKING_DIR`, помечен `is_current=True`.
  - Вход: `tmp_root/active/`, `tmp_root/inactive/`. `config.WORKING_DIR = tmp_root/active`.
  - Ожидаемо: `result[0].is_current=True` для `active`, `False` для `inactive`.
  - Тип: unit.

### Базовое переключение (TestSwitchProject, 6 кейсов)

- **test_switch_between_two_projects_with_one_claude_session** — базовое переключение.
  - Вход: `tmp_root/proj_a/`, `tmp_root/proj_b/`. В A — одна Claude-сессия с raw_count=10. `WORKING_DIR=proj_a`. Вызов `switch_project(proj_b)`.
  - Ожидаемо: `success=True, already_active=False`. `config.WORKING_DIR == proj_b`. В `unread_buffer._snapshots` запись `(uuid, BackendName.CLAUDE) → raw_count=10`. `pause_all` вызвана 1 раз, `resume_all` — 1 раз. `reset_state` вызван у session_manager, daily_session_registry, session_watcher.
  - Тип: unit (с моками).
- **test_switch_with_active_claude_and_codex_sessions** — две сессии разных бэкендов.
  - Вход: в A две активные сессии — `chat_1: ActiveSession("uuid-c", BackendName.CLAUDE)`, `chat_1: ActiveSession("uuid-x", BackendName.CODEX)` (разные chat_id). Watcher вернёт `seen_claude={"uuid-c": 5}`, `seen_codex={"uuid-x": 7}`. Вызов `switch_project(proj_b)`.
  - Ожидаемо: вызовы `unread_buffer.save_snapshot("uuid-c", CLAUDE, 5)` и `save_snapshot("uuid-x", CODEX, 7)` оба сделаны. Каждый snapshot — отдельная запись по composite key.
  - Тип: unit.
- **test_already_active_returns_noop** — переключение на тот же проект.
  - Вход: `WORKING_DIR=proj_a`, вызов `switch_project(proj_a)`.
  - Ожидаемо: `success=True, already_active=True`, `pause_all` не вызывался, `reset_state` не вызывался, `save_snapshot` не вызывался.
  - Тип: unit.
- **test_path_traversal_blocked** — попытка переключиться на `/etc`.
  - Вход: `config.PROJECTS_ROOT_DIR=/Users/ivan/Desktop/claude-sandbox`, `switch_project("/etc")`.
  - Ожидаемо: `success=False, error_message` содержит «Путь вне корневой папки проектов».
  - Тип: error.
- **test_nonexistent_target_path_fails** — путь не существует.
  - Вход: `switch_project("/nonexistent")`.
  - Ожидаемо: `success=False, error_message` содержит «Папка не существует».
  - Тип: error.
- **test_target_is_file_fails** — путь указывает на файл.
  - Вход: `switch_project(tmp_root/file.txt)`.
  - Ожидаемо: `success=False, error_message` содержит «Это не папка».
  - Тип: error.

### Частичные сбои (TestPartialFailures, 5 кейсов)

- **test_pause_codex_watcher_failure_still_calls_resume_for_both** — `pause_all` бросает после паузы Claude.
  - Вход: мок `session_watcher.pause_all` бросает `RuntimeError("codex pause failed")`. Параллельно мокается `resume_all` (счётчик вызовов).
  - Ожидаемо: `switch_project` ловит, возвращает `success=False`. `resume_all` вызван **минимум один раз** (внутри `_perform_switch.finally`). После теста watcher в активном состоянии (mock проверяет это).
  - Тип: error.
- **test_save_snapshot_failure_on_one_session_does_not_block_others** — `unread_buffer.save_snapshot` бросает на первой сессии.
  - Вход: две активные сессии. Мок `save_snapshot` бросает `OSError` на первой и работает на второй.
  - Ожидаемо: переключение `success=True`. `save_snapshot` вызван дважды, второй вызов прошёл успешно (вторая запись присутствует). В логах `warning` про первую сессию.
  - Тип: error.
- **test_reset_state_failure_on_one_module_still_resumes_watcher** — `daily_session_registry.reset_state` бросает.
  - Вход: мок `daily_session_registry.reset_state` бросает `OSError("disk full")`. Остальные reset_state работают.
  - Ожидаемо: `switch_project` возвращает `success=False`. `session_watcher.resume_all` вызван (потому что в `finally`). Откат через `_rollback_switch` сработал, `config.WORKING_DIR == old_path`.
  - Тип: error.
- **test_corrupted_sessions_json_in_new_project_fallback_to_empty** — битый `sessions.json` в новом проекте.
  - Вход: `tmp_root/proj_b/sessions.json` содержит `not a json`. `switch_project(proj_b)`.
  - Ожидаемо: `success=True` (по контракту session_manager — битый файл = пустой state, не ошибка). `session_manager.get_all_bindings() == {}` после переключения.
  - Тип: edge case.
- **test_rollback_failure_logged_not_propagated** — и переключение, и откат бросают.
  - Вход: `_perform_switch` бросает `RuntimeError("first error")`. Внутри `_rollback_switch` мокаем `_reset_all_state_modules` бросать `RuntimeError("rollback error")`.
  - Ожидаемо: `switch_project` возвращает `success=False, error_message` содержит «first error». В логах `error` с `exc_info` про rollback. Исключение наружу не вышло.
  - Тип: error.

### Доставка непрочитанных (TestPendingDeliveries, 5 кейсов)

- **test_pending_messages_delivered_on_return_within_ttl** — возврат через 5 минут.
  - Вход: уйти из A в B (за 5 мин до теста, через monkeypatch `_now()`). В файле сессии A появилось 2 новых assistant-сообщения. Вернуться в A.
  - Ожидаемо: `pending_messages_count == 2`. Каждый `PendingDeliveryItem` содержит `session_id`, `backend=BackendName.CLAUDE`, `text`. Последний — `is_final=True` (turn завершён в файле).
  - Тип: unit.
- **test_pending_not_delivered_after_ttl_expired** — возврат через 4 часа.
  - Вход: уйти из A в B за 4 часа до теста. В A 5 новых сообщений. Вернуться в A.
  - Ожидаемо: `pending_messages_count == 0`. `unread_buffer.restore_snapshot` вернул `None` для просроченной записи.
  - Тип: edge case.
- **test_no_pending_when_session_was_not_tracked** — новая сессия в новом проекте без снапшота.
  - Вход: `unread_buffer._snapshots` пуст для всех сессий нового проекта. Вернуться.
  - Ожидаемо: `pending_messages_count == 0`. `restore_snapshot` возвращает `None`, дельта не вычисляется.
  - Тип: edge case.
- **test_clear_expired_called_after_collect** — `clear_expired` вызывается в конце `_collect_pending_deliveries`.
  - Вход: моки `restore_snapshot` для одной сессии возвращают `None` (TTL истёк). Параллельно мок `clear_expired` (счётчик вызовов).
  - Ожидаемо: `clear_expired` вызван 1 раз после цикла.
  - Тип: unit.
- **test_only_assistant_messages_in_pending** — фильтрация по `role == "assistant"`.
  - Вход: дельта из 4 сообщений: user, assistant, system, assistant.
  - Ожидаемо: `pending_messages_count == 2` (только два assistant). user и system отфильтрованы.
  - Тип: unit.

### Глобальные модули (TestGlobalRegistries, 2 кейса)

- **test_current_backend_preserved_across_switch** — `current_backend_registry` не сбрасывается.
  - Вход: до переключения `current_backend_registry.set_current(BackendName.CODEX)`. `switch_project(proj_b)`. Прочитать `current_backend_registry.get_current()`.
  - Ожидаемо: `BackendName.CODEX` (без изменения). `_reset_all_state_modules` НЕ вызывал ни одной функции `current_backend_registry`.
  - Тип: unit (архитектурный гарант).
- **test_processes_not_stopped_on_switch** — `process_manager._processes` не модифицируется.
  - Вход: запустить mock-процесс в `process_manager._processes[("uuid", BackendName.CLAUDE)]`. Переключиться. Проверить, что процесс на месте.
  - Ожидаемо: `process_manager._processes[("uuid", BackendName.CLAUDE)]` существует, ссылается на тот же объект.
  - Тип: unit (архитектурный гарант).

### Циклический обход через resolve_neighbor_project (TestResolveNeighbor, 4 кейса)

- **test_resolve_next_cycles_to_first** — последний → первый.
  - Вход: список `[a, b, c]`, `WORKING_DIR=c`. Вызов `resolve_neighbor_project("next")`.
  - Ожидаемо: `result.name == "a"`.
  - Тип: unit.
- **test_resolve_prev_cycles_to_last** — первый → последний.
  - Вход: список `[a, b, c]`, `WORKING_DIR=a`. Вызов `resolve_neighbor_project("prev")`.
  - Ожидаемо: `result.name == "c"`.
  - Тип: unit.
- **test_resolve_neighbor_returns_none_for_single_project** — список из одного.
  - Вход: список `[a]`, `WORKING_DIR=a`. Вызов `resolve_neighbor_project("next")`.
  - Ожидаемо: `None`.
  - Тип: edge case.
- **test_resolve_neighbor_when_current_not_in_list** — `WORKING_DIR` указывает вне корня.
  - Вход: список `[a, b, c]`, `WORKING_DIR=/tmp/external`. Вызов `resolve_neighbor_project("next")`.
  - Ожидаемо: `result.name == "a"` (первый из списка).
  - Тип: edge case.

### Персистентность (TestPersistence, 4 кейса)

- **test_save_and_load_round_trip** — сохранили путь, перезагрузили.
  - Вход: `save_selected_project("/Users/ivan/Desktop/claude-sandbox/proj_a")`, затем `load_last_selected_project()`.
  - Ожидаемо: возвращает тот же путь.
  - Тип: unit.
- **test_load_returns_none_when_no_file** — файла нет.
  - Вход: `LAST_PROJECT_FILE` отсутствует.
  - Ожидаемо: `None`.
  - Тип: edge case.
- **test_load_returns_none_when_path_invalid** — путь в файле указывает на удалённый проект.
  - Вход: `LAST_PROJECT_FILE` содержит `/Users/ivan/Desktop/claude-sandbox/deleted_proj` (не существует).
  - Ожидаемо: `None`, лог `warning`.
  - Тип: error.
- **test_save_uses_atomic_rename** — паттерн `tmp + os.replace`.
  - Вход: моки `Path.write_text` и `os.replace`. `save_selected_project("/path")`.
  - Ожидаемо: `write_text` вызван на `<file>.tmp`, затем `os.replace(<tmp>, <file>)`. Прямой записи в `<file>` нет.
  - Тип: unit.

### Интеграционные тесты (`tests/integration/test_project_switching.py`)

- **test_full_round_trip_with_real_files** — полный цикл с реальной файловой системой.
  - Вход: создать `tmp_root/{proj_a, proj_b}/`. В каждом `sessions.json` и `daily_sessions.json` (валидные). Переключиться A → B → A.
  - Ожидаемо: после возврата в A `session_manager.get_all_bindings()` содержит ровно те привязки, что были до ухода. `daily_session_registry` загружен заново.
  - Тип: integration.
- **test_processes_continue_running_during_switch** — фоновые процессы не убиваются.
  - Вход: запустить fake-процесс через `asyncio.subprocess.create_subprocess_exec("/usr/bin/yes")`, добавить в `process_manager._processes`. Переключиться A → B. Проверить `process.returncode is None` (живой). Затем `process.kill()`.
  - Ожидаемо: процесс жив на момент проверки.
  - Тип: integration.
- **test_concurrent_switches_serialized** — два параллельных `switch_project` через `asyncio.gather`.
  - Вход: `await asyncio.gather(switch_project(proj_b), switch_project(proj_c))`.
  - Ожидаемо: оба вернули `SwitchResult`. Финальное `WORKING_DIR` — один из двух (последний по семантике Lock). State не перемешан (либо все привязки B, либо все привязки C).
  - Тип: integration (concurrency).
- **test_restart_recovery_with_last_project_file** — рестарт бота.
  - Вход: `save_selected_project("/path/proj_a")`, симуляция рестарта (новый процесс — фактически вызов `load_last_selected_project()` после reset module-level state).
  - Ожидаемо: `load_last_selected_project()` вернул `/path/proj_a`. `main.py` использует это для инициализации `config.WORKING_DIR`.
  - Тип: integration.

### Сводка тест-плана

- **Юнит-тесты:** 32 кейса (6 + 6 + 5 + 5 + 2 + 4 + 4 = 32)
- **Интеграционные:** 4 кейса
- **Итого:** 36 тест-кейсов, минимум 12 из требований пользователя покрыты:
  1. ✅ Базовое переключение Claude-сессии — `test_switch_between_two_projects_with_one_claude_session`.
  2. ✅ Активные Claude и Codex одновременно — `test_switch_with_active_claude_and_codex_sessions`.
  3. ✅ Сбой pause Codex-watcher → resume для обоих — `test_pause_codex_watcher_failure_still_calls_resume_for_both`.
  4. ✅ Сбой save_snapshot на одной → остальные сохранены — `test_save_snapshot_failure_on_one_session_does_not_block_others`.
  5. ✅ Сбой reset_state одного → watcher resume — `test_reset_state_failure_on_one_module_still_resumes_watcher`.
  6. ✅ Битый sessions.json → fallback — `test_corrupted_sessions_json_in_new_project_fallback_to_empty`.
  7. ✅ TTL истёк → дельта не доставляется — `test_pending_not_delivered_after_ttl_expired`.
  8. ✅ current_backend_registry сохраняется — `test_current_backend_preserved_across_switch`.
  9. ✅ Возврат через 5 минут → дельта — `test_pending_messages_delivered_on_return_within_ttl`.
  10. ✅ Возврат через 4 часа → не доставлена — `test_pending_not_delivered_after_ttl_expired`.
  11. ✅ Внутренний циклический обход работает — `test_resolve_next_cycles_to_first`, `test_resolve_prev_cycles_to_last`.
  12. ✅ Восстановление после рестарта — `test_restart_recovery_with_last_project_file`.

## История ревизий спеки

- **06-05-2026** — полная переписка под backend-абстракцию. Старая Claude-only версия (10-04-2026) перемещается в `dev/docs/specs/realised/project_manager_spec.md` после реализации backend-пакета. Основания для переписки:
  - Новый `unread_buffer` API (`save_snapshot(session_id, backend, raw_record_count)`, `restore_snapshot(session_id, backend)`).
  - `session_manager` хранит `ActiveSession(session_id, backend)`, `daily_session_registry` хранит `DailySessionEntry(session_id, backend)`.
  - `session_watcher` — две независимые инстанции (Claude и Codex), `pause_all` / `resume_all` синхронные и работают над обеими.
  - `current_backend_registry` — глобальный, **НЕ сбрасывается** при переключении проекта.
  - Доставка непрочитанных — backend-aware дельта-чтение через `backend.read_session_file_snapshot`, без помощи `unread_buffer.get_pending_messages` (отброшен).
  - Внутренний циклический обход — поддерживается через `resolve_neighbor_project(direction)`; пользовательские команды `/next` и `/prev` не входят в текущий объём.
- **10-04-2026** (legacy, перемещается в `realised/`) — первая Claude-only версия.
