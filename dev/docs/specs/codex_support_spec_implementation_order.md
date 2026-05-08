# Очередность реализации Codex-support спецификаций

## Контекст

Документ фиксирует рабочую очередь реализации спецификаций, которые переводят Telegram-бот с Claude-only архитектуры на backend-aware архитектуру с поддержкой Claude Code CLI и Codex CLI.

Дата: 07-05-2026

## Назначение

Этот документ отвечает на один вопрос: в каком порядке брать активные спеки из `dev/docs/specs/`, чтобы бот начал работать с Codex без промежуточных состояний, где UI уже позволяет выбрать Codex, а внутреннее состояние сессий, процессов и watcher-а всё ещё остаётся Claude-only.

Документ не заменяет подробные module specs. Он является routing-документом для `feature-pipeline` и `implement-module`: какую спеку брать следующей, какие зависимости должны быть закрыты до неё, и какой минимальный gate должен пройти этап.

## Источники

- `dev/docs/specs/module-dependency-graph.md`
- `dev/docs/specs/realised/coding_agent_backend_spec.md`
- `dev/docs/specs/realised/claude_code_backend_spec.md`
- `dev/docs/specs/realised/codex_backend_spec.md`
- `dev/docs/specs/realised/current_backend_registry_spec.md`
- `dev/docs/specs/realised/08.05_16.38-backend-aware-daily_session_registry_spec.md`
- `dev/docs/specs/realised/08.05_16.38-backend-aware-session_manager_spec.md`
- `dev/docs/specs/realised/08.05_16.38-backend-aware-process_manager_spec.md`
- `dev/docs/specs/realised/08.05_16.38-backend-aware-session_watcher_spec.md`
- `dev/docs/specs/realised/unread_buffer_spec.md`
- `dev/docs/specs/realised/project_manager_spec.md`
- `dev/docs/specs/realised/telegram_agent_backend_integration_spec.md`
- `dev/docs/specs/realised/agent_backend_selection_user_journey_spec.md`
- `dev/docs/session-reports/07-05/10-03_codex-support-specs-analysis.md`

## Глобальные инварианты очереди

- **Сначала ownership, потом UI.** Команда `/agent` подключается только после того, как `daily_session_registry`, `session_manager`, `process_manager`, `session_watcher` и unread delivery умеют работать с парой `(session_id, backend)`.
- **Существующая сессия всегда открывается своим backend-ом.** `current_backend_registry.get_current()` используется только для новых сессий, а не для `/N`, `/stop`, watcher-а или pending delivery.
- **Голый `session_id` не является полным идентификатором.** В местах запуска subprocess, остановки процесса, поиска владельца, чтения файла сессии и восстановления непрочитанных используется пара `(session_id, backend)`.
- **Telegram-facing слой подключается последним.** `bot.py` и `claude_interaction.py` должны связывать уже готовые нижние контракты, а не компенсировать их отсутствие ветвлениями.
- **Codex CLI контракт проверяется эмпирически.** Для `codex_backend` обязательны contract/integration tests с реальным CLI или явно помеченный skip, если бинарник недоступен в окружении.

## Подготовительный этап: закрыть противоречия документации

### 0.1. Зафиксировать номер CJM для `/agent`

**Проблема:** `agent_backend_selection_user_journey_spec.md` и `module-dependency-graph.md` называют `/agent` как `CJM-14`, но в `dev/docs/brd/brd-user-journeys.md` `CJM-14` уже занят сценарием `/restart`, а `CJM-15` занят silence mode.

**Действие перед кодом:**
- добавить `/agent` в BRD под свободным номером, например `CJM-16`;
- обновить ссылки на `/agent` в backend-aware спеках с `CJM-14` на выбранный свободный номер;
- не переиспользовать существующий `CJM-14`, чтобы не смешать `/restart` и выбор CLI-бэкенда.

**Gate:** `rg -n "CJM-14.*agent|CJM-14.*бэкенд|CJM-14.*backend" dev/docs/specs dev/docs/brd -g '!codex_support_spec_implementation_order.md'` не должен находить ссылки, где `/agent` всё ещё привязан к занятому номеру `CJM-14`.

