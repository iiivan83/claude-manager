# Граф зависимостей модулей

Дата обновления: 07-05-2026

Источники:
- `dev/docs/brd/brd-user-journeys.md`
- `CLAUDE.md`
- `dev/docs/specs/coding_agent_backend_spec.md`
- `dev/docs/specs/claude_code_backend_spec.md`
- `dev/docs/specs/codex_backend_spec.md`
- `dev/docs/specs/current_backend_registry_spec.md`
- `dev/docs/specs/daily_session_registry_spec.md`
- `dev/docs/specs/session_manager_spec.md`
- `dev/docs/specs/process_manager_spec.md`
- `dev/docs/specs/session_watcher_spec.md`
- `dev/docs/specs/unread_buffer_spec.md`
- `dev/docs/session-reports/07-05/10-03_codex-support-specs-analysis.md`

Этот граф заменяет Claude-only граф от 29-03-2026 для работ по поддержке Codex. Старые реализованные спеки остаются в `dev/docs/specs/realised/` как исторический снимок текущей реализации.

## Ключевые решения

- **Контракт сессии расширяется до пары `(session_id, backend)`**. Голый `session_id` больше не является полным идентификатором там, где нужно запускать процесс, читать файл сессии, искать владельца или доставлять непрочитанные сообщения.
- **Текущий backend используется только для новых сессий**. Уже существующая сессия всегда открывается тем CLI, который её создал. Команда `/agent` не переносит активную или старую сессию между Claude и Codex.
- **Дневная нумерация остаётся общей**. Пользователь видит одну линейку `/1`, `/2`, `/3`, а не отдельные номера для Claude и Codex.
- **Watcher работает по одному экземпляру на backend**. Координация общих операций (`pause_all`, `resume_all`, `reset_state`) остаётся фасадом поверх двух backend-aware watcher-инстанций.
- **`claude_runner.py` становится тонкой subprocess-обёрткой**. Формирование CLI-команды, кодирование stdin и парсинг stdout переезжают в реализации `CodingAgentBackend`.
- **Telegram-facing слой подключается последним**. `bot.py` и `claude_interaction.py` должны только связать уже готовые backend-aware контракты с пользовательскими командами.
- **`/agent` нельзя реализовывать раньше session ownership**. Иначе пользователь сможет переключить глобальный backend, но `/N`, `/stop`, watcher и unread-доставка продолжат жить в Claude-only модели.

## Список CJM

- **CJM-01** — Первый запуск и настройка
- **CJM-02** — Отправка текстового сообщения
- **CJM-03** — Отправка фотографии или файла
- **CJM-04** — Создание новой сессии (`/new`)
- **CJM-05** — Просмотр списка сессий (`/sessions`)
- **CJM-06** — Переключение на сессию (`/N`)
- **CJM-07** — Мониторинг всех сессий (`/all`)
- **CJM-08** — Остановка активного CLI-процесса (`/stop`)
- **CJM-09** — Защита от двойного запуска бота
- **CJM-10** — Живой стриминг промежуточных обновлений через watcher
- **CJM-11** — Переключение между проектами
- **CJM-12** — Доставка файлов из ответа CLI
- **CJM-13** — Отправка альбома фотографий
- **CJM-14** — Перезапуск бота (`/restart`)
- **CJM-15** — Режим тишины (Silence mode)
- **CJM-16** — Переключение CLI-бэкенда (`/agent`)

## Слой 0: базовые независимые модули

- **`config`** — загрузка `.env`, пути к state-файлам, рабочая директория, белый список пользователей. CJM: 01, все сквозные механизмы. Зависимости: нет.
- **`message_splitter`** — подготовка HTML/Markdown сообщений Telegram и разбивка по лимиту 4096 символов. CJM: 02, 03, 07, 12. Зависимости: нет.
- **`coding_agent_backend`** — общий интерфейс CLI-бэкендов, enum `BackendName`, DTO для событий, файлов сессий, stop strategy и unread state. CJM: 02, 03, 04, 05, 06, 07, 08, 16. Зависимости: нет.

## Слой 1: backend implementations и state без верхних слоёв

