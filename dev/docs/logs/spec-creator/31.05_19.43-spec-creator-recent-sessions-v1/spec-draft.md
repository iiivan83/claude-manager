# Спецификация: recent_sessions v1 для быстрых /sessions и /all

## Контекст

Claude Manager показывает пользователю последние диалоги через `/sessions` и слушает
активность во всех проектах через `/all`. Сейчас эти сценарии могут повторно обходить
историю внешних CLI. Для Claude Code это относительно дёшево, потому что сессии лежат в
папке конкретного проекта. Для Codex это дорого: rollout-файлы лежат в общей истории
`~/.codex/sessions/YYYY/MM/DD/`, а принадлежность к проекту определяется только после
чтения metadata из JSONL.

`recent_sessions` v1 должен стать лёгким persistent-индексом последних сессий. Он хранит
быстрые заголовки сессий и preview, чтобы `/sessions` и `/all` не запускали полный scan
истории в пользовательском hot path. Полный текст сообщений остаётся в исходных session
files; таблица не становится новым источником истины для истории.

Эта спецификация описывает поведение и границы code change. Это не пошаговый implementation
plan и не требует переписывать архитектуру на event-driven индексатор в v1.

## Glossary / Terms

- **recent_sessions** — persistent-таблица быстрых кандидатов сессий. Хранит backend,
  project path, session id, путь к файлу, время изменения, preview и лёгкую cursor metadata.
  Не хранит полный текст сообщений.
- **Session header** — короткая запись о сессии без message body: ключ, файл, mtime, preview
  и служебные timestamps.
- **Hot path** — пользовательский путь, который блокирует ответ в Telegram или работает в
  частом цикле. Для этой задачи hot path: `/sessions`, `/all` enable, `/all` poll каждые
  2 секунды, переходы из `/all` в проект и любые cursor-checks внутри них.
- **Full historical scan** — обход всей истории session files внешнего CLI, особенно Codex
  sessions root без bounded lookback. Такой scan запрещён в hot path.
- **Safety-refresh** — ограниченное обновление `recent_sessions`, которое лечит пустой или
  устаревший индекс. Оно bounded по lookback, количеству кандидатов, known session id или
  timeout и не превращается в полный historical scan.
- **Cursor** — лёгкая позиция чтения session file: сколько raw records уже видели или
  доставили, какой был `last_modified_at`, и какой индекс последнего доставленного сообщения
  относится к конкретному режиму. Cursor нужен, чтобы не читать полный snapshot, если файл не
  изменился, и не переотправлять старую историю.
- **Storage cap** — ограничение хранения: `recent_sessions` держит последние 30 сессий на
  `project_path` across all backends.
- **UI cap** — ограничение отображения: `/sessions` показывает максимум 15 строк, даже если в
  storage есть 30.
- **/all global cap** — отдельный лимит глобального мониторинга: `/all` по умолчанию берёт
  максимум 80 кандидатов после сортировки across projects and backends.

## Цели

1. Сделать `/sessions` быстрым: основной путь читает `recent_sessions` для текущего проекта,
   а не вызывает backend listing как источник списка.
2. Сделать `/all` быстрым: enable и poll используют recent candidates и не запускают
   повторный full scan Codex history.
3. Хранить последние 30 сессий на проект across all backends, сохраняя backend-aware ключ.
4. Сохранить текущие пользовательские контракты: кликабельные `/1`, `/2`, `/3`, дневная
   нумерация через `daily_session_registry`, `/3s12` links, отдельные `/all` cursors и
   pending-семантика через `unread_buffer`.
5. Ввести bounded safety-refresh для empty/stale/degraded состояний без event-driven слоя.
6. Зафиксировать тесты, которые доказывают не только корректный текст ответа, но и отсутствие
   запрещённых scan-ов.

## Границы

### Входит в scope

- Новый storage-контракт для `recent_sessions`.
- Отдельный refresh-контракт для bounded safety-refresh.
- Интеграция `/sessions` с `recent_sessions` как основным источником кандидатов.
- Интеграция `/all` с `recent_sessions` как источником monitored candidates.
- Сохранение cursor/read state вне prunable header rows или доказанная совместимость с уже
  существующим независимым state-store.
- Поведение при empty, stale, locked, corrupt и missing-file состояниях.
- Unit и integration tests с forbidden-scan assertions.

### Не входит в scope