**Статус:** выполнено 2026-05-07.
**Проверка:** `rg -n "CJM-14.*agent|CJM-14.*бэкенд|CJM-14.*backend|CJM-NEW" dev/docs/specs dev/docs/brd -g '*.md' -g '!realised/**' -g '!codex_support_spec_implementation_order.md'` — нет совпадений.
**Артефакты:** `dev/docs/brd/brd-user-journeys.md`, active specs under `dev/docs/specs/`.

### 0.2. Синхронизировать dependency graph

**Спека:** `dev/docs/specs/module-dependency-graph.md`

**Действие:** обновить список CJM и трейсабельность после решения пункта 0.1. Сам слойный порядок из графа сохраняется.

**Gate:** граф ссылается на актуальный номер CJM для `/agent` и остаётся согласованным с BRD.

**Статус:** выполнено 2026-05-07.
**Проверка:** `rg -n "CJM-16|CJM: 01–16|Переключение CLI-бэкенда|CJM-14|CJM-15" dev/docs/specs/module-dependency-graph.md` — граф содержит `CJM-16` для `/agent`, `bot` покрывает `CJM: 01–16`, `CJM-14` и `CJM-15` сохранены за `/restart` и silence mode.
**Артефакты:** `dev/docs/specs/module-dependency-graph.md`.

## Фаза 1: общий backend contract

### 1.1. `coding_agent_backend_spec.md`

**Что реализовать:** `src/claude_manager/coding_agent_backend.py`.

**Почему первым:** это корневой контракт для `BackendName`, `CodingAgentBackend`, DTO событий, snapshot-ов, stop strategy, фабрики `get_backend` и списка `get_all_backends`. Все следующие спеки импортируют эти типы или вызывают фабрику.

**Зависимости:** нет.

**Минимальный gate:**
- unit tests на enum, DTO, abstract class, фабрику и unknown backend errors;
- тесты не импортируют `bot.py`, `process_manager.py`, `session_manager.py`;
- модуль не создаёт циклических импортов с конкретными backend implementations.

**Статус:** выполнено 2026-05-07.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_coding_agent_backend.py -q` — 12 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python - <<'PY' ... PY` — `contract import check passed`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 933 passed, 3 warnings.
**Артефакты:** `src/claude_manager/coding_agent_backend.py`, `tests/test_coding_agent_backend.py`, moved `dev/docs/specs/coding_agent_backend_spec.md` to `dev/docs/specs/realised/coding_agent_backend_spec.md`.

## Фаза 2: backend implementations и глобальный выбор backend-а

### 2.1. `claude_code_backend_spec.md`

**Что реализовать:** `src/claude_manager/claude_code_backend.py`.

**Почему раньше Codex:** Claude-путь уже работает в текущем коде; перенос существующего поведения в adapter даёт baseline и снижает риск регрессии перед добавлением нового CLI.

**Зависимости:** `coding_agent_backend`.

**Минимальный gate:**
- unit tests покрывают перенос текущих Claude CLI аргументов, stdin JSONL, stdout parsing, чтение JSONL-файлов сессий и stop strategy;
- contract test с реальным Claude CLI остаётся зелёным или явно пропускается при отсутствии бинарника.

