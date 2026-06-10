# Сессия 10-06: разбиение process_manager по ответственностям

## Коротко

Большой модуль управления CLI-процессами был разрезан на маленькие файлы, чтобы дальше не менять retry, `/stop` и чтение событий внутри одного файла больше 1000 строк. Поведение для пользователя не менялось: это рефакторинг внутренней структуры с сохранением старого входа через `process_manager.py`.

## Резюме

В этой сессии большой `process_manager.py` разрезан на небольшие модули по ответственности: запуск, отправка, чтение событий, retry, `/stop`, backend-aware path и общие типы. Старый модуль оставлен как compatibility facade, поэтому внешний контракт для production-кода и старых импортов сохранён.

## Рабочие файлы

- **Process lifecycle code** — `src/claude_manager/process_manager.py` и новые `src/claude_manager/process_*.py`; это продакшен-код, который управляет запуском Claude/Codex CLI, retry, `/stop` и обработкой stream-json событий.
- **Regression tests** — `tests/test_process_manager.py`, `tests/test_stop_triggers_retry_blackbox.py`, `tests/test_stop_triggers_retry_whitebox.py`, `tests/integration/test_cwd_pinning_across_retries.py`; эти тесты доказывают, что разрез не сломал старые сценарии.
- **Documentation artifacts** — ADR, CLAUDE.md update log, docs-index update, architecture update and refactoring orchestrator log; эти документы фиксируют, почему файл разрезан и где теперь искать конкретную ответственность.

## Изменённые файлы

- **`src/claude_manager/process_manager.py`** — изменён — стал фасадом, который реэкспортирует старые имена и сохраняет совместимость.
- **`src/claude_manager/process_types.py`** — создан — общие типы, dataclass-результаты и callback-типы process lifecycle.
- **`src/claude_manager/process_events.py`** — создан — чтение stream-json событий, progress/result и обновление session_id.
- **`src/claude_manager/process_stop.py`** — создан — `/stop`, стратегия остановки процессов и остановка всех процессов.
- **`src/claude_manager/process_lifecycle.py`** — создан — temp session id, запуск subprocess и restart.
- **`src/claude_manager/process_retry.py`** — создан — retry-loop, ожидание с проверкой `/stop` и permanent-error classification.
- **`src/claude_manager/process_send.py`** — создан — `send_message` dispatcher и legacy Claude send path.
- **`src/claude_manager/process_backend_send.py`** — создан — backend-aware Claude/Codex send path.
- **`tests/test_process_manager.py`** — изменён — patch-точки перенесены на новые профильные модули.
- **`tests/test_stop_triggers_retry_blackbox.py`** — изменён — проверки `/stop` и retry адаптированы к разрезу.
- **`tests/test_stop_triggers_retry_whitebox.py`** — изменён — whitebox-проверки retry/stop адаптированы к новым модулям.
- **`tests/integration/test_cwd_pinning_across_retries.py`** — изменён — mock `_process_events` перенесён на `process_retry`.
- **`dev/docs/logs/refactoring/10.06_14.17-test-guarded-refactoring-process-manager-split/orchestrator-log.json`** — создан — журнал шагов refactoring-прохода, включая timeout автоматического planner.
- **`CLAUDE.md`** — изменён — структура проекта и архитектурные принципы описывают новые `process_*` модули.
- **`dev/docs/adr/project_architecture.md`** — изменён — инфраструктурный слой описывает `process_manager.py` как фасад и перечисляет новые process lifecycle модули.
- **`dev/docs/docs-index.md`** — изменён — добавлено назначение `logs/refactoring/`.
- **`dev/docs/adr/10.06_14.44-session-change-documenter-process-manager-responsibility-split.md`** — создан — ADR по разбиению `process_manager.py`.
- **`dev/docs/claude-md-updates/10.06_14.44-session-change-documenter.md`** — создан — лог изменения `CLAUDE.md`.
- **`dev/docs/session-reports/10-06/14-44_process-manager-responsibility-split.md`** — создан — этот отчёт.

## Решения

- **Решение**: оставить `process_manager.py` как compatibility facade. **Причина**: production-код и часть тестов исторически импортируют старые публичные и приватные имена из этого модуля.
- **Решение**: разбить process lifecycle по ответственности, а не по тестовым группам. **Причина**: будущие правки чаще будут относиться к одному поведению: retry, `/stop`, чтению событий, запуску процесса или backend-aware отправке.
- **Решение**: не менять пользовательское поведение. **Причина**: задача была рефакторингом техдолга, поэтому тесты должны доказывать сохранение старых сценариев, а не вводить новую функциональность.

