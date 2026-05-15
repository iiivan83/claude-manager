# Спецификация модуля: unread_buffer

Дата: 06-05-2026
Слой: 0/1 (зависит только от `config` для TTL и от `coding_agent_backend` за типами `BackendName` и `SessionUnreadState`. Не зависит ни от `session_reader`, ни от каких бы то ни было других модулей бизнес-логики или инфраструктуры)
Файл: `src/claude_manager/unread_buffer.py`

## Связанные спеки

- `coding_agent_backend_spec.md` — определяет тип `BackendName` (Enum со значениями `"claude"` и `"codex"`), который входит в составной ключ записей, и DTO `SessionUnreadState`.
- `project_manager_spec.md` — потребитель: при переключении проекта вызывает `save_snapshot` для всех сессий обоих backend'ов, которые уже отслеживает watcher; при возврате в проект — `restore_snapshot` для каждой сессии и сам читает JSONL для вычисления дельты сообщений.
- `session_watcher_spec.md` — потребитель (две инстанции на каждый backend): источник `raw_record_count` и `last_delivered_idx` для `save_snapshot`, место, где живёт собственное состояние watcher-а после возврата в проект.

## Расхождение с предыдущим API

Ранее `unread_buffer` был «толстым» модулем: ключ — `project_path: str`, значение — `ProjectSnapshot(seen_counts: dict[str, int], switch_time: datetime)`, плюс асинхронная функция `get_pending_messages(project_path)`, которая читала JSONL через `session_reader` и возвращала уже готовый `list[PendingMessage]`. После backend-абстракции эта роль уходит — потому что:

- session_id одинаковой формы (UUID) может встретиться у двух разных CLI (Claude и Codex). Ключ только по `session_id` неоднозначен.
- Чтение JSONL — это знание формата конкретного бэкенда (`backend.read_session_file_snapshot(file_path)`). Модулю буфера незачем знать про два разных формата файлов.
- Ключ по `project_path` теряет смысл: запись о сессии Codex живёт в общей директории `~/.codex/sessions/YYYY/MM/DD/` и не привязана к проекту через путь файла, а только через поле `cwd` внутри файла.

Поэтому новый `unread_buffer` — тонкое in-memory хранилище cursor-состояния (`SessionUnreadState`) с ключом `(session_id: str, backend: BackendName)`, без файлового I/O и без знания о форме сообщений Claude/Codex. Состояние хранит и сырой счётчик строк, и индекс последнего доставленного сообщения, чтобы потребители не вычисляли границу сообщений из служебных JSONL-строк. Старый project-path API сохраняется только как compatibility no-op слой: он не создаёт backend-aware snapshot-ы и не читает сообщения.

## Назначение

Тонкое потокобезопасное in-memory хранилище cursor-состояния непрочитанных сообщений по сессиям. Решает задачу: при переключении проекта watcher перестаёт следить за сессиями старого проекта, но процессы Claude/Codex продолжают писать в свои JSONL-файлы. Чтобы при возврате доставить накопившуюся дельту, нужно зафиксировать «вот сколько строк JSONL было видно и какое последнее сообщение уже дошло до пользователя». Модуль и хранит эти отметки.

Сами сообщения и их извлечение из JSONL — НЕ ответственность этого модуля. Он хранит `SessionUnreadState(raw_record_count, last_delivered_idx)` для каждой пары `(session_id, backend)`, плюс время сохранения (для TTL). Решение о том, как прочитать новые сообщения, принимает потребитель (`project_manager` через `coding_agent_backend.read_session_file_snapshot`).

★ Insight ─────────────────────────────────────
Здесь применяется принцип «тонкого ядра»: один маленький модуль с минимальной зоной ответственности легче тестировать, переносить и менять. Расширенная логика (чтение JSONL, фильтрация служебных сообщений, доставка) уезжает к потребителям — и каждый потребитель сам выбирает, как именно обработать дельту.
`─────────────────────────────────────────────────`