- Полноценный event-driven file watcher или inotify/watchdog слой.
- Offline migration/backfill всей старой истории из Telegram command path.
- Хранение полного текста сообщений в SQLite.
- Замена `daily_session_registry` или link registry в `/all`.
- Изменение `.claude/**`, generated `.agents/**` или Claude/Codex skill mirrors.
- Рефакторинг больших модулей сверх минимальной интеграции, если он не нужен для этого
  контракта.

## Требования

### Обязательные (must have)

1. `recent_sessions` должен быть отдельным lightweight storage-слоем, а не новой логикой
   внутри `all_projects_monitor.py`, `telegram_session_handlers.py`,
   `codex_session_file_listing.py`, `session_reader.py`, `daily_session_registry.py` или
   `coding_agent_backend.py`.
2. Уникальный ключ session header: `project_path + backend + session_id`.
3. Storage cap: после каждого upsert/prune таблица хранит максимум 30 самых свежих rows на
   `project_path` across all backends. Лимит не считается отдельно на Claude и Codex.
4. Sort order для query и pruning должен быть стабильным: `last_modified_at desc`, затем
   `updated_at desc`, затем deterministic tie-breaker `backend`, `session_id` или `file_path`.
5. `/sessions` должен читать rows из `recent_sessions` для `config.WORKING_DIR`, сортировать
   по storage order и показывать максимум `SESSION_LIST_LIMIT = 15`.
6. `/sessions` должен сохранять plain text response (`parse_mode=None`), чтобы Telegram
   команды `/1`, `/2`, `/3` оставались кликабельными.
7. `/sessions` должен регистрировать показанные rows через существующий
   `daily_session_registry.register_session()` и продолжать предпочитать
   `daily_session_registry.get_session_summary()` над stored preview, если summary есть.
8. Основной путь `/sessions` не должен напрямую вызывать
   `backend.list_session_files_for_project()` как источник списка. Этот вызов допустим только
   внутри bounded safety-refresh и только если backend-реализация не делает full historical
   scan.
9. `/all` enable должен получать candidates из `recent_sessions`, применять global sort по
   `last_modified_at desc` across projects/backends и ограничивать список default cap 80.
10. `/all` poll каждые 2 секунды не должен запускать discovery, backend bulk listing или
    refresh таблицы. Он работает по snapshot candidates enabled state и проверяет только
    известные active files через mtime/cursor-first путь.
11. `/all` должен сохранить текущие отдельные cursors, link registry для команд вида
    `/<project_number>s<session_number>` и unread snapshot перед доставкой, чтобы сообщения,
    показанные в `/all`, оставались pending для исходного проекта.
12. `recent_sessions` хранит metadata и preview cache, но не message bodies. Полный snapshot
    читается только для доставки, pending-сбора или changed cursor.
13. Preview должен инвалидироваться по `file_path + last_modified_at` и, если доступно, по
    `raw_record_count`. Неизменившаяся сессия не должна открывать JSONL ради повторного preview.
14. Cursor/read state, нужный для защиты от replay/loss, не должен храниться только в prunable
    row `recent_sessions`. Нужна companion table `session_cursor_state` или доказанное
    переиспользование существующего независимого state-store.
15. При empty table `/sessions` выполняет один bounded project safety-refresh и повторяет query.
    Если rows всё ещё нет, отвечает быстрым empty-сообщением без full historical scan.
16. При empty table `/all` выполняет bounded all-project safety-refresh с timeout и global cap.
    Если candidates всё ещё нет, режим не должен молча делать вид, что мониторит историю:
    пользователь получает понятное degraded/empty-сообщение.
17. При stale rows команды должны отвечать быстро: показывать имеющиеся rows с фоновым или
    bounded refresh, либо возвращать degraded-сообщение. Stale state не является разрешением
    на синхронный full historical scan.
18. SQLite, если выбран как storage backend, не должен блокировать event loop. Все операции
    идут через `asyncio.to_thread`, dedicated async wrapper или worker с write lock, короткими
    transactions и `busy_timeout`.
19. Corrupt DB, locked DB, schema mismatch, missing session file и missing project path должны
    быть recoverable состояниями: логируются, пропускают конкретный candidate или возвращают
    degraded response, но не валят весь бот и не запускают full historical scan в hot path.
20. Тесты должны содержать forbidden-scan assertions: регрессия, которая снова вызывает полный
    Codex listing в `/sessions`, `/all enable` или `/all poll`, должна падать.

### Желательные (nice to have)

1. `recent_sessions` может хранить `last_refresh_status` и `stale_reason`, чтобы degraded
   responses были понятнее в логах.