## Выполненные команды

- `.venv/bin/python -m pytest tests/test_process_manager.py tests/integration/test_message_path.py tests/integration/test_cwd_pinning_across_retries.py tests/test_stop_triggers_retry_whitebox.py tests/test_stop_triggers_retry_blackbox.py -q`
- `python3.13 -m py_compile src/claude_manager/process_manager.py src/claude_manager/process_send.py src/claude_manager/process_backend_send.py src/claude_manager/process_retry.py src/claude_manager/process_lifecycle.py src/claude_manager/process_events.py src/claude_manager/process_stop.py src/claude_manager/process_types.py tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py tests/integration/test_cwd_pinning_across_retries.py`
- `git diff --check -- src/claude_manager/process_manager.py src/claude_manager/process_send.py src/claude_manager/process_backend_send.py src/claude_manager/process_retry.py src/claude_manager/process_lifecycle.py src/claude_manager/process_events.py src/claude_manager/process_stop.py src/claude_manager/process_types.py tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py tests/integration/test_cwd_pinning_across_retries.py`
- `.venv/bin/python -m pytest tests/ -q`

## Проблемы и решения

- **Проблема**: автоматический planner-проход через SuperPowers завис и не создал артефакты. **Решение**: процесс остановлен, событие зафиксировано в `orchestrator-log.json`, рефакторинг продолжен напрямую с test-guarded проверками.
- **Проблема**: часть whitebox-тестов патчила приватные функции через старый `process_manager.py`. **Решение**: patch-точки перенесены на новые владельцы поведения: `process_events`, `process_retry`, `process_send` и `process_lifecycle`.
- **Проблема**: файл был выше 1000 строк и молча продолжать его рост было нельзя. **Решение**: большой модуль уменьшен с 1233 до 84 строк, все новые `process_*.py` остались меньше 300 строк.

## Результаты тестирования

- Точечный process/retry/stop/message-path набор: 107 passed.
- Полный набор `tests/`: 1198 passed, 4 skipped, 3 warnings.
- Warnings относятся к `python-telegram-bot` в тесте Telegram sender и не связаны с process lifecycle refactoring.

## Проверки

- Файлы process lifecycle компилируются через `python3.13 -m py_compile`.
- `git diff --check` по изменённым code/test файлам прошёл без ошибок.
- `orchestrator-log.json` читается через `python3.13 -m json.tool`.
- В документах документалиста нет markdown-таблиц, чтобы они не ломались при пересылке в Telegram.

## Риски и ограничения

- Рабочее дерево было грязным до начала рефакторинга. В нём есть изменения `.claude/.agents`, `src/claude_manager/claude_code_session_file_reader.py`, `tests/test_claude_code_backend.py` и другие untracked документы; они не относятся к этому рефакторингу.
- Некоторые тесты всё ещё патчат приватные внутренние функции. После разбиения patch-точки стали точнее, но это остаётся whitebox-сцеплением с реализацией.
- E2E через реальный Telegram не запускались, потому что поведение пользовательских сценариев не менялось и полный unit/integration suite прошёл.

## Продолжение

- Для retry-логики работать в `process_retry.py`, для `/stop` — в `process_stop.py`, для stream-json событий — в `process_events.py`.
- Не возвращать новую функциональность в `process_manager.py`; он должен оставаться тонким compatibility facade.
- Перед коммитом текущей сессии не смешивать этот рефакторинг с посторонними изменениями, которые уже были в рабочем дереве.

## Контекст для следующей сессии

Рабочее дерево уже содержало посторонние изменения до запуска рефакторинга: изменения и удаления в `.claude/.agents`, правки `src/claude_manager/claude_code_session_file_reader.py`, `tests/test_claude_code_backend.py`, `dev/docs/docs-index.md` и несколько untracked документов. Они не относятся к этому разрезу и не должны попадать в коммит рефакторинга без отдельного решения.

Следующий технический шаг по process lifecycle — не новый большой разрез, а точечная работа в конкретном модуле. Например, retry-изменения должны идти в `process_retry.py`, `/stop` — в `process_stop.py`, чтение stream-json событий — в `process_events.py`.