**Статус:** выполнено 2026-05-07.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_code_backend.py tests/test_coding_agent_backend.py tests/integration/test_claude_cli_contract.py -q` — 31 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 951 passed, 3 warnings.
**Артефакты:** `src/claude_manager/claude_code_backend.py`, `src/claude_manager/claude_code_session_file_reader.py`, `src/claude_manager/claude_code_session_path.py`, `tests/test_claude_code_backend.py`, `tests/integration/test_claude_cli_contract.py`, moved `dev/docs/specs/claude_code_backend_spec.md` to `dev/docs/specs/realised/claude_code_backend_spec.md`.

### 2.2. `codex_backend_spec.md`

**Что реализовать:** `src/claude_manager/codex_backend.py`.

**Почему после Claude adapter:** Codex adapter должен реализовать тот же интерфейс и пройти симметричные тесты, но его CLI-контракт новый и рискованнее.

**Зависимости:** `coding_agent_backend`.

**Минимальный gate:**
- unit tests покрывают `codex exec`, `codex exec resume`, `--json`, `--dangerously-bypass-approvals-and-sandbox`, `--skip-git-repo-check`, пустой stdin и parsing stdout;
- tests на чтение `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` через `response_item`;
- stop strategy начинается с `SIGINT`;
- contract tests с реальным Codex CLI v0.128.0 или skip с явной причиной.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py -q` — падение на `ModuleNotFoundError: No module named 'claude_manager.codex_backend'`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_codex_backend.py tests/test_coding_agent_backend.py tests/integration/test_codex_cli_contract.py -q` — 36 passed, 1 skipped.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 975 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/codex_backend.py`, `src/claude_manager/codex_session_file_reader.py`, `src/claude_manager/codex_session_file_listing.py`, `tests/test_codex_backend.py`, `tests/integration/test_codex_cli_contract.py`, moved `dev/docs/specs/codex_backend_spec.md` to `dev/docs/specs/realised/codex_backend_spec.md`.

### 2.3. `current_backend_registry_spec.md`

**Что реализовать:** `src/claude_manager/current_backend_registry.py` и константу `config.CURRENT_BACKEND_FILE`.

**Почему до session ownership:** `/new` и будущий Telegram-layer должны иметь стабильный источник текущего backend-а, но этот источник пока не подключается к UI.

**Зависимости:** `coding_agent_backend`, `config`.

**Минимальный gate:**
- JSON-формат `{"backend": "claude" | "codex"}`;
- legacy plain-text migration;
- `set_current` меняет память только после успешной атомарной записи;
- failed load блокирует последующий `set_current` через `RuntimeError`.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_current_backend_registry.py tests/test_config.py -q` — падение на `ImportError: cannot import name 'current_backend_registry'`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_current_backend_registry.py tests/test_config.py -q` — 53 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 989 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/current_backend_registry.py`, `src/claude_manager/config.py`, `tests/test_current_backend_registry.py`, `tests/test_config.py`, moved `dev/docs/specs/current_backend_registry_spec.md` to `dev/docs/specs/realised/current_backend_registry_spec.md`.

### 2.4. `claude_runner` из `telegram_agent_backend_integration_spec.md`

**Что реализовать:** тонкую subprocess-обёртку `start_subprocess_for_backend(...)` в `src/claude_manager/claude_runner.py`.

**Почему на этой фазе:** `process_manager` следующей фазы должен запускать оба CLI через единый adapter contract, а не собирать Claude/Codex команды сам.

**Зависимости:** `coding_agent_backend`, `claude_code_backend`, `codex_backend`.

**Минимальный gate:**
- обёртка получает command args только через `backend.compose_subprocess_command_args`;
- stdin bytes получает только через `backend.encode_user_message_for_cli_stdin`;
- Claude-specific parsing удалён из runner-а или оставлен только как compatibility wrapper, не используемый новым flow.

**Статус:** выполнено 2026-05-07.
**Проверка RED:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_runner.py -q` — падение на `ImportError: cannot import name 'BackendSubprocess'`.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/test_claude_runner.py -q` — 33 passed.
**Проверка:** `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` — 992 passed, 1 skipped, 3 warnings.
**Артефакты:** `src/claude_manager/claude_runner.py`, `tests/test_claude_runner.py`.

## Фаза 3: хранение владения сессиями

### 3.1. `daily_session_registry_spec.md`

**Что реализовать:** backend-aware `src/claude_manager/daily_session_registry.py`.

**Почему первым в ownership:** дневной номер `/N` должен хранить `DailySessionEntry(session_id, backend)`, иначе следующий слой не сможет безопасно переключать сессии.

**Зависимости:** `coding_agent_backend`, `config`, конкретные backend-ы через `get_backend` для orphan cleanup.

**Минимальный gate:**
- старый формат `number -> "uuid"` мигрирует в `DailySessionEntry(..., BackendName.CLAUDE)`;
- `register_session(session_id, backend)` идемпотентен по паре `(session_id, backend)`;
- `lookup_by_number` возвращает entry, а не строку;
- orphan cleanup вызывает `backend.session_file_exists_for_project(...)`, а не читает Claude path напрямую.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/daily_session_registry.py`, `tests/test_daily_session_registry.py`, `dev/docs/specs/realised/08.05_16.38-backend-aware-daily_session_registry_spec.md`.