## Обслуживаемые сценарии

- **CJM-11 (Переключение между проектами), шаг 5 «Доставка непрочитанных сообщений»** — модуль хранит cursor-состояние для каждой отслеживаемой watcher-ом сессии обоих backend'ов на момент ухода из проекта. При возврате потребитель использует `restore_snapshot` для вычисления дельты «какие сообщения появились за время отсутствия». TTL 3 часа защищает от лавины старых сообщений (требование BRD).

CJM-11 явно описывает (BRD строки 507–514):

- При возврате в проект бот сравнивает текущие JSONL-файлы сессий с ранее сохранённым cursor-состоянием → этот snapshot хранит данный модуль.
- Сообщения старше 3 часов не доставляются → TTL модуля.
- После успешной доставки всех непрочитанных сообщений для пары `(session_id, backend)` потребитель вызывает `clear_snapshot_for_session_backend_pair(session_id, backend)`. Ждать TTL нельзя: иначе повторный возврат в проект до истечения TTL может повторно доставить ту же дельту.

## Публичный API

### `save_snapshot(session_id: str, backend: BackendName, raw_record_count: int, last_delivered_idx: int) -> None`

Сохраняет (или перезаписывает) cursor-состояние для пары `(session_id, backend)` с текущим временем. Используется потребителем при переключении проекта — фиксирует точку «здесь пользователь ушёл из проекта; сколько строк JSONL было видно и какое последнее сообщение уже доставлено».

**Аргументы:**

- `session_id` (`str`) — UUID сессии. Формат идентичен у Claude и Codex (UUID v4-подобная строка), но один и тот же UUID может встретиться у обоих CLI — поэтому ключ всегда составной.
- `backend` (`BackendName`) — `BackendName.CLAUDE` или `BackendName.CODEX`. Импортируется из `coding_agent_backend`.
- `raw_record_count` (`int`) — общее количество строк JSONL в файле сессии на момент ухода из проекта. Источник — поле `SessionFileSnapshot.raw_record_count`, возвращаемое `backend.read_session_file_snapshot(file_path)`. Считаются ВСЕ строки (включая `system`, `result`, любые служебные); это нужно потому, что счётчик должен быть стабилен для дельта-чтения, а не зависеть от фильтрации.
- `last_delivered_idx` (`int`) — индекс последнего сообщения из `SessionFileSnapshot.messages`, которое watcher уже доставил или сознательно пропустил. `-1` валиден и означает «ещё не доставлено ни одного сообщения».

**Возвращает:** `None`

**Исключения:** не выбрасывает (in-memory операция без валидации; невалидные значения `raw_record_count` вроде `-1` или некорректный `last_delivered_idx` будут сохранены как есть, но это считается ошибкой вызывающего и проверяется им).

**Постусловия:**

- В `_snapshots[(session_id, backend)]` хранится свежий `SessionUnreadSnapshot(state=SessionUnreadState(raw_record_count=<входное число>, last_delivered_idx=<входное число>), saved_at=datetime.now())`.
- Если запись для той же пары уже существовала — она перезаписывается без предупреждения.

---

### `restore_snapshot(session_id: str, backend: BackendName) -> SessionUnreadState | None`

Возвращает ранее сохранённое cursor-состояние для пары или `None`, если записи нет либо она просрочена по TTL. При просрочке lazily удаляет запись (без отдельного вызова `clear_expired`).

**Аргументы:**

- `session_id` (`str`) — UUID сессии.
- `backend` (`BackendName`) — backend сессии. Пара `(session_id, BackendName.CLAUDE)` и `(session_id, BackendName.CODEX)` — РАЗНЫЕ ключи; восстановление по одному backend не возвращает состояние другого.

**Возвращает:** `SessionUnreadState | None`

- `SessionUnreadState` — сохранённые `raw_record_count` и `last_delivered_idx`, если запись существует и не просрочена.
- `None` — если записи нет ИЛИ запись просрочена (старше `config.UNREAD_BUFFER_TTL_HOURS`).

