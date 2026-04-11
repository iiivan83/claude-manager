# Индекс документации

Живой документ — обновляется при появлении новых документов или изменении структуры.

## Структура `dev/docs/`

- **brd/** — бизнес-требования и пользовательские пути (CJM, Customer Journey Map)
- **adr/** — записи архитектурных решений (ADR, Architecture Decision Records). Каждая запись фиксирует решение раз и навсегда — не редактируется
- **specs/** — спецификации скиллов и модулей. После реализации переносятся в `specs/realised/`
- **session-reports/** — отчёты по рабочим сессиям, сгруппированы по дате (`DD-MM/`)
- **logs/** — логи работы пайплайнов: root-cause отчёты и артефакты тестирования
- **changelog/** — история изменений проекта
  - [changelog/2026-04.md](changelog/2026-04.md) — апрель 2026: asyncio.Lock в process_manager, watcher уведомляет только владельца сессии
- **claude-md-updates/** — лог изменений CLAUDE.md (пока пусто)

## BRD и пользовательские пути

- [brd/brd-user-journeys.md](brd/brd-user-journeys.md) — основной BRD: все пользовательские сценарии бота (что он должен делать)
- [brd/brd-user-journeys.BEFORE.md](brd/brd-user-journeys.BEFORE.md) — предыдущая редакция BRD до последнего пересмотра, для исторической сверки
- [brd/brd-validation-report_29-03-23-47.md](brd/brd-validation-report_29-03-23-47.md) — отчёт технического аудита BRD от 29 марта

## Архитектурные решения

- [adr/project_architecture.md](adr/project_architecture.md) — архитектурная запись: слоистая модель кода, правила зависимостей, роли модулей

## Справочники и руководства (корневой уровень dev/docs/)

- [deployment-guide.md](deployment-guide.md) — пошаговая инструкция по развёртыванию бота: виртуальное окружение, .env, LaunchAgent, логи
- [claude-cli-stream-json-protocol.md](claude-cli-stream-json-protocol.md) — справочник протокола `stream-json`: форматы сообщений, типы событий, известные баги Claude CLI
- [review-checklists.md](review-checklists.md) — чеклисты для ревью кода (качество, безопасность, архитектура, соответствие BRD)

## Спецификации

### Активные (в работе)

- [specs/pipeline-spec.md](specs/pipeline-spec.md) — спецификация главного оркестратора `pipeline-run`
- [specs/module-dependency-graph.md](specs/module-dependency-graph.md) — граф зависимостей модулей: порядок реализации по слоям
- [specs/feature-pipeline-spec.md](specs/feature-pipeline-spec.md) — спецификация пайплайна `feature-pipeline` (доработка существующего функционала)
- [specs/autofix-e2e-skill-prompt.md](specs/autofix-e2e-skill-prompt.md) — промпт-черновик для скилла `autofix-e2e`

### Реализованные

Спецификации уже построенных модулей проекта. Лежат в `specs/realised/`:

- [specs/realised/main_spec.md](specs/realised/main_spec.md) — точка входа: настройка логов, файловая блокировка, запуск polling
- [specs/realised/config_spec.md](specs/realised/config_spec.md) — загрузка и валидация настроек из `.env`
- [specs/realised/bot_spec.md](specs/realised/bot_spec.md) — транспортный слой: обработчики Telegram-команд и сообщений
- [specs/realised/claude_runner_spec.md](specs/realised/claude_runner_spec.md) — обёртка для запуска Claude Code CLI через subprocess и протокол stream-json
- [specs/realised/process_manager_spec.md](specs/realised/process_manager_spec.md) — жизненный цикл процессов Claude: ретраи, `/stop`, прогресс
- [specs/realised/session_manager_spec.md](specs/realised/session_manager_spec.md) — привязка `chat_id ↔ session_id`, переключение сессий
- [specs/realised/session_reader_spec.md](specs/realised/session_reader_spec.md) — чтение JSONL-файлов сессий Claude Code с диска
- [specs/realised/session_watcher_spec.md](specs/realised/session_watcher_spec.md) — мониторинг сессий в реальном времени (polling каждые 2 секунды)
- [specs/realised/daily_session_registry_spec.md](specs/realised/daily_session_registry_spec.md) — дневная нумерация сессий (#1, #2, #3...)
- [specs/realised/message_splitter_spec.md](specs/realised/message_splitter_spec.md) — Markdown в HTML и разбивка на части до 4096 символов

## Сессионные отчёты

Отчёты о проделанной работе, сгруппированные по дате. Каждый файл — неизменяемый снимок одной сессии.

- **session-reports/28-03/** — 6 отчётов: миграция скиллов из проекта Soulmain, перенос `rename-entity` и `create-doc`, первая версия `docs-index`, создание BRD и архитектурного документа
- **session-reports/29-03/** — 7 отчётов: исследование, проектирование спеки пайплайна, чистка проекта, ревью и обновление BRD, реализация пайплайна
- **session-reports/30-03/** — 20 отчётов: полный прогон пайплайна, фиксы запуска и автостарта, адаптация скилла RCA, починка `/resume` и watcher, исправление иконок прогресса, E2E тестирование через Telethon, автофикс E2E
- **session-reports/03-04/** — 4 отчёта: фиксы `thinking` и таймаутов, создание и обновление стандартной dev-структуры, ArtifactFlow для pipeline-designer
- **session-reports/06-04/** — 1 отчёт: универсализация скилла `root-cause-analysis` под произвольные проекты

## Логи

### Root-cause отчёты

Глубокий анализ первопричин багов — неизменяемые документы. Новые ошибки кладутся в корень `logs/root-cause-reports/`, после починки ссылка переезжает:

- **Активные** (в корне `logs/root-cause-reports/`):
  - [logs/root-cause-reports/30-03_03-06_progress-icon-checkmark.md](logs/root-cause-reports/30-03_03-06_progress-icon-checkmark.md) — иконка прогресса не меняется на галочку
  - [logs/root-cause-reports/30-03_04-50_session-counter-reset.md](logs/root-cause-reports/30-03_04-50_session-counter-reset.md) — счётчик сессий сбрасывается
  - [logs/root-cause-reports/30-03_05-08_message-not-reaching-terminal-session.md](logs/root-cause-reports/30-03_05-08_message-not-reaching-terminal-session.md) — сообщение не доходит до терминальной сессии
  - [logs/root-cause-reports/30-03_06-17_watcher-checkmark-and-thinking-italic.md](logs/root-cause-reports/30-03_06-17_watcher-checkmark-and-thinking-italic.md) — watcher не ставит галочку, thinking не выделяется курсивом

- **В процессе починки** (`logs/root-cause-reports/fix-process/`):
  - [logs/root-cause-reports/fix-process/30-03_04-00_new-session-no-response.md](logs/root-cause-reports/fix-process/30-03_04-00_new-session-no-response.md) — новая сессия без ответа
  - [logs/root-cause-reports/fix-process/30-03_04-01_progress-icon-checkmark.md](logs/root-cause-reports/fix-process/30-03_04-01_progress-icon-checkmark.md) — иконка прогресса (в работе)
  - [logs/root-cause-reports/fix-process/30-03_06-50_tg-deep-link-confirmation-dialog.md](logs/root-cause-reports/fix-process/30-03_06-50_tg-deep-link-confirmation-dialog.md) — диалог подтверждения deep-link в Telegram

- **Исправленные** (`logs/root-cause-reports/resolved/`):
  - [logs/root-cause-reports/resolved/30-03_03-20_new-session-no-response.md](logs/root-cause-reports/resolved/30-03_03-20_new-session-no-response.md) — новая сессия без ответа (решено)
  - [logs/root-cause-reports/resolved/30-03_04-49_watcher-is-final-heuristic.md](logs/root-cause-reports/resolved/30-03_04-49_watcher-is-final-heuristic.md) — эвристика watcher для is_final (решено)
  - [logs/root-cause-reports/resolved/30-03_06-42_tg-deep-link-confirmation-dialog.md](logs/root-cause-reports/resolved/30-03_06-42_tg-deep-link-confirmation-dialog.md) — диалог подтверждения deep-link (решено)

### Результаты тестирования

Планы и результаты E2E прогонов — каждый файл неизменяемый снимок одного запуска:

- [logs/testing/e2e-test-plan_30-03-02-20.md](logs/testing/e2e-test-plan_30-03-02-20.md) — план E2E тестов от 30.03, 02:20
- [logs/testing/e2e-test-plan_30-03-11-38.md](logs/testing/e2e-test-plan_30-03-11-38.md) — план от 30.03, 11:38
- [logs/testing/e2e-test-results_30-03-11-55.md](logs/testing/e2e-test-results_30-03-11-55.md) — результаты прогона от 30.03, 11:55
- [logs/testing/e2e-test-plan_30-03-12-29.md](logs/testing/e2e-test-plan_30-03-12-29.md) — план от 30.03, 12:29
- [logs/testing/e2e-test-results_30-03-12-54.md](logs/testing/e2e-test-results_30-03-12-54.md) — результаты от 30.03, 12:54
- [logs/testing/e2e-test-plan_30-03-12-58.md](logs/testing/e2e-test-plan_30-03-12-58.md) — план от 30.03, 12:58
- [logs/testing/e2e-test-results_30-03-13-05.md](logs/testing/e2e-test-results_30-03-13-05.md) — результаты от 30.03, 13:05

## Живые vs неизменяемые документы

- **Живые** (обновляются на месте) — `docs-index.md`, `skill-index.md`, `CLAUDE.md`, `TODO.md`, `brd-user-journeys.md`
- **Неизменяемые** (каждый раз новый файл) — ADR, сессионные отчёты, root-cause отчёты, планы и результаты тестирования

## Глобальные референсы

Общие правила и стандарты лежат в `~/.claude/references/` и распространяются на все проекты:

- **writing-style-guide.md** — единый стандарт стиля письма для всех текстов
- **skill-testing-standard.md** — стандарт тестирования скиллов
- **document-naming-and-placement.md** — правила именования и размещения документов
- **agent-document-triggers.md** — правила триггеров для агентов при создании документов
- **evals-schema.md** — единая JSON-схема формата `evals.json` для тестовых наборов
- **cli-testing-patterns.md** — паттерны тестирования через CLI-подпроцесс
- **pipeline-skill-checklist.md** — чеклист для проверки пайплайн-скиллов