- **`claude_code_backend`** — реализация `CodingAgentBackend` для Claude Code CLI. CJM: 02, 03, 04, 05, 06, 07, 08, 16. Зависимости: `coding_agent_backend`.
- **`codex_backend`** — реализация `CodingAgentBackend` для Codex CLI. CJM: 02, 03, 04, 05, 06, 07, 08, 16. Зависимости: `coding_agent_backend`.
- **`current_backend_registry`** — глобальное персистентное хранилище выбранного backend для новых сессий (`~/.claude-manager-current-backend`). CJM: 01, 04, 16. Зависимости: `config`, `coding_agent_backend`.
- **`daily_session_registry`** — дневная нумерация сессий с записью `DailySessionEntry(session_id, backend)`. CJM: 02, 03, 04, 05, 06, 07, 11. Зависимости: `config`, `coding_agent_backend`.
- **`unread_buffer`** — тонкий in-memory буфер cursor-состояния непрочитанных сообщений по ключу `(session_id, backend)`. CJM: 11. Зависимости: `config`, `coding_agent_backend`.
- **`claude_runner`** — тонкая subprocess-обёртка для запуска CLI через backend-адаптер. CJM: 02, 03, 04, 06, 08. Зависимости: `config`, `coding_agent_backend`.
- **`session_reader`** — legacy/compatibility утилита для чтения Claude-сессий. В backend-aware архитектуре прямое чтение файлов должно идти через `CodingAgentBackend`; `session_reader` остаётся только для совместимости до завершения миграции потребителей. CJM: 05, 06, 07. Зависимости: `config`.

## Слой 2: ownership и lifecycle

- **`session_manager`** — связка `chat_id ↔ ActiveSession(session_id, backend)`, режим `/all`, переключение `/N`, миграция старого `sessions.json`. CJM: 02, 03, 04, 06, 07, 11. Зависимости: `config`, `daily_session_registry`, `session_reader`, `coding_agent_backend`.
- **`process_manager`** — lifecycle subprocess-ов по ключу `(session_id, backend)`, ретраи, `/stop`, temp→real remap, backend-specific stop strategy. CJM: 02, 03, 04, 08. Зависимости: `config`, `coding_agent_backend`, `current_backend_registry`, `claude_runner`.

## Слой 3: фоновые сессии и проекты

- **`session_watcher`** — мониторинг файлов сессий двумя backend-aware watcher-инстанциями, buffer-and-hold, pause/resume, доставка watcher-сообщений с backend. CJM: 02, 03, 07, 10, 11. Зависимости: `config`, `coding_agent_backend`, `daily_session_registry`, `session_manager`.
- **`project_manager`** — переключение проектов, глобальная пауза watcher, сброс state-модулей, восстановление pending messages. CJM: 11. Зависимости: `config`, `session_manager`, `daily_session_registry`, `session_watcher`, `unread_buffer`, `coding_agent_backend`.

## Слой 4: orchestration без Telegram API

- **`claude_interaction`** — оркестрация запроса из Telegram в активный CLI backend: проверка занятости, pause/resume watcher, watchdog тишины, callback-и progress/retry/session-id, обработка `SendResult`. Имя файла остаётся историческим; поведение становится backend-aware. CJM: 02, 03, 04, 08, 11. Зависимости: `config`, `session_manager`, `daily_session_registry`, `process_manager`, `session_watcher`, `coding_agent_backend`.

## Слой 5: Telegram transport

- **`bot`** — обработчики Telegram-команд и сообщений: `/agent`, `/new`, `/sessions`, `/N`, `/all`, `/stop`, `/projects`, file delivery, media groups, silence mode, restart. CJM: 01–16. Зависимости: все нижние слои.

## Слой 6: запуск приложения

- **`main`** — настройка логов, single-instance lock, загрузка конфигурации, запуск Telegram polling. CJM: 01, 09. Зависимости: `config`, `bot`, `session_manager`, `current_backend_registry`.

## Порядок реализации