**Исключения:** не выбрасывает.

**Побочные эффекты:**

- Если запись просрочена — она удаляется из `_snapshots` (lazy cleanup).
- Записи, которые свежее TTL, не трогаются и могут быть восстановлены повторно (вторая попытка вернёт то же состояние до явной чистки или перезаписи).

---

### `clear_expired() -> None`

Массово удаляет все записи, время сохранения которых старше `config.UNREAD_BUFFER_TTL_HOURS`. Используется как periodic cleanup — например, при каждом успешном переключении проекта потребитель вызывает эту функцию, чтобы старые записи не оставались в памяти неограниченно (если пользователь больше никогда не вернётся в тот проект и `restore_snapshot` для них не позовётся).

**Аргументы:** нет.

**Возвращает:** `None`

**Исключения:** не выбрасывает.

**Побочные эффекты:**

- Удаляет записи из `_snapshots`, для которых `_is_expired(record)` истинно.
- Если удалена хотя бы одна запись — пишет в лог `info`-сообщение «удалено N просроченных снапшотов из unread_buffer».

---

### `clear_snapshot_for_session_backend_pair(session_id: str, backend: BackendName) -> None`

Удаляет snapshot для конкретной пары `(session_id, backend)` после успешной доставки накопленных pending-сообщений. Идемпотентна: если записи нет, ничего не делает и не выбрасывает.

**Аргументы:**

- `session_id` (`str`) — UUID сессии.
- `backend` (`BackendName`) — backend сессии.

**Возвращает:** `None`

**Исключения:** не выбрасывает.

**Побочные эффекты:**

- Удаляет `_snapshots[(session_id, backend)]`, если запись существует.
- Логирует `debug`, если запись была удалена; отсутствие записи не логирует.

## Внутренние функции

### `_now() -> datetime`

Один источник правды для текущего времени. Вынесен в отдельную функцию исключительно для удобства подмены в тестах через monkey-patch (стандартная техника изоляции от часов системы).

**Возвращает:** `datetime` — текущее локальное время через `datetime.now()`.

### `_is_expired(record: SessionUnreadSnapshot) -> bool`

Проверяет, что разница между `_now()` и `record.saved_at` строго больше TTL в часах из `config.UNREAD_BUFFER_TTL_HOURS`.

**Возвращает:** `bool` — `True` если запись просрочена, иначе `False`.

**Граничный случай:** ровно на границе TTL запись считается ещё валидной — сравнение строгое (`>`, не `>=`).

## DTO модуля

### `SessionUnreadSnapshot` (frozen dataclass)

Внутренняя структура одной записи буфера. Потребители получают через `restore_snapshot` только поле `state`; время `saved_at` остаётся внутренним для проверки TTL.

```python
@dataclass(frozen=True)
class SessionUnreadSnapshot:
    state: SessionUnreadState
    saved_at: datetime
```

`frozen=True` — гарантия, что после создания запись не модифицируется случайно.

## Алгоритм работы

### `save_snapshot`

1. Создать `state = SessionUnreadState(raw_record_count=raw_record_count, last_delivered_idx=last_delivered_idx)`.
2. Создать `record = SessionUnreadSnapshot(state=state, saved_at=_now())`.
3. Записать `_snapshots[(session_id, backend)] = record` (перезаписывая существующую, если была).
4. Залогировать на уровне `debug`: «снапшот сохранён для сессии {session_id} ({backend.value}): raw_count={raw_record_count}, last_delivered_idx={last_delivered_idx}». Уровень `debug`, потому что эта операция выполняется при каждом переключении проекта для каждой отслеживаемой сессии — на `info` логи зашумятся.

### `restore_snapshot`

1. Получить `record = _snapshots.get((session_id, backend))`.
2. Если `record is None` — вернуть `None`. Не логировать (отсутствие записи — нормальная ситуация).
3. Если `_is_expired(record)`:
   - Удалить: `del _snapshots[(session_id, backend)]`.
   - Залогировать `info`: «снапшот для сессии {session_id} ({backend.value}) просрочен (TTL {hours} ч), удалён».
   - Вернуть `None`.