2. Background safety-refresh можно запускать после успешного создания или продолжения session,
   чтобы первый последующий `/sessions` уже читал свежий row.
3. Реализация может добавить offline maintenance command для полного backfill, но такая команда
   не должна вызываться из Telegram hot path.

## Технический дизайн

### Граница модулей

- **Новый storage-модуль** отвечает за schema init, migrations, upsert, query, pruning и
  non-blocking async facade. Он не знает про Telegram.
- **Новый refresh-модуль** отвечает за bounded safety-refresh, преобразование
  `SessionFileInfo` в session header, preview refresh и cursor metadata refresh. Он не
  отправляет сообщения пользователю.
- **`telegram_session_handlers.py`** остаётся тонким транспортным слоем: `/sessions` вызывает
  query/refresh facade и собирает plain-text ответ.
- **`all_projects_monitor.py`** остаётся монитором `/all`: берёт snapshot candidates из
  storage facade, строит baseline cursor и poll-ит known files. Storage/refresh logic туда не
  переносится.
- **Backend adapters** остаются источником file metadata. Persistence `recent_sessions` не
  становится обязанностью `CodingAgentBackend`.
- **File readers** остаются источником полного текста и lightweight cursors. Таблица не
  заменяет `read_session_file_cursor()` и `read_session_file_snapshot()`.

### Schema

SQLite является допустимой v1-реализацией, если соблюдён non-blocking contract. Другой
persistent backend допустим только при сохранении тех же ключей, индексов, retention и
поведения hot path.

Минимальная schema:

```sql
CREATE TABLE recent_sessions (
    project_path TEXT NOT NULL,
    backend TEXT NOT NULL,
    session_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    last_modified_at REAL NOT NULL,
    preview TEXT NOT NULL DEFAULT '',
    raw_record_count INTEGER,
    cursor_record_count INTEGER,
    file_missing INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_refreshed_at TEXT NOT NULL,
    refresh_status TEXT NOT NULL DEFAULT 'ok',
    stale_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_path, backend, session_id)
);

CREATE INDEX idx_recent_sessions_project_mtime
ON recent_sessions (project_path, last_modified_at DESC, updated_at DESC);

CREATE INDEX idx_recent_sessions_global_mtime
ON recent_sessions (last_modified_at DESC, updated_at DESC);

CREATE INDEX idx_recent_sessions_file_path
ON recent_sessions (file_path);
```

Companion cursor schema:

```sql
CREATE TABLE session_cursor_state (
    project_path TEXT NOT NULL,
    backend TEXT NOT NULL,
    session_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    raw_record_count INTEGER,
    last_delivered_idx INTEGER,
    last_modified_at REAL,
    cursor_scope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_path, backend, session_id, cursor_scope)
);

CREATE INDEX idx_session_cursor_state_updated_at
ON session_cursor_state (updated_at DESC);
```

Schema versioning:

```sql
CREATE TABLE recent_sessions_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Required schema semantics:

- `backend` stores the existing backend name value, not a display label.
- `project_path` stores the absolute project path used by `config.WORKING_DIR` and project
  switching.
- `file_path` stores the absolute path to the session JSONL/rollout file.
- `last_modified_at` uses the same numeric timestamp family as `SessionFileInfo`.
- `preview` is a cached UI preview, not source-of-truth text.
- `raw_record_count` and `cursor_record_count` are lightweight cursor metadata for fast
  change detection. Delivery cursor ownership remains separate by `cursor_scope`.
- `file_missing = 1` removes a row from active query results unless a refresh later sees the
  file again.
- `refresh_status` values should be small and testable, for example `ok`, `stale`, `missing`,
  `error`.

### Storage contract

The storage facade must expose behavior, not necessarily these exact function names:

- Initialize schema idempotently.
- Upsert one or many session headers inside a transaction.
- Query recent sessions for one project with limit, default 30 for storage consumers and 15
  for `/sessions` UI.
- Query global recent sessions with default cap 80 after global sort.
- Prune `recent_sessions` to 30 rows per `project_path` across all backends.
- Preserve `session_cursor_state` when a header row is pruned.
- Mark missing files without deleting cursor state.
- Return typed rows that can be converted to existing `SessionFileInfo` or an equivalent
  internal DTO.

Storage must not call Telegram handlers, `project_manager.scan_available_projects()`, backend
listing, or session file readers. It is a persistence boundary only.

### Safety-refresh contract

Safety-refresh is the only v1 mechanism that may discover or update rows. It is deliberately
separate from hot path query logic.

Allowed triggers:

- Empty `recent_sessions` for current project when `/sessions` is called.
- Empty global candidate set when `/all` is enabled.
- Stale `last_refreshed_at` for a project or global query.
- Successful creation or continuation of a known session, where backend/session id/file path is
  already known.
- Known session update from watcher/pending code.
- Periodic background refresh with coarse interval.

Bounds:

- Codex refresh must use `codex_session_index` with bounded `lookback_days`, known UUIDv7
  candidate paths, or another bounded source. It must not call
  `list_all_session_file_infos_by_project()` in full-scan mode and must not call
  `_list_all_rollout_files_blocking()` over the whole sessions root from hot path.
- Claude refresh may inspect the project-specific Claude sessions folder, but still applies a
  bounded candidate limit and does not read full message history for every file.
- Refresh reads full snapshot only for rows whose mtime/cursor indicates change and only when
  preview or delivery requires it.
- Refresh failures are logged and surfaced as degraded behavior; they do not silently fall back
  to full historical scan.

### /sessions contract

Expected behavior:

1. Query `recent_sessions` for `config.WORKING_DIR`.
2. If rows are empty, run one bounded project safety-refresh, then query again.
3. If rows are stale but present, return rows quickly and schedule or run bounded refresh
   according to timeout budget.
4. Limit visible rows to `SESSION_LIST_LIMIT = 15`.
5. Register visible rows through `daily_session_registry.register_session()`.
6. Render the same command style as today: plain text with `/1`, `/2`, `/3`.
7. Prefer existing daily registry summary over stored preview, then fall back to stored preview.

Forbidden behavior:

- No primary-path call to backend UI listing.
- No Codex full historical scan.
- No full JSONL snapshot read for unchanged rows just to render the list.
- No expansion of `telegram_session_handlers.py` into storage/refresh logic.

### /all contract

Expected enable behavior:

- `/all` may still scan configured project directories through `project_manager` if needed for
  project numbers; this is not session history scan.
- Session candidates come from `recent_sessions` global query, sorted by `last_modified_at desc`
  and capped by `ALL_MODE_SESSION_CANDIDATE_LIMIT = 80` by default.
- Baseline reads lightweight cursors for selected candidates and stores `/all`-specific enabled
  state.
- If candidate query fails because DB is locked/corrupt, `/all` should not leave normal watcher
  paused indefinitely. It should restore previous watcher state and tell the user that global
  monitoring is temporarily unavailable.

Expected poll behavior:

- `poll_once()` uses the candidate snapshot captured at enable or at an explicit controlled
  candidate rebuild.
- It checks mtime/cursor for known files.
- If mtime is unchanged, it does not read full snapshot.
- If mtime changed, it reads the minimal cursor/snapshot required to deliver new messages,
  preserves unread snapshot, and updates only `/all` cursor state.
- Background refresh may update `recent_sessions`, but those changes are not applied halfway
  through a single poll. Candidate set changes happen at controlled rebuild points to keep link
  registry stable.

Forbidden behavior:

- No backend bulk full listing on every poll.
- No full Codex sessions root scan in enable.
- No discovery/refresh inside the 2-second polling loop.
- No update of ordinary watcher/project pending cursor just because `/all` delivered a message.

## Empty, stale and degraded behavior

- **Empty project table for `/sessions`** — run one bounded refresh for the current project.
  If still empty, return the existing empty-list meaning: no recent sessions found. Do not start
  full history scan.
- **Empty global table for `/all`** — run bounded all-project refresh with global cap and
  timeout. If still empty, respond that global monitoring has no indexed recent sessions yet or
  cannot be enabled with candidates. Do not silently monitor nothing as if history was covered.
- **Stale rows with data** — return stored rows quickly. Mark refresh needed and run bounded
  refresh in background or within a small timeout. Stale rows are better than blocking the user
  on full scan.
- **DB locked** — wait only through configured `busy_timeout`; if still locked, return degraded
  response and log warning. Event loop must stay responsive.
- **DB corrupt or schema mismatch** — do not crash the bot. Log error, quarantine or ignore the
  broken store according to implementation policy, initialize a clean schema if safe, and use
  bounded refresh only.
- **Missing file** — mark row missing or remove it from active results; keep cursor state unless
  a separate retention policy says it can be deleted.
- **Missing project path** — skip rows for that project in `/all`, preserve DB rows until normal
  pruning or maintenance.
- **Refresh failure for one backend/project** — skip that backend/project and continue others.
  A single broken file must not disable `/all` globally.

## Non-blocking SQLite contract

If sqlite3 from the Python standard library is used:

- No direct sqlite call from async Telegram handlers, watcher pollers or `/all` poll loop.
- Async facade wraps DB work with `asyncio.to_thread` or a dedicated worker.
- Writes are serialized with an async lock or worker queue.
- Connections are short-lived per operation or explicitly bound to the worker thread.
- `busy_timeout` is configured.
- Schema init and migrations run in transactions.
- Upsert + prune runs in one transaction per project batch.
- WAL mode is allowed if it improves read/write coexistence, but tests must not depend on a
  machine-specific SQLite configuration.
- Exceptions are converted into typed degraded results for callers that must answer Telegram.

## File-size constraints

The implementation must actively avoid silent module growth:

- For every edited or newly created code module, the implementer must also count top-level
  public functions. If a module has more than 10, explicitly assess the god-module risk and
  propose splitting by responsibility unless the functions are only a thin contract layer.
- `src/claude_manager/all_projects_monitor.py` is already about 633 lines and above the 500-line
  tech-debt threshold. Add only thin integration for candidate source and controlled rebuilds.
  Do not place storage, schema, pruning or refresh algorithms here.
- `src/claude_manager/codex_session_file_listing.py` is about 499 lines, one line before the
  500-line threshold. Do not add persistent recent_sessions logic here. Any Codex discovery
  change should reuse bounded functions or move new logic to a new module.
- `src/claude_manager/session_reader.py` is about 328 lines and already above the 300-line
  warning threshold. Do not add cross-backend recent_sessions code here.
- `src/claude_manager/daily_session_registry.py` is about 628 lines and above the 500-line
  tech-debt threshold. Keep it as the owner of daily numbers and summaries; integrate only
  through existing public functions.
- `src/claude_manager/coding_agent_backend.py` is about 319 lines and above the 300-line warning
  threshold. Avoid broad new abstract methods. `SessionFileInfo` should remain enough for this
  v1 unless a minimal, well-justified contract is unavoidable.
- `src/claude_manager/telegram_session_handlers.py` is about 280 lines, with roughly 20 lines
  before the 300-line warning threshold. Keep handler changes small and delegate storage/refresh
  to new modules.

If any edited code file crosses 300, 500, 700 or 1000 lines during implementation, the
developer must explicitly report it and either split the module or justify the temporary
increase according to project rules.

## Зависимости

- Existing `SessionFileInfo` DTO from `coding_agent_backend.py`.
- Existing backend adapters for Claude and Codex file metadata.
- Existing lightweight cursor readers and snapshot readers.
- Existing `daily_session_registry` for Telegram-visible session numbers and summaries.
- Existing `unread_buffer`, `/all` link registry and project pending delivery semantics.
- Python stdlib `sqlite3` is acceptable only behind non-blocking async facade.

No new runtime dependency is required by the specification. If implementation proposes one, it
must justify why stdlib sqlite3 behind an async wrapper is insufficient.

## Ограничения и риски

- A full historical scan hidden inside refresh would reintroduce the root performance bug. Tests
  must fail on forbidden calls, not only verify rendered text.
- Cursor state in pruned rows can cause replay or message loss. Header retention and cursor
  retention must be separate.
- `/all` can still become expensive if it monitors 30 rows per project across many projects.
  The separate default global cap 80 is mandatory.
- Stale rows can show an outdated order. This is acceptable only when the command answers
  quickly and schedules bounded refresh.
- DB lock/corruption handling must not leave watchers paused or Telegram handlers blocked.
- Existing modules are near size thresholds; large diffs in them should be treated as a design
  smell and moved to new modules.

## Критерии приёмки

- `recent_sessions` schema initializes idempotently and stores backend-aware session headers.
- Storage cap keeps exactly the 30 newest rows per project across Claude and Codex combined.
- `/sessions` displays at most 15 rows and keeps clickable plain-text session commands.
- `/sessions` uses `recent_sessions` as primary source and does not perform full backend listing
  on repeated calls.
- `/all` enable uses global query from `recent_sessions`, applies default cap 80 and builds
  baseline only for selected candidates.
- `/all` poll does not run discovery/refresh and does not full-scan Codex history.
- Unchanged mtime avoids full snapshot reads.
- Changed mtime updates cursor/preview through existing file reader contracts.
- Messages delivered in `/all` remain pending for the source project.
- Empty, stale, locked, corrupt and missing-file cases return controlled empty/degraded behavior.
- SQLite access, if used, is non-blocking for the event loop.
- Tests include forbidden-scan assertions for `/sessions`, `/all enable`, `/all poll` and
  safety-refresh.
- Implementation respects file-size constraints and does not silently grow large modules.

## Тест-план

### Unit tests for storage

- Schema init is idempotent and writes schema version metadata.
- Upsert by `project_path + backend + session_id` updates an existing row instead of duplicating.
- Query for one project returns rows sorted by `last_modified_at desc` with stable tie-breaker.
- Global query returns rows sorted across projects/backends and enforces cap 80 by default.
- Pruning with 31+ rows for one project leaves 30 newest rows total across Claude and Codex.
- Backend-aware uniqueness allows the same `session_id` in different backends without collision.
- Cursor state survives pruning of `recent_sessions` header rows.
- Missing file marks/removes active header without deleting independent cursor state.
- DB locked/corrupt/schema mismatch paths return typed degraded result instead of raising through
  Telegram-facing async code.

### Unit tests for safety-refresh

- Empty project refresh uses bounded source and then upserts rows.
- Empty global refresh uses bounded source and applies global cap.
- Codex refresh never calls full `list_all_session_file_infos_by_project()` mode or
  `_list_all_rollout_files_blocking()` over the whole sessions root.
- Known Codex session refresh uses known UUIDv7 candidate path or existing bounded index path.
- Preview refresh opens full snapshot only when `last_modified_at` or `raw_record_count` changed.
- Stale rows trigger bounded/background refresh without blocking the caller on full scan.
- Refresh failure for one backend logs and continues other backends/projects.

### Unit tests for /sessions

- Handler queries `recent_sessions` first and renders rows from it.
- Handler enforces UI cap 15 even when storage has 30 rows.
- Handler registers visible rows in `daily_session_registry` with backend-aware ownership.
- Handler prefers daily summary over stored preview.
- Empty table triggers exactly one bounded refresh and then returns empty message if still empty.
- Repeated `/sessions` on Codex project does not call backend listing or session_meta scan.

Forbidden-scan assertions for `/sessions`:

- Spy/counter on `backend.list_session_files_for_project()` must stay zero on primary-path repeat.
- Spy/counter on Codex full rollout listing must stay zero.
- Spy/counter on full snapshot reader must stay zero for unchanged rows.

### Unit tests for /all

- Enable reads candidates from `recent_sessions`, not backend bulk full listing.
- Enable applies global cap 80 after global sort.
- Baseline reads lightweight cursors only for capped candidates.
- Poll uses enabled candidate snapshot and does not call discovery.
- Two consecutive polls with unchanged mtime do not read full snapshot.
- Changed mtime reads the required cursor/snapshot and delivers only new messages.
- Delivery in `/all` writes unread snapshot and does not advance ordinary project pending cursor.
- Background refresh during enabled `/all` does not mutate link registry mid-poll.
- DB failure during enable resumes normal watcher state and tells the user monitoring is degraded
  or unavailable.

Forbidden-scan assertions for `/all`:

- Spy/counter on `backend.list_all_session_files_for_projects()` must stay zero in poll.
- Spy/counter on Codex `list_all_session_file_infos_by_project()` full mode must stay zero in
  enable and poll.
- Spy/counter on `_list_all_rollout_files_blocking()` must stay zero in hot path.
- Spy/counter on refresh entrypoint must stay zero inside the 2-second poll loop.
- Spy/counter on full snapshot reader must stay zero when mtime did not change.

### Integration/regression tests

- Repeated `/sessions` after warm store stays fast by call-count contract, not by timing-only
  assertion.
- `/all` enable with many stored projects caps candidates at 80 and keeps link resolution valid.
- `/all` poll for 80 candidates checks mtime/cursor-first and skips unchanged snapshots.
- Empty DB after restart returns controlled empty/degraded behavior and uses only bounded refresh.
- Stale DB after bot downtime returns stored rows quickly and schedules bounded refresh.
- Pruning does not break `/3s12` fallback behavior that depends on daily registry/link registry.

### Manual smoke

- Start bot with a warmed store, run `/sessions`, verify 15 clickable rows.
- Enter `/all`, verify confirmation is quick and messages still appear with `/3s12` links.
- Switch from `/all` into a project after a delivered message and verify pending behavior remains
  consistent with current contract.