1. **Закрыть документационные пробелы** — этот граф, `telegram_agent_backend_integration_spec.md`, `agent_backend_selection_user_journey_spec.md`.
2. **Ввести общий контракт** — `coding_agent_backend`.
3. **Добавить backend implementations и глобальный выбор backend-а** — `claude_code_backend`, `codex_backend`, `current_backend_registry`, новые константы в `config`, тонкий `claude_runner`.
4. **Перевести хранение владения сессией** — `daily_session_registry`, `session_manager`, `unread_buffer`.
5. **Перевести lifecycle процессов** — `process_manager` с composite key, backend-specific stop strategy, `SendResult.backend`.
6. **Перевести фоновые ответы и переключение проектов** — `session_watcher`, `project_manager`, pending delivery через `SessionUnreadState`.
7. **Подключить Telegram-facing слой** — `claude_interaction`, `bot.py`, `/agent`, `/new`, `/sessions`, `/N`, `/stop`, media/file flows.
8. **Обновить запуск и тесты** — `main.post_init`, unit/integration/E2E проверки полного пользовательского контракта.

## Граф зависимостей

```text
main
 ├── config
 ├── current_backend_registry
 │    ├── config
 │    └── coding_agent_backend
 ├── session_manager
 │    ├── config
 │    ├── coding_agent_backend
 │    ├── daily_session_registry
 │    │    ├── config
 │    │    └── coding_agent_backend
 │    └── session_reader
 │         └── config
 └── bot
      ├── config
      ├── message_splitter
      ├── coding_agent_backend
      │    ├── claude_code_backend
      │    └── codex_backend
      ├── current_backend_registry
      ├── claude_interaction
      │    ├── session_manager
      │    ├── daily_session_registry
      │    ├── process_manager
      │    │    ├── config
      │    │    ├── coding_agent_backend
      │    │    ├── current_backend_registry
      │    │    └── claude_runner
      │    │         ├── config
      │    │         └── coding_agent_backend
      │    └── session_watcher
      │         ├── config
      │         ├── coding_agent_backend
      │         ├── daily_session_registry
      │         └── session_manager
      ├── project_manager
      │    ├── config
      │    ├── session_manager
      │    ├── daily_session_registry
      │    ├── session_watcher
      │    ├── unread_buffer
      │    │    ├── config
      │    │    └── coding_agent_backend
      │    └── coding_agent_backend
      └── transport utilities
```

## Трейсабельность CJM → модули

- **CJM-01** → `config`, `main`, `bot`, `session_manager`, `daily_session_registry`, `current_backend_registry`
- **CJM-02** → `bot`, `claude_interaction`, `session_manager`, `process_manager`, `claude_runner`, `coding_agent_backend`, конкретный backend, `message_splitter`, `daily_session_registry`, `session_watcher`
- **CJM-03** → `bot`, `telegram_file_downloader`, `media_group_handler`, `claude_interaction`, `process_manager`, `coding_agent_backend`, конкретный backend, `message_splitter`, `daily_session_registry`, `session_watcher`
- **CJM-04** → `bot`, `current_backend_registry`, `session_manager`, `daily_session_registry`, `process_manager`
- **CJM-05** → `bot`, `coding_agent_backend`, `claude_code_backend`, `codex_backend`, `daily_session_registry`, `session_manager`, `message_splitter`
- **CJM-06** → `bot`, `session_manager`, `daily_session_registry`, `coding_agent_backend`, `process_manager`
- **CJM-07** → `bot`, `session_watcher`, `coding_agent_backend`, `daily_session_registry`, `session_manager`
- **CJM-08** → `bot`, `session_manager`, `process_manager`, `coding_agent_backend`
- **CJM-09** → `main`, `config`
- **CJM-10** → `session_watcher`, `coding_agent_backend`, конкретный backend, `bot`, `claude_interaction`
- **CJM-11** → `bot`, `project_manager`, `session_watcher`, `unread_buffer`, `session_manager`, `daily_session_registry`, `coding_agent_backend`
- **CJM-12** → `bot`, `file_delivery`, `file_sender`, `message_splitter`
- **CJM-13** → `bot`, `media_group_handler`, `telegram_file_downloader`, `claude_interaction`, `process_manager`
- **CJM-14** → `bot`
- **CJM-15** → `bot`, `silence_mode_registry`
- **CJM-16** → `bot`, `current_backend_registry`, `coding_agent_backend`, `session_manager`, `daily_session_registry`