4. Иначе — вернуть `record.state`.

### `clear_expired`

1. Собрать `expired_keys = [key for key, record in _snapshots.items() if _is_expired(record)]`.
2. Для каждого ключа из `expired_keys` — `del _snapshots[key]`.
3. Если `expired_keys` непустой — залогировать `info`: «удалено {len(expired_keys)} просроченных снапшотов из unread_buffer».
4. Если `expired_keys` пустой — ничего не логировать.

### `clear_snapshot_for_session_backend_pair`

1. Выполнить `removed = _snapshots.pop((session_id, backend), None)`.
2. Если `removed is not None` — залогировать `debug`: «снапшот очищен после доставки для сессии {session_id} ({backend.value})».

### Compatibility wrappers старого project-path API

Старые функции `get_pending_messages(project_path)`, `clear_snapshot(project_path)` и `has_pending(project_path)` остаются только для обратной совместимости старых callers/tests. Они не участвуют в backend-aware доставке и не читают JSONL.

- `async get_pending_messages(project_path: str) -> list[PendingMessage]` — возвращает пустой список.
- `clear_snapshot(project_path: str) -> None` — no-op, ничего не удаляет из `_snapshots`.
- `has_pending(project_path: str) -> bool` — всегда возвращает `False`.

Новый код обязан использовать `save_snapshot(session_id, backend, raw_record_count, last_delivered_idx)`, `restore_snapshot(session_id, backend)` и `clear_snapshot_for_session_backend_pair(session_id, backend)`.
3. Если записи не было — ничего не логировать.

## Зависимости

- **`config`** — `config.UNREAD_BUFFER_TTL_HOURS` (значение `3`, единица — часы). Используется только в `_is_expired`. Зачем: ровно одно место для TTL, можно поменять через .env без правки модуля.
- **`coding_agent_backend`** — тип `BackendName` (Enum) и DTO `SessionUnreadState`. `BackendName` используется как часть составного ключа `_snapshots`; `SessionUnreadState` — как возвращаемый cursor.

Зависимостей от `session_reader`, `session_watcher`, `session_manager`, `daily_session_registry` — НЕТ. Это сознательное проектное решение: тонкий модуль не должен знать о JSONL и сообщениях.

## Обработка ошибок

- **Запись отсутствует** — `restore_snapshot` возвращает `None`. Потребитель трактует это как «нет смысла вычислять дельту, эта сессия не отслеживалась через переключение проекта».
- **Запись просрочена** — `restore_snapshot` lazily удаляет запись и возвращает `None`. Потребитель видит то же самое, что и для отсутствующей записи. Логирование `info` оставляет след для диагностики.
- **Очистка отсутствующей записи после доставки** — `clear_snapshot_for_session_backend_pair` ничего не делает. Это важно для idempotency: если потребитель повторно вызвал clear после partial retry, модуль не должен падать.
- **Невалидный backend** — статически невозможен (`BackendName` — Enum, мимо него ничего не пройдёт). Дополнительная валидация в рантайме не нужна.
- **Невалидный `raw_record_count` или `last_delivered_idx`** (например, отрицательный `raw_record_count`) — модуль не проверяет, сохраняет как есть. `last_delivered_idx=-1` валиден, остальные невалидные значения считаются ошибкой вызывающего, контрактно не поддерживаются. Тестом не покрывается, чтобы не закреплять «принимаем мусор» как часть контракта.

★ Insight ─────────────────────────────────────
Этот модуль — пример «boring infrastructure»: он не делает героических вещей, не валидирует входы, не логирует на info без надобности. Чем меньше у тонкого модуля поверхностей контакта, тем проще все, кто его использует. Любой `try/except` или защитная проверка здесь сделали бы модуль шумным, не добавив надёжности.
`─────────────────────────────────────────────────`