### 3.2. `session_manager_spec.md`

**Что реализовать:** backend-aware `src/claude_manager/session_manager.py`.

**Почему после registry:** `switch_to_session` должен брать backend из `DailySessionEntry`, а `create_new_session` должен регистрировать temp-id с явным backend.

**Зависимости:** `coding_agent_backend`, `daily_session_registry`, `session_reader`, `config`.

**Минимальный gate:**
- `sessions.json` мигрирует из `{"chat_id": "uuid"}` в `{"chat_id": {"session_id": "uuid", "backend": "claude"}}`;
- `ActiveSession(session_id, backend)` используется для активной привязки;
- `find_chat_by_session_id(session_id, backend)` ищет по паре;
- `/N` не читает `current_backend_registry`.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/session_manager.py`, `tests/test_session_manager.py`, `dev/docs/specs/realised/08.05_16.38-backend-aware-session_manager_spec.md`.

### 3.3. `unread_buffer_spec.md`

**Что реализовать:** тонкий `src/claude_manager/unread_buffer.py` по ключу `(session_id, backend)`.

**Почему после ownership:** pending state должен использовать тот же ключ, что session ownership, иначе project switching снова потеряет backend.

**Зависимости:** `coding_agent_backend`, `config`.

**Минимальный gate:**
- snapshot для одинакового `session_id` под Claude и Codex хранится как две разные записи;
- модуль не читает JSONL и не импортирует `session_reader`;
- TTL и explicit clear работают по паре `(session_id, backend)`.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/unread_buffer.py`, `tests/test_unread_buffer.py`, `dev/docs/specs/realised/unread_buffer_spec.md`.

## Фаза 4: lifecycle процессов

### 4.1. `process_manager_spec.md`

**Что реализовать:** backend-aware `src/claude_manager/process_manager.py`.

**Почему после ownership:** `send_message`, `/stop`, busy flags и retry loop должны получать backend от активной сессии, а не пытаться вывести его из глобального выбора.

**Зависимости:** `coding_agent_backend`, `current_backend_registry`, `claude_runner`, `config`.

**Минимальный gate:**
- `_processes`, `_busy_flags`, `_stop_events` ключуются через `(session_id, BackendName)`;
- `send_message(..., backend=...)` захватывает backend на старте и не перечитывает глобальный registry во время turn-а;
- temp-to-real remap сохраняет backend;
- `/stop` применяет `backend.get_stop_strategy()`;
- retry на `turn.failed` Codex идёт по явному `TerminalStatus.FAILED`, а не по пустому тексту.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/process_manager.py`, `tests/test_process_manager.py`, `dev/docs/specs/realised/08.05_16.38-backend-aware-process_manager_spec.md`.

## Фаза 5: watcher и project switching

### 5.1. `session_watcher_spec.md`

**Что реализовать:** backend-aware `src/claude_manager/session_watcher.py`.

**Почему после process/session ownership:** watcher должен доставлять сообщения владельцу по паре `(session_id, backend)` и координироваться с активным turn-ом через тот же ключ.

**Зависимости:** `coding_agent_backend`, `daily_session_registry`, `session_manager`, `config`.

**Минимальный gate:**
- две независимые watcher-инстанции, по одной на backend;
- `pause_session`, `resume_session`, `update_session_id` принимают backend;
- чтение файлов идёт через `backend.list_all_session_files_for_project` и `backend.read_session_file_snapshot`;
- buffer-and-hold не доставляет последний assistant-текст как final до terminal record.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/session_watcher.py`, `tests/test_session_watcher.py`, `dev/docs/specs/realised/08.05_16.38-backend-aware-session_watcher_spec.md`.

