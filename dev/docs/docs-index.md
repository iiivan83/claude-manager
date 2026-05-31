# Индекс документации

Живой документ — обновляется при появлении новых документов или изменении структуры.

## Структура `dev/docs/`

- **brd/** — бизнес-требования и пользовательские пути (CJM, Customer Journey Map)
- **adr/** — записи архитектурных решений (ADR, Architecture Decision Records). Каждая запись фиксирует решение раз и навсегда — не редактируется
- **specs/** — спецификации скиллов и модулей. После реализации переносятся в `specs/realised/`
- **session-reports/** — отчёты по рабочим сессиям, сгруппированы по дате (`DD-MM/`)
- **logs/** — логи работы пайплайнов: root-cause отчёты и артефакты тестирования
- **changelog/** — история изменений проекта
  - [changelog/2026-04.md](changelog/2026-04.md) — апрель 2026 (11.04–25.04): asyncio.Lock в process_manager, watcher уведомляет только владельца сессии, text-блоки в прогрессе, ConnectionError, /restart, MediaGroup, SHOW_FILE, watchdog тишины, EDEADLK retry, silence mode
  - [changelog/12.04_19.05-md-file-delivery.md](changelog/12.04_19.05-md-file-delivery.md) — 12.04.2026: отправка файлов из ответа Claude пользователю через маркеры `[SEND_FILE:path]` и telegramify-markdown
  - [changelog/12.04_21.17-session-id-callback.md](changelog/12.04_21.17-session-id-callback.md) — 12.04.2026: раннее уведомление о смене session_id через callback — закрытие гонки watcher/handler при создании новых сессий
  - [changelog/13.04_12.08-cross-project-session-race-condition.md](changelog/13.04_12.08-cross-project-session-race-condition.md) — 13.04.2026: исправление гонки при переключении проектов — глобальная пауза watcher, кросс-проверка записей реестра, защита от лог-спама
  - [changelog/17.04_12.58-feature-pipeline-file-send-header-overflow-fix.md](changelog/17.04_12.58-feature-pipeline-file-send-header-overflow-fix.md) — 17.04.2026: фикс переполнения первого чанка при отправке текстового файла — резервирование места под заголовок в `render_file_for_telegram`
  - [changelog/21.04_17.58-text-block-progress-display.md](changelog/21.04_17.58-text-block-progress-display.md) — 21.04.2026: показ text-блоков Claude в прогрессе Telegram (не только thinking)
  - [changelog/22.04_00.14-connection-reset-error-fix.md](changelog/22.04_00.14-connection-reset-error-fix.md) — 22.04.2026: перехват ConnectionError вместо BrokenPipeError, проверка is_closing(), устранение дублирования логов
  - [changelog/22.04_01.15-restart-media-group-show-file.md](changelog/22.04_01.15-restart-media-group-show-file.md) — 22.04.2026: команда /restart, фото-альбомы (MediaGroupAggregator), маркер [SHOW_FILE], HTTP-таймауты
  - [changelog/22.04_11.36-agent-silence-watchdog.md](changelog/22.04_11.36-agent-silence-watchdog.md) — 22.04.2026: watchdog тишины Agent tool (60 сек), автоочистка зависших пауз (120 сек), логирование INFO
  - [changelog/22.04_13.57-edeadlk-retry-project-switch.md](changelog/22.04_13.57-edeadlk-retry-project-switch.md) — 22.04.2026: retry OSError (EDEADLK) при загрузке состояния, 5×1 сек с fallback
- **claude-md-updates/** — лог изменений CLAUDE.md
  - [claude-md-updates/10.04_22.19-file-delivery-rule.md](claude-md-updates/10.04_22.19-file-delivery-rule.md) — 10.04.2026: глобальное правило File Delivery Rule в `~/.claude/CLAUDE.md` — маркеры `[SEND_FILE:path]` для доставки файлов через бот
  - [claude-md-updates/03.05_10.58-venv-launchd-tcc-migration.md](claude-md-updates/03.05_10.58-venv-launchd-tcc-migration.md) — 03.05.2026: миграция venv и скрипта запуска из TCC-зоны Desktop — новые принципы изоляции, обновлённые пути (теперь устарело — см. запись от 28.05.2026 ниже про обратную миграцию на Linux)
  - [claude-md-updates/28.05_19.45-session-change-documenter.md](claude-md-updates/28.05_19.45-session-change-documenter.md) — 28.05.2026: миграция инфраструктуры с macOS на Linux — удалены принципы TCC-изоляции venv и скрипта, retry-обёртка для launchd, буллет про EDEADLK; переписаны принципы verify-before-and-after и запрета самоперезапуска под systemctl; обновлены команды разработки, структура проекта, ОС-линия

## BRD и пользовательские пути

- [brd/brd-user-journeys.md](brd/brd-user-journeys.md) — основной BRD: все пользовательские сценарии бота (что он должен делать)
- [brd/brd-user-journeys.BEFORE.md](brd/brd-user-journeys.BEFORE.md) — предыдущая редакция BRD до последнего пересмотра, для исторической сверки
- [brd/brd-validation-report_29-03-23-47.md](brd/brd-validation-report_29-03-23-47.md) — отчёт технического аудита BRD от 29 марта

## Архитектурные решения

- [adr/project_architecture.md](adr/project_architecture.md) — архитектурная запись: слоистая модель кода, правила зависимостей, роли модулей

## Справочники и руководства (корневой уровень dev/docs/)

- [deployment-guide.md](deployment-guide.md) — пошаговая инструкция по развёртыванию бота под Linux: виртуальное окружение, .env, systemd user service, логи
- [bot-handoff-package-guide.md](bot-handoff-package-guide.md) — инструкция для передачи бота другому пользователю: что входит в архив, какие личные данные исключаются, как запустить и где искать код для исправлений
- [bot-launch-infrastructure.md](bot-launch-infrastructure.md) — карта всех компонентов запуска бота под Linux: что где лежит, systemd user service, цепочка ExecStart → entry point → Python, /restart через отвязанный subprocess, диагностика через journalctl
- [claude-cli-stream-json-protocol.md](claude-cli-stream-json-protocol.md) — справочник протокола `stream-json`: форматы сообщений, типы событий, известные баги Claude CLI
- `router-configuration.md` — локальная конфигурация MikroTik L009UiGS: сетевая топология, firewall, DNS, WireGuard VPN, контентная фильтрация, список устройств. Не включается в handoff-архивы для передачи бота другому пользователю
- [review-checklists.md](review-checklists.md) — чеклисты для ревью кода (качество, безопасность, архитектура, соответствие BRD)

## Спецификации

### Активные (в работе)

- [specs/module-dependency-graph.md](specs/module-dependency-graph.md) — backend-aware граф зависимостей модулей: порядок реализации поддержки Claude/Codex по слоям
- [specs/codex_support_spec_implementation_order.md](specs/codex_support_spec_implementation_order.md) — рабочая очередь реализации Codex-support спек: фазы, зависимости, gates и запреты на опасный порядок
- [specs/claude_code_backend_spec.md](specs/claude_code_backend_spec.md) — реализация общего backend-интерфейса для Claude Code CLI
- [specs/codex_backend_spec.md](specs/codex_backend_spec.md) — реализация общего backend-интерфейса для Codex CLI
- [specs/current_backend_registry_spec.md](specs/current_backend_registry_spec.md) — персистентный выбор текущего CLI-бэкенда для новых сессий
- [specs/daily_session_registry_spec.md](specs/daily_session_registry_spec.md) — backend-aware дневная нумерация сессий
- [specs/session_manager_spec.md](specs/session_manager_spec.md) — backend-aware привязка `chat_id ↔ ActiveSession(session_id, backend)`
- [specs/process_manager_spec.md](specs/process_manager_spec.md) — backend-aware жизненный цикл subprocess-ов, ретраи и `/stop`
- [specs/session_watcher_spec.md](specs/session_watcher_spec.md) — backend-aware watcher для фоновой доставки сообщений из Claude и Codex
- [specs/unread_buffer_spec.md](specs/unread_buffer_spec.md) — буфер непрочитанных сообщений по ключу `(session_id, backend)`
- [specs/telegram_agent_backend_integration_spec.md](specs/telegram_agent_backend_integration_spec.md) — integration spec для `bot.py`, `claude_interaction.py`, `claude_runner.py` и backend-aware контрактов
- [specs/agent_backend_selection_user_journey_spec.md](specs/agent_backend_selection_user_journey_spec.md) — пользовательский сценарий `/agent`: выбор CLI-бэкенда для новых сессий
- [specs/project_manager_spec.md](specs/project_manager_spec.md) — спецификация модуля `project_manager`: сканирование проектов, переключение, восстановление последнего выбора
- [specs/31.05_04.56-reply-routing-v1-spec.md](specs/31.05_04.56-reply-routing-v1-spec.md) — пользовательское и архитектурное поведение адресных text reply на сообщения бота
- [specs/31.05_05.02-reply-routing-v1-implementation-plan.md](specs/31.05_05.02-reply-routing-v1-implementation-plan.md) — план реализации reply-routing v1 по слоям: registry, delivery, input и тесты
- [specs/31.05_05.28-reply-anchor-busy-hardening-spec.md](specs/31.05_05.28-reply-anchor-busy-hardening-spec.md) — доработка busy-сценариев reply-anchor: busy-ответы не должны стирать anchor активного хода и могут отдельно стать Telegram reply к rejected-сообщению

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
- [specs/realised/20-04_media-group-handling-spec.md](specs/realised/20-04_media-group-handling-spec.md) — спецификация обработки медиа-групп (альбомов фото) в Telegram-боте
- [specs/realised/autofix-e2e-skill-prompt.md](specs/realised/autofix-e2e-skill-prompt.md) — промпт-черновик для скилла `autofix-e2e`
- [specs/realised/coding_agent_backend_spec.md](specs/realised/coding_agent_backend_spec.md) — общий интерфейс CLI-бэкендов Claude/Codex и контракт владения сессией
- [specs/realised/feature-pipeline-spec.md](specs/realised/feature-pipeline-spec.md) — спецификация пайплайна `feature-pipeline` (доработка существующего функционала)
- [specs/realised/pipeline-spec.md](specs/realised/pipeline-spec.md) — спецификация главного оркестратора `pipeline-run`

## Сессионные отчёты

Отчёты о проделанной работе, сгруппированные по дате. Каждый файл — неизменяемый снимок одной сессии.

- **session-reports/30-03/** — полный прогон пайплайна, фиксы запуска и автостарта, адаптация скилла RCA, починка `/resume` и watcher, исправление иконок прогресса, E2E тестирование через Telethon
- **session-reports/03-04/** — фиксы `thinking` и таймаутов, создание и обновление стандартной dev-структуры, ArtifactFlow для pipeline-designer
- **session-reports/06-04/** — универсализация скилла `root-cause-analysis` под произвольные проекты
- **session-reports/09-04/** — **session-reports/14-04/** — файловая доставка, session_id callback, гонки при переключении проектов, E2E тесты
- **session-reports/19-04/** — **session-reports/22-04/** — root-cause исправления: ConnectionError, прогресс text-блоков, рестарт бота, watchdog тишины Agent, EDEADLK retry
- **session-reports/10-05/** — диагностика и исправление бага pending-доставки при возврате в проект: `silence mode` скрывал промежуточные сообщения, а доставка очищала snapshot непрочитанных
- **session-reports/13-05/** — исправление preview Codex-сессий в `/sessions`: фильтрация bootstrap-блока `AGENTS.md instructions`, извлечение подписи файловой задачи как исходного запроса пользователя, регрессионные тесты, полный pytest-прогон; отчёт о баге `/restart` при активных дочерних Codex-задачах
- **session-reports/14-05/** — реализация и стабилизация глобального режима `/all`: мониторинг всех проектов, кликабельные команды `/3s12`, сохранение pending-доставки и восстановление all-mode после неудачного переключения проекта
- **session-reports/15-05/** — handoff по ветке `codex-support-spec-implementation-cycle`: состояние уже влитой Codex-support работы, незакоммиченные summary `/sessions` и оптимизация `/all`, план стабилизации и отдельного фикса `/restart`
- **session-reports/30-05/** — handoff по RCA медленного переключения проектов: причина 7-17 секунд, решение через 4-дневный Codex session index, ограничения по pending и план продолжения; handoff по дизайну reply-якорей для Telegram-ответов, watcher-сообщений, `/all`, `/stop`, переключения проектов и сессий
- **session-reports/31-05/** — handoff по текущему состоянию подготовки reply-якорей: перенос доставки Telegram-ответов из `bot.py` в `telegram_response_delivery.py`, целевая проверка `172 passed`, size gate для больших файлов и следующие шаги реализации

## Логи

### Root-cause отчёты

Глубокий анализ первопричин багов — неизменяемые документы. Хранятся в нескольких папках по статусу:

- **Исправленные** (`logs/root-cause-reports/realized/`) — 12 отчётов, включая: session-reader path encoding, feature-pipeline delegation failures, connection-reset-error, EDEADLK project switch, cross-project message leak, retry cascade cwd mismatch, first-message-silent-stale-session-id
- **Решённые** (`logs/root-cause-reports/resolved/`) — 3 отчёта: new-session-no-response, watcher is_final heuristic, tg deep-link confirmation dialog
- **Открытые** (`logs/root-cause-reports/`) — текущие исследования: retry-loop session proliferation, concurrent session callback leak, night session proliferation misdiagnosis, /stop orphan subprocesses, brd-generator slim fix insufficient
- **Отдельные** (`root-cause-reports/`) — исследования не привязанные к пайплайну: telegram album delivery, SEND_FILE inline bug, restart self-kill, bash tool SIGKILL, readline timeout
- [logs/pending-delegations.md](logs/pending-delegations.md) — трекер делегированных рекомендаций из root-cause отчётов

### Результаты тестирования

Планы и результаты E2E прогонов — каждый файл неизменяемый снимок одного запуска:

- [logs/testing/e2e-test-plan_30-03-02-20.md](logs/testing/e2e-test-plan_30-03-02-20.md) — план E2E тестов от 30.03, 02:20
- [logs/testing/e2e-test-plan_30-03-11-38.md](logs/testing/e2e-test-plan_30-03-11-38.md) — план от 30.03, 11:38
- [logs/testing/e2e-test-results_30-03-11-55.md](logs/testing/e2e-test-results_30-03-11-55.md) — результаты прогона от 30.03, 11:55
- [logs/testing/e2e-test-plan_30-03-12-29.md](logs/testing/e2e-test-plan_30-03-12-29.md) — план от 30.03, 12:29
- [logs/testing/e2e-test-results_30-03-12-54.md](logs/testing/e2e-test-results_30-03-12-54.md) — результаты от 30.03, 12:54
- [logs/testing/e2e-test-plan_30-03-12-58.md](logs/testing/e2e-test-plan_30-03-12-58.md) — план от 30.03, 12:58
- [logs/testing/e2e-test-results_30-03-13-05.md](logs/testing/e2e-test-results_30-03-13-05.md) — результаты от 30.03, 13:05
- [logs/testing/e2e-test-plan_10-04-18-54.md](logs/testing/e2e-test-plan_10-04-18-54.md) — план E2E тестов от 10.04, 18:54
- [logs/testing/e2e-test-results_10-04-19-02.md](logs/testing/e2e-test-results_10-04-19-02.md) — результаты прогона от 10.04, 19:02
- [logs/testing/e2e-test-plan_13-04-16-50.md](logs/testing/e2e-test-plan_13-04-16-50.md) — план от 13.04, 16:50
- [logs/testing/e2e-test-results_13-04-17-00.md](logs/testing/e2e-test-results_13-04-17-00.md) — результаты от 13.04, 17:00

## Живые vs неизменяемые документы

- **Живые** (обновляются на месте) — `docs-index.md`, `CLAUDE.md`, `brd-user-journeys.md`
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