## Контракты с внешними системами

Модуль НЕ работает с внешними системами:

- **Нет файлового I/O.** `_snapshots` — обычный `dict` на уровне модуля. Никаких чтений с диска, никаких записей.
- **Нет subprocess.** Ничего не запускает.
- **Нет сетевых вызовов.** Не обращается ни к Telegram API, ни к Claude/Codex CLI.
- **Нет JSON-сериализации.** Записи живут только в памяти; при перезапуске бота буфер очищается до пустого состояния, и это намеренно (см. ниже «Поведение при рестарте бота»).

→ Раздел «контракты с внешними системами» к этому модулю **не применим**.

## Поведение при рестарте бота

Буфер живёт только в памяти процесса. После рестарта (например, через `restart-claude-manager.sh` или launchd) `_snapshots` пуст. Это намеренно — после рестарта watcher перестраивает свой `seen_counts` с нуля по всем сессиям проекта (через `session_watcher` с пустым стартовым state, см. CJM-07), и уже находит все накопленные сообщения как новые. Если бы буфер был персистентным, потребовалась бы синхронизация старого буфера с новым watcher state — лишняя сложность ради сценария, который и без того отрабатывается через стандартное восстановление в режим `/all` после рестарта.

## Контекст использования (для потребителей)

Эта секция — не часть API, а описание того, как `project_manager` будет вызывать модуль. Помогает реализатору понять, для какого паттерна спроектирован API.

### При уходе из проекта (внутри `project_manager._perform_switch`)

```text
для каждой пары (session_id, unread_state) в session_watcher.get_seen_counts_snapshot(backend):
    unread_buffer.save_snapshot(
        session_id=session_id,
        backend=backend,
        raw_record_count=unread_state.raw_record_count,
        last_delivered_idx=unread_state.last_delivered_idx,
    )
```

### При возврате в проект (внутри `project_manager._collect_pending_messages`)

```text
unread_buffer.clear_expired()  # гигиена: очистить просроченные

для каждой сессии проекта (через backend.list_all_session_files_for_project):
    old_state = unread_buffer.restore_snapshot(session_id, backend)
    if old_state is None:
        continue  # сессия не отслеживалась или просрочена

    snapshot = await backend.read_session_file_snapshot(session.file_path)
    if snapshot.raw_record_count <= old_state.raw_record_count and len(snapshot.messages) <= old_state.last_delivered_idx + 1:
        continue  # ничего нового

    new_messages = snapshot.messages[old_state.last_delivered_idx + 1:]
    доставить пользователю
```

Смещение по `messages` берётся из `old_state.last_delivered_idx`. Потребитель не должен вычислять индекс сообщения из `raw_record_count`: raw-счётчик включает служебные и невалидные строки.

## Константы

- `_snapshots: dict[tuple[str, BackendName], SessionUnreadSnapshot]` — внутреннее хранилище записей на уровне модуля. Ключ — пара `(session_id: str, backend: BackendName)`, значение — `SessionUnreadSnapshot(state=SessionUnreadState(...), saved_at=...)`. В тестах сбрасывается через autouse-фикстуру `_snapshots.clear()`.

TTL не вынесен в константу модуля — берётся из `config.UNREAD_BUFFER_TTL_HOURS` каждый раз. Это позволяет менять TTL через `.env` без перезаписи модуля.

## Тест-план

Все тесты — синхронные юнит-тесты (модуль не использует async). Тип `unit`, если не указано иное.

### Юнит-тесты счастливого пути

- **test_save_and_restore_for_claude_session** — сохранить Claude snapshot с `raw_record_count=42`, `last_delivered_idx=5`, затем восстановить.
  - Вход: `save_snapshot("aaa-1", BackendName.CLAUDE, raw_record_count=42, last_delivered_idx=5)`, затем `restore_snapshot("aaa-1", BackendName.CLAUDE)`.
  - Ожидаемо: `SessionUnreadState(raw_record_count=42, last_delivered_idx=5)`.