### 5.2. `project_manager_spec.md`

**Что реализовать:** backend-aware `src/claude_manager/project_manager.py`.

**Почему после watcher/unread:** переключение проектов сохраняет и восстанавливает cursor-состояние watcher-а; без готовых watcher snapshot-ов и unread buffer-а эта логика будет неполной.

**Зависимости:** `coding_agent_backend`, `unread_buffer`, `session_manager`, `daily_session_registry`, `session_watcher`, `config`.

**Минимальный gate:**
- `current_backend_registry` не сбрасывается при переключении проекта;
- snapshots сохраняются для всех отслеживаемых сессий обоих backend-ов;
- pending delivery содержит `backend`;
- `pause_all` и `resume_all` применяются ко всем watcher-инстанциям;
- процессы не убиваются при переключении проекта.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/project_manager.py`, `tests/test_project_manager.py`, `dev/docs/specs/realised/project_manager_spec.md`.

## Фаза 6: Telegram-facing integration

### 6.1. `telegram_agent_backend_integration_spec.md`

**Что реализовать:** backend-aware связку `bot.py`, `claude_interaction.py`, `claude_runner.py`, `main.py`.

**Почему после нижних фаз:** этот слой только связывает готовые контракты. Если реализовать его раньше, придётся временно ветвиться и обходить отсутствие ownership.

**Зависимости:** все фазы 1-5.

**Минимальный gate:**
- `/new` берёт backend из `current_backend_registry.get_current()` и передаёт в `session_manager.create_new_session`;
- обычное сообщение берёт backend из `session_manager.get_active_session`;
- `/sessions` объединяет сессии всех backend-ов и ограничивает общий список до 15 после merge;
- `/N` использует backend из `SwitchResult`;
- `/stop` вызывает `process_manager.stop_process(session_id, backend)`;
- watcher callback и pending delivery передают backend до `send_response`;
- `main.post_init` загружает `current_backend_registry`.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Артефакты:** `src/claude_manager/bot.py`, `src/claude_manager/claude_interaction.py`, `src/claude_manager/claude_runner.py`, `src/claude_manager/main.py`, `tests/test_bot.py`, `tests/test_claude_interaction.py`, `dev/docs/specs/realised/telegram_agent_backend_integration_spec.md`.

### 6.2. `agent_backend_selection_user_journey_spec.md`

**Что реализовать:** команду `/agent` и callback handler.

**Почему последней пользовательской фичей:** `/agent` меняет глобальный backend для новых сессий. Подключать её до полной backend-aware цепочки запрещено: пользователь сможет выбрать Codex, но существующие команды будут продолжать жить в Claude-only модели.

**Зависимости:** `current_backend_registry`, `coding_agent_backend`, `session_manager`, Telegram integration.

**Минимальный gate:**
- `/agent` показывает текущий backend и inline-кнопки всех backend-ов;
- callback data использует `BackendName.value`;
- повторный выбор текущего backend-а не пишет файл;
- активная сессия не меняется после переключения;
- подтверждение явно говорит, что текущая сессия остаётся на своём backend-е;
- ошибка записи в registry видна пользователю и не меняет in-memory backend.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Проверка E2E:** `tests/e2e/test_agent_backend_selection.py` и `tests/e2e/test_project_switching.py::test_codex_pending_message_delivered_on_project_return_with_backend_header` покрывают выбор Codex, новые Codex-сессии, сохранение backend у старых Claude-сессий, `/stop` и pending delivery после переключения проекта. Последний зафиксированный прогон описан в `dev/docs/session-reports/08-05/19-04_codex-integration-e2e-tests.md`.
**Артефакты:** `src/claude_manager/bot.py`, `tests/test_bot.py`, `tests/e2e/test_agent_backend_selection.py`, `tests/e2e/test_project_switching.py`, `dev/docs/specs/realised/agent_backend_selection_user_journey_spec.md`.

## Фаза 7: сквозная проверка

### 7.1. Unit tests

**Запуск:**

```bash
python -m pytest tests/ -q
```

**Gate:** весь существующий unit/integration набор зелёный; новые tests покрывают оба backend-а там, где логика backend-neutral.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_daily_session_registry.py tests/test_session_manager.py tests/test_unread_buffer.py tests/test_process_manager.py tests/test_session_watcher.py tests/test_project_manager.py tests/test_bot.py tests/test_claude_interaction.py -q` — 355 passed.
**Проверка:** `.venv/bin/python -m pytest tests/test_coding_agent_backend.py tests/test_claude_code_backend.py tests/test_codex_backend.py tests/test_current_backend_registry.py tests/test_claude_runner.py tests/integration/test_claude_cli_contract.py tests/integration/test_codex_cli_contract.py -q` — 101 passed, 1 skipped.