- **test_save_and_restore_for_codex_session** — то же для Codex.
  - Вход: `save_snapshot("bbb-2", BackendName.CODEX, raw_record_count=17, last_delivered_idx=3)`, затем `restore_snapshot("bbb-2", BackendName.CODEX)`.
  - Ожидаемо: `SessionUnreadState(raw_record_count=17, last_delivered_idx=3)`.

- **test_same_session_id_different_backend_independent** — критический тест составного ключа. `save_snapshot("uuid-shared", BackendName.CLAUDE, 100, 4)` и `save_snapshot("uuid-shared", BackendName.CODEX, 200, 8)`. Записи независимы.
  - Вход: две `save_snapshot` с одним session_id, разными backend.
  - Ожидаемо: `restore` для каждого возвращает свой `raw_record_count` и `last_delivered_idx`.

- **test_save_overwrites_existing_record_for_same_pair** — `save_snapshot("aaa", CLAUDE, 5, 1)`, затем `save_snapshot("aaa", CLAUDE, 50, 7)`. Перезапись без слияния.
  - Вход: два `save_snapshot` для одной пары.
  - Ожидаемо: `restore` возвращает `SessionUnreadState(raw_record_count=50, last_delivered_idx=7)`.

- **test_restore_returns_none_for_unknown_pair** — без предварительного `save_snapshot` `restore_snapshot("never-saved", BackendName.CLAUDE)` возвращает `None`.
  - Вход: `restore_snapshot` без save.
  - Ожидаемо: `None`.

- **test_restore_does_not_delete_fresh_record_after_call** — `save_snapshot("x", CLAUDE, 7, 2)`, два последовательных `restore_snapshot("x", CLAUDE)` — оба возвращают одно состояние. `restore` не разрушает запись (это не pop).
  - Вход: один save, два последовательных restore.
  - Ожидаемо: оба restore возвращают `SessionUnreadState(raw_record_count=7, last_delivered_idx=2)`.

### Граничные случаи (TTL и edge cases)

- **test_restore_returns_none_for_expired_record_and_lazily_deletes** — вручную вставить `_snapshots[("old", BackendName.CLAUDE)] = SessionUnreadSnapshot(state=SessionUnreadState(raw_record_count=99, last_delivered_idx=4), saved_at=datetime.now() - timedelta(hours=4))`. `restore_snapshot("old", BackendName.CLAUDE)` возвращает `None` И запись удалена из `_snapshots`.
  - Вход: запись возрастом 4 ч (TTL=3 ч).
  - Ожидаемо: `None`, ключ удалён.
  - Тип: edge case.

- **test_fresh_record_within_ttl_is_returned** — запись возрастом 1 час → `restore` возвращает состояние. Запись не удалена.
  - Вход: запись возрастом 1 ч.
  - Ожидаемо: состояние возвращено, ключ остался в `_snapshots`.
  - Тип: edge case.

- **test_record_at_exact_ttl_boundary_is_still_valid** — запись с `saved_at = _now() - timedelta(hours=3) + timedelta(seconds=1)` (на 1 секунду младше границы) → restore возвращает состояние. Сравнение TTL строгое.
  - Вход: запись на границе TTL.
  - Ожидаемо: состояние возвращено.
  - Тип: edge case.

- **test_record_just_past_ttl_boundary_is_expired** — запись с `saved_at = _now() - timedelta(hours=3) - timedelta(seconds=1)` (на 1 секунду старше границы) → restore возвращает `None`, запись удалена.
  - Вход: запись на 1 секунду за границей TTL.
  - Ожидаемо: `None`, ключ удалён.
  - Тип: edge case.