### 7.2. CLI contract tests

**Gate для Claude:** реальный Claude binary проходит contract tests либо тесты явно пропущены из-за отсутствия бинарника.

**Gate для Codex:** кастомная сборка Codex CLI v0.128.0 проходит contract tests на `codex exec`, `codex exec resume`, stdout `--json`, JSONL session files, `view_image` через путь в prompt text и `SIGINT` stop strategy.

**Статус:** выполнено 2026-05-08.
**Проверка:** `.venv/bin/python -m pytest tests/test_coding_agent_backend.py tests/test_claude_code_backend.py tests/test_codex_backend.py tests/test_current_backend_registry.py tests/test_claude_runner.py tests/integration/test_claude_cli_contract.py tests/integration/test_codex_cli_contract.py -q` — 101 passed, 1 skipped.

### 7.3. E2E tests через Telegram

**Минимальные сценарии:**
- `/agent` показывает текущий backend и переключает на Codex;
- `/new` после выбора Codex создаёт Codex-сессию;
- `/N` на старую Claude-сессию после выбора Codex продолжает работать через Claude;
- `/stop` останавливает активный backend правильной стратегией;
- `/all` доставляет ответы от обоих backend-ов;
- `/projects` / `/pN` сохраняют и доставляют pending messages с backend-aware заголовками.

**Статус:** выполнено 2026-05-08.
**Проверка:** последний live-прогон Codex E2E описан в `dev/docs/session-reports/08-05/19-04_codex-integration-e2e-tests.md`: `tests/e2e/test_agent_backend_selection.py` — 9 passed; `tests/e2e/test_agent_backend_selection.py::test_codex_uploaded_file_uses_codex_session_header` — 1 passed; `tests/e2e/test_project_switching.py::test_codex_pending_message_delivered_on_project_return_with_backend_header` — 1 passed.

## Запрещённые варианты очередности

- **Нельзя начинать с `/agent`.** Это создаёт UI-переключатель без корректной модели владения сессиями.
- **Нельзя реализовывать `codex_backend.py` и сразу подключать его в `process_manager` без `coding_agent_backend`.** Иначе детали Codex протекут в верхние слои.
- **Нельзя переводить `process_manager` на Codex до миграции `session_manager` и `daily_session_registry`.** Иначе backend будет браться из глобального выбора, а не из ownership сессии.
- **Нельзя переводить watcher до `SessionFileSnapshot`.** Для Codex финальность turn-а определяется terminal record, а не последним assistant-текстом.
- **Нельзя обновлять `/sessions` как «15 Claude + 15 Codex».** Итоговый пользовательский список ограничивается до 15 после объединения и сортировки.

## Текущее состояние

1. CJM-нумерация `/agent` зафиксирована в BRD как `CJM-16`.
2. Backend-aware реализация Claude/Codex завершена и покрыта unit/integration/E2E gate-ами.
3. Module specs перенесены в `dev/docs/specs/realised/` без перезаписи старых Claude-only документов.
4. Оставшаяся работа перед слиянием ветки — финальная проверка полного набора тестов и аккуратная разборка незакоммиченных/чужих документов в рабочем дереве.