- **test_empty_cursor_values_are_valid** — `save_snapshot("x", CLAUDE, raw_record_count=0, last_delivered_idx=-1)` → restore возвращает состояние, а не `None`. Нулевой счётчик и `last_delivered_idx=-1` валидны для новой сессии.
  - Вход: `raw_record_count=0`, `last_delivered_idx=-1`.
  - Ожидаемо: restore возвращает `SessionUnreadState(raw_record_count=0, last_delivered_idx=-1)`.
  - Тип: edge case.

- **test_session_unread_snapshot_is_frozen** — попытка `record.state = SessionUnreadState(...)` на созданном `SessionUnreadSnapshot` должна выбросить `dataclasses.FrozenInstanceError`. Защита от случайной мутации.
  - Вход: попытка присваивания атрибута созданному dataclass.
  - Ожидаемо: `FrozenInstanceError`.
  - Тип: edge case.

### Тесты `clear_expired`

- **test_clear_expired_removes_only_expired_records** — заполнить `_snapshots` двумя записями: одна возрастом 4 ч (`("old", CLAUDE)`, state raw=11 idx=1), вторая возрастом 30 минут (`("fresh", CODEX)`, state raw=22 idx=2). `clear_expired()` — после: `("old", CLAUDE)` отсутствует, `("fresh", CODEX)` сохранена.
  - Вход: смешанные по возрасту записи.
  - Ожидаемо: удалены только просроченные.

- **test_clear_expired_noop_when_all_fresh** — все записи свежие → после `clear_expired()` ничего не удалено, `len(_snapshots)` сохранён.
  - Вход: все записи в пределах TTL.
  - Ожидаемо: словарь не изменился, лог не пишется.

- **test_clear_expired_on_empty_buffer_does_not_fail** — `clear_expired()` на пустом `_snapshots` не выбрасывает исключений и не пишет ничего в лог.
  - Вход: пустой `_snapshots`.
  - Ожидаемо: без ошибок, без лога.

- **test_clear_expired_logs_info_when_records_removed** — после `clear_expired()`, удалившего ≥1 запись, в логе `info`-сообщение содержит число удалённых. Проверяется через `caplog`.

### Тесты `clear_snapshot_for_session_backend_pair`

- **test_clear_snapshot_for_session_backend_pair_removes_fresh_record_after_successful_delivery** — сохранить свежий snapshot, вызвать `clear_snapshot_for_session_backend_pair("uuid", BackendName.CLAUDE)`, затем `restore_snapshot("uuid", BackendName.CLAUDE)` возвращает `None`.

- **test_clear_snapshot_for_session_backend_pair_keeps_other_backend_same_session_id** — сохранить два snapshot с одинаковым `session_id`, но разными backend. Очистить Claude-пару. Ожидаемо: Claude-запись удалена, Codex-запись доступна.

- **test_clear_snapshot_for_session_backend_pair_noop_for_missing_record** — вызвать clear для отсутствующей пары. Исключений нет, существующие записи других пар не меняются.
  - Вход: одна просроченная запись.
  - Ожидаемо: `caplog.records` содержит запись уровня `INFO` с подстрокой «удалено 1».

### Тесты ошибок

Раздел практически пуст — модуль контрактно не выбрасывает исключений (только `FrozenInstanceError` для DTO, покрыт выше). Это сознательное решение: тонкое in-memory хранилище без I/O, в нём негде ломаться.

- **test_save_snapshot_does_not_raise_on_negative_raw_count** — вызвать `save_snapshot("x", CLAUDE, raw_record_count=-5, last_delivered_idx=1)` (контрактно ошибка вызывающего, но модуль не валидирует). Должно отработать без исключений; restore вернёт state с `raw_record_count=-5`. Тест фиксирует контракт «модуль ничего не валидирует, валидация — на стороне вызывающего».
  - Вход: отрицательный `raw_record_count`.
  - Ожидаемо: сохранилось как есть, `restore` возвращает `SessionUnreadState(raw_record_count=-5, last_delivered_idx=1)`.
  - Тип: error (документирует, что модуль не защищается).

### Сводка тест-плана

- **Unit (счастливый путь):** 6
- **Edge case (TTL и frozen):** 6
- **`clear_expired`:** 4
- **`clear_snapshot_for_session_backend_pair`:** 3
- **Error/contract:** 1

**Итого: 17 тест-кейсов** (минимум по требованию пользователя — 8). Все синхронные.

Параметризация по `BackendName.CLAUDE` и `BackendName.CODEX` — где это даёт ценность (round-trip, составной ключ). В `clear_expired` параметризация не требуется — ключ внутри dict обрабатывается одинаково для обоих backend'ов.

## Чеклист проверки покрытия CJM-11

Проверка соответствия модуля BRD CJM-11 (шаги 5.1–5.4 «Доставка непрочитанных сообщений», строки 510–514):

- [x] **CJM-11.5.1 «Сравнение текущих JSONL с ранее сохранённым снапшотом счётчиков»** → API `restore_snapshot` возвращает старое cursor-состояние, потребитель сравнивает текущий `SessionFileSnapshot` с `raw_record_count` и `last_delivered_idx`. Покрыто тестами `test_save_and_restore_for_claude_session`, `test_save_and_restore_for_codex_session`, `test_same_session_id_different_backend_independent`.
- [x] **CJM-11.5.2 «Сообщения старше 3 часов не доставляются (TTL)»** → константа `config.UNREAD_BUFFER_TTL_HOURS = 3` + алгоритм `_is_expired`. Покрыто тестами `test_restore_returns_none_for_expired_record_and_lazily_deletes`, `test_fresh_record_within_ttl_is_returned`, `test_record_at_exact_ttl_boundary_is_still_valid`, `test_record_just_past_ttl_boundary_is_expired`.
- [x] **CJM-11.5.3 «Каждое непрочитанное сообщение доставляется отдельно с заголовком `#N ✅ текст`»** → НЕ покрывается этим модулем (доставка сообщений — задача `bot.py` и `message_splitter`). Модуль возвращает только cursor-состояние, формат заголовка `#N ✅` фиксируется в потребителе.
- [x] **CJM-11.5.4 «После доставки всех непрочитанных снапшот обнуляется — повторной доставки не будет»** → после успешной доставки потребитель вызывает `clear_snapshot_for_session_backend_pair(session_id, backend)` для соответствующей пары. TTL остаётся страховкой для забытых/недоставленных snapshot, но не механизмом штатной очистки после доставки.

## Что НЕ входит в модуль (для ясности)

Эти задачи могли бы показаться частью модуля, но сознательно отданы потребителям:

- **Чтение JSONL и извлечение текста сообщений** → `coding_agent_backend.read_session_file_snapshot()` и `coding_agent_backend.read_messages_from_session_file()`.
- **Фильтрация служебных сообщений** (вроде `"No response requested."`) → `session_watcher` через `backend.text_markers_indicating_empty_response()`.
- **Получение списка сессий проекта для snapshot** → `session_watcher.get_seen_counts_snapshot()` (по одной инстанции на backend) и/или `daily_session_registry.list_sessions_for_date`.
- **Доставка сообщений в Telegram** → `bot.py` через `message_splitter` и `telegram_sender`.
- **Персистентность через .env** или файлы → намеренно отсутствует; буфер живёт только в памяти.
- **Изоляция по `chat_id`** → не нужна (однопользовательский инвариант, см. CLAUDE.md → «Однопользовательский инвариант»).
- **`reset_state()`** → не нужен; буфер обязан переживать переключение проекта (это его смысл существования).

## История ревизий спеки

- **06-05-2026** — первая версия backend-aware. Основной API заменяет старый `project_path`-контракт на ключ `(session_id, backend)`, но старые функции `get_pending_messages`, `clear_snapshot` и `has_pending` остаются compatibility no-op wrapper-ами. Основания: пакет backend-абстракции (`coding_agent_backend_spec.md`), требование к составному ключу `(session_id, backend)` и контракт сохранения cursor-состояния (`raw_record_count`, `last_delivered_idx`).
